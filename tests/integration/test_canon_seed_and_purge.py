"""Reference world, project purge, and media audit on real PostgreSQL + Neo4j (2D/2G/2I/2L)."""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.canon_tables import CANON_PROJECT_TABLES
from backend.adapters.canon_writer import Neo4jCanonWriter
from backend.adapters.journal import PostgresCanonJournal
from backend.adapters.media_store import PostgresMediaStore
from backend.adapters.passage_store import PostgresManifestStore
from backend.adapters.schema_store import PostgresSchemaVersionStore
from backend.adapters.source_store import PostgresSourceStore
from backend.adapters.studio_store import PostgresStudioStore
from backend.core.database import create_engine, create_session_factory, session_scope
from backend.graph.manager import GraphManager
from backend.seed.apply import SEED_PROJECT_ID, SEED_WORKSPACE_ID
from backend.seed.canon import (
    ALT_PROJECT_ID,
    CHART_ASSET,
    PRIMARY_HEAD,
    SEED_PROJECTS,
    apply_canon_seed,
)
from backend.seed.schema import apply_schema
from backend.seed.story import apply_story_seed
from backend.seed.tenancy import WS_ALT, apply_tenancy, clear_tenancy
from backend.services.canon_equivalence import CanonEquivalenceService
from backend.services.projection_service import GraphProjectionService
from tests.doubles import InMemoryObjectStore

pytestmark = pytest.mark.integration

PRIMARY = {"workspace_id": SEED_WORKSPACE_ID, "project_id": SEED_PROJECT_ID}
ALT = {"workspace_id": WS_ALT, "project_id": ALT_PROJECT_ID}


@pytest_asyncio.fixture
async def seeded(disposable_database):
    dsn = disposable_database.psycopg_dsn
    apply_tenancy(dsn)
    for project in SEED_PROJECTS:
        apply_schema(dsn, workspace_id=project.workspace_id, project_id=project.project_id)
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    stores = {
        "journal": PostgresCanonJournal(factory),
        "repository": PostgresCanonRepository(factory),
        "schemas": PostgresSchemaVersionStore(factory),
        "media": PostgresMediaStore(factory),
        "objects": InMemoryObjectStore(),
    }
    report = await apply_canon_seed(**stores)
    # The layers beside canon too, so purging and isolation cover them (QLT-001).
    story = await apply_story_seed(**_story_stores(stores, factory))
    assert story.ok, story.problems
    try:
        yield report, stores, factory, dsn
    finally:
        await engine.dispose()


def _story_stores(stores: dict, factory) -> dict:
    return {
        "journal": stores["journal"],
        "repository": stores["repository"],
        "schemas": stores["schemas"],
        "sources": PostgresSourceStore(factory),
        "studio": PostgresStudioStore(factory),
        "manifests": PostgresManifestStore(factory),
    }


async def _row_counts(factory, clause: str, params: dict) -> dict[str, int]:
    counts = {}
    async with session_scope(factory) as session:
        for table in CANON_PROJECT_TABLES:
            counts[table] = (
                await session.execute(text(f"SELECT count(*) FROM {table} WHERE {clause}"), params)
            ).scalar()
    return counts


@pytest.mark.asyncio
async def test_reference_world_seeds_idempotently_on_postgres(seeded) -> None:
    report, stores, _factory, _dsn = seeded
    assert report.ok, report.problems
    assert report.committed == PRIMARY_HEAD + 2

    again = await apply_canon_seed(**stores)
    assert again.ok and again.heads == report.heads
    layers = await apply_story_seed(**_story_stores(stores, _factory))
    assert layers.ok and layers.written == [], layers

    for project in SEED_PROJECTS:
        equivalence = await CanonEquivalenceService(
            canon=stores["repository"], journal=stores["journal"]
        ).verify(project.workspace_id, project.project_id)
        assert equivalence.ok, equivalence.problems
    events = await stores["media"].list_lifecycle_events(asset_id=CHART_ASSET, **PRIMARY)
    assert [event.action.value for event in events] == ["asset_imported", "version_imported"]


