"""Generation-versioned vectors in PostgreSQL/pgvector (CTX-012, migration 0047).

Covers: moving legacy ``vector_documents`` rows across without embedding again;
generations of different sizes in one table, each with its own partial HNSW index;
the trigger that refuses a mixed space; building, switching and rolling back with
real media rows; and the gates applied in the same query as the ranking.
"""

from __future__ import annotations

import hashlib
import io
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from alembic import command
from pgvector.psycopg import register_vector

from backend.adapters.embedding import MiniLMTextEmbedding
from backend.adapters.embedding_fake import DeterministicEmbedding
from backend.adapters.embedding_generations import PgEmbeddingIndexStore
from backend.adapters.vector import PgVectorStore
from backend.domain.branching import working_in
from backend.domain.embedding import GenerationState, Modality, SearchGate
from backend.migrate import alembic_config
from backend.services.embedding_index import EmbeddingIndexService, picture_row_id
from backend.services.media_search import MediaSearchService
from tests.doubles import FakeEmbeddingProvider
from tests.integration.conftest import _psycopg_dsn, _sqlalchemy_dsn

pytestmark = pytest.mark.integration

W, P = "ws_gen", "prj_gen"


@pytest.fixture
def database_at_0046() -> Iterator[tuple[str, object]]:
    name = f"novel_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(_psycopg_dsn("postgres"), autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}"')
    try:
        config = alembic_config()
        config.set_main_option("sqlalchemy.url", _sqlalchemy_dsn(name))
        command.upgrade(config, "0046_media_variants")
        yield _psycopg_dsn(name), config
    finally:
        with psycopg.connect(_psycopg_dsn("postgres"), autocommit=True) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_legacy_rows_move_across_without_embedding_again(database_at_0046) -> None:  # type: ignore[no-untyped-def]
    dsn, config = database_at_0046
    legacy = FakeEmbeddingProvider(model="all-MiniLM-L6-v2", version="1")
    rows = [
        ("d_coast", "the basalt coast", "all-MiniLM-L6-v2", "1"),
        ("d_forge", "the forge at dawn", "all-MiniLM-L6-v2", "1"),
        ("d_other", "made by another model", "other-model", "2"),
        ("d_unknown", "no model recorded", None, None),
    ]
    with psycopg.connect(dsn, autocommit=True) as connection:
        register_vector(connection)
        for doc_id, text, model, version in rows:
            connection.execute(
                "INSERT INTO vector_documents (id, document, metadata, embedding, embedding_model, "
                "embedding_version, embedding_dimension, workspace_id, project_id) VALUES "
                '(%s, %s, \'{"kind": "passage"}\', %s::vector, %s, %s, 384, %s, %s)',
                (doc_id, text, str(legacy.embed(text)), model, version, W, P),
            )
    command.upgrade(config, "head")

    store = PgEmbeddingIndexStore(dsn)
    generations = {g.model: g for g in store.list_generations()}
    active = store.active()
    assert active is not None and active.model == "all-MiniLM-L6-v2"
    assert generations["other-model"].state is GenerationState.RETIRED
    assert generations["unknown"].state is GenerationState.RETIRED
    assert store.count(active.id) == 2
    with psycopg.connect(dsn) as connection:
        assert connection.execute("SELECT to_regclass('vector_documents')").fetchone() == (None,)

    # The configured MiniLM searches the moved rows as they were, nothing embedded again.
    calls_before = len(legacy.calls)
    vectors = PgVectorStore(dsn, MiniLMTextEmbedding(legacy), dimension=384, index=store)
    hits = vectors.query("the basalt coast", workspace_id=W, project_id=P, n_results=5)
    assert hits[0]["id"] == "d_coast"
    assert {hit["id"] for hit in hits} == {"d_coast", "d_forge"}  # other models never mixed in
    assert len(legacy.calls) == calls_before + 1  # only the query

    command.downgrade(config, "0046_media_variants")
    with psycopg.connect(dsn) as connection:
        back = connection.execute("SELECT id FROM vector_documents ORDER BY id").fetchall()
    assert back == [("d_coast",), ("d_forge",)]


