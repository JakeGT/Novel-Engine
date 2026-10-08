from datetime import UTC, datetime

import pytest

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.canon_writer import Neo4jCanonWriter
from backend.adapters.journal import PostgresCanonJournal
from backend.core.database import create_engine, create_session_factory
from backend.domain.journal import CommitEnvelope
from backend.domain.operations import (
    CreateEntityOp,
    canonicalize_operations,
    operations_checksum,
)
from backend.graph.manager import GraphManager
from backend.services.projection_service import GraphProjectionService

pytestmark = pytest.mark.integration

WORKSPACE = "ws_proj_it"
PROJECT = "pr_proj_it"


@pytest.mark.asyncio
async def test_commit_then_project_replays_into_graph(
    disposable_database, neo4j_settings, neo4j_driver
) -> None:
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    journal = PostgresCanonJournal(factory)
    branch = await journal.ensure_root_branch(workspace_id=WORKSPACE, project_id=PROJECT)
    repository = PostgresCanonRepository(factory)
    manager = GraphManager(neo4j_settings)
    writer = Neo4jCanonWriter(manager)
    projection = GraphProjectionService(canon=repository, writer=writer)

    operations = [CreateEntityOp(id="ent_proj_it", type="Character", name="Mara")]
    envelope = CommitEnvelope(
        commit_id="cmt_proj_it_1",
        mutation_id="mut_proj_it",
        workspace_id=WORKSPACE,
        project_id=PROJECT,
        branch_id=branch.id,
        revision=1,
        expected_head=0,
        canonical=canonicalize_operations(operations),
        checksum=operations_checksum(operations),
        actor_user_id="usr_proj_it",
        created_at=datetime.now(UTC),
    )
    try:
        await repository.apply_commit(envelope=envelope, operations=operations)

        # Canon is authoritative immediately; the graph projection lags until run.
        assert (
            await writer.get_entity(
                workspace_id=WORKSPACE, project_id=PROJECT, entity_id="ent_proj_it"
            )
            is None
        )
        lag = await projection.lag(workspace_id=WORKSPACE, project_id=PROJECT, branch_id=branch.id)
        assert lag.head_revision == 1
        assert lag.applied_revision == 0
        assert lag.lag == 1

        report = await projection.project_pending(
            workspace_id=WORKSPACE, project_id=PROJECT, branch_id=branch.id
        )
        assert report.applied == 1

        entity = await writer.get_entity(
            workspace_id=WORKSPACE, project_id=PROJECT, entity_id="ent_proj_it"
        )
        assert entity is not None and entity.name == "Mara"
        assert (
            await projection.lag(workspace_id=WORKSPACE, project_id=PROJECT, branch_id=branch.id)
        ).current

        rebuilt = await projection.rebuild(
            workspace_id=WORKSPACE, project_id=PROJECT, branch_id=branch.id
        )
        assert rebuilt.applied == 1
        assert (
            await writer.get_entity(
                workspace_id=WORKSPACE, project_id=PROJECT, entity_id="ent_proj_it"
            )
            is not None
        )
    finally:
        await writer.discard(workspace_id=WORKSPACE, project_id=PROJECT, branch_id=branch.id)
        manager.close()
        await engine.dispose()
