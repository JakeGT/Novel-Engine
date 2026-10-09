import pytest

from backend.domain.identity import utcnow
from backend.domain.journal import CommitEnvelope
from backend.domain.operations import (
    CreateEntityOp,
    canonicalize_operations,
    operations_checksum,
)
from backend.domain.projection import ProjectionStatus
from backend.services.projection_service import GraphProjectionService
from tests.doubles import InMemoryCanonRepository, InMemoryCanonWriter

pytestmark = pytest.mark.unit

WS, PR, BR = "ws_proj", "pr_proj", "br_proj"


def _envelope(revision: int, expected_head: int, operations, commit_id: str) -> CommitEnvelope:
    canonical = canonicalize_operations(operations)
    return CommitEnvelope(
        commit_id=commit_id,
        mutation_id=f"mut_{commit_id}",
        workspace_id=WS,
        project_id=PR,
        branch_id=BR,
        revision=revision,
        expected_head=expected_head,
        canonical=canonical,
        checksum=operations_checksum(operations),
        actor_user_id="usr_author",
        created_at=utcnow(),
    )


async def _seed_commit(repo: InMemoryCanonRepository, *, commit_id: str, name: str):
    operations = [
        CreateEntityOp(op="create_entity", id=f"ent_{commit_id}", type="Character", name=name)
    ]
    envelope = _envelope(1, 0, operations, commit_id)
    await repo.apply_commit(envelope=envelope, operations=operations)
    return operations


@pytest.mark.asyncio
async def test_projection_advances_watermark_and_is_idempotent() -> None:
    repo = InMemoryCanonRepository()
    writer = InMemoryCanonWriter()
    service = GraphProjectionService(canon=repo, writer=writer)
    await _seed_commit(repo, commit_id="c1", name="Mara")

    lag = await service.lag(workspace_id=WS, project_id=PR, branch_id=BR)
    assert lag.head_revision == 1
    assert lag.applied_revision == 0
    assert lag.lag == 1
    assert lag.status is ProjectionStatus.LAGGING

    report = await service.project_pending(workspace_id=WS, project_id=PR, branch_id=BR)
    assert report.applied == 1
    assert report.revision == 1
    assert report.status is ProjectionStatus.CURRENT

    entity = await writer.get_entity(workspace_id=WS, project_id=PR, entity_id="ent_c1")
    assert entity is not None and entity.name == "Mara"

    assert (await service.lag(workspace_id=WS, project_id=PR, branch_id=BR)).current

    # Second pass applies nothing and must not duplicate.
    again = await service.project_pending(workspace_id=WS, project_id=PR, branch_id=BR)
    assert again.applied == 0
    assert again.revision == 1


@pytest.mark.asyncio
async def test_rebuild_discards_and_replays() -> None:
    repo = InMemoryCanonRepository()
    writer = InMemoryCanonWriter()
    service = GraphProjectionService(canon=repo, writer=writer)
    await _seed_commit(repo, commit_id="c1", name="Mara")

    await service.project_pending(workspace_id=WS, project_id=PR, branch_id=BR)
    assert (await service.lag(workspace_id=WS, project_id=PR, branch_id=BR)).current

    report = await service.rebuild(workspace_id=WS, project_id=PR, branch_id=BR)
    assert report.applied == 1
    assert report.status is ProjectionStatus.CURRENT
    entity = await writer.get_entity(workspace_id=WS, project_id=PR, entity_id="ent_c1")
    assert entity is not None and entity.name == "Mara"
    assert (await service.lag(workspace_id=WS, project_id=PR, branch_id=BR)).current