def _uses_index(dsn: str, generation_id: str, dimension: int) -> bool:
    with psycopg.connect(dsn) as connection:
        connection.execute("SET enable_seqscan = off")
        plan = connection.execute(
            f"EXPLAIN SELECT id FROM embedding_vectors WHERE generation_id = '{generation_id}' "
            f"ORDER BY embedding::vector({dimension}) <=> "
            f"'[{','.join(['0.1'] * dimension)}]'::vector({dimension}) LIMIT 5"
        ).fetchall()
    return any(f"embedding_vectors_hnsw_{generation_id}" in line[0] for line in plan)


def test_generations_of_different_sizes_share_the_table_never_a_space(disposable_database) -> None:  # type: ignore[no-untyped-def]
    dsn = disposable_database.psycopg_dsn
    store = PgEmbeddingIndexStore(dsn)
    small = DeterministicEmbedding(dimension=256, model="small")
    large = DeterministicEmbedding(dimension=512, model="large")
    first = PgVectorStore(dsn, small, index=store)
    first.add_documents(
        ["harbour lantern"], [{"kind": "passage"}], ["p1"], workspace_id=W, project_id=P
    )
    small_generation = store.active()
    assert small_generation is not None and small_generation.dimension == 256

    building = store.create_building(large.space)
    second = PgVectorStore(dsn, large, index=store)
    second.add_documents(
        ["harbour lantern"], [{"kind": "passage"}], ["p1"], workspace_id=W, project_id=P
    )
    assert store.count(building.id) == 1
    # The old space no longer holds a row for a document written with the new model.
    assert store.count(small_generation.id) == 0

    assert _uses_index(dsn, building.id, 512)

    with psycopg.connect(dsn, autocommit=True) as connection, pytest.raises(psycopg.Error):
        connection.execute(
            "INSERT INTO embedding_vectors (generation_id, workspace_id, project_id, id, kind, "
            "modality, task, content_hash, embedding) VALUES (%s, %s, %s, 'bad', 'passage', "
            "'text', 'document', 'h', %s::vector)",
            (building.id, W, P, "[" + ",".join(["0.1"] * 256) + "]"),
        )

    store.activate(building.id)
    with psycopg.connect(dsn, autocommit=True) as connection, pytest.raises(psycopg.Error):
        connection.execute(
            "INSERT INTO embedding_vectors (generation_id, workspace_id, project_id, id, kind, "
            "modality, task, content_hash, embedding) VALUES (%s, %s, %s, 'late', 'passage', "
            "'text', 'document', 'h', %s::vector)",
            (small_generation.id, W, P, "[" + ",".join(["0.1"] * 256) + "]"),
        )
    assert store.rollback().id == small_generation.id
    store.discard(building.id)
    with psycopg.connect(dsn) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'embedding_vectors'"
            )
        }
    assert f"embedding_vectors_hnsw_{building.id}" not in names


def _png(seed: int) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (seed, 255 - seed, (seed * 3) % 256)).save(buffer, format="PNG")
    return buffer.getvalue()


class _Objects:
    def __init__(self) -> None:
        self.data: dict[str, bytes] = {}

    def open(self, key: str, *, workspace_id: str, project_id: str) -> bytes:
        return self.data[f"{workspace_id}/{project_id}/{key}"]


