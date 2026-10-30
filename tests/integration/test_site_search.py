"""The site's search setup end to end: migration, sealed key, rotation, and a build."""

from __future__ import annotations

import asyncio
import json

import httpx
import psycopg
import pytest
from sqlalchemy import text

from backend.adapters.embedding_fake import DeterministicEmbedding
from backend.adapters.embedding_generations import PgEmbeddingIndexStore
from backend.adapters.embedding_site import SiteEmbedder
from backend.adapters.site_search import SiteSearchStore
from backend.adapters.vector import PgVectorStore
from backend.core.crypto import generate_kek
from backend.domain.embedding import EmbeddingTask, TextInput
from backend.services.embedding_index import EmbeddingIndexService
from backend.services.site_search import SiteSearchService

pytestmark = pytest.mark.integration

W, P = "ws_site", "prj_site"
DIMENSION = 256


class StandIn:
    """A stand-in embeddings service whose vectors come from the deterministic embedder."""

    def __init__(self, dimension: int = DIMENSION) -> None:
        self.words = DeterministicEmbedding(dimension=dimension)
        self.sent: list[list[str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.sent.append(list(body["input"]))
        vectors = self.words.embed(
            [TextInput(text=text) for text in body["input"]], task=EmbeddingTask.DOCUMENT
        )
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": index, "embedding": list(vector.values)}
                    for index, vector in enumerate(vectors)
                ]
            },
        )


def test_migration_creates_site_tables_and_authenticated_at(disposable_database, pg_engine) -> None:
    with pg_engine.connect() as connection:
        assert (
            connection.execute(text("SELECT to_regclass('public.site_search_setup')")).scalar()
            is not None
        )
        assert (
            connection.execute(text("SELECT to_regclass('public.site_search_secrets')")).scalar()
            is not None
        )
        # The column exists, is not null, and is backfilled to created_at.
        nullable = connection.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'sessions' AND column_name = 'authenticated_at'"
            )
        ).scalar()
        assert nullable == "NO"


def test_the_site_key_round_trips_and_relocks_under_a_new_kek(disposable_database) -> None:
    dsn = disposable_database.psycopg_dsn
    old_kek, new_kek = generate_kek(), generate_kek()
    store = SiteSearchStore(dsn, kek=old_kek, key_version=1)
    secret = store.save_secret(
        provider="openai", host="api.openai.com", plaintext_key="sk-test-not-real", created_by="u1"
    )
    assert secret.key_last4 == "real"
    assert store.resolve_plaintext() == "sk-test-not-real"

    with psycopg.connect(dsn) as connection:
        row = connection.execute(
            "SELECT ciphertext, wrapped_dek, key_version FROM site_search_secrets WHERE id = 'site'"
        ).fetchone()
    assert "sk-test" not in row[0] and "sk-test" not in row[1]
    assert row[2] == 1

    rotating = SiteSearchStore(dsn, kek=new_kek, key_version=2, previous_keks={1: old_kek})
    assert rotating.verify().ok and rotating.verify().old_active == 1
    dry = rotating.rekey(dry_run=True)
    assert (
        dry.ok
        and len(dry.rekeyed) == 1
        and {item.key_version for item in rotating.key_versions()} == {1}
    )
    applied = rotating.rekey(dry_run=False)
    assert applied.ok and [item.credential_id for item in applied.rekeyed] == ["site"]

    with psycopg.connect(dsn) as connection:
        version = connection.execute(
            "SELECT key_version FROM site_search_secrets WHERE id = 'site'"
        ).fetchone()[0]
    assert version == 2
    # With the old key gone, the key still opens, and the old version is safe to retire.
    retired = SiteSearchStore(dsn, kek=new_kek, key_version=2)
    assert retired.resolve_plaintext() == "sk-test-not-real"
    assert retired.verify().ok and retired.verify().opened == 1
    assert rotating.verify().retirable_versions == [1]


