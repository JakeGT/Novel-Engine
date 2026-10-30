"""Mutation-set state machine, per-operation decisions, and the high-impact gate (2E)."""

import pytest

from backend.domain.identity import AccessScope, AuthMode
from backend.domain.journal import MutationNotFound, MutationStatus, RevisionConflict
from backend.domain.operations import CreateEntityOp, RetireEntityOp, UpdateEntityOp
from backend.domain.review import IllegalTransition
from backend.domain.validation import CanonValidationError
from backend.services.mutation_service import MutationService, ReviewRequiredError
from tests.doubles import InMemoryCanonJournal, InMemoryCanonRepository

pytestmark = pytest.mark.application

WS, PR = "ws1", "pr1"


def _scope(actor: str = "u1") -> AccessScope:
    return AccessScope(
        actor_user_id=actor,
        auth_mode=AuthMode.LOCAL,
        workspace_id=WS,
        project_id=PR,
        correlation_id="corr-1",
    )


def _service() -> tuple[MutationService, InMemoryCanonJournal, InMemoryCanonRepository]:
    journal = InMemoryCanonJournal()
    repository = InMemoryCanonRepository(journal=journal)
    return MutationService(journal, repository, local_mode=True), journal, repository


async def _draft(service, operations, *, origin="author", expected_head=0, key=""):
    return await service.create_draft(
        _scope(),
        workspace_id=WS,
        project_id=PR,
        operations=operations,
        expected_head=expected_head,
        origin=origin,
        idempotency_key=key,
    )


def _statuses(journal, mutation_id) -> list[tuple[str, str]]:
    return [
        (item.from_status.value, item.to_status.value)
        for item in journal.transitions
        if item.mutation_id == mutation_id
    ]


@pytest.mark.asyncio
async def test_author_direct_commit_records_transitions_with_actor_and_reason() -> None:
    service, journal, _ = _service()
    mutation = await _draft(service, [CreateEntityOp(id="tmp:a", type="Character", name="Mara")])

    await service.commit(_scope(), mutation.id)

    assert _statuses(journal, mutation.id) == [("draft", "approved"), ("approved", "committed")]
    for transition in journal.transitions:
        assert transition.actor_user_id == "u1"
        assert transition.reason
        assert transition.created_at is not None
    assert [d.note for d in journal.decisions] == ["author-direct"]
    assert journal.mutations[mutation.id].status is MutationStatus.COMMITTED


@pytest.mark.asyncio
async def test_committed_and_rejected_sets_are_terminal() -> None:
    service, journal, _ = _service()
    committed = await _draft(service, [CreateEntityOp(id="tmp:a", type="Character", name="A")])
    await service.commit(_scope(), committed.id)
    with pytest.raises(RevisionConflict):
        await service.approve(_scope(), committed.id)
    with pytest.raises(RevisionConflict):
        await service.revise_operation(
            _scope(), committed.id, 0, CreateEntityOp(id="tmp:a", type="Character", name="B")
        )
    with pytest.raises(IllegalTransition):
        await journal.transition_mutation(
            mutation_id=committed.id,
            to_status=MutationStatus.DRAFT,
            actor_user_id="u1",
            reason="reopen",
        )

    rejected = await _draft(
        service, [CreateEntityOp(id="tmp:b", type="Character", name="B")], expected_head=1
    )
    await service.reject(_scope(), rejected.id, note="not canon")
    with pytest.raises(CanonValidationError, match="review_rejected"):
        await service.commit(_scope(), rejected.id)
    with pytest.raises(RevisionConflict):
        await service.approve(_scope(), rejected.id)


