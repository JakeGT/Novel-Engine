"""Export from one environment and restore into a completely clean one (2K).

"Clean" means a separate PostgreSQL database, an empty object store, and a
Neo4j scope with no nodes: nothing but the archive crosses between them.
"""

import uuid

import psycopg
import pytest
from alembic import command

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.canon_writer import Neo4jCanonWriter
from backend.adapters.journal import PostgresCanonJournal
from backend.adapters.media_store import PostgresMediaStore
from backend.adapters.schema_store import PostgresSchemaVersionStore
from backend.core.database import create_engine, create_session_factory
from backend.domain.archive import ImportStatus
from backend.domain.canon.assertions import truth_agreement
from backend.graph.manager import GraphManager
from backend.migrate import alembic_config
from backend.seed.apply import SEED_PROJECT_ID, SEED_WORKSPACE_ID
from backend.seed.canon import (
    CHART_BYTES,
    CHART_LINK,
    EXPECTED_TRUTH_AGREEMENT,
    PRIMARY_HEAD,
    SEED_PROJECTS,
    apply_canon_seed,
)
from backend.seed.schema import apply_schema
from backend.seed.tenancy import apply_tenancy
from backend.services.canon_archive import CanonArchiveService
from backend.services.canon_equivalence import CanonEquivalenceService
from backend.services.projection_service import GraphProjectionService
from tests.doubles import InMemoryObjectStore
from tests.integration.conftest import DisposableDatabase, _psycopg_dsn, _sqlalchemy_dsn

pytestmark = pytest.mark.integration


class _Environment:
    def __init__(self, database: DisposableDatabase, manager: GraphManager) -> None:
        self.engine = create_engine(database.settings())
        factory = create_session_factory(self.engine)
        self.journal = PostgresCanonJournal(factory)
        self.repo = PostgresCanonRepository(factory)
        self.schemas = PostgresSchemaVersionStore(factory)
        self.media = PostgresMediaStore(factory)
        self.objects = InMemoryObjectStore()
        self.writer = Neo4jCanonWriter(manager)
        self.projector = GraphProjectionService(canon=self.repo, writer=self.writer)
        self.equivalence = CanonEquivalenceService(
            canon=self.repo, journal=self.journal, writer=self.writer
        )
        self.archive = CanonArchiveService(
            canon=self.repo,
            journal=self.journal,
            schemas=self.schemas,
            media=self.media,
            objects=self.objects,
            projector=self.projector,
            equivalence=self.equivalence,
            canon_writer=self.writer,
            local_mode=True,
        )


@pytest.fixture
def second_database():
    name = f"novel_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(_psycopg_dsn("postgres"), autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}"')
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", _sqlalchemy_dsn(name))
    command.upgrade(config, "head")
    try:
        yield DisposableDatabase(
            name=name, psycopg_dsn=_psycopg_dsn(name), sqlalchemy_dsn=_sqlalchemy_dsn(name)
        )
    finally:
        with psycopg.connect(_psycopg_dsn("postgres"), autocommit=True) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.mark.asyncio
