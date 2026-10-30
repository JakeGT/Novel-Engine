import time
from urllib.parse import parse_qs, quote, urlparse

import pytest

from backend.domain.identity import IdentityClaims

pytestmark = pytest.mark.contract


def test_me_requires_a_session(api_client) -> None:
    assert api_client.get("/auth/me").status_code == 401


def test_local_login_creates_session(api_client, fake_container) -> None:
    settings = fake_container.settings
    response = api_client.get("/auth/login", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/"
    assert api_client.cookies.get(settings.SESSION_COOKIE_NAME)

    me = api_client.get("/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["auth_mode"] == "local"
    assert body["user"]["id"]


def test_local_logout_requires_csrf_then_revokes(api_client, fake_container) -> None:
    settings = fake_container.settings
    api_client.get("/auth/login", follow_redirects=False)
    csrf = api_client.cookies.get(settings.CSRF_COOKIE_NAME)
    assert csrf

    assert api_client.post("/auth/logout").status_code == 403

    ok = api_client.post("/auth/logout", headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 200
    assert ok.json()["status"] == "logged_out"
    assert api_client.get("/auth/me").status_code == 401


def test_hosted_login_redirects_to_provider(hosted_client, hosted_container) -> None:
    response = hosted_client.get("/auth/login", follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://idp.test/authorize")
    query = parse_qs(urlparse(location).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] and query["nonce"]
    assert hosted_container.identity_provider.authorization_calls[0]["redirect_uri"] == (
        hosted_container.settings.OIDC_REDIRECT_URI
    )


def test_a_fresh_login_asks_the_provider_for_the_password_again(hosted_client) -> None:
    fresh = hosted_client.get("/auth/login?fresh=1", follow_redirects=False)
    ordinary = hosted_client.get("/auth/login", follow_redirects=False)

    fresh_query = parse_qs(urlparse(fresh.headers["location"]).query)
    ordinary_query = parse_qs(urlparse(ordinary.headers["location"]).query)
    assert fresh_query["prompt"] == ["login"]
    assert fresh_query["max_age"] == ["0"]
    assert "prompt" not in ordinary_query and "max_age" not in ordinary_query


def _hosted_login(
    hosted_client,
    hosted_container,
    *,
    fresh: bool = False,
    auth_time: int | None = None,
    return_to: str | None = None,
):
    query = []
    if fresh:
        query.append("fresh=1")
    if return_to is not None:
        query.append(f"return_to={quote(return_to, safe='')}")
    login = hosted_client.get(
        "/auth/login" + (f"?{'&'.join(query)}" if query else ""), follow_redirects=False
    )
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    hosted_container.identity_provider._claims = IdentityClaims(
        issuer=hosted_container.settings.OIDC_ISSUER,
        subject="subject-1",
        email="user@example.com",
        display_name="Test User",
        auth_time=auth_time,
    )
    return hosted_client.get(f"/auth/callback?state={state}&code=code-123", follow_redirects=False)


@pytest.mark.parametrize("auth_time", [None, int(time.time()) - 3600], ids=["missing", "stale"])
def test_a_fresh_login_refuses_a_missing_or_old_auth_time(
    hosted_client, hosted_container, auth_time
) -> None:
    response = _hosted_login(hosted_client, hosted_container, fresh=True, auth_time=auth_time)

    assert response.status_code == 302
    assert response.headers["location"] == "/?signin=stale"
    assert not hosted_client.cookies.get(hosted_container.settings.SESSION_COOKIE_NAME)


@pytest.mark.parametrize("auth_time", [None, int(time.time()) - 3600], ids=["missing", "old"])
def test_a_missing_or_old_auth_time_is_fine_when_fresh_was_not_asked(
    hosted_client, hosted_container, auth_time
) -> None:
    response = _hosted_login(hosted_client, hosted_container, auth_time=auth_time)

    assert response.status_code == 302
    assert response.headers["location"] == "/"
    assert hosted_client.cookies.get(hosted_container.settings.SESSION_COOKIE_NAME)


def test_a_fresh_login_allows_a_minute_of_clock_skew(hosted_client, hosted_container) -> None:
    response = _hosted_login(
        hosted_client,
        hosted_container,
        fresh=True,
        auth_time=int(time.time()) - (5 * 60 + 30),
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/"
    assert hosted_client.cookies.get(hosted_container.settings.SESSION_COOKIE_NAME)


def test_return_to_is_honoured_after_a_hosted_login(hosted_client, hosted_container) -> None:
    response = _hosted_login(hosted_client, hosted_container, return_to="/site")

    assert response.status_code == 302
    assert response.headers["location"] == "/site"
    assert hosted_client.cookies.get(hosted_container.settings.SESSION_COOKIE_NAME)


def test_return_to_is_honoured_in_local_mode(api_client, fake_container) -> None:
    response = api_client.get("/auth/login?return_to=%2Fsite", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/site"
    assert api_client.cookies.get(fake_container.settings.SESSION_COOKIE_NAME)


@pytest.mark.parametrize(
    "return_to", ["//evil.example", "https://evil.example", "/\\evil", "/" + "a" * 200]
)
def test_an_unsafe_return_to_is_ignored(api_client, return_to) -> None:
    response = api_client.get(
        "/auth/login?return_to=" + quote(return_to, safe=""), follow_redirects=False
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/"


@pytest.mark.parametrize("return_to", ["//evil.example", "https://evil.example", "/\\evil"])
def test_a_hosted_login_ignores_an_unsafe_return_to(
    hosted_client, hosted_container, return_to
) -> None:
    response = _hosted_login(hosted_client, hosted_container, return_to=return_to)

    assert response.status_code == 302
    assert response.headers["location"] == "/"


def test_hosted_callback_completes_login(hosted_client, hosted_container) -> None:
    login = hosted_client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

    callback = hosted_client.get(
        f"/auth/callback?state={state}&code=code-123", follow_redirects=False
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/"
    assert hosted_client.cookies.get(hosted_container.settings.SESSION_COOKIE_NAME)

    me = hosted_client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["auth_mode"] == "hosted"


def test_hosted_callback_rejects_unknown_state(hosted_client) -> None:
    response = hosted_client.get(
        "/auth/callback?state=unknown&code=code-123", follow_redirects=False
    )
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_hosted_logout_returns_provider_logout_url(hosted_client, hosted_container) -> None:
    login = hosted_client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    hosted_client.get(f"/auth/callback?state={state}&code=code-123", follow_redirects=False)

    csrf = hosted_client.cookies.get(hosted_container.settings.CSRF_COOKIE_NAME)
    response = hosted_client.post("/auth/logout", headers={"X-CSRF-Token": csrf})

    assert response.status_code == 200
    # The session's own ID token goes with the return address, so the provider
    # sends the person back to the app instead of leaving them on its page.
    logout_url = response.json()["logout_url"]
    assert logout_url.startswith("https://idp.test/end-session?post_logout_redirect_uri=")
    assert logout_url.endswith("&id_token_hint=id-token-for-code-123")
    assert hosted_client.get("/auth/me").status_code == 401
    # The token is never part of what the session endpoint reports.
    assert "id_token" not in response.text.replace("id_token_hint", "")


def test_hosted_logout_by_link_also_returns_home_and_survives_rotation(
    hosted_client, hosted_container
) -> None:
    login = hosted_client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    hosted_client.get(f"/auth/callback?state={state}&code=code-123", follow_redirects=False)
    me = hosted_client.get("/auth/me")
    assert me.status_code == 200 and "id_token" not in me.text

    # Age the session past the rotation threshold: the replacement keeps the token.
    store = hosted_container.auth_service._session_store
    from datetime import timedelta

    for key, session in list(store.sessions.items()):
        store.sessions[key] = session.model_copy(
            update={"created_at": session.created_at - timedelta(hours=2)}
        )

    response = hosted_client.get("/auth/logout", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].endswith("&id_token_hint=id-token-for-code-123")
    assert store.sessions == {}


def _age_sessions(container, hours: int = 2) -> None:
    from datetime import timedelta

    store = container.auth_service._session_store
    for key, session in list(store.sessions.items()):
        store.sessions[key] = session.model_copy(
            update={"created_at": session.created_at - timedelta(hours=hours)}
        )


def test_a_save_made_after_an_hour_signed_in_works_and_keeps_the_person_signed_in(
    auth_client, fake_container
) -> None:
    """After an hour the session id is replaced on the next request. When that request
    was a save, it used to be refused and the browser left with no session at all."""
    from tests.contract.test_canon_entities import _project_id, _publish_schema

    project_id = _project_id(fake_container)
    _publish_schema(auth_client, project_id)
    before = auth_client.cookies.get(fake_container.settings.SESSION_COOKIE_NAME)
    _age_sessions(fake_container)

    saved = auth_client.post(
        f"/api/v1/projects/{project_id}/entities",
        json={"type": "Character", "name": "Mara", "expected_revision": 0},
    )

    assert saved.status_code == 201, saved.text
    after = auth_client.cookies.get(fake_container.settings.SESSION_COOKIE_NAME)
    assert after and after != before
    # Still signed in, reading and writing, with the same write token.
    assert auth_client.get("/auth/me").status_code == 200
    again = auth_client.post(
        f"/api/v1/projects/{project_id}/entities",
        json={"type": "Character", "name": "Tobin", "expected_revision": 1},
    )
    assert again.status_code == 201, again.text


def test_the_replacement_session_reaches_the_browser_even_when_the_request_fails(
    auth_client, fake_container
) -> None:
    from tests.contract.test_canon_entities import _project_id, _publish_schema

    project_id = _project_id(fake_container)
    _publish_schema(auth_client, project_id)
    before = auth_client.cookies.get(fake_container.settings.SESSION_COOKIE_NAME)
    _age_sessions(fake_container)

    refused = auth_client.post(
        f"/api/v1/projects/{project_id}/entities",
        json={"type": "Nonesuch", "name": "Mara", "expected_revision": 0},
    )

    assert refused.status_code >= 400
    # The failure is about the content. The person is not signed out by it.
    after = auth_client.cookies.get(fake_container.settings.SESSION_COOKIE_NAME)
    assert after and after != before
    fake_container.auth_service._session_store.sessions.pop(before, None)
    assert auth_client.get("/auth/me").status_code == 200
