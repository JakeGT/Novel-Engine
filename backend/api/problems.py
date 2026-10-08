from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.middleware import get_correlation_id
from backend.domain.errors import DomainError
from backend.domain.identity import IdentityError, IdentityProviderUnavailable
from backend.domain.journal import HeadConflict, RevisionConflict
from backend.domain.operations import OperationValidationError
from backend.domain.schemas import SchemaCompatibilityError, SchemaGovernanceError
from backend.domain.tenancy import InvitationNotFound, TenancyError
from backend.domain.validation import CanonValidationError
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
                # Never echo provider text; upstream messages may contain
                # account, request, or prompt details.
                detail="The AI provider request failed.",
                correlation_id=_correlation_id(request),
            ),
        )

    @app.exception_handler(IdentityProviderUnavailable)
    async def _handle_identity_unavailable(
        request: Request, exc: IdentityProviderUnavailable
    ) -> JSONResponse:
        return _problem_response(
            503,
            problem_body(
                status=503,
                title="Identity provider unavailable",
                code="capability_unavailable",
                detail="The identity provider is not configured or unreachable.",
                correlation_id=_correlation_id(request),
            ),
        )

    @app.exception_handler(IdentityError)
    async def _handle_identity_error(request: Request, exc: IdentityError) -> JSONResponse:
        # Never echo token/claim details back to the client.
        return _problem_response(
            401,
            problem_body(
                status=401,
                title="Authentication failed",
                code="unauthorized",
                detail="The authentication request could not be completed.",
                correlation_id=_correlation_id(request),
            ),
        )

    @app.exception_handler(InvitationNotFound)
    async def _handle_invitation_not_found(
        request: Request, exc: InvitationNotFound
    ) -> JSONResponse:
        return _problem_response(
            404,
            problem_body(
                status=404,
                title="Not found",
                code="not_found",
                detail=str(exc),
                correlation_id=_correlation_id(request),
            ),
        )

    @app.exception_handler(SchemaCompatibilityError)
    async def _handle_schema_compatibility(
        request: Request, exc: SchemaCompatibilityError
    ) -> JSONResponse:
        return _problem_response(
            409,
            problem_body(
                status=409,
                title="Schema change rejected",
                code="schema_incompatible",
                detail=str(exc),
                correlation_id=_correlation_id(request),
            ),
        )

    @app.exception_handler(SchemaGovernanceError)
    async def _handle_schema_governance(
        request: Request, exc: SchemaGovernanceError
    ) -> JSONResponse:
        return _problem_response(
            422,
            problem_body(
                status=422,
                title="Invalid schema definition",
                code="invalid_schema",
                detail=str(exc),
                correlation_id=_correlation_id(request),
            ),
        )

    @app.exception_handler(TenancyError)
    async def _handle_tenancy_error(request: Request, exc: TenancyError) -> JSONResponse:
        return _problem_response(
            409,
            problem_body(
                status=409,
                title="Conflict",
                code="conflict",
                detail=str(exc),
                correlation_id=_correlation_id(request),
            ),
        )

    @app.exception_handler(OperationValidationError)
    @app.exception_handler(CanonValidationError)
    async def _handle_operation_validation(request: Request, exc: Exception) -> JSONResponse:
        return _problem_response(
            422,
            problem_body(
                status=422,
                title="Mutation validation failed",
                code="validation_error",
                detail=str(exc),
                correlation_id=_correlation_id(request),
            ),
        )

    @app.exception_handler(HeadConflict)
    @app.exception_handler(RevisionConflict)
    async def _handle_revision_conflict(request: Request, exc: Exception) -> JSONResponse:
        return _problem_response(
            409,
            problem_body(
                status=409,
                title="Revision conflict",
                code="revision_conflict",
                detail="The branch head or commit revision changed; reload and retry.",
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
