import json
from datetime import UTC, datetime

import pytest

from backend.domain.canon.propositions import Proposition, StringObject
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.journal import CommitEnvelope
from backend.domain.operations import (
    AssertBeliefOp,
    AssertFactOp,
    CloseFactOp,
    CreateEntityOp,
    CreateEventOp,
    RetireEventOp,
    UpdateEntityOp,
)
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


@pytest.mark.asyncio
async def test_fact_belief_at_revision_and_revision_diff() -> None:
    journal = InMemoryCanonJournal()
    repo = InMemoryCanonRepository(journal=journal)
    branch = await journal.ensure_root_branch(workspace_id="ws", project_id="pr")
    scope = AccessScope(actor_user_id="usr_1", auth_mode=AuthMode.LOCAL)
    service = CanonHistoryService(canon=repo, journal=journal, local_mode=True)

    await _apply(
        repo, branch, "cmt_1", 0, [CreateEntityOp(id="ent_1", type="Character", name="Mara")]
    )
    proposition = Proposition(
        subject_id="ent_1", predicate="HAS_NAME", object=StringObject(kind="string", value="Mara")
    )
    await _apply(repo, branch, "cmt_2", 1, [AssertFactOp(id="fact_1", proposition=proposition)])
    await _apply(
        repo,
        branch,
        "cmt_3",
        2,
        [
            AssertBeliefOp(
                id="bel_1",
                holder_entity_id="ent_1",
                proposition=proposition,
                source_ref="chronicle",
            )
        ],
    )
    await _apply(repo, branch, "cmt_4", 3, [CloseFactOp(id="fact_1")])

    assert (
        await service.fact_at_revision(scope, "ws", "pr", "fact_1", 2)
    ).status.value == "asserted"
    assert (await service.fact_at_revision(scope, "ws", "pr", "fact_1", 4)).status.value == "closed"

    belief = await service.belief_at_revision(scope, "ws", "pr", "bel_1", 3)
    assert belief.holder_entity_id == "ent_1"
    assert belief.source_ref == "chronicle"

    diff = await service.diff_revisions(scope, "ws", "pr", from_revision=2, to_revision=4)
    assert {(entry.revision, entry.record_type) for entry in diff} == {(3, "belief"), (4, "fact")}


@pytest.mark.asyncio
async def test_event_at_revision_includes_retirement() -> None:
    journal = InMemoryCanonJournal()
    repo = InMemoryCanonRepository(journal=journal)
    branch = await journal.ensure_root_branch(workspace_id="ws", project_id="pr")
    scope = AccessScope(actor_user_id="usr_1", auth_mode=AuthMode.LOCAL)
    service = CanonHistoryService(canon=repo, journal=journal, local_mode=True)

    await _apply(
        repo, branch, "cmt_1", 0, [CreateEntityOp(id="ent_1", type="Character", name="Mara")]
    )
    await _apply(
        repo,
        branch,
        "cmt_2",
        1,
        [
            CreateEventOp(
                id="evt_1",
                kind="battle",
                name="Fall of Harbor",
                participants=[{"entity_id": "ent_1", "role": "witness"}],
                location_ids=["ent_1"],
                world_position="winter, year 12",
            )
        ],
    )
    await _apply(repo, branch, "cmt_3", 2, [RetireEventOp(id="evt_1")])

    created = await service.event_at_revision(scope, "ws", "pr", "evt_1", 2)
    assert created.name == "Fall of Harbor"
    assert created.retired_revision is None
    assert created.participants[0].entity_id == "ent_1"

    retired = await service.event_at_revision(scope, "ws", "pr", "evt_1", 3)
    assert retired.retired_revision == 3