@pytest.mark.asyncio
async def test_projection_failure_is_visible_lag_not_corruption() -> None:
    repo = InMemoryCanonRepository()
    writer = InMemoryCanonWriter()
    writer.fail_after_ops = 0
    service = GraphProjectionService(canon=repo, writer=writer)
    await _seed_commit(repo, commit_id="c1", name="Mara")

    report = await service.project_pending(workspace_id=WS, project_id=PR, branch_id=BR)
    assert report.status is ProjectionStatus.ERROR
    assert report.applied == 0

    # Canon head is unchanged and the watermark records the error, so the lag is visible.
    lag = await service.lag(workspace_id=WS, project_id=PR, branch_id=BR)
    assert lag.head_revision == 1
    assert lag.applied_revision == 0
    assert lag.status is ProjectionStatus.ERROR

    watermark = await repo.get_watermark(
        projection="graph", workspace_id=WS, project_id=PR, branch_id=BR
    )
    assert watermark is not None
    assert watermark.status is ProjectionStatus.ERROR
    assert watermark.last_error is not None


@pytest.mark.asyncio
async def test_projection_retries_after_transient_failure() -> None:
    repo = InMemoryCanonRepository()
    writer = InMemoryCanonWriter()
    writer.fail_after_ops = 0
    service = GraphProjectionService(canon=repo, writer=writer)
    await _seed_commit(repo, commit_id="c1", name="Mara")

    first = await service.project_pending(workspace_id=WS, project_id=PR, branch_id=BR)
    assert first.status is ProjectionStatus.ERROR

    # Transient failure cleared: a retry resumes from the same watermark without
    # duplicating canon and catches up.
    writer.fail_after_ops = None
    second = await service.project_pending(workspace_id=WS, project_id=PR, branch_id=BR)
    assert second.status is ProjectionStatus.CURRENT
    assert second.applied == 1
    assert (await service.lag(workspace_id=WS, project_id=PR, branch_id=BR)).current


@pytest.mark.asyncio
async def test_watermark_from_an_older_projection_layout_forces_a_rebuild() -> None:
    """A stale-layout watermark is lag, never freshness, and is replayed from genesis."""
    from backend.domain.identity import AccessScope, AuthMode, utcnow
    from backend.domain.operations import CreateEntityOp
    from backend.domain.projection import ProjectionStatus, ProjectionWatermark
    from backend.services.mutation_service import MutationService
    from backend.services.projection_service import (
        PROJECTION_SCHEMA_VERSION,
        GraphProjectionService,
    )
    from tests.doubles import InMemoryCanonJournal, InMemoryCanonRepository, InMemoryCanonWriter

    journal = InMemoryCanonJournal()
    repo = InMemoryCanonRepository(journal=journal)
    writer = InMemoryCanonWriter()
    scope = AccessScope(
        actor_user_id="u", auth_mode=AuthMode.LOCAL, workspace_id="ws", project_id="pr"
    )
    mutation = MutationService(journal, repo, local_mode=True)
    draft = await mutation.create_draft(
        scope,
        workspace_id="ws",
        project_id="pr",
        operations=[CreateEntityOp(id="ent_1", type="Character", name="Mara")],
        expected_head=0,
    )
    await mutation.commit(scope, draft.id)
    branch = await journal.ensure_root_branch(workspace_id="ws", project_id="pr")
    target = {"workspace_id": "ws", "project_id": "pr", "branch_id": branch.id}

    # The previous layout believed it was current at revision 1 and left a node behind.
    await repo.set_watermark(
        ProjectionWatermark(
            projection="graph",
            revision=1,
            projection_schema_version="1",
            status=ProjectionStatus.CURRENT,
            updated_at=utcnow(),
            **target,
        )
    )
    writer.entities[("ws", "pr", "ent_stale")] = {"branch_id": branch.id, "name": "leftover"}
    service = GraphProjectionService(canon=repo, writer=writer)
    assert PROJECTION_SCHEMA_VERSION != "1"

    lag = await service.lag(**target)
    assert (lag.applied_revision, lag.lag, lag.current) == (0, 1, False)

    report = await service.project_pending(**target)
    assert report.applied == 1 and report.error is None
    assert ("ws", "pr", "ent_stale") not in writer.entities
    assert ("ws", "pr", "ent_1") in writer.entities
    watermark = await repo.get_watermark(projection="graph", **target)
    assert watermark.projection_schema_version == PROJECTION_SCHEMA_VERSION
    assert (await service.lag(**target)).current
