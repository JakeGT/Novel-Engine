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
    from backend.domain.usage import UsageLimit
    from backend.graph.schema import SchemaRegistry
    from backend.services.auth_service import AuthService
    from backend.services.character_service import CharacterService
    from backend.services.credential_service import CredentialService
    from backend.services.mutation_service import MutationService
    from backend.services.tenancy_service import TenancyService
    from backend.services.usage_service import ProviderUsageService
    from tests.doubles import (
        FakeClock,
        FakeDatabase,
        FakeEmbeddingProvider,
        FakeIdentityProvider,
        FakeMigrationVerifier,
        InMemoryCanonJournal,
        InMemoryCanonRepository,
        InMemoryCanonWriter,
        InMemoryCredentialVault,
        InMemoryGraphRepository,
        InMemoryIdentityStore,
        InMemoryLoginAttemptStore,
        InMemoryMediaStore,
        InMemoryObjectStore,
        InMemoryProviderUsageStore,
        InMemoryRateLimiter,
        InMemorySchemaVersionStore,
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
    vault = InMemoryCredentialVault()
    usage_store = InMemoryProviderUsageStore()
    from backend.adapters.purge import StoreWorkspacePurge

    objects = InMemoryObjectStore()
    purger = StoreWorkspacePurge(
        graph=graph,
        vectors=vectors,
        credentials=vault,
        usage=usage_store,
        objects=objects,
        artifacts=artifacts,
    )
    tenancy_service = TenancyService(
        tenancy,
        identity_store=identity_store,
        session_store=session_store,
        rate_limiter=rate_limiter,
        settings=settings,
        purger=purger,
    )
    credential_service = CredentialService(vault, tenancy=tenancy_service)
    usage_service = ProviderUsageService(
        usage_store,
        limit=UsageLimit(per_job_token_limit=200_000, period_token_limit=2_000_000),
    )
    from backend.services.schema_service import SchemaService

    schema_versions = InMemorySchemaVersionStore()
    schema_service = SchemaService(
        schema_versions, tenancy=tenancy_service, local_mode=not settings.is_hosted
    )
    journal = InMemoryCanonJournal()
    canon_writer = InMemoryCanonWriter()
    media_store = InMemoryMediaStore()
    canon_repository = InMemoryCanonRepository(journal=journal, media=media_store)
    mutation_service = MutationService(journal, canon_repository, local_mode=not settings.is_hosted)
    from backend.services.outbox_consumer import OutboxConsumer
    from backend.services.projection_service import GraphProjectionService

    projection_service = GraphProjectionService(canon=canon_repository, writer=canon_writer)
    outbox_consumer = OutboxConsumer(journal=journal, projector=projection_service)
    from backend.services.canon_entity_service import CanonEntityService

    canon_entity_service = CanonEntityService(
        mutation=mutation_service,
        reader=canon_repository,
        schema_store=schema_versions,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
    )
    from backend.services.canon_assertion_service import CanonAssertionService

    canon_assertion_service = CanonAssertionService(
        mutation=mutation_service,
        reader=canon_repository,
        schema_store=schema_versions,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
    )
    from backend.services.canon_history_service import CanonHistoryService

    canon_history_service = CanonHistoryService(
        canon=canon_repository,
        journal=journal,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
    )
    from backend.services.media_service import MediaService

    media_service = MediaService(
        media=media_store,
        objects=objects,
        mutation=mutation_service,
        reader=canon_repository,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
    )
    from backend.services.canon_archive import CanonArchiveService

    archive_service = CanonArchiveService(
        canon=canon_repository,
        journal=journal,
        schemas=schema_versions,
        media=media_store,
        objects=objects,
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
        credentials=vault,
        credential_service=credential_service,
        usage=usage_store,
        usage_service=usage_service,
        objects=objects,
        schema_versions=schema_versions,
        schema_service=schema_service,
        journal=journal,
        canon_writer=canon_writer,
        canon_repository=canon_repository,
        media_store=media_store,
        media_service=media_service,
        archive_service=archive_service,
        projection_service=projection_service,
        outbox_consumer=outbox_consumer,
        mutation_service=mutation_service,
        canon_entity_service=canon_entity_service,
        canon_assertion_service=canon_assertion_service,
        canon_history_service=canon_history_service,
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
        # Scoping tests provision workspaces through the API; provisioning policy
        # itself is exercised directly in tests/application/test_workspace_provisioning.py.
        ALLOW_WORKSPACE_SELF_CREATION=True,
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