@pytest.mark.asyncio
async def test_model_proposal_is_parked_then_committed_after_approval() -> None:
    service, journal, repository = _service()
    mutation = await _draft(
        service, [CreateEntityOp(id="tmp:a", type="Character", name="Mara")], origin="model"
    )

    with pytest.raises(ReviewRequiredError) as parked:
        await service.commit(_scope(), mutation.id)
    assert parked.value.reasons == ("protected_origin",)
    assert journal.mutations[mutation.id].status is MutationStatus.NEEDS_REVIEW
    assert repository.entities == {}

    await service.approve(_scope("reviewer"), mutation.id, note="looks right")
    result = await service.commit(_scope(), mutation.id)

    assert result.revision == 1
    assert _statuses(journal, mutation.id) == [
        ("draft", "needs_review"),
        ("needs_review", "approved"),
        ("approved", "committed"),
    ]
    assert journal.transitions[1].actor_user_id == "reviewer"
    assert journal.transitions[1].correlation_id == "corr-1"


@pytest.mark.asyncio
async def test_destructive_author_operation_needs_explicit_acknowledgement() -> None:
    service, journal, repository = _service()
    created = await _draft(service, [CreateEntityOp(id="ent_1", type="Character", name="Mara")])
    await service.commit(_scope(), created.id)

    retire = await _draft(service, [RetireEntityOp(id="ent_1")], expected_head=1)
    with pytest.raises(ReviewRequiredError) as parked:
        await service.commit_author_change(_scope(), retire.id)
    assert parked.value.reasons == ("destructive_operation",)
    assert repository.entities[(WS, PR, "ent_1")].lifecycle.value == "active"

    result = await service.commit_author_change(_scope(), retire.id, acknowledge_high_impact=True)
    assert result.revision == 2
    assert repository.entities[(WS, PR, "ent_1")].lifecycle.value == "retired"
    assert "acknowledged high-impact" in journal.decisions[-1].note


@pytest.mark.asyncio
async def test_protected_origin_cannot_be_self_acknowledged() -> None:
    service, _journal, repository = _service()
    mutation = await _draft(
        service, [CreateEntityOp(id="tmp:a", type="Character", name="Mara")], origin="import"
    )
    with pytest.raises(ReviewRequiredError):
        await service.commit_author_change(_scope(), mutation.id, acknowledge_high_impact=True)
    assert repository.entities == {}


@pytest.mark.asyncio
async def test_rejected_operation_is_excluded_from_the_commit() -> None:
    service, journal, repository = _service()
    mutation = await _draft(
        service,
        [
            CreateEntityOp(id="tmp:a", type="Character", name="Mara"),
            CreateEntityOp(id="tmp:b", type="Character", name="Unwanted"),
        ],
        origin="model",
    )
    await service.decide_operation(_scope(), mutation.id, 0, "accepted")
    await service.decide_operation(_scope(), mutation.id, 1, "rejected", note="duplicate")
    await service.approve(_scope(), mutation.id)
    await service.commit(_scope(), mutation.id)

    names = sorted(entity.name for entity in repository.entities.values())
    assert names == ["Mara"]
    assert [d.decision for d in journal.operation_decisions] == ["accepted", "rejected"]
    assert journal.operation_decisions[1].actor_user_id == "u1"


@pytest.mark.asyncio
async def test_rejecting_a_dependency_blocks_dependants_instead_of_committing_a_dangling_ref() -> (
    None
):
    from backend.domain.canon.propositions import EntityRefObject, Proposition
    from backend.domain.operations import AssertFactOp, UnresolvedReferenceError

    service, _journal, repository = _service()
    mutation = await _draft(
        service,
        [
            CreateEntityOp(id="tmp:a", type="Character", name="Mara"),
            CreateEntityOp(id="tmp:b", type="Location", name="Harbor"),
            AssertFactOp(
                id="tmp:f",
                proposition=Proposition(
                    subject_id="tmp:a",
                    predicate="LOCATED_IN",
                    object=EntityRefObject(kind="entity_ref", entity_id="tmp:b", project_id=PR),
                ),
            ),
        ],
        origin="model",
    )
    await service.decide_operation(_scope(), mutation.id, 1, "rejected")
    await service.approve(_scope(), mutation.id)
    with pytest.raises(UnresolvedReferenceError):
        await service.commit(_scope(), mutation.id)
    assert repository.entities == {}