def _add_picture(
    connection: psycopg.Connection,
    objects: _Objects,
    embedder: DeterministicEmbedding,
    *,
    asset: str,
    words: str,
    workspace: str = W,
    project: str = P,
    visibility: str | None = None,
    authority: str | None = None,
    retired: bool = False,
) -> str:
    data = _png(len(objects.data) * 17 % 256)
    checksum = hashlib.sha256(data).hexdigest()
    version = f"ver_{asset}"
    embedder.describe(data, words)
    objects.data[f"{workspace}/{project}/obj_{asset}"] = data
    connection.execute(
        "INSERT INTO media_assets (workspace_id, project_id, id, name, created_revision) "
        "VALUES (%s, %s, %s, %s, 1)",
        (workspace, project, asset, asset),
    )
    connection.execute(
        "INSERT INTO media_asset_versions (workspace_id, project_id, id, asset_id, version, "
        "object_key, content_type, byte_length, checksum, authority, visibility, "
        "created_revision, retired_revision) VALUES (%s, %s, %s, %s, 1, %s, 'image/png', %s, "
        "%s, %s, %s, 1, %s)",
        (
            workspace,
            project,
            version,
            asset,
            f"orig_{asset}",
            len(data),
            checksum,
            authority,
            visibility,
            2 if retired else None,
        ),
    )
    connection.execute(
        "INSERT INTO media_variants (workspace_id, project_id, asset_version_id, kind, object_key, "
        "content_type, width, height, byte_length, checksum, source_checksum, processor_version) "
        "VALUES (%s, %s, %s, 'web', %s, 'image/png', 4, 4, %s, %s, %s, 'p1')",
        (workspace, project, version, f"obj_{asset}", len(data), checksum, checksum),
    )
    connection.execute(
        "INSERT INTO media_segments (workspace_id, project_id, id, asset_id, asset_version_id, "
        "kind, x, y, width, height, content_hash) VALUES (%s, %s, %s, %s, %s, 'whole', 0, 0, 4, 4, %s)",
        (workspace, project, f"seg_{asset}", asset, version, checksum),
    )
    return version


