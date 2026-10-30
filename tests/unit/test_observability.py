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


def test_sentry_disabled_under_pytest() -> None:
    # A developer .env may set a DSN; the test gate must never start the reporter.
    settings = _settings(APP_ENV="development", SENTRY_DSN_BACKEND="https://key@sentry.example/1")
    assert sentry_enabled(settings) is False
    assert init_sentry(settings) is False


def test_sentry_enabled_outside_test_with_dsn(monkeypatch) -> None:
    import backend.core.observability as observability

    monkeypatch.setattr(observability, "_running_under_pytest", lambda: False)
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


def test_scrub_event_removes_locals_extras_and_contexts() -> None:
    event = {
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "stacktrace": {
                        "frames": [
                            {"filename": "app.py", "vars": {"prompt": "secret manuscript"}},
                            {"filename": "other.py"},
                        ]
                    },
                }
            ]
        },
        "extra": {"provider_response": "raw text"},
        "contexts": {"manuscript": {"text": "chapter one"}},
    }

    scrubbed = scrub_event(event)

    assert "vars" not in scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert "extra" not in scrubbed
    assert "contexts" not in scrubbed


def test_errors_are_reported_under_the_commit_that_is_running() -> None:
    from backend.core.observability import release_name

    assert release_name(_settings(APP_RELEASE="26564cf")) == "novel-engine@26564cf"
    # Outside a deployment there is no commit; the package version stands in.
    assert release_name(_settings(APP_RELEASE="")) == "novel-engine@0.1.0"


def test_the_page_is_given_a_dsn_only_when_one_is_configured() -> None:
    from backend.core.observability import client_config

    assert client_config(_settings(APP_ENV="development", SENTRY_DSN_WEB="")) is None
    assert client_config(_settings(APP_ENV="test", SENTRY_DSN_WEB="https://k@s.example/2")) is None
    assert client_config(
        _settings(APP_ENV="development", SENTRY_DSN_WEB="https://k@s.example/2", APP_RELEASE="abc")
    ) == {
        "sentryDsn": "https://k@s.example/2",
        "environment": "development",
        "release": "novel-engine@abc",
    }


def test_scrubbing_keeps_ids_and_drops_anything_else_attached_to_an_event() -> None:
    event = {
        "user": {"id": "usr_1", "email": "jake@example.com", "ip_address": "10.0.0.1"},
        "tags": {
            "workspace_id": "ws_1",
            "project_id": "prj_1",
            "auth_mode": "hosted",
            "story": "The Salt Road",
        },
        # A trace is scrubbed by the same function as an error.
        "spans": [
            {
                "op": "db",
                "description": "SELECT * FROM entities WHERE name = 'Mara'",
                "data": {"q": 1},
            },
            {"op": "canon.commit", "description": "commit", "data": {"x": 1}},
        ],
    }

    scrubbed = scrub_event(event)

    assert scrubbed["user"] == {"id": "usr_1"}
    assert scrubbed["tags"] == {
        "workspace_id": "ws_1",
        "project_id": "prj_1",
        "auth_mode": "hosted",
    }
    assert scrubbed["spans"] == [{"op": "db"}, {"op": "canon.commit", "description": "commit"}]
    assert "Mara" not in str(scrubbed) and "Salt Road" not in str(scrubbed)


def test_identifying_a_request_does_nothing_when_reporting_is_off() -> None:
    from backend.core.observability import identify_request

    # Must be safe to call on every request of a site with no DSN.
    identify_request(user_id="usr_1", workspace_id="ws_1", project_id="prj_1", auth_mode="local")
