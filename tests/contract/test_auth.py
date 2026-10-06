from urllib.parse import parse_qs, urlparse

import pytest

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
    assert response.json()["logout_url"].startswith("https://idp.test/end-session")
    assert hosted_client.get("/auth/me").status_code == 401
