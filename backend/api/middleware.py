from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from backend.adapters.system import uuid7
from backend.core.logging import get_correlation_id, reset_correlation_id, set_correlation_id

__all__ = ["CorrelationIdMiddleware", "get_correlation_id"]


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Propagate or generate a correlation ID for every request/response."""

    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid7())
        request.state.correlation_id = correlation_id
        token = set_correlation_id(correlation_id)
        try:
            response = await call_next(request)
        finally:
            reset_correlation_id(token)
        response.headers["X-Correlation-ID"] = correlation_id
        return response
