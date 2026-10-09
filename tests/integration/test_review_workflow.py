"""Review state machine, operation revisions/decisions, and gate against PostgreSQL (2E)."""

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.journal import PostgresCanonJournal
from backend.core.database import create_engine, create_session_factory, session_scope
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.journal import MutationStatus, RevisionConflict
from backend.domain.operations import CreateEntityOp, RetireEntityOp
from backend.domain.review import IllegalTransition
from backend.domain.validation import CanonValidationError
from backend.services.mutation_service import MutationService, ReviewRequiredError

pytestmark = pytest.mark.integration

WS, PR = "ws_review_it", "pr_review_it"


def _scope(actor: str = "usr_author") -> AccessScope:
    return AccessScope(
        actor_user_id=actor,
        auth_mode=AuthMode.LOCAL,
        workspace_id=WS,
        project_id=PR,
        correlation_id="corr-review-it",
    )


@pytest_asyncio.fixture
async def stack(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    journal = PostgresCanonJournal(factory)
    repository = PostgresCanonRepository(factory)
    try:
        yield MutationService(journal, repository, local_mode=True), journal, repository, factory
    finally:
        await engine.dispose()


async def _draft(service, operations, *, origin="author", expected_head=0):
    return await service.create_draft(
        _scope(),
        workspace_id=WS,
        project_id=PR,
        operations=operations,
        expected_head=expected_head,
        origin=origin,
    )


async def _transitions(journal, mutation_id):
    return [
        (item.from_status.value, item.to_status.value, item.actor_user_id, item.reason)
        for item in await journal.list_transitions(mutation_id=mutation_id)
    ]


@pytest.mark.asyncio
async def test_author_direct_commit_records_decision_and_transitions_atomically(stack) -> None:
    service, journal, _repository, _ = stack
    mutation = await _draft(service, [CreateEntityOp(id="tmp:a", type="Character", name="Mara")])

    result = await service.commit(_scope(), mutation.id)

    assert result.revision == 1
    stored = await journal.get_mutation(workspace_id=WS, project_id=PR, mutation_id=mutation.id)
    assert stored is not None and stored.status is MutationStatus.COMMITTED
    assert await _transitions(journal, mutation.id) == [
        ("draft", "approved", "usr_author", "author-direct"),
        ("approved", "committed", "usr_author", "committed as revision 1"),
    ]
    decisions = await journal.list_decisions(mutation_id=mutation.id)
    assert [(d.decision, d.note) for d in decisions] == [("approved", "author-direct")]
    # Retrying returns the original revision without new evidence rows.
    assert (await service.commit(_scope(), mutation.id)).revision == 1
    assert len(await journal.list_transitions(mutation_id=mutation.id)) == 2


@pytest.mark.asyncio
async def test_failed_commit_rolls_back_transitions_and_decisions(stack) -> None:
    service, journal, _repository, _ = stack
    mutation = await _draft(service, [CreateEntityOp(id="tmp:a", type="Alien", name="Zorp")])

    with pytest.raises(CanonValidationError, match="unknown_type"):
        await service.commit(_scope(), mutation.id)

    stored = await journal.get_mutation(workspace_id=WS, project_id=PR, mutation_id=mutation.id)
    assert stored is not None and stored.status is MutationStatus.DRAFT
    assert await journal.list_transitions(mutation_id=mutation.id) == []
    assert await journal.list_decisions(mutation_id=mutation.id) == []


@pytest.mark.asyncio
async def test_protected_origin_review_edit_and_partial_acceptance(stack) -> None:
    service, journal, repository, _ = stack
    mutation = await _draft(
        service,
        [
            CreateEntityOp(id="tmp:a", type="Character", name="Mra"),
            CreateEntityOp(id="tmp:b", type="Character", name="Unwanted"),
        ],
        origin="model",
    )
    with pytest.raises(ReviewRequiredError):
        await service.commit(_scope(), mutation.id)

    await service.revise_operation(
        _scope("usr_editor"),
        mutation.id,
        0,
        CreateEntityOp(id="tmp:a", type="Character", name="Mara"),
        reason="fix spelling",
    )
    await service.decide_operation(_scope("usr_reviewer"), mutation.id, 1, "rejected", "duplicate")
    await service.approve(_scope("usr_reviewer"), mutation.id, note="reviewed")
    result = await service.commit(_scope(), mutation.id)

    assert result.revision == 1
    entities = await repository.list_entities(workspace_id=WS, project_id=PR)
    assert [entity.name for entity in entities] == ["Mara"]
    revisions = await journal.list_operation_revisions(mutation_id=mutation.id)
    assert [(r.ordinal, r.revision, r.payload["name"]) for r in revisions] == [
        (0, 1, "Mra"),
        (0, 2, "Mara"),
        (1, 1, "Unwanted"),
    ]
    assert revisions[1].actor_user_id == "usr_editor"
    decisions = await journal.list_operation_decisions(mutation_id=mutation.id)
    assert [(d.ordinal, d.decision, d.operation_revision) for d in decisions] == [
        (1, "rejected", 1)
    ]
    assert [row[:2] for row in await _transitions(journal, mutation.id)] == [
        ("draft", "needs_review"),
        ("needs_review", "approved"),
        ("approved", "committed"),
    ]


@pytest.mark.asyncio
async def test_edit_after_approval_requires_re_review(stack) -> None:
    service, journal, _repository, _ = stack
    mutation = await _draft(
        service, [CreateEntityOp(id="tmp:a", type="Character", name="A")], origin="import"
    )
    await service.approve(_scope(), mutation.id)
    await service.revise_operation(
        _scope(), mutation.id, 0, CreateEntityOp(id="tmp:a", type="Character", name="B")
    )
    stored = await journal.get_mutation(workspace_id=WS, project_id=PR, mutation_id=mutation.id)
    assert stored is not None and stored.status is MutationStatus.NEEDS_REVIEW
    with pytest.raises(ReviewRequiredError):
        await service.commit(_scope(), mutation.id)


@pytest.mark.asyncio
async def test_destructive_author_change_needs_acknowledgement(stack) -> None:
    service, _journal, repository, _ = stack
    created = await _draft(service, [CreateEntityOp(id="ent_rw", type="Character", name="Mara")])
    await service.commit(_scope(), created.id)
    retire = await _draft(service, [RetireEntityOp(id="ent_rw")], expected_head=1)

    with pytest.raises(ReviewRequiredError):
        await service.commit_author_change(_scope(), retire.id)
    entity = await repository.get_entity(workspace_id=WS, project_id=PR, entity_id="ent_rw")
    assert entity is not None and entity.is_active

    await service.commit_author_change(_scope(), retire.id, acknowledge_high_impact=True)
    entity = await repository.get_entity(workspace_id=WS, project_id=PR, entity_id="ent_rw")
    assert entity is not None and not entity.is_active


@pytest.mark.asyncio
async def test_terminal_states_and_database_constraints(stack) -> None:
    service, journal, _repository, factory = stack
    mutation = await _draft(service, [CreateEntityOp(id="tmp:a", type="Character", name="A")])
    await service.reject(_scope(), mutation.id, note="no")

    with pytest.raises(IllegalTransition):
        await journal.transition_mutation(
            mutation_id=mutation.id,
            to_status=MutationStatus.APPROVED,
            actor_user_id="usr_author",
            reason="reopen",
        )
    with pytest.raises(RevisionConflict):
        await service.revise_operation(
            _scope(), mutation.id, 0, CreateEntityOp(id="tmp:a", type="Character", name="B")
        )
    with pytest.raises(CanonValidationError, match="review_rejected"):
        await service.commit(_scope(), mutation.id)

    # The database itself refuses an unknown status or a reasonless transition.
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with session_scope(factory) as session:
            await session.execute(
                text("UPDATE mutation_sets SET status = 'published' WHERE id = :id"),
                {"id": mutation.id},
            )
    with pytest.raises(IntegrityError):
        async with session_scope(factory) as session:
            await session.execute(
                text(
                    "INSERT INTO mutation_transitions (id, mutation_id, workspace_id, "
                    "project_id, from_status, to_status, reason) "
                    "VALUES ('opn_x', :id, :w, :p, 'draft', 'approved', '')"
                ),
                {"id": mutation.id, "w": WS, "p": PR},
            )


@pytest.mark.asyncio
async def test_list_mutations_filters_by_status_and_project(stack) -> None:
    service, journal, _repository, _ = stack
    first = await _draft(service, [CreateEntityOp(id="tmp:a", type="Character", name="A")])
    second = await _draft(
        service, [CreateEntityOp(id="tmp:b", type="Character", name="B")], origin="model"
    )
    await service.submit(_scope(), second.id)

    pending = await journal.list_mutations(workspace_id=WS, project_id=PR, status="needs_review")
    assert [m.id for m in pending] == [second.id]
    everything = await journal.list_mutations(workspace_id=WS, project_id=PR)
    assert {m.id for m in everything} == {first.id, second.id}
    assert await journal.list_mutations(workspace_id=WS, project_id="pr_other") == []