async def test_project_round_trips_into_a_clean_environment(
    disposable_database, second_database, neo4j_settings, neo4j_driver
) -> None:
    manager = GraphManager(neo4j_settings)
    source = _Environment(disposable_database, manager)
    target = _Environment(second_database, manager)
    suffix = uuid.uuid4().hex[:8]
    target_ws, target_pr = f"ws_restored_{suffix}", f"prj_restored_{suffix}"
    source_scope = {"workspace_id": SEED_WORKSPACE_ID, "project_id": SEED_PROJECT_ID}
    target_scope = {"workspace_id": target_ws, "project_id": target_pr}
    try:
        # --- source environment: the full reference world ---------------------
        apply_tenancy(disposable_database.psycopg_dsn)
        for project in SEED_PROJECTS:
            apply_schema(
                disposable_database.psycopg_dsn,
                workspace_id=project.workspace_id,
                project_id=project.project_id,
            )
        seeded = await apply_canon_seed(
            journal=source.journal,
            repository=source.repo,
            schemas=source.schemas,
            media=source.media,
            objects=source.objects,
        )
        assert seeded.ok, seeded.problems
        await source.writer.purge(**source_scope)
        source_branch = await source.journal.ensure_root_branch(**source_scope)
        await source.projector.project_pending(branch_id=source_branch.id, **source_scope)
        source_report = await source.equivalence.verify(SEED_WORKSPACE_ID, SEED_PROJECT_ID)
        assert source_report.ok, source_report.problems
        archive = await source.archive.export_project(SEED_WORKSPACE_ID, SEED_PROJECT_ID)

        # --- target environment: nothing but migrations ------------------------
        assert await target.repo.list_entities(**target_scope) == []
        assert target.objects.list_objects(**target_scope) == []

        result = await target.archive.import_project(target_ws, target_pr, archive)

        assert result["commits"] == PRIMARY_HEAD
        assert result["projection_rebuilt"] is True and result["verified"] is True
        [record] = await target.repo.list_imports(**target_scope)
        assert record.status is ImportStatus.COMPLETED and record.commits_applied == PRIMARY_HEAD

        # Same accepted history: every same-scope envelope checksum is preserved and
        # only the two commits holding project-scoped entity references differ.
        source_commits = await source.repo.list_commits(branch_id=source_branch.id, **source_scope)
        target_branch = await target.journal.ensure_root_branch(**target_scope)
        target_commits = await target.repo.list_commits(branch_id=target_branch.id, **target_scope)
        assert [c.revision for c in target_commits] == list(range(1, PRIMARY_HEAD + 1))
        same = [
            s.envelope_checksum == t.envelope_checksum
            for s, t in zip(source_commits, target_commits, strict=True)
        ]
        assert same == [True, False, False, True, True, True, True]

        # Same canonical state, independent of ids' project scope.
        target_report = await target.equivalence.verify(target_ws, target_pr)
        assert target_report.ok, target_report.problems
        source_entities = {
            e.id: (e.name, e.type, e.lifecycle, e.aliases, e.external_ids, e.attributes)
            for e in await source.repo.list_entities(
                include_retired=True, limit=500, **source_scope
            )
        }
        target_entities = {
            e.id: (e.name, e.type, e.lifecycle, e.aliases, e.external_ids, e.attributes)
            for e in await target.repo.list_entities(
                include_retired=True, limit=500, **target_scope
            )
        }
        assert target_entities == source_entities and len(target_entities) == 9

        # False/unknown/true beliefs survive as derived results, never stored flags.
        facts = await target.repo.list_facts(limit=500, **target_scope)
        for belief_id, expected in EXPECTED_TRUTH_AGREEMENT.items():
            belief = await target.repo.get_belief(belief_id=belief_id, **target_scope)
            assert truth_agreement(belief, facts) is expected

        # Retired record, close/reassert history, review evidence, and media.
        closed = await target.repo.list_record_versions(
            record_type="fact", record_id="fct_seed_compass_color", **target_scope
        )
        assert sorted(v.operation_type for v in closed) == ["assert_fact", "close_fact"]
        retire_commit = await target.repo.get_envelope(target_commits[5].commit_id)
        decisions = await target.journal.list_decisions(mutation_id=retire_commit.mutation_id)
        assert decisions and decisions[-1].decision == "approved"
        link = await target.repo.get_link(link_id=CHART_LINK, **target_scope)
        assert link is not None and link.role.value == "MAP_OF"
        version = await target.media.get_version(version_id=link.asset_version_id, **target_scope)
        assert target.objects.open(version.object_key, **target_scope) == CHART_BYTES
        assert version.licence == "CC0-1.0" and version.attribution

        # The rebuilt projection answers the same relationship queries.
        source_edges = await source.writer.list_fact_edges(**source_scope)
        target_edges = await target.writer.list_fact_edges(**target_scope)
        assert target_edges == source_edges and len(target_edges) == 5

        # Nothing from the source tenant's other project came along.
        assert archive["project"]["project_id"] == SEED_PROJECT_ID
        assert (
            await target.repo.list_entities(
                workspace_id="ws_seed_harbor_alt", project_id="prj_seed_alt"
            )
            == []
        )
    finally:
        await source.writer.purge(**source_scope)
        await target.writer.purge(**target_scope)
        await source.engine.dispose()
        await target.engine.dispose()
        manager.close()


