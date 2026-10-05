from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.middleware import get_correlation_id
from backend.domain.errors import DomainError
from backend.ports import AIError

PROBLEM_MEDIA_TYPE = "application/problem+json"


def _correlation_id(request: Request) -> str:
    return getattr(request.state, "correlation_id", "") or get_correlation_id()


def problem_body(
    *,
    status: int,
    title: str,
    code: str,
    detail: str,
    field_errors: dict[str, str] | None = None,
    correlation_id: str = "",
) -> dict[str, Any]:
    return {
        "type": "about:blank",
        "title": title,
        "status": status,
        "code": code,
        "detail": detail,
        "field_errors": field_errors or {},
        "correlation_id": correlation_id,
    }


def _problem_response(status: int, body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=status, content=body, media_type=PROBLEM_MEDIA_TYPE)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return _problem_response(
            exc.status_code,
            problem_body(
                status=exc.status_code,
                title=exc.title,
                code=exc.code,
                detail=exc.detail,
                field_errors=exc.field_errors,
                correlation_id=_correlation_id(request),
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        field_errors: dict[str, str] = {}
        for error in exc.errors():
            location = ".".join(
                str(part) for part in error.get("loc", []) if part not in ("body", "query", "path")
            )
            field_errors[location or "body"] = str(error.get("msg", "invalid"))
        return _problem_response(
            422,
            problem_body(
                status=422,
                title="Request validation failed",
                code="validation_error",
                detail="Request body or parameters are invalid.",
                field_errors=field_errors,
                correlation_id=_correlation_id(request),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem_response(
            exc.status_code,
            problem_body(
                status=exc.status_code,
                title="HTTP error",
                code=f"http_{exc.status_code}",
                detail=str(exc.detail),
                correlation_id=_correlation_id(request),
            ),
        )

    @app.exception_handler(AIError)
    async def _handle_ai_error(request: Request, exc: AIError) -> JSONResponse:
        return _problem_response(
            502,
            problem_body(
                status=502,
                title="AI provider failure",
                code="provider_failure",
                detail=str(exc),
                correlation_id=_correlation_id(request),
            ),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        return _problem_response(
            500,
            problem_body(
                status=500,
                title="Internal error",
                code="internal_error",
                detail="An unexpected error occurred.",
                correlation_id=_correlation_id(request),
            ),
        )
