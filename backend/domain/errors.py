"""Domain/application errors mapped to the HTTP problem contract."""

from __future__ import annotations


class DomainError(Exception):
    status_code: int = 500
    code: str = "internal_error"
    title: str = "Internal error"

    def __init__(self, detail: str, *, field_errors: dict[str, str] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.field_errors = field_errors or {}


class ValidationError(DomainError):
    status_code = 400
    code = "validation_error"
    title = "Validation failed"


class UnauthorizedError(DomainError):
    status_code = 401
    code = "unauthorized"
    title = "Authentication required"


class ForbiddenError(DomainError):
    status_code = 403
    code = "forbidden"
    title = "Forbidden"


class NotFoundError(DomainError):
    status_code = 404
    code = "not_found"
    title = "Not found"


class ConflictError(DomainError):
    status_code = 409
    code = "conflict"
    title = "Conflict"


class CapabilityUnavailableError(DomainError):
    status_code = 503
    code = "capability_unavailable"
    title = "Capability unavailable"


class ProviderFailureError(DomainError):
    status_code = 502
    code = "provider_failure"
    title = "AI provider failure"


class PersistenceError(DomainError):
    status_code = 500
    code = "persistence_error"
    title = "Persistence failure"


class FeatureNotImplementedError(DomainError):
    status_code = 501
    code = "not_implemented"
    title = "Not implemented"
