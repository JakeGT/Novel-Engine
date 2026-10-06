import pytest

from backend.core.config import Settings
from backend.core.observability import init_sentry, scrub_event, sentry_enabled

pytestmark = pytest.mark.unit


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_sentry_disabled_without_dsn() -> None:
    assert sentry_enabled(_settings(SENTRY_DSN_BACKEND="")) is False


def test_sentry_disabled_in_test_environment() -> None:
    settings = _settings(APP_ENV="test", SENTRY_DSN_BACKEND="https://key@sentry.example/1")
    assert sentry_enabled(settings) is False
    assert init_sentry(settings) is False


def test_sentry_enabled_outside_test_with_dsn() -> None:
    settings = _settings(APP_ENV="development", SENTRY_DSN_BACKEND="https://key@sentry.example/1")
    assert sentry_enabled(settings) is True


def test_scrub_event_removes_content_credentials_and_pii() -> None:
    event = {
        "request": {
            "data": "secret manuscript",
            "cookies": {"session": "abc"},
            "query_string": "token=abc",
            "headers": {
                "Authorization": "Bearer x",
                "Cookie": "s=1",
                "X-Correlation-ID": "cid",
                "Accept": "application/json",
            },
        },
        "user": {"id": "u1", "email": "a@b.c", "ip_address": "1.2.3.4", "username": "mara"},
        "breadcrumbs": [{"message": "manuscript text"}],
    }

    scrubbed = scrub_event(event)

    assert "data" not in scrubbed["request"]
    assert "cookies" not in scrubbed["request"]
    assert "query_string" not in scrubbed["request"]
    assert scrubbed["request"]["headers"] == {"Accept": "application/json"}
    assert scrubbed["user"] == {"id": "u1"}
    assert "breadcrumbs" not in scrubbed
