"""Authentication HTTP routes: login, callback, session, and logout.

Hosted mode delegates to OIDC; local mode issues a trusted single-user session.
Session cookies are host-only and CSRF-protected for cookie-authenticated writes.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.api.dependencies import get_auth_service, get_settings, get_tenancy_service
from backend.core.config import Settings
from backend.core.logging import log_event
from backend.core.observability import identify_request
from backend.domain.branching import set_working_branch
from backend.domain.errors import (
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from backend.domain.identity import AccessScope, AuthMode, Session, StaleSignIn
from backend.domain.tenancy import ROLE_PERMISSIONS, Permission, Role
from backend.services.auth_service import AuthenticatedSession, AuthService, safe_return_to
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
        # The session id was replaced: re-issue the cookies. Also recorded on the
        # request, because cookies set here are dropped when the request then fails
        # (a validation error, say), and the browser must get the new id regardless.
        _set_session_cookies(response, settings, resolved.rotated_session)
        request.state.rotated_session = resolved.rotated_session
    # Errors from here on are reported with who was signed in: an internal id only.
    identify_request(
        user_id=resolved.user.id, workspace_id=None, project_id=None, auth_mode=settings.AUTH_MODE
    )
    return resolved


class RotatedSessionCookieMiddleware(BaseHTTPMiddleware):
    """Deliver a replaced session's cookies on every outcome, including errors."""

    def __init__(self, app: Any, *, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        rotated = getattr(request.state, "rotated_session", None)
        cookie = f"{self._settings.SESSION_COOKIE_NAME}="
        already = any(value.startswith(cookie) for value in response.headers.getlist("set-cookie"))
        if rotated is not None and not already:
            _set_session_cookies(response, self._settings, rotated)
        return response


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


async def require_platform_admin_read(
    auth_session: AuthenticatedSession = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedSession:
    """Read-only platform authority (the site's settings page), without CSRF."""
    if settings.is_hosted and auth_session.user.id not in settings.platform_admin_user_ids:
        raise ForbiddenError("Platform administrator authority is required.")
    return auth_session


async def require_local_schema_write(
    auth_session: AuthenticatedSession = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedSession:
    """Global (process-wide) schema mutation is a local development capability only.

    Hosted deployments must use project-scoped schema versions instead of mutating
    a shared process-global registry.
    """
    if settings.is_hosted:
        raise ForbiddenError(
            "Global schema mutation is disabled in hosted mode; "
            "publish project schema versions instead."
        )
    return auth_session


async def require_local_graph_write(
    auth_session: AuthenticatedSession = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedSession:
    """Generic graph writes are a local/admin escape hatch only.

    Hosted deployments must write canon through the mutation commit saga
    (``/projects/{project_id}/entities``), which records a journal revision.
    """
    if settings.is_hosted:
        raise ForbiddenError(
            "Generic graph writes are disabled in hosted mode; "
            "use the canonical mutation endpoints instead."
        )
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
    # Only once membership is proven: never a value a client merely claimed.
    identify_request(
        user_id=auth_session.user.id,
        workspace_id=workspace_id,
        project_id=None,
        auth_mode=settings.AUTH_MODE,
    )
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
        # Local mode has exactly one project. A path naming any other project is
        # not found, so a request can never mix the URL's project with the local
        # scope's project.
        path_project = request.path_params.get("project_id")
        if path_project is not None and path_project != settings.AUTH_LOCAL_PROJECT_ID:
            raise NotFoundError("Project not found.")
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
    identify_request(
        user_id=auth_session.user.id,
        workspace_id=workspace_id,
        project_id=project_id,
        auth_mode=settings.AUTH_MODE,
    )
    return scope


BRANCH_HEADER = "X-Story-Branch"


async def _enter_story_branch(request: Request, scope: AccessScope) -> None:
    """Work in the what-if the request names, once it is proven to be this project's.

    No header, or the main story's own id, means the main story. A header naming a
    branch of any other project is "not found", never a quiet fall back to main.
    """
    branch_id = request.headers.get(BRANCH_HEADER) or request.headers.get(BRANCH_HEADER.lower())
    if not branch_id:
        set_working_branch(None)
        return
    journal = request.app.state.container.journal
    branch = await journal.get_branch(
        workspace_id=scope.workspace_id or "",
        project_id=scope.project_id or "",
        branch_id=branch_id,
    )
    if branch is None:
        raise NotFoundError("That what-if was not found.")
    set_working_branch(None if branch.is_root else branch.id)


async def get_project_scope(
    request: Request,
    auth_session: AuthenticatedSession = Depends(require_session),
    settings: Settings = Depends(get_settings),
    tenancy: TenancyService = Depends(get_tenancy_service),
) -> AccessScope:
    """Read-only project scope for project-owned routes."""
    scope = await _resolve_project_scope(
        request, auth_session, settings, tenancy, permission=Permission.PROJECT_READ
    )
    await _enter_story_branch(request, scope)
    return scope


async def require_project_scope(
    request: Request,
    auth_session: AuthenticatedSession = Depends(require_csrf),
    settings: Settings = Depends(get_settings),
    tenancy: TenancyService = Depends(get_tenancy_service),
) -> AccessScope:
    """CSRF-protected project scope for project-owned mutations."""
    scope = await _resolve_project_scope(
        request, auth_session, settings, tenancy, permission=Permission.PROJECT_WRITE
    )
    await _enter_story_branch(request, scope)
    return scope


async def project_access(
    request: Request,
    scope: AccessScope = Depends(get_project_scope),
    settings: Settings = Depends(get_settings),
    tenancy: TenancyService = Depends(get_tenancy_service),
) -> dict[str, object]:
    """What the caller may do in the active project (drives permission-aware UI)."""
    if settings.is_hosted:
        membership = await tenancy.require_membership(scope, scope.workspace_id or "")
        role = membership.role
    else:
        role = Role.OWNER
    permissions = sorted(permission.value for permission in ROLE_PERMISSIONS[role])
    return {
        "role": role.value,
        "permissions": permissions,
        "can_write": Permission.PROJECT_WRITE.value in permissions,
        "can_admin": Permission.PROJECT_ADMIN.value in permissions,
        "auth_mode": settings.AUTH_MODE,
        # Hosted inference needs a workspace credential; local mode uses the server key.
        "hosted": settings.is_hosted,
    }


@router.get("/login")
async def login(
    fresh: str | None = None,
    return_to: str | None = None,
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    target = safe_return_to(return_to)
    if settings.is_hosted:
        # Only "fresh=1" asks the provider for the password again; anything else is
        # an ordinary login that may reuse the provider's session.
        return RedirectResponse(
            await auth.start_hosted_login(fresh=fresh == "1", return_to=target),
            status_code=302,
        )
    session = await auth.start_local_login()
    response = RedirectResponse(target or "/", status_code=302)
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
    try:
        completed = await auth.complete_hosted_login(state=state, code=code)
    except StaleSignIn:
        return RedirectResponse("/?signin=stale", status_code=302)
    response = RedirectResponse(completed.return_to or "/", status_code=302)
    _set_session_cookies(response, settings, completed.session)
    return response


@router.get("/me")
async def me(
    auth_session: AuthenticatedSession = Depends(require_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    user = auth_session.user
    body: dict[str, object] = {
        "user": {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
        },
        "auth_mode": settings.AUTH_MODE,
        # Local mode is the trusted single-user; hosted mode needs a listed admin id.
        "platform_admin": not settings.is_hosted or user.id in settings.platform_admin_user_ids,
    }
    if not settings.is_hosted:
        # Local mode has one workspace and project and no tenancy rows to list;
        # the client learns their ids here instead of from build-time config.
        body["local"] = {
            "workspace_id": settings.AUTH_LOCAL_WORKSPACE_ID,
            "project_id": settings.AUTH_LOCAL_PROJECT_ID,
        }
    return body


@router.post("/logout")
async def logout(
    request: Request,
    auth_session: AuthenticatedSession = Depends(require_csrf),
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    id_token = auth_session.session.id_token
    await auth.logout(auth_session.session.id)
    # If signing out is what replaced the session, end the one it replaced as well.
    await auth.logout(request.cookies.get(settings.SESSION_COOKIE_NAME))
    body: dict[str, object] = {"status": "logged_out"}
    if settings.is_hosted:
        body["logout_url"] = await auth.hosted_logout_url(id_token)
    response = JSONResponse(body)
    _clear_session_cookies(response, settings)
    return response


@router.get("/logout")
async def logout_redirect(
    request: Request,
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    raw_session = request.cookies.get(settings.SESSION_COOKIE_NAME)
    current = await auth.resolve(raw_session)
    id_token = current.session.id_token if current is not None else None
    # Resolving may have rotated the session; end whichever one is current.
    await auth.logout(current.session.id if current is not None else None)
    await auth.logout(raw_session)
    target = await auth.hosted_logout_url(id_token) if settings.is_hosted else "/"
    response = RedirectResponse(target, status_code=302)
    _clear_session_cookies(response, settings)
    return response