@pytest.mark.asyncio
async def test_seeded_world_projects_and_matches_neo4j(
    seeded, neo4j_settings, neo4j_driver
) -> None:
    _report, stores, _factory, _dsn = seeded
    writer = Neo4jCanonWriter(GraphManager(neo4j_settings))
    projector = GraphProjectionService(canon=stores["repository"], writer=writer)
    equivalence = CanonEquivalenceService(
        canon=stores["repository"], journal=stores["journal"], writer=writer
    )
    branches = {}
    try:
        for scope in (PRIMARY, ALT):
            await writer.purge(**scope)
            branch = await stores["journal"].ensure_root_branch(**scope)
            branches[scope["project_id"]] = branch.id
            report = await projector.project_pending(branch_id=branch.id, **scope)
            assert report.error is None
            result = await equivalence.verify(scope["workspace_id"], scope["project_id"])
            assert result.ok, result.problems
        edges = await writer.list_fact_edges(**PRIMARY)
        assert {(edge[2]) for edge in edges} == {"LOCATED_AT", "MEMBER_OF", "OWNS", "KNOWS"}
        # Five, and the compass kept at Saltkeep for a stretch of story time.
        assert len(edges) == 6
        # Identical ids in the other tenant project to their own nodes and edges only.
        assert await writer.list_fact_edges(**ALT) == [
            ("fct_seed_mara_at", "ent_seed_mara", "LOCATED_AT", "ent_seed_saltkeep")
        ]
    finally:
        for scope in (PRIMARY, ALT):
            await writer.purge(**scope)


@pytest.mark.asyncio
async def test_project_purge_erases_canon_but_not_other_projects(
    seeded, neo4j_settings, neo4j_driver
) -> None:
    _report, stores, factory, _dsn = seeded
    writer = Neo4jCanonWriter(GraphManager(neo4j_settings))
    projector = GraphProjectionService(canon=stores["repository"], writer=writer)
    try:
        for scope in (PRIMARY, ALT):
            await writer.purge(**scope)
            branch = await stores["journal"].ensure_root_branch(**scope)
            await projector.project_pending(branch_id=branch.id, **scope)

        before = await _row_counts(
            factory, "workspace_id = :w AND project_id = :p", {"w": WS_ALT, "p": ALT_PROJECT_ID}
        )
        removed = await stores["repository"].purge_project(**PRIMARY)
        nodes = await writer.purge(**PRIMARY)

        assert removed > 50 and nodes > 20
        after_primary = await _row_counts(
            factory,
            "workspace_id = :w AND project_id = :p",
            {"w": SEED_WORKSPACE_ID, "p": SEED_PROJECT_ID},
        )
        assert set(after_primary.values()) == {0}, after_primary
        # The other tenant's rows, with identical record ids, are untouched.
        assert (
            await _row_counts(
                factory, "workspace_id = :w AND project_id = :p", {"w": WS_ALT, "p": ALT_PROJECT_ID}
            )
            == before
        )
        assert await stores["repository"].get_entity(entity_id="ent_seed_mara", **PRIMARY) is None
        assert await stores["repository"].get_entity(entity_id="ent_seed_mara", **ALT) is not None
        assert await writer.get_entity(entity_id="ent_seed_mara", **PRIMARY) is None
        assert await writer.get_entity(entity_id="ent_seed_mara", **ALT) is not None
        with neo4j_driver.session() as session:
            leftover = session.run(
                "MATCH (n) WHERE n.workspace_id = $w AND n.project_id = $p RETURN count(n) AS c",
                w=SEED_WORKSPACE_ID,
                p=SEED_PROJECT_ID,
            ).single()["c"]
        assert leftover == 0
    finally:
        for scope in (PRIMARY, ALT):
            await writer.purge(**scope)


