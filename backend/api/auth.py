"""Authentication HTTP routes: login, callback, session, and logout.

Hosted mode delegates to OIDC; local mode issues a trusted single-user session.
Session cookies are host-only and CSRF-protected for cookie-authenticated writes.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse

from backend.api.dependencies import get_auth_service, get_settings
from backend.core.config import Settings
from backend.core.logging import log_event
from backend.domain.errors import ForbiddenError, UnauthorizedError, ValidationError
from backend.domain.identity import AccessScope, AuthMode, Session
from backend.services.auth_service import AuthenticatedSession, AuthService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookies(
    response: RedirectResponse | JSONResponse, settings: Settings, session: Session
) -> None:
    common = {
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
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> AuthenticatedSession:
    resolved = await auth.resolve(request.cookies.get(settings.SESSION_COOKIE_NAME))
    if resolved is None:
        raise UnauthorizedError("A valid session is required.")
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
