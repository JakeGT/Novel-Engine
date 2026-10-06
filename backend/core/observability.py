"""Optional, privacy-first error reporting (Sentry).

Error reporting is opt-in: it initializes only when a backend DSN is configured
and the application is not running tests. Events are scrubbed before they leave
the process — request bodies, query strings, cookies, sensitive headers, and user
PII are removed, and breadcrumbs are dropped — so manuscript content, prompts,
and credentials are never sent to the provider.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.core.config import Settings

logger = logging.getLogger(__name__)

_SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "proxy-authorization",
    "set-cookie",
    "x-api-key",
    "x-correlation-id",
}
_USER_PII_KEYS = ("email", "username", "ip_address", "name", "geo")

_initialized = False


def sentry_enabled(settings: Settings) -> bool:
    """Report whether backend error reporting is configured and permitted."""
    return bool(settings.SENTRY_DSN_BACKEND) and settings.APP_ENV != "test"


def scrub_event(event: dict[str, Any], hint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Remove request content, credentials, and user PII from a Sentry event."""
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("data", "cookies", "query_string", "env"):
            request.pop(key, None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            for key in list(headers):
                if key.lower() in _SENSITIVE_HEADERS:
                    headers.pop(key, None)

    user = event.get("user")
    if isinstance(user, dict):
        for key in _USER_PII_KEYS:
            user.pop(key, None)

    # Breadcrumbs can carry rendered log messages or request content.
    event.pop("breadcrumbs", None)
    return event


def init_sentry(settings: Settings) -> bool:
    """Initialize Sentry once, when configured. Returns whether it is active."""
    global _initialized
    if _initialized or not sentry_enabled(settings):
        return _initialized

    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN_BACKEND,
        environment=settings.APP_ENV,
        release=f"{settings.PROJECT_NAME}@{settings.PROJECT_VERSION}",
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
        max_request_body_size="never",
        before_send=scrub_event,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            LoggingIntegration(level=logging.WARNING, event_level=None),
        ],
    )
    _initialized = True
    logger.info("sentry enabled for environment %s", settings.APP_ENV)
    return True
