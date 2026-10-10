"""Serve the built single-page app from the API process, and set browser security headers.

One container is then the whole site: the reverse proxy needs a single upstream
and the session cookie, the API, and the UI share one origin. Nothing here runs
unless ``FRONTEND_DIST_DIR`` points at a real build.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from backend.api.problems import PROBLEM_MEDIA_TYPE, problem_body
from backend.core.config import Settings

# Slim base images have no system MIME table entry for web fonts.
mimetypes.add_type("font/woff2", ".woff2")

# Paths the API owns. A miss under these is an API 404, never the app shell, so a
# client that mistypes an endpoint gets a problem response instead of HTML.
_API_PREFIXES = ("api/", "auth/", "health/")
# Exact paths that are never UI routes. The docs are switched off on a hosted
# site; answering them with the app shell would look as if they were still there.
_API_PATHS = frozenset({"api", "auth", "health", "docs", "redoc", "openapi.json"})


def content_security_policy(settings: Settings) -> str:
    """Same-origin only, plus the configured browser error-reporting endpoint."""
    connect = ["'self'"]
    if settings.SENTRY_DSN_WEB:
        host = urlsplit(settings.SENTRY_DSN_WEB).hostname
        if host:
            connect.append(f"https://{host}")
    return "; ".join(
        (
            "default-src 'self'",
            "script-src 'self'",
            # React sets inline style attributes; no inline scripts are allowed.
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self'",
            f"connect-src {' '.join(connect)}",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        )
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Headers every response from a hosted or production site carries."""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        super().__init__(app)
        self._policy = content_security_policy(settings)

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("Content-Security-Policy", self._policy)
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "same-origin")
        headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        # TLS terminates at the proxy; the site is only ever published over HTTPS.
        headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if request.url.path.startswith(("/api/", "/auth/")):
            # Canon and session responses must never be stored by a shared cache.
            headers.setdefault("Cache-Control", "no-store")
        return response


def frontend_directory(settings: Settings) -> Path | None:
    """The configured build directory, if it holds an app shell."""
    if not settings.FRONTEND_DIST_DIR:
        return None
    directory = Path(settings.FRONTEND_DIST_DIR).resolve()
    return directory if (directory / "index.html").is_file() else None


def mount_frontend(app: FastAPI, directory: Path) -> None:
    """Serve hashed assets as immutable files and the app shell for every UI route."""
    index = directory / "index.html"
    assets = directory / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="frontend-assets")

    def shell() -> FileResponse:
        # The shell names the current hashed assets, so it must always be revalidated.
        return FileResponse(index, media_type="text/html", headers={"Cache-Control": "no-cache"})

    @app.get("/", include_in_schema=False)
    def frontend_root() -> FileResponse:
        return shell()

    @app.get("/{path:path}", include_in_schema=False)
    def frontend_route(path: str, request: Request) -> Response:
        if path.startswith(_API_PREFIXES) or path in _API_PATHS:
            return JSONResponse(
                status_code=404,
                media_type=PROBLEM_MEDIA_TYPE,
                content=problem_body(
                    status=404,
                    title="Not found",
                    code="not_found",
                    detail="No such endpoint.",
                    correlation_id=getattr(request.state, "correlation_id", ""),
                ),
            )
        # Top-level static files from the build (favicon and the like). The
        # resolved path must stay inside the build directory.
        candidate = (directory / path).resolve()
        if candidate.is_file() and directory in candidate.parents:
            return FileResponse(candidate)
        return shell()


__all__ = [
    "SecurityHeadersMiddleware",
    "content_security_policy",
    "frontend_directory",
    "mount_frontend",
]
