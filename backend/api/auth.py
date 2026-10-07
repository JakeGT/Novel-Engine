"""Authentication HTTP routes: login, callback, session, and logout.

Hosted mode delegates to OIDC; local mode issues a trusted single-user session.
Session cookies are host-only and CSRF-protected for cookie-authenticated writes.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from backend.api.dependencies import get_auth_service, get_settings, get_tenancy_service
from backend.core.config import Settings
from backend.core.logging import log_event
from backend.domain.errors import ForbiddenError, UnauthorizedError, ValidationError
from backend.domain.identity import AccessScope, AuthMode, Session
from backend.domain.tenancy import Permission
from backend.services.auth_service import AuthenticatedSession, AuthService
from backend.services.tenancy_service import TenancyService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookies(response: Response, settings: Settings, session: Session) -> None:
    common: dict[str, Any] = {
        "max_age": settings.SESSION_TTL_SECONDS,
        "secure": settings.session_cookie_secure,
        "samesite": "lax",
        "path": "/",
    }
    response.set_cookie(settings.SESSION_COOKIE_NAME, session.id, httponly=True, **common)
    # CSRF token is intentionally readable by the SPA (double-submit + server check).
    response.set_cookie(settings.CSRF_COOKIE_NAME, session.csrf_token, httponly=False, **common)


def _clear_session_cookies(response: RedirectResponse | JSONResponse, settings: Settings) -> None:
    response.delete_cookie(settings.SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(settings.CSRF_COOKIE_NAME, path="/")


async def require_session(
    request: Request,
    response: Response,
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedSession:
    resolved = await auth.resolve(request.cookies.get(settings.SESSION_COOKIE_NAME))
    if resolved is None:
        raise UnauthorizedError("A valid session is required.")
    if resolved.rotated_session is not None:
        # TTL-boundary rotation: re-issue the cookie for the new session id.
        _set_session_cookies(response, settings, resolved.rotated_session)
    return resolved


async def require_csrf(
    request: Request,
    auth_session: AuthenticatedSession = Depends(require_session),
) -> AuthenticatedSession:
    token = request.headers.get("X-CSRF-Token") or request.headers.get("x-csrf-token")
    if not AuthService.csrf_valid(auth_session.session, token):
        raise ForbiddenError("CSRF token missing or invalid.")
    return auth_session


def _build_scope(
    request: Request, auth_session: AuthenticatedSession, settings: Settings
) -> AccessScope:
    mode = AuthMode.HOSTED if settings.is_hosted else AuthMode.LOCAL
    return AccessScope(
        actor_user_id=auth_session.user.id,
        auth_mode=mode,
        correlation_id=getattr(request.state, "correlation_id", ""),
    )


async def get_access_scope(
    request: Request,
    auth_session: AuthenticatedSession = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> AccessScope:
    """Read-only scope for the authenticated actor."""
    return _build_scope(request, auth_session, settings)


async def require_scope(
    request: Request,
    auth_session: AuthenticatedSession = Depends(require_csrf),
    settings: Settings = Depends(get_settings),
) -> AccessScope:
    """CSRF-protected scope for cookie-authenticated mutations."""
    return _build_scope(request, auth_session, settings)


async def require_platform_admin(
    auth_session: AuthenticatedSession = Depends(require_csrf),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedSession:
    """CSRF-protected authority for platform-wide actions (global schema, provisioning)."""
    if settings.is_hosted and auth_session.user.id not in settings.platform_admin_user_ids:
        raise ForbiddenError("Platform administrator authority is required.")
    return auth_session


async def _resolve_workspace_scope(
    request: Request,
    auth_session: AuthenticatedSession,
    settings: Settings,
    tenancy: TenancyService,
) -> AccessScope:
    """Resolve the active workspace and assert membership in hosted mode.

    Local mode uses the explicit local workspace. Hosted mode requires an
    ``X-Workspace-Id`` header naming a workspace the actor belongs to.
    """
    mode = AuthMode.HOSTED if settings.is_hosted else AuthMode.LOCAL
    correlation_id = getattr(request.state, "correlation_id", "")
    base = AccessScope(
        actor_user_id=auth_session.user.id, auth_mode=mode, correlation_id=correlation_id
    )
    if mode is AuthMode.HOSTED:
        workspace_id = request.headers.get("X-Workspace-Id") or request.headers.get(
            "x-workspace-id"
        )
        if not workspace_id:
            raise ValidationError("X-Workspace-Id header is required in hosted mode.")
        await tenancy.require_membership(base, workspace_id)
    else:
        workspace_id = settings.AUTH_LOCAL_WORKSPACE_ID
    return AccessScope(
        actor_user_id=auth_session.user.id,
        auth_mode=mode,
        correlation_id=correlation_id,
        workspace_id=workspace_id,
    )


async def get_workspace_scope(
    request: Request,
    auth_session: AuthenticatedSession = Depends(require_session),
    settings: Settings = Depends(get_settings),
    tenancy: TenancyService = Depends(get_tenancy_service),
) -> AccessScope:
    """Read-only workspace scope for graph/vector reads."""
    return await _resolve_workspace_scope(request, auth_session, settings, tenancy)


async def require_workspace_scope(
    request: Request,
    auth_session: AuthenticatedSession = Depends(require_csrf),
    settings: Settings = Depends(get_settings),
    tenancy: TenancyService = Depends(get_tenancy_service),
) -> AccessScope:
    """CSRF-protected workspace scope for graph/vector mutations."""
    return await _resolve_workspace_scope(request, auth_session, settings, tenancy)


async def _resolve_project_scope(
    request: Request,
    auth_session: AuthenticatedSession,
    settings: Settings,
    tenancy: TenancyService,
    *,
    permission: Permission,
) -> AccessScope:
    """Resolve and authorize the active workspace **and** project.

    Local mode uses the explicit local workspace/project (trusted single user).
    Hosted mode requires ``X-Workspace-Id`` and takes the project from the
    ``/projects/{project_id}`` path (or an ``X-Project-Id`` header on aliases),
    then authorizes membership in the workspace and proves the project belongs to it.
    """
    mode = AuthMode.HOSTED if settings.is_hosted else AuthMode.LOCAL
    correlation_id = getattr(request.state, "correlation_id", "")
    if mode is AuthMode.LOCAL:
        return AccessScope(
            actor_user_id=auth_session.user.id,
            auth_mode=mode,
            correlation_id=correlation_id,
            workspace_id=settings.AUTH_LOCAL_WORKSPACE_ID,
            project_id=settings.AUTH_LOCAL_PROJECT_ID,
        )

    workspace_id = request.headers.get("X-Workspace-Id") or request.headers.get("x-workspace-id")
    if not workspace_id:
        raise ValidationError("X-Workspace-Id header is required in hosted mode.")
    project_id = (
        request.path_params.get("project_id")
        or request.headers.get("X-Project-Id")
        or request.headers.get("x-project-id")
    )
    if not project_id:
        raise ValidationError(
            "X-Project-Id header or a /projects/{project_id} path is required in hosted mode."
        )
    scope = AccessScope(
        actor_user_id=auth_session.user.id,
        auth_mode=mode,
        correlation_id=correlation_id,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    await tenancy.require_project(scope, workspace_id, project_id, permission)
    return scope


async def get_project_scope(
    request: Request,
    auth_session: AuthenticatedSession = Depends(require_session),
    settings: Settings = Depends(get_settings),
    tenancy: TenancyService = Depends(get_tenancy_service),
) -> AccessScope:
    """Read-only project scope for project-owned routes."""
    return await _resolve_project_scope(
        request, auth_session, settings, tenancy, permission=Permission.PROJECT_READ
    )


async def require_project_scope(
    request: Request,
    auth_session: AuthenticatedSession = Depends(require_csrf),
    settings: Settings = Depends(get_settings),
    tenancy: TenancyService = Depends(get_tenancy_service),
) -> AccessScope:
    """CSRF-protected project scope for project-owned mutations."""
    return await _resolve_project_scope(
        request, auth_session, settings, tenancy, permission=Permission.PROJECT_WRITE
    )


@router.get("/login")
async def login(
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if settings.is_hosted:
        return RedirectResponse(await auth.start_hosted_login(), status_code=302)
    session = await auth.start_local_login()
    response = RedirectResponse("/", status_code=302)
    _set_session_cookies(response, settings, session)
    return response


@router.get("/callback")
async def callback(
    request: Request,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    if not settings.is_hosted:
        raise ValidationError("OIDC callback is not available in local mode.")
    if error:
        log_event(logger, "auth.callback.provider_error", level=logging.WARNING, error=error)
        raise UnauthorizedError("The identity provider rejected the login.")
    if not state or not code:
        raise ValidationError("Missing 'state' or 'code'.")
    session = await auth.complete_hosted_login(state=state, code=code)
    response = RedirectResponse("/", status_code=302)
    _set_session_cookies(response, settings, session)
    return response


@router.get("/me")
async def me(
    auth_session: AuthenticatedSession = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    user = auth_session.user
    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
        },
        "auth_mode": settings.AUTH_MODE,
    }


@router.post("/logout")
async def logout(
    auth_session: AuthenticatedSession = Depends(require_csrf),
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    await auth.logout(auth_session.session.id)
    body: dict[str, object] = {"status": "logged_out"}
    if settings.is_hosted:
        body["logout_url"] = await auth.hosted_logout_url()
    response = JSONResponse(body)
    _clear_session_cookies(response, settings)
    return response


@router.get("/logout")
async def logout_redirect(
    request: Request,
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    await auth.logout(request.cookies.get(settings.SESSION_COOKIE_NAME))
    target = await auth.hosted_logout_url() if settings.is_hosted else "/"
    response = RedirectResponse(target, status_code=302)
    _clear_session_cookies(response, settings)
    return response