@pytest.mark.asyncio
async def test_media_lifecycle_audit_is_append_only(seeded) -> None:
    _report, stores, factory, _dsn = seeded
    media = stores["media"]
    asset = await media.create_asset(name="Portrait", actor_user_id="usr_seed_owner", **PRIMARY)
    version = await media.create_version(
        asset_id=asset.id,
        object_key="opaque-key",
        content_type="image/png",
        byte_length=3,
        checksum="abc",
        created_revision=PRIMARY_HEAD,
        actor_user_id="usr_seed_owner",
        **PRIMARY,
    )
    await media.retire_version(
        version_id=version.id,
        retired_revision=PRIMARY_HEAD,
        actor_user_id="usr_seed_editor",
        **PRIMARY,
    )
    await media.retire_asset(
        asset_id=asset.id, retired_revision=PRIMARY_HEAD, actor_user_id="usr_seed_editor", **PRIMARY
    )
    # Repeating a retirement changes nothing and records nothing.
    await media.retire_asset(asset_id=asset.id, retired_revision=99, actor_user_id="x", **PRIMARY)
    await media.retire_version(
        version_id=version.id, retired_revision=99, actor_user_id="x", **PRIMARY
    )

    events = await media.list_lifecycle_events(asset_id=asset.id, **PRIMARY)
    assert [(e.action.value, e.actor_user_id, e.canon_revision) for e in events] == [
        ("asset_registered", "usr_seed_owner", None),
        ("version_registered", "usr_seed_owner", PRIMARY_HEAD),
        ("version_retired", "usr_seed_editor", PRIMARY_HEAD),
        ("asset_retired", "usr_seed_editor", PRIMARY_HEAD),
    ]
    stored = await media.get_asset(asset_id=asset.id, **PRIMARY)
    assert stored.retired_revision == PRIMARY_HEAD
    assert await media.list_lifecycle_events(asset_id=asset.id, **ALT) == []

    with pytest.raises(DBAPIError, match="append-only"):
        async with session_scope(factory) as session:
            await session.execute(
                text("UPDATE media_lifecycle_events SET actor_user_id = 'forged'")
            )


@pytest.mark.asyncio
async def test_dev_reset_clears_every_canon_table(seeded) -> None:
    _report, _stores, factory, dsn = seeded
    assert clear_tenancy(dsn) is True
    counts = await _row_counts(factory, "true", {})
    assert set(counts.values()) == {0}, counts


@pytest.mark.asyncio
async def test_canon_projection_and_legacy_graph_do_not_see_each_other(
    seeded, neo4j_settings, neo4j_driver
) -> None:
    """Both stores share one Neo4j database and one project scope."""
    from backend.adapters.graph import Neo4jGraphRepository
    from backend.graph.schema import Node

    _report, stores, _factory, _dsn = seeded
    manager = GraphManager(neo4j_settings)
    writer = Neo4jCanonWriter(manager)
    legacy = Neo4jGraphRepository(manager)
    projector = GraphProjectionService(canon=stores["repository"], writer=writer)
    equivalence = CanonEquivalenceService(
        canon=stores["repository"], journal=stores["journal"], writer=writer
    )
    try:
        await writer.purge(**PRIMARY)
        # Legacy nodes typed like canon kinds, one reusing a canon entity id.
        for node_id, node_type, properties in (
            ("legacy_belief", "Belief", {"proposition": "not a canon proposition"}),
            ("legacy_event", "Event", {"name": "Legacy event"}),
            ("ent_seed_mara", "Character", {"name": "Legacy Mara"}),
        ):
            legacy.add_node(Node(id=node_id, type=node_type, properties=properties, **PRIMARY))
        branch = await stores["journal"].ensure_root_branch(**PRIMARY)
        report = await projector.project_pending(branch_id=branch.id, **PRIMARY)
        assert report.error is None

        # Canon reads and the equivalence proof ignore every legacy node.
        result = await equivalence.verify(SEED_WORKSPACE_ID, SEED_PROJECT_ID)
        assert result.ok, result.problems
        assert {b.id for b in await writer.list_beliefs(limit=100, **PRIMARY)} == {
            "blf_seed_mara_haunted",
            "blf_seed_iven_pop",
            "blf_seed_iven_compass",
        }
        assert (await writer.get_entity(entity_id="ent_seed_mara", **PRIMARY)).name == "Mara Venn"

        # Legacy reads see only legacy nodes, including the colliding id.
        characters = legacy.get_nodes(node_type="Character", **PRIMARY)
        assert [node.id for node in characters] == ["ent_seed_mara"]
        assert legacy.get_node("ent_seed_mara", **PRIMARY).properties["name"] == "Legacy Mara"
        assert legacy.get_node("ent_seed_iven", **PRIMARY) is None

        # Rebuilding or purging the projection never deletes legacy nodes.
        await projector.rebuild(branch_id=branch.id, **PRIMARY)
        await writer.purge(**PRIMARY)
        assert legacy.get_node("legacy_belief", **PRIMARY) is not None
        assert legacy.get_node("ent_seed_mara", **PRIMARY).properties["name"] == "Legacy Mara"
    finally:
        await writer.purge(**PRIMARY)
        legacy.delete_project_nodes(SEED_WORKSPACE_ID, SEED_PROJECT_ID)
        manager.close()
