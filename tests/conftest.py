"""Shared deterministic fixtures for the Phase 0 test harness."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from backend.core.config import Settings
from tests.doubles import FakeClock, OrderedIdGenerator, ScriptedAIClient


@pytest.fixture
def fixed_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def id_generator() -> OrderedIdGenerator:
    return OrderedIdGenerator()


@pytest.fixture
def settings_factory() -> Callable[..., Settings]:
    def _factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "OPENAI_API_KEY": "",
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASSWORD": "password",
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    return _factory


@pytest.fixture
def scripted_ai() -> Callable[[list[Any]], ScriptedAIClient]:
    def _factory(responses: list[Any] | None = None) -> ScriptedAIClient:
        return ScriptedAIClient(responses)

    return _factory


HOSTED_ISSUER = "https://idp.test/application/o/novel-engine/"


def _build_fake_container(settings, tmp_path):
    from backend.adapters.artifacts import NullArtifactStore
    from backend.container import Container
    from backend.graph.schema import SchemaRegistry
    from backend.services.auth_service import AuthService
    from backend.services.character_service import CharacterService
    from backend.services.tenancy_service import TenancyService
    from tests.doubles import (
        FakeClock,
        FakeDatabase,
        FakeEmbeddingProvider,
        FakeIdentityProvider,
        FakeMigrationVerifier,
        InMemoryGraphRepository,
        InMemoryIdentityStore,
        InMemoryLoginAttemptStore,
        InMemoryRateLimiter,
        InMemorySessionStore,
        InMemoryTenancyStore,
        InMemoryVectorStore,
        OrderedIdGenerator,
        ScriptedAIGateway,
    )

    graph = InMemoryGraphRepository()
    vectors = InMemoryVectorStore()
    embeddings = FakeEmbeddingProvider()
    ai = ScriptedAIGateway()
    registry = SchemaRegistry(config_path=str(tmp_path / "custom_schema.json"))
    artifacts = NullArtifactStore()
    service = CharacterService(
        graph=graph,
        ai=ai,
        embeddings=embeddings,
        schema_registry=registry,
        artifacts=artifacts,
    )
    identity_provider = FakeIdentityProvider(issuer=settings.OIDC_ISSUER or HOSTED_ISSUER)
    identity_store = InMemoryIdentityStore()
    session_store = InMemorySessionStore()
    auth_service = AuthService(
        settings=settings,
        identity_provider=identity_provider,
        identity_store=identity_store,
        session_store=session_store,
        login_attempt_store=InMemoryLoginAttemptStore(),
    )
    tenancy = InMemoryTenancyStore()
    rate_limiter = InMemoryRateLimiter()
    tenancy_service = TenancyService(
        tenancy,
        identity_store=identity_store,
        session_store=session_store,
        rate_limiter=rate_limiter,
    )
    return Container(
        settings=settings,
        graph=graph,
        vectors=vectors,
        embeddings=embeddings,
        ai=ai,
        schema_registry=registry,
        clock=FakeClock(),
        ids=OrderedIdGenerator(),
        artifacts=artifacts,
        neo4j_migrations=FakeMigrationVerifier(current=True),
        database=FakeDatabase(ready=True),
        character_service=service,
        identity_provider=identity_provider,
        auth_service=auth_service,
        tenancy=tenancy,
        tenancy_service=tenancy_service,
        rate_limiter=rate_limiter,
    )


@pytest.fixture
def fake_container(settings_factory, tmp_path):
    return _build_fake_container(settings_factory(APP_ENV="test"), tmp_path)


@pytest.fixture
def hosted_container(settings_factory, tmp_path):
    settings = settings_factory(
        APP_ENV="test",
        AUTH_MODE="hosted",
        OIDC_ISSUER=HOSTED_ISSUER,
        OIDC_CLIENT_ID="test-client",
        OIDC_CLIENT_SECRET="test-secret",
        OIDC_REDIRECT_URI="https://novel-engine.jaketalling.com/auth/callback",
        OIDC_POST_LOGOUT_REDIRECT_URI="https://novel-engine.jaketalling.com/",
    )
    return _build_fake_container(settings, tmp_path)


@pytest.fixture
def api_client(fake_container):
    from fastapi.testclient import TestClient

    from backend.main import create_app

    app = create_app(container=fake_container)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth_client(api_client, fake_container):
    """Local-mode client with a session and the CSRF header pre-set.

    Graph/context/character operations are workspace-scoped and require an
    authenticated scope; this fixture provides the local workspace session.
    """
    api_client.get("/auth/login", follow_redirects=False)
    csrf = api_client.cookies.get(fake_container.settings.CSRF_COOKIE_NAME)
    assert csrf, "local login did not set a CSRF cookie"
    api_client.headers["X-CSRF-Token"] = csrf
    return api_client


@pytest.fixture
def hosted_client(hosted_container):
    from fastapi.testclient import TestClient

    from backend.main import create_app

    app = create_app(container=hosted_container)
    # HTTPS base URL so Secure session cookies are stored and resent by the client.
    with TestClient(app, base_url="https://testserver") as client:
        yield client