@pytest.mark.asyncio
async def test_the_rekey_command_moves_the_site_key_too(disposable_database) -> None:
    from backend.devtools import rekey

    dsn = disposable_database.psycopg_dsn
    old_kek, new_kek = generate_kek(), generate_kek()
    SiteSearchStore(dsn, kek=old_kek, key_version=1).save_secret(
        provider="openai", host="api.openai.com", plaintext_key="sk-test-not-real", created_by="u1"
    )
    settings = disposable_database.settings(
        CREDENTIAL_KEK=new_kek, CREDENTIAL_KEK_VERSION=2, CREDENTIAL_PREVIOUS_KEKS=f"1:{old_kek}"
    )

    code, dry = await rekey.run("rekey", settings)
    assert code == 0 and dry["would_move"] == 1
    code, moved = await rekey.run("rekey", settings, apply=True)
    assert code == 0 and moved["moved"] == 1
    code, check = await rekey.run("verify", settings)
    assert code == 0 and check["opened"] == 1 and check["versions_safe_to_remove"] == [1]
    assert new_kek not in str(check) and old_kek not in str(moved)


@pytest.mark.asyncio
async def test_authenticated_at_survives_session_rotation(disposable_database) -> None:
    from backend.adapters.identity_store import PostgresIdentityStore, PostgresSessionStore
    from backend.core.database import create_engine, create_session_factory
    from backend.services.auth_service import AuthService
    from tests.doubles import FakeIdentityProvider, InMemoryLoginAttemptStore

    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    try:
        settings = disposable_database.settings(AUTH_MODE="local", SESSION_ROTATION_SECONDS=0)
        auth = AuthService(
            settings=settings,
            identity_provider=FakeIdentityProvider(),
            identity_store=PostgresIdentityStore(factory),
            session_store=PostgresSessionStore(factory),
            login_attempt_store=InMemoryLoginAttemptStore(),
        )
        session = await auth.start_local_login()
        original = session.authenticated_at

        resolved = await auth.resolve(session.id)

        assert resolved is not None and resolved.rotated_session is not None
        assert resolved.rotated_session.authenticated_at == original
        assert resolved.rotated_session.id != session.id
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_saved_setup_is_found_and_embeds_through_the_stand_in(disposable_database) -> None:
    dsn = disposable_database.psycopg_dsn
    store = SiteSearchStore(dsn, kek=generate_kek())
    store.save_setup(
        provider="openai",
        model="text-embedding-3-small",
        dimension=DIMENSION,
        base_url="https://embed.example.com/v1",
        updated_by="u1",
        verified=True,
    )
    store.save_secret(
        provider="openai",
        host="embed.example.com",
        plaintext_key="sk-test-not-real",
        created_by="u1",
    )
    standin = StandIn()
    embedder = SiteEmbedder(
        settings=disposable_database.settings(AUTH_MODE="local"),
        store=store,
        built_in=None,
        transport=httpx.MockTransport(standin),
    )

    assert embedder.source == "page"
    where = embedder.where_words_go()
    assert where.host == "embed.example.com"
    assert where.name == "OpenAI"
    vectors = embedder.embed([TextInput(text="lantern")], task=EmbeddingTask.QUERY)
    assert len(vectors) == 1
    assert standin.sent[-1] == ["lantern"]


@pytest.mark.asyncio
async def test_a_build_started_through_the_service_activates_when_complete(
    disposable_database,
) -> None:
    dsn = disposable_database.psycopg_dsn
    store = SiteSearchStore(dsn, kek=generate_kek())
    store.save_setup(
        provider="openai",
        model="text-embedding-3-small",
        dimension=DIMENSION,
        base_url="https://embed.example.com/v1",
        updated_by="u1",
        verified=True,
    )
    store.save_secret(
        provider="openai",
        host="embed.example.com",
        plaintext_key="sk-test-not-real",
        created_by="u1",
    )
    settings = disposable_database.settings(AUTH_MODE="local")
    embedder = SiteEmbedder(
        settings=settings, store=store, built_in=None, transport=httpx.MockTransport(StandIn())
    )
    index = PgEmbeddingIndexStore(dsn)
    vectors = PgVectorStore(dsn, embedder, index=index)
    vectors.add_documents(
        ["the lighthouse lantern went dark"],
        [{"kind": "passage"}],
        ["p_lamp"],
        workspace_id=W,
        project_id=P,
    )
    index_service = EmbeddingIndexService(index=index, embedder=embedder)
    service = SiteSearchService(
        settings=settings, store=store, embedder=embedder, index=index_service
    )

    await service.start_build(sign_in_needed=False)
    assert service._build_task is not None
    await asyncio.wait_for(service._build_task, timeout=30)

    active = index.active()
    assert active is not None and active.space_key == embedder.space.key
    assert index.building() is None
    assert index.count(active.id, workspace_id=W) == 1
