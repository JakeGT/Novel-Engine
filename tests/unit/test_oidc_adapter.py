import time

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey

from backend.adapters.oidc import OIDCIdentityProvider
from backend.domain.identity import InvalidIdentityToken

pytestmark = pytest.mark.unit

ISSUER = "https://idp.test/application/o/novel-engine/"
CLIENT_ID = "test-client"
REDIRECT_URI = "https://novel-engine.jaketalling.com/auth/callback"
NONCE = "nonce-abc"


def _key_and_jwks() -> tuple[object, dict]:
    key = RSAKey.generate_key(2048, private=True)
    jwk = key.as_dict(private=False)
    jwk["kid"] = "test-key"
    jwk["alg"] = "RS256"
    return key, {"keys": [jwk]}


def _discovery() -> dict:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": ISSUER + "authorize/",
        "token_endpoint": ISSUER + "token/",
        "jwks_uri": ISSUER + "jwks/",
        "end_session_endpoint": ISSUER + "end-session/",
    }


def _make_provider(token: str, jwks: dict) -> OIDCIdentityProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=_discovery())
        if request.url.path.endswith("/jwks/"):
            return httpx.Response(200, json=jwks)
        if request.url.path.endswith("/token/"):
            return httpx.Response(200, json={"id_token": token, "access_token": "a"})
        return httpx.Response(404)

    return OIDCIdentityProvider(
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret="secret",
        scopes=["openid", "profile", "email"],
        transport=httpx.MockTransport(handler),
    )


def _sign(
    key: object,
    *,
    iss: str = ISSUER,
    aud: str = CLIENT_ID,
    nonce: str = NONCE,
    exp: int | None = None,
    auth_time: int | None = None,
) -> str:
    claims = {
        "iss": iss,
        "aud": aud,
        "sub": "subject-1",
        "email": "user@example.com",
        "name": "Test User",
        "nonce": nonce,
        "iat": int(time.time()),
        "exp": exp if exp is not None else int(time.time()) + 300,
    }
    if auth_time is not None:
        claims["auth_time"] = auth_time
    return jwt.encode({"alg": "RS256", "kid": "test-key"}, claims, key)


@pytest.mark.asyncio
async def test_authorization_url_includes_pkce_and_state() -> None:
    _, jwks = _key_and_jwks()
    provider = _make_provider("unused", jwks)
    try:
        url = await provider.authorization_url(
            state="state-1", nonce=NONCE, code_challenge="challenge-1", redirect_uri=REDIRECT_URI
        )
    finally:
        await provider.aclose()
    assert "response_type=code" in url
    assert "code_challenge=challenge-1" in url
    assert "code_challenge_method=S256" in url
    assert "state=state-1" in url
    assert "nonce=nonce-abc" in url


@pytest.mark.asyncio
async def test_authorization_url_asks_for_the_password_only_when_fresh() -> None:
    from urllib.parse import parse_qs, urlparse

    _, jwks = _key_and_jwks()
    provider = _make_provider("unused", jwks)
    try:
        ordinary = await provider.authorization_url(
            state="state-1", nonce=NONCE, code_challenge="challenge-1", redirect_uri=REDIRECT_URI
        )
        fresh = await provider.authorization_url(
            state="state-2",
            nonce=NONCE,
            code_challenge="challenge-2",
            redirect_uri=REDIRECT_URI,
            fresh=True,
        )
    finally:
        await provider.aclose()

    ordinary_query = parse_qs(urlparse(ordinary).query)
    fresh_query = parse_qs(urlparse(fresh).query)
    assert "prompt" not in ordinary_query and "max_age" not in ordinary_query
    assert fresh_query["prompt"] == ["login"]
    assert fresh_query["max_age"] == ["0"]


@pytest.mark.asyncio
async def test_valid_id_token_keeps_the_auth_time_claim() -> None:
    key, jwks = _key_and_jwks()
    auth_time = int(time.time()) - 120
    provider = _make_provider(_sign(key, auth_time=auth_time), jwks)
    try:
        claims = await provider.exchange_code(
            code="code-1", code_verifier="v", redirect_uri=REDIRECT_URI, expected_nonce=NONCE
        )
    finally:
        await provider.aclose()
    assert claims.auth_time == auth_time


@pytest.mark.asyncio
async def test_valid_id_token_is_accepted() -> None:
    key, jwks = _key_and_jwks()
    provider = _make_provider(_sign(key), jwks)
    try:
        claims = await provider.exchange_code(
            code="code-1", code_verifier="v", redirect_uri=REDIRECT_URI, expected_nonce=NONCE
        )
    finally:
        await provider.aclose()
    assert claims.issuer == ISSUER
    assert claims.subject == "subject-1"
    assert claims.email == "user@example.com"
    assert claims.display_name == "Test User"


@pytest.mark.asyncio
async def test_nonce_mismatch_is_rejected() -> None:
    key, jwks = _key_and_jwks()
    provider = _make_provider(_sign(key, nonce="different"), jwks)
    try:
        with pytest.raises(InvalidIdentityToken):
            await provider.exchange_code(
                code="code-1", code_verifier="v", redirect_uri=REDIRECT_URI, expected_nonce=NONCE
            )
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_audience_mismatch_is_rejected() -> None:
    key, jwks = _key_and_jwks()
    provider = _make_provider(_sign(key, aud="someone-else"), jwks)
    try:
        with pytest.raises(InvalidIdentityToken):
            await provider.exchange_code(
                code="code-1", code_verifier="v", redirect_uri=REDIRECT_URI, expected_nonce=NONCE
            )
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_expired_id_token_is_rejected() -> None:
    key, jwks = _key_and_jwks()
    provider = _make_provider(_sign(key, exp=int(time.time()) - 60), jwks)
    try:
        with pytest.raises(InvalidIdentityToken):
            await provider.exchange_code(
                code="code-1", code_verifier="v", redirect_uri=REDIRECT_URI, expected_nonce=NONCE
            )
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_discovery_issuer_mismatch_is_rejected() -> None:
    _, jwks = _key_and_jwks()

    def handler(request: httpx.Request) -> httpx.Response:
        wrong = dict(_discovery())
        wrong["issuer"] = "https://evil.test/"
        return httpx.Response(200, json=wrong)

    provider = OIDCIdentityProvider(
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret="secret",
        scopes=["openid"],
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(InvalidIdentityToken):
            await provider.authorization_url(
                state="s", nonce="n", code_challenge="c", redirect_uri=REDIRECT_URI
            )
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_logout_url_sends_a_return_address_only_with_an_id_token() -> None:
    from urllib.parse import parse_qs, urlparse

    _, jwks = _key_and_jwks()
    provider = _make_provider("unused", jwks)
    try:
        bare = await provider.logout_url(post_logout_redirect_uri="https://app.test/")
        hinted = await provider.logout_url(
            post_logout_redirect_uri="https://app.test/", id_token_hint="token-1"
        )
    finally:
        await provider.aclose()
    # A return address without the ID token is refused by the provider as malformed.
    assert bare == ISSUER + "end-session/"
    assert parse_qs(urlparse(hinted).query) == {
        "post_logout_redirect_uri": ["https://app.test/"],
        "id_token_hint": ["token-1"],
    }