@pytest.mark.asyncio
async def test_build_switch_and_search_with_real_gates(disposable_database) -> None:  # type: ignore[no-untyped-def]
    dsn = disposable_database.psycopg_dsn
    store = PgEmbeddingIndexStore(dsn)
    objects = _Objects()
    text_only = DeterministicEmbedding(dimension=256, modalities=(Modality.TEXT,), model="text")
    multi = DeterministicEmbedding(dimension=512, model="multi")
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO branches (id, workspace_id, project_id, name, is_root) "
            "VALUES ('br_root', %s, %s, 'main', true)",
            (W, P),
        )
        _add_picture(connection, objects, multi, asset="ast_coast", words="windswept basalt coast")
        _add_picture(
            connection,
            objects,
            multi,
            asset="ast_secret",
            words="windswept basalt coast cave",
            visibility="secret",
        )
        _add_picture(
            connection,
            objects,
            multi,
            asset="ast_rejected",
            words="windswept basalt coast",
            authority="rejected",
        )
        _add_picture(
            connection,
            objects,
            multi,
            asset="ast_retired",
            words="windswept basalt coast",
            retired=True,
        )
        _add_picture(
            connection, objects, multi, asset="ast_linked", words="windswept basalt coast smugglers"
        )
        _add_picture(
            connection,
            objects,
            multi,
            asset="ast_elsewhere",
            words="windswept basalt coast",
            project="prj_other",
        )
        connection.execute(
            "INSERT INTO asset_links (workspace_id, project_id, branch_id, id, asset_id, role, "
            "target_record_type, target_record_id, created_revision, visibility, known_by) "
            "VALUES (%s, %s, 'br_root', 'lnk_1', 'ast_linked', 'DEPICTS', 'entity', "
            "'ent_smuggler', 1, 'secret', '[\"ent_mara\"]')",
            (W, P),
        )
        # The same picture secretly linked in a what-if, where only Tobin knows.
        connection.execute(
            "INSERT INTO branches (id, workspace_id, project_id, name, is_root) "
            "VALUES ('br_whatif', %s, %s, 'what if', false)",
            (W, P),
        )
        connection.execute(
            "INSERT INTO asset_links (workspace_id, project_id, branch_id, id, asset_id, role, "
            "target_record_type, target_record_id, created_revision, visibility, known_by) "
            "VALUES (%s, %s, 'br_whatif', 'lnk_2', 'ast_linked', 'DEPICTS', 'entity', "
            "'ent_smuggler', 1, 'secret', '[\"ent_tobin\"]')",
            (W, P),
        )

    # A text-only install first: its index never holds pictures.
    PgVectorStore(dsn, text_only, index=store).add_documents(
        ["the lantern on the harbour wall"],
        [{"kind": "passage"}],
        ["passage:s1:1"],
        workspace_id=W,
        project_id=P,
    )
    text_service = EmbeddingIndexService(index=store, embedder=text_only, objects=objects)
    old = store.active()
    assert old is not None
    await text_service.build_all(old.id)
    assert store.count(old.id) == 1

    # Switch to a model that takes pictures: build beside, switch, search, roll back.
    service = EmbeddingIndexService(index=store, embedder=multi, objects=objects)
    new = await service.start_build()
    assert (await service.status()).building_lag["documents"] == 1  # type: ignore[index]
    report = await service.build_all(new.id)
    # Four pictures here and one in another story; the retired version is not ready.
    assert report.documents == 1 and report.pictures == 5
    await service.activate(new.id)
    hashes = store.hashes(new.id, workspace_id=W, project_id=P)
    assert picture_row_id("ver_ast_coast") in hashes
    assert picture_row_id("ver_ast_retired") not in hashes

    search = MediaSearchService(index=store, embedder=multi)
    everything = await search.pictures_for(W, P, query="windswept basalt coast", limit=10)
    found = {p.asset_id for p in everything.pictures}
    assert "ast_coast" in found and "ast_rejected" not in found and "ast_elsewhere" not in found

    pov = SearchGate(point_of_view=True, point_of_view_id="ent_tobin")
    seen = {
        p.asset_id
        for p in (
            await search.pictures_for(W, P, query="windswept basalt coast", gate=pov, limit=10)
        ).pictures
    }
    assert seen == {"ast_coast"}
    # The link's target is not what decides: whether the character is among those who know.
    knows = SearchGate(point_of_view=True, point_of_view_id="ent_mara")
    seen = {
        p.asset_id
        for p in (
            await search.pictures_for(W, P, query="basalt coast", gate=knows, limit=10)
        ).pictures
    }
    assert seen == {"ast_coast", "ast_linked"}
    nobody = SearchGate(point_of_view=True)
    assert {
        p.asset_id
        for p in (
            await search.pictures_for(W, P, query="basalt coast", gate=nobody, limit=10)
        ).pictures
    } == {"ast_coast"}
    # In the what-if the roles swap: Tobin knows there, Mara does not.
    with working_in("br_whatif"):
        in_whatif = {
            p.asset_id
            for p in (
                await search.pictures_for(W, P, query="basalt coast", gate=pov, limit=10)
            ).pictures
        }
        assert in_whatif == {"ast_coast", "ast_linked"}
        assert "ast_linked" not in {
            p.asset_id
            for p in (
                await search.pictures_for(W, P, query="basalt coast", gate=knows, limit=10)
            ).pictures
        }

    linked = await search.pictures_for(
        W, P, query="harbour", linked_asset_ids=["ast_secret", "ast_coast"], gate=pov
    )
    assert linked.pictures[0].asset_id == "ast_coast" and linked.pictures[0].reason == "linked"
    assert "ast_secret" not in {p.asset_id for p in linked.pictures}

    # A retired picture version is forgotten in every generation.
    removed = await service.versions_removed(W, P, ["ver_ast_coast"])
    assert removed == 1

    restored = await service.rollback()
    assert restored.id == old.id
    assert store.get(new.id).state is GenerationState.RETIRED  # type: ignore[union-attr]


def test_one_server_at_a_time_holds_the_catch_up_lock(disposable_database) -> None:  # type: ignore[no-untyped-def]
    dsn = disposable_database.psycopg_dsn
    first, second = PgEmbeddingIndexStore(dsn), PgEmbeddingIndexStore(dsn)
    held = first.hold_lock("embedding-catch-up")
    assert held is not None
    assert second.hold_lock("embedding-catch-up") is None
    other = second.hold_lock("something else")
    assert other is not None
    second.release_lock(other)
    first.release_lock(held)
    again = second.hold_lock("embedding-catch-up")
    assert again is not None
    second.release_lock(again)