@pytest.mark.asyncio
async def test_failed_import_is_removed_exactly_on_postgres(
    disposable_database, second_database, neo4j_settings, neo4j_driver
) -> None:
    from backend.domain.archive import ArchiveImportError
    from backend.domain.schema_defs import SchemaDefinition
    from backend.services.schema_compiler import compute_content_hash

    manager = GraphManager(neo4j_settings)
    source = _Environment(disposable_database, manager)
    target = _Environment(second_database, manager)
    suffix = uuid.uuid4().hex[:8]
    ws, pr = f"ws_partial_{suffix}", f"prj_partial_{suffix}"
    scope = {"workspace_id": ws, "project_id": pr}
    try:
        apply_tenancy(disposable_database.psycopg_dsn)
        for project in SEED_PROJECTS:
            apply_schema(
                disposable_database.psycopg_dsn,
                workspace_id=project.workspace_id,
                project_id=project.project_id,
            )
        await apply_canon_seed(
            journal=source.journal,
            repository=source.repo,
            schemas=source.schemas,
            media=source.media,
            objects=source.objects,
        )
        archive = await source.archive.export_project(SEED_WORKSPACE_ID, SEED_PROJECT_ID)

        # The target already has its own published schema and a registered asset.
        definition = SchemaDefinition(
            label="own", entity_extensions=[{"entity_type": "Ship", "fields": []}]
        ).canonical()
        own = await target.schemas.create_draft(
            definition=definition,
            content_hash=compute_content_hash(definition),
            compatibility="additive",
            created_by_user_id=None,
            **scope,
        )
        await target.schemas.publish(version_id=own.id, **scope)
        own_asset = await target.media.create_asset(name="Own", **scope)

        real_apply = target.repo.apply_commit
        calls = 0

        async def crash(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise RuntimeError("simulated crash")
            return await real_apply(**kwargs)

        target.repo.apply_commit = crash
        with pytest.raises(RuntimeError):
            await target.archive.import_project(ws, pr, archive)
        target.repo.apply_commit = real_apply

        [failed] = await target.repo.list_imports(**scope)
        assert failed.status is ImportStatus.FAILED
        assert (failed.commits_applied, failed.error) == (3, "RuntimeError")
        assert len(await target.repo.list_entities(include_retired=True, limit=100, **scope)) == 9
        assert len(await target.schemas.list(**scope)) == 2

        removed = await target.archive.remove_import(ws, pr, failed.id)

        assert removed.status is ImportStatus.REMOVED
        assert await target.repo.list_entities(include_retired=True, limit=100, **scope) == []
        branch = await target.journal.ensure_root_branch(**scope)
        assert await target.repo.get_head(branch_id=branch.id, **scope) == 0
        assert [version.id for version in await target.schemas.list(**scope)] == [own.id]
        assert [asset.id for asset in await target.media.list_assets(**scope)] == [own_asset.id]
        assert target.objects.list_objects(**scope) == []
        with pytest.raises(ArchiveImportError):
            await target.archive.remove_import(ws, pr, failed.id)

        # The project is clean enough to restore into again, successfully this time.
        result = await target.archive.import_project(ws, pr, archive)
        assert result["verified"] is True and result["commits"] == PRIMARY_HEAD
    finally:
        await target.writer.purge(**scope)
        await source.engine.dispose()
        await target.engine.dispose()
        manager.close()
