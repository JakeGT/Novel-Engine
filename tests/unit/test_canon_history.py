import json
from datetime import UTC, datetime

import pytest

from backend.domain.identity import AccessScope, AuthMode
from backend.domain.journal import CommitEnvelope
from backend.domain.operations import CreateEntityOp, UpdateEntityOp
from backend.services.canon_history_service import CanonHistoryService
from tests.doubles import InMemoryCanonJournal, InMemoryCanonRepository

pytestmark = pytest.mark.unit


async def _apply(repo, branch, commit_id, expected, ops, checksum="c"):
    envelope = CommitEnvelope(
        commit_id=commit_id,
        mutation_id="mut_1",
        workspace_id="ws",
        project_id="pr",
        branch_id=branch.id,
        revision=expected + 1,
        expected_head=expected,
        schema_version_id="scv_1",
        canonical=json.dumps([op.model_dump(mode="json") for op in ops]),
        checksum=checksum,
        actor_user_id="usr_1",
        created_at=datetime.now(UTC),
    )
    return await repo.apply_commit(envelope=envelope, operations=ops)


@pytest.mark.asyncio
async def test_revision_history_state_at_revision_and_tags() -> None:
    journal = InMemoryCanonJournal()
    repo = InMemoryCanonRepository(journal=journal)
    branch = await journal.ensure_root_branch(workspace_id="ws", project_id="pr")
    scope = AccessScope(actor_user_id="usr_1", auth_mode=AuthMode.LOCAL)
    service = CanonHistoryService(canon=repo, journal=journal, local_mode=True)

    await _apply(
        repo, branch, "cmt_1", 0, [CreateEntityOp(id="ent_1", type="Character", name="Mara")]
    )
    await _apply(repo, branch, "cmt_2", 1, [UpdateEntityOp(id="ent_1", name="Mara Voss")])

    revisions = await service.list_revisions(scope, "ws", "pr")
    assert revisions[0].revision == 2
    assert revisions[0].operation_count == 1
    assert revisions[0].commit_id == "cmt_2"

    history = await service.record_history(
        scope, "ws", "pr", record_type="entity", record_id="ent_1"
    )
    assert [entry.operation_type for entry in history] == ["create_entity", "update_entity"]

    at_one = await service.entity_at_revision(scope, "ws", "pr", "ent_1", 1)
    assert at_one.name == "Mara"
    at_two = await service.entity_at_revision(scope, "ws", "pr", "ent_1", 2)
    assert at_two.name == "Mara Voss"

    tag = await service.set_tag(scope, "ws", "pr", name="v1", revision=1)
    assert tag.revision == 1 and tag.commit_id == "cmt_1"
    assert (await service.list_tags(scope, "ws", "pr"))[0].name == "v1"

    await service.set_tag(scope, "ws", "pr", name="v1", revision=2)
    tag_hist = await service.tag_history(scope, "ws", "pr", "v1")
    assert tag_hist[0].revision == 1

    assert await service.delete_tag(scope, "ws", "pr", "v1") is True
    assert await service.list_tags(scope, "ws", "pr") == []