@pytest.mark.asyncio
async def test_edit_creates_new_revision_and_invalidates_approval_and_decision() -> None:
    service, journal, repository = _service()
    mutation = await _draft(
        service, [CreateEntityOp(id="tmp:a", type="Character", name="Mra")], origin="model"
    )
    await service.decide_operation(_scope(), mutation.id, 0, "rejected", note="typo")
    with pytest.raises(CanonValidationError, match="empty_mutation"):
        await service.approve(_scope(), mutation.id)

    revised = await service.revise_operation(
        _scope("editor"),
        mutation.id,
        0,
        CreateEntityOp(id="tmp:a", type="Character", name="Mara"),
        reason="fix spelling",
    )
    assert revised.revision == 2
    # The original revision and the decision made against it are preserved as evidence.
    history = await journal.list_operation_revisions(mutation_id=mutation.id)
    assert [(item.revision, item.payload["name"]) for item in history] == [(1, "Mra"), (2, "Mara")]
    assert history[1].actor_user_id == "editor" and history[1].reason == "fix spelling"
    assert journal.operation_decisions[0].operation_revision == 1

    await service.approve(_scope(), mutation.id)
    # Editing after approval sends the set back to review.
    await service.revise_operation(
        _scope(), mutation.id, 0, CreateEntityOp(id="tmp:a", type="Character", name="Mara Venn")
    )
    assert journal.mutations[mutation.id].status is MutationStatus.NEEDS_REVIEW
    with pytest.raises(ReviewRequiredError):
        await service.commit(_scope(), mutation.id)

    await service.approve(_scope(), mutation.id)
    await service.commit(_scope(), mutation.id)
    assert [entity.name for entity in repository.entities.values()] == ["Mara Venn"]


@pytest.mark.asyncio
async def test_operation_decision_after_approval_requires_re_review() -> None:
    service, journal, _ = _service()
    mutation = await _draft(
        service,
        [
            CreateEntityOp(id="tmp:a", type="Character", name="A"),
            CreateEntityOp(id="tmp:b", type="Character", name="B"),
        ],
        origin="model",
    )
    await service.approve(_scope(), mutation.id)
    await service.decide_operation(_scope(), mutation.id, 1, "rejected")
    assert journal.mutations[mutation.id].status is MutationStatus.NEEDS_REVIEW


@pytest.mark.asyncio
async def test_direct_repository_write_cannot_skip_review_of_a_reviewed_set() -> None:
    """Substituting or re-adding a rejected operation at the repository is refused."""
    from datetime import UTC, datetime

    from backend.domain.journal import CommitEnvelope
    from backend.domain.operations import (
        canonicalize_operations,
        operations_checksum,
        parse_operation,
        resolve_temporary_ids,
    )
    from backend.services.mutation_service import _deterministic_id

    service, journal, repository = _service()
    mutation = await _draft(
        service,
        [
            CreateEntityOp(id="tmp:a", type="Character", name="A"),
            CreateEntityOp(id="tmp:b", type="Character", name="B"),
        ],
        origin="model",
    )
    await service.decide_operation(_scope(), mutation.id, 1, "rejected")
    await service.approve(_scope(), mutation.id)

    stored = await journal.list_operations(mutation_id=mutation.id)
    everything, _ = resolve_temporary_ids(
        [parse_operation(item.payload) for item in stored],
        new_id=lambda kind, tmp: _deterministic_id(mutation.id, kind, tmp),
    )
    envelope = CommitEnvelope(
        commit_id="cmt_bypass",
        mutation_id=mutation.id,
        workspace_id=WS,
        project_id=PR,
        branch_id=mutation.branch_id,
        revision=1,
        expected_head=0,
        canonical=canonicalize_operations(everything),
        checksum=operations_checksum(everything),
        created_at=datetime.now(UTC),
    )
    with pytest.raises(RevisionConflict, match="reviewed mutation"):
        await repository.apply_commit(envelope=envelope, operations=everything)
    assert repository.entities == {}


