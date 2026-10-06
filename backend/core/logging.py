from __future__ import annotations

import contextvars
import functools
import json
import logging
import re
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

REDACTED = "***REDACTED***"

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="")

_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "dsn",
    "neo4j_password",
    "openai_api_key",
    "passwd",
    "password",
    "postgres_password",
    "private_key",
    "secret",
    "token",
}

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_\-]{12,}"), REDACTED),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"), f"Bearer {REDACTED}"),
    (
        re.compile(r"(://[^:/@\s]+:)([^@/\s]+)(@)"),
        rf"\1{REDACTED}\3",
    ),
    (
        re.compile(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key|authorization)"
            r"(\"?\s*[:=]\s*\"?)([^\s,;\"'}]+)"
        ),
        rf"\1\2{REDACTED}",
    ),
)


def get_correlation_id() -> str:
    """Return the correlation ID bound to the current execution context, if any."""
    return _correlation_id.get()


def set_correlation_id(value: str) -> contextvars.Token[str]:
    return _correlation_id.set(value)


def reset_correlation_id(token: contextvars.Token[str]) -> None:
    _correlation_id.reset(token)


def redact_text(text: str) -> str:
    """Remove common secret shapes from free text before it is logged."""
    redacted = text
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def redact_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Redact values whose key looks sensitive; recurse into nested mappings/lists."""
    redacted: dict[str, Any] = {}
    for key, value in fields.items():
        if key.lower() in _SECRET_KEYS:
            redacted[key] = REDACTED
        elif isinstance(value, dict):
            redacted[key] = redact_fields(value)
        elif isinstance(value, list):
            redacted[key] = [
                redact_fields(item)
                if isinstance(item, dict)
                else redact_text(item)
                if isinstance(item, str)
                else item
                for item in value
            ]
        elif isinstance(value, str):
            redacted[key] = redact_text(value)
        else:
            redacted[key] = value
    return redacted


class JsonLogFormatter(logging.Formatter):
    """Render log records as single-line JSON with correlation IDs and redaction."""

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", None) or record.getMessage()
        fields = getattr(record, "fields", None) or {}
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": redact_text(str(event)),
            "correlation_id": get_correlation_id(),
        }
        if fields:
            payload["fields"] = redact_fields(fields)
        if record.exc_info:
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", stream: Any | None = None) -> None:
    """Configure a single structured JSON handler on the root logger.

    Safe to call repeatedly: an existing handler is reconfigured rather than
    duplicated, so importing modules never emit duplicate log lines.
    """
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonLogFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """Log a structured event whose fields are redacted before emission."""
    logger.log(level, event, extra={"event": event, "fields": fields})


def instrument(event: str, logger: logging.Logger) -> Callable:
    """Log duration and result count for an adapter call without logging content.

    Successful calls log at DEBUG; failures log at WARNING with only the error
    type, then re-raise so degradation stays explicit rather than swallowed.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
            except Exception as error:
                log_event(
                    logger,
                    f"{event}.error",
                    level=logging.WARNING,
                    duration_ms=round((time.perf_counter() - start) * 1000, 3),
                    error_type=type(error).__name__,
                )
                raise
            count = len(result) if isinstance(result, (list, tuple)) else None
            log_event(
                logger,
                event,
                level=logging.DEBUG,
                duration_ms=round((time.perf_counter() - start) * 1000, 3),
                result_count=count,
            )
            return result

        return wrapper

    return decorator
