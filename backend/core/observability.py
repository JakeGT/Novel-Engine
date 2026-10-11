"""Optional, privacy-first error reporting (Sentry).

Error reporting is opt-in: it initializes only when a backend DSN is configured
and the application is not running tests. Events are scrubbed before they leave
the process — request bodies, query strings, cookies, sensitive headers, user
PII, exception local variables, structured extras/contexts, and breadcrumbs are
removed — so manuscript content, prompts, and credentials are never sent to the
provider. What stays is what an error needs to be found: the stack, the route,
the release, and the internal ids of the person, workspace and project.

An exception's own message is sent. Code that raises must therefore never put an
author's words, a prompt or a credential in a message; provider errors are
answered without being reported for exactly that reason.

The browser reports to its own DSN, which the server hands to the page it serves
(``client_config``): one image runs anywhere, and nothing is baked in at build time.

``python -m backend.core.observability`` sends one test event and says whether it
was delivered.
"""

from __future__ import annotations

import logging
import os
import sys
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
# The only tags an event may carry: fixed names with internal ids or fixed vocabularies.
_ALLOWED_TAGS = frozenset({"workspace_id", "project_id", "auth_mode", "test_event"})

_initialized = False


def _running_under_pytest() -> bool:
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules


def sentry_enabled(settings: Settings) -> bool:
    """Report whether backend error reporting is configured and permitted.

    Never enabled under pytest, so a developer ``.env`` with a DSN cannot start a
    reporter (and its background transport thread) during the test gate.
    """
    if not settings.SENTRY_DSN_BACKEND or settings.APP_ENV == "test":
        return False
    return not _running_under_pytest()


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

    # Exception frames can capture local variables (prompts, manuscript text,
    # credentials); strip every local-variable scope.
    exception = event.get("exception")
    if isinstance(exception, dict):
        for value in exception.get("values") or []:
            if not isinstance(value, dict):
                continue
            stacktrace = value.get("stacktrace")
            if isinstance(stacktrace, dict):
                for frame in stacktrace.get("frames") or []:
                    if isinstance(frame, dict):
                        frame.pop("vars", None)

    # Structured extras and contexts can carry arbitrary application data.
    event.pop("extra", None)
    event.pop("contexts", None)
    tags = event.get("tags")
    if isinstance(tags, dict):
        event["tags"] = {key: value for key, value in tags.items() if key in _ALLOWED_TAGS}

    # Performance spans can carry SQL and outgoing addresses in their descriptions.
    for span in event.get("spans") or []:
        if isinstance(span, dict):
            span.pop("data", None)
            if span.get("op") != "canon.commit":
                span.pop("description", None)

    # Breadcrumbs can carry rendered log messages or request content.
    event.pop("breadcrumbs", None)
    return event


def release_name(settings: Settings) -> str:
    """The same name on the server and in the browser, so their errors line up."""
    return f"novel-engine@{settings.APP_RELEASE or settings.PROJECT_VERSION}"


def client_config(settings: Settings) -> dict[str, str] | None:
    """What the page needs to report its own errors, or None when that is off.

    A browser DSN is not a secret (every visitor's browser holds it), but it is
    only handed out when the server itself is allowed to report.
    """
    if not settings.SENTRY_DSN_WEB or settings.APP_ENV == "test":
        return None
    return {
        "sentryDsn": settings.SENTRY_DSN_WEB,
        "environment": settings.APP_ENV,
        "release": release_name(settings),
    }


def identify_request(
    *, user_id: str | None, workspace_id: str | None, project_id: str | None, auth_mode: str
) -> None:
    """Attach who and where to errors raised while handling this request: ids only."""
    if not _initialized:
        return
    import sentry_sdk

    scope = sentry_sdk.get_isolation_scope()
    scope.set_user({"id": user_id} if user_id else None)
    scope.set_tag("auth_mode", auth_mode)
    if workspace_id:
        scope.set_tag("workspace_id", workspace_id)
    if project_id:
        scope.set_tag("project_id", project_id)


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
        release=release_name(settings),
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        shutdown_timeout=2.0,
        before_send=scrub_event,  # type: ignore[arg-type]
        # Traces are off unless sampled; when on, they are scrubbed like errors.
        before_send_transaction=scrub_event,  # type: ignore[arg-type]
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            LoggingIntegration(level=logging.WARNING, event_level=None),
        ],
    )
    _initialized = True
    logger.info("sentry enabled for environment %s", settings.APP_ENV)
    return True


def send_test_event(settings: Settings) -> bool:
    """Send one harmless event and wait for it. True when the provider accepted it."""
    if not init_sentry(settings):
        return False
    import sentry_sdk

    with sentry_sdk.new_scope() as scope:
        scope.set_tag("test_event", "yes")
        sentry_sdk.capture_message("Novel Engine test event: error reporting is working")
    delivered: bool = sentry_sdk.flush(timeout=10.0) is not False
    return delivered


def main() -> int:
    from backend.core.config import settings

    if not sentry_enabled(settings):
        print("Error reporting is off: no backend DSN is configured.")
        return 1
    if send_test_event(settings):
        print(f"Sent a test event as {release_name(settings)} ({settings.APP_ENV}).")
        return 0
    print("The test event could not be delivered. Check the DSN and outbound network access.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
