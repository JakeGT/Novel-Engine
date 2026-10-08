import pytest

from backend.adapters.canon_writer import Neo4jCanonWriter
from backend.adapters.system import uuid7
from backend.domain.commit import ApplyOutcome
from backend.domain.journal import RevisionConflict
from backend.domain.operations import CreateEntityOp
from backend.graph.manager import GraphManager

pytestmark = pytest.mark.integration

_SCOPE = {"workspace_id": "ws_int_writer", "project_id": "pr_int_writer", "branch_id": "br_int"}


@pytest.mark.asyncio
async def test_canon_writer_applies_idempotently_and_rejects_checksum_drift(
    neo4j_settings, neo4j_driver
) -> None:
    manager = GraphManager(neo4j_settings)
    writer = Neo4jCanonWriter(manager)
    commit_id = f"cmt_{uuid7()}"
    operations = [CreateEntityOp(id="ent_int_1", type="Character", name="Mara")]
    try:
        first = await writer.apply_commit(
            commit_id=commit_id,
            revision=1,
            checksum="sum-1",
            operations=operations,
            schema_version_id="scv_int",
            **_SCOPE,
        )
        assert first == ApplyOutcome.APPLIED

        entity = await writer.get_entity(
            workspace_id=_SCOPE["workspace_id"],
            project_id=_SCOPE["project_id"],
            entity_id="ent_int_1",
        )
        assert entity is not None
        assert entity.name == "Mara"
        assert entity.schema_version_id == "scv_int"
        listed = await writer.list_entities(
            workspace_id=_SCOPE["workspace_id"], project_id=_SCOPE["project_id"]
        )
        assert [item.id for item in listed] == ["ent_int_1"]
        searched = await writer.search_entities(
            workspace_id=_SCOPE["workspace_id"], project_id=_SCOPE["project_id"], query="mar"
        )
        assert [item.id for item in searched] == ["ent_int_1"]

        marker = await writer.get_marker(commit_id)
        assert marker is not None
        assert marker.checksum == "sum-1"
        assert marker.revision == 1

        repeat = await writer.apply_commit(
            commit_id=commit_id, revision=1, checksum="sum-1", operations=operations, **_SCOPE
        )
        assert repeat == ApplyOutcome.ALREADY_APPLIED

        with pytest.raises(RevisionConflict):
            await writer.apply_commit(
                commit_id=commit_id,
                revision=1,
                checksum="different",
                operations=operations,
                **_SCOPE,
            )

        with neo4j_driver.session() as session:
            record = session.run(
                """
                MATCH (n:Entity {workspace_id: $workspace_id, project_id: $project_id, id: 'ent_int_1'})
                RETURN n.name AS name, n.lifecycle AS lifecycle
                """,
                **_SCOPE,
            ).single()
        assert record is not None
        assert record["name"] == "Mara"
        assert record["lifecycle"] == "active"
    finally:
        manager.close()
        with neo4j_driver.session() as session:
            session.run(
                "MATCH (n) WHERE n.workspace_id = $workspace_id DETACH DELETE n",
                workspace_id=_SCOPE["workspace_id"],
            ).consume()
            session.run(
                "MATCH (m:CommitMarker {commit_id: $commit_id}) DETACH DELETE m",
                commit_id=commit_id,
            ).consume()
