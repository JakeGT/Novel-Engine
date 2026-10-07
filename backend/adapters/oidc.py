"""Provider-neutral OIDC identity adapter.

Implements the authorization-code + PKCE flow against any OIDC discovery
endpoint. ID tokens are validated for signature (JWKS, cached with a bounded
TTL), issuer, audience, expiry, and nonce. Provider text is never surfaced.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.jwk import KeySet
from joserfc.jwt import JWTClaimsRegistry

from backend.core.logging import log_event
from backend.domain.identity import (
    IdentityClaims,
    IdentityProviderUnavailable,
    InvalidIdentityToken,
)

logger = logging.getLogger(__name__)

DISCOVERY_SUFFIX = "/.well-known/openid-configuration"
DEFAULT_JWKS_TTL_SECONDS = 300.0


class OIDCIdentityProvider:
    """Confidential-client OIDC adapter with strict, cached validation."""

    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        client_secret: str,
        scopes: list[str],
        timeout: float = 10.0,
        jwks_ttl_seconds: float = DEFAULT_JWKS_TTL_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._issuer = issuer.rstrip("/") + "/" if issuer else ""
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = scopes
        self._timeout = timeout
        self._jwks_ttl = jwks_ttl_seconds
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._discovery: dict[str, Any] | None = None
        self._jwks: dict[str, Any] | None = None
        self._jwks_fetched_at = 0.0

    def is_available(self) -> bool:
        return bool(self._issuer and self._client_id and self._client_secret)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, transport=self._transport)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _discover(self) -> dict[str, Any]:
        if self._discovery is None:
            if not self.is_available():
                raise IdentityProviderUnavailable("OIDC provider is not configured")
            url = self._issuer.rstrip("/") + DISCOVERY_SUFFIX
            response = await self._http().get(url)
            response.raise_for_status()
            document = response.json()
            if document.get("issuer") != self._issuer:
                raise InvalidIdentityToken("discovery issuer does not match configured issuer")
            self._discovery = document
        return self._discovery

    async def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        redirect_uri: str,
    ) -> str:
        discovery = await self._discover()
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self._scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{discovery['authorization_endpoint']}?{urlencode(params)}"

    async def _jwks_document(self, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        expired = self._jwks is None or (now - self._jwks_fetched_at) > self._jwks_ttl
        if force or expired:
            discovery = await self._discover()
            response = await self._http().get(discovery["jwks_uri"])
            response.raise_for_status()
            self._jwks = response.json()
            self._jwks_fetched_at = now
        return self._jwks or {}

    def _decode_and_validate(self, id_token: str) -> dict[str, Any]:
        key_set = KeySet.import_key_set(self._jwks or {})  # type: ignore[arg-type]
        token = jwt.decode(id_token, key_set, algorithms=["RS256", "ES256", "PS256"])
        registry = JWTClaimsRegistry(
            iss={"essential": True, "value": self._issuer},
            aud={"essential": True, "value": self._client_id},
            sub={"essential": True},
            exp={"essential": True},
        )
        registry.validate(token.claims)
        return dict(token.claims)

    async def _validate_id_token(self, id_token: str, expected_nonce: str) -> IdentityClaims:
        try:
            await self._jwks_document()
            claims = self._decode_and_validate(id_token)
        except Exception:
            # A signing key may have rotated in; refresh JWKS once and retry.
            try:
                await self._jwks_document(force=True)
                claims = self._decode_and_validate(id_token)
            except Exception as error:
                log_event(
                    logger,
                    "oidc.id_token.invalid",
                    level=logging.WARNING,
                    error_type=type(error).__name__,
                )
                raise InvalidIdentityToken("ID token validation failed") from error

        if claims.get("nonce") != expected_nonce:
            raise InvalidIdentityToken("ID token nonce mismatch")

        return IdentityClaims(
            issuer=str(claims["iss"]),
            subject=str(claims["sub"]),
            email=claims.get("email"),
            display_name=claims.get("name") or claims.get("preferred_username"),
        )

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        expected_nonce: str,
    ) -> IdentityClaims:
        discovery = await self._discover()
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "code_verifier": code_verifier,
        }
        response = await self._http().post(discovery["token_endpoint"], data=data)
        if response.status_code != 200:
            log_event(
                logger,
                "oidc.token_exchange.failed",
                level=logging.WARNING,
                status=response.status_code,
            )
            raise InvalidIdentityToken("token endpoint rejected the authorization code")
        id_token = response.json().get("id_token")
        if not id_token:
            raise InvalidIdentityToken("token response did not include an id_token")
        return await self._validate_id_token(id_token, expected_nonce)

    async def logout_url(
        self,
        *,
        post_logout_redirect_uri: str,
        id_token_hint: str | None = None,
        state: str | None = None,
    ) -> str:
        try:
            discovery = await self._discover()
        except Exception:
            return post_logout_redirect_uri
        endpoint = discovery.get("end_session_endpoint")
        if not endpoint:
            return post_logout_redirect_uri
        params: dict[str, str] = {"post_logout_redirect_uri": post_logout_redirect_uri}
        if id_token_hint:
            params["id_token_hint"] = id_token_hint
        if state:
            params["state"] = state
        return f"{endpoint}?{urlencode(params)}"
