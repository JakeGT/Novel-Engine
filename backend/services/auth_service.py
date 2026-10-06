"""Authentication orchestration.

Hosted mode runs the OIDC authorization-code + PKCE flow and maps validated
claims to a local user (without granting any workspace access). Local mode issues
a trusted single-user session for self-hosting. Sessions are always server-side.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.core.config import Settings
from backend.core.security import constant_time_equals, generate_pkce_pair, generate_token
from backend.domain.identity import (
    IdentityClaims,
    IdentityProviderUnavailable,
    InvalidIdentityToken,
    Session,
    User,
)
from backend.ports import (
    IdentityProviderPort,
    IdentityStorePort,
    LoginAttemptStorePort,
    SessionStorePort,
)

LOGIN_ATTEMPT_TTL_SECONDS = 600


@dataclass
class AuthenticatedSession:
    user: User
    session: Session


class AuthService:
    def __init__(
        self,
        *,
        settings: Settings,
        identity_provider: IdentityProviderPort,
        identity_store: IdentityStorePort,
        session_store: SessionStorePort,
        login_attempt_store: LoginAttemptStorePort,
    ) -> None:
        self._settings = settings
        self._identity_provider = identity_provider
        self._identity_store = identity_store
        self._session_store = session_store
        self._login_attempt_store = login_attempt_store

    @property
    def is_hosted(self) -> bool:
        return self._settings.is_hosted

    async def resolve(self, raw_session_id: str | None) -> AuthenticatedSession | None:
        if not raw_session_id:
            return None
        session = await self._session_store.get(raw_session_id)
        if session is None:
            return None
        user = await self._identity_store.get_user(session.user_id)
        if user is None or not user.is_active:
            await self._session_store.revoke(raw_session_id)
            return None
        return AuthenticatedSession(user=user, session=session)

    async def start_hosted_login(self) -> str:
        if not self._identity_provider.is_available():
            raise IdentityProviderUnavailable("OIDC provider is not configured")
        state = generate_token(24)
        nonce = generate_token(24)
        code_verifier, code_challenge = generate_pkce_pair()
        attempt = await self._login_attempt_store.create(
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            redirect_uri=self._settings.OIDC_REDIRECT_URI,
            ttl_seconds=LOGIN_ATTEMPT_TTL_SECONDS,
        )
        return await self._identity_provider.authorization_url(
            state=attempt.state,
            nonce=attempt.nonce,
            code_challenge=code_challenge,
            redirect_uri=attempt.redirect_uri,
        )

    async def complete_hosted_login(self, *, state: str, code: str) -> Session:
        attempt = await self._login_attempt_store.consume(state)
        if attempt is None:
            raise InvalidIdentityToken("login state is missing or expired")
        claims = await self._identity_provider.exchange_code(
            code=code,
            code_verifier=attempt.code_verifier,
            redirect_uri=attempt.redirect_uri,
            expected_nonce=attempt.nonce,
        )
        user = await self._identity_store.get_or_create_user(claims)
        return await self._new_session(user.id)

    async def start_local_login(self) -> Session:
        claims = IdentityClaims(
            issuer="local",
            subject=self._settings.AUTH_LOCAL_USER_ID,
            display_name="Local Author",
        )
        user = await self._identity_store.get_or_create_user(claims)
        return await self._new_session(user.id)

    async def _new_session(self, user_id: str) -> Session:
        return await self._session_store.create(
            user_id=user_id,
            ttl_seconds=self._settings.SESSION_TTL_SECONDS,
            csrf_token=generate_token(24),
        )

    async def logout(self, raw_session_id: str | None) -> None:
        if raw_session_id:
            await self._session_store.revoke(raw_session_id)

    async def hosted_logout_url(self) -> str:
        post_logout = self._settings.OIDC_POST_LOGOUT_REDIRECT_URI or "/"
        if not self._identity_provider.is_available():
            return post_logout
        return await self._identity_provider.logout_url(post_logout_redirect_uri=post_logout)

    @staticmethod
    def csrf_valid(session: Session, header_value: str | None) -> bool:
        if not header_value:
            return False
        return constant_time_equals(session.csrf_token, header_value)