@pytest.mark.asyncio
async def test_a_stale_proposal_touching_nothing_that_changed_moves_and_keeps_its_approval() -> (
    None
):
    from backend.domain.journal import HeadConflict

    service, journal, _ = _service()
    stale = await _draft(
        service, [CreateEntityOp(id="tmp:a", type="Character", name="A")], origin="model"
    )
    await service.approve(_scope(), stale.id)
    other = await _draft(service, [CreateEntityOp(id="tmp:z", type="Character", name="Z")])
    await service.commit(_scope(), other.id)

    report = await service.validate(_scope(), stale.id)
    assert [issue.code for issue in report.issues] == ["stale_head"]
    with pytest.raises(HeadConflict):
        await service.commit(_scope(), stale.id)
    assert (await service.review(_scope(), stale.id)).changed_since == ()

    # Record-level rebase (ADR-020): nothing it touches changed, so the approval stands.
    await service.retarget(_scope(), stale.id, 1)
    assert journal.mutations[stale.id].status is MutationStatus.APPROVED
    assert (await service.commit(_scope(), stale.id)).revision == 2


@pytest.mark.asyncio
async def test_a_stale_proposal_touching_a_changed_record_must_be_reviewed_again() -> None:
    service, journal, _ = _service()
    created = await _draft(service, [CreateEntityOp(id="ent_1", type="Character", name="Mara")])
    await service.commit(_scope(), created.id)
    stale = await _draft(
        service, [UpdateEntityOp(id="ent_1", name="Mara Venn")], origin="model", expected_head=1
    )
    await service.approve(_scope(), stale.id)
    other = await _draft(service, [UpdateEntityOp(id="ent_1", name="Mara Ashe")], expected_head=1)
    await service.commit(_scope(), other.id)

    assert (await service.review(_scope(), stale.id)).changed_since == ("ent_1",)
    await service.retarget(_scope(), stale.id, 2)
    assert journal.mutations[stale.id].status is MutationStatus.NEEDS_REVIEW
    await service.approve(_scope(), stale.id)
    assert (await service.commit(_scope(), stale.id)).revision == 3


@pytest.mark.asyncio
async def test_review_aggregate_shows_before_state_and_classification() -> None:
    service, _journal, _ = _service()
    created = await _draft(service, [CreateEntityOp(id="ent_1", type="Character", name="Mara")])
    await service.commit(_scope(), created.id)
    mutation = await _draft(
        service,
        [UpdateEntityOp(id="ent_1", name="Mara Venn"), RetireEntityOp(id="ent_1")],
        expected_head=1,
    )

    review = await service.review(_scope(), mutation.id)

    assert review.targets_before[0]["name"] == "Mara"
    assert review.classification.requires_review
    assert review.head_revision == 1
    assert [op.revision for op in review.operations] == [1, 1]


@pytest.mark.asyncio
async def test_unknown_mutation_and_operation_are_not_found() -> None:
    service, _journal, _ = _service()
    with pytest.raises(MutationNotFound):
        await service.review(_scope(), "mut_missing")
    mutation = await _draft(service, [CreateEntityOp(id="tmp:a", type="Character", name="A")])
    with pytest.raises(MutationNotFound):
        await service.decide_operation(_scope(), mutation.id, 9, "accepted")


@pytest.mark.asyncio
async def test_proposals_cannot_claim_trusted_origins() -> None:
    from backend.domain.errors import ValidationError

    service, _journal, _ = _service()
    for origin in ("migration", "system", "derived"):
        with pytest.raises(ValidationError):
            await service.propose(
                _scope(),
                workspace_id=WS,
                project_id=PR,
                operations=[CreateEntityOp(id="tmp:a", type="Character", name="A")],
                expected_head=0,
                origin=origin,
            )
