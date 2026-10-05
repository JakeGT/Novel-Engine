from __future__ import annotations

import contextvars

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from backend.adapters.system import uuid7

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="")


def get_correlation_id() -> str:
    return _correlation_id.get()


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Propagate or generate a correlation ID for every request/response."""

    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid7())
        request.state.correlation_id = correlation_id
        token = _correlation_id.set(correlation_id)
        try:
            response = await call_next(request)
        finally:
            _correlation_id.reset(token)
        response.headers["X-Correlation-ID"] = correlation_id
        return response
