"""The app shell hands the browser its error-reporting settings without inline script."""

import json
import re
from html import unescape

import pytest

from backend.api.frontend import CLIENT_CONFIG_META, content_security_policy, shell_html
from backend.core.config import Settings

pytestmark = pytest.mark.unit

INDEX = "<!doctype html><html><head><title>x</title></head><body></body></html>"


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_the_shell_is_untouched_when_browser_reporting_is_off() -> None:
    assert shell_html(INDEX, _settings(APP_ENV="development", SENTRY_DSN_WEB="")) == INDEX


def test_the_shell_carries_the_browser_settings_as_an_escaped_meta_tag() -> None:
    settings = _settings(
        APP_ENV="development",
        # A value that would break out of an attribute if it were not escaped.
        SENTRY_DSN_WEB='https://key@o1.ingest.sentry.io/2"><script>alert(1)</script>',
        APP_RELEASE="abc1234",
    )

    page = shell_html(INDEX, settings)

    assert "<script>" not in page
    found = re.search(rf'<meta name="{CLIENT_CONFIG_META}" content="([^"]*)" />', page)
    assert found is not None and page.index(found.group(0)) < page.index("</head>")
    assert json.loads(unescape(found.group(1))) == {
        "sentryDsn": settings.SENTRY_DSN_WEB,
        "environment": "development",
        "release": "novel-engine@abc1234",
    }


def test_the_page_may_reach_the_reporting_host_and_nothing_else_outside_itself() -> None:
    policy = content_security_policy(
        _settings(SENTRY_DSN_WEB="https://key@o1.ingest.de.sentry.io/2")
    )
    assert "connect-src 'self' https://o1.ingest.de.sentry.io" in policy
    assert "script-src 'self';" in policy
    assert "connect-src 'self';" in content_security_policy(_settings(SENTRY_DSN_WEB=""))
