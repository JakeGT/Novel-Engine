"""Merging inside the PostgreSQL commit, undoing it, and the kept threads (migration 0054)."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.conversation_store import PostgresConversationStore
from backend.adapters.journal import PostgresCanonJournal
from backend.core.database import create_engine, create_session_factory
from backend.domain.canon.events import EventParticipant
from backend.domain.canon.plans import PlanKind, PlanRef
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.canon.registry import CORE_PREDICATES
from backend.domain.conversations import RunKind, ThreadKind
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.journal import MutationStatus
from backend.domain.merge import plan_merge, read_records
from backend.domain.operations import (
    AssertFactOp,
    CreateEntityOp,
    CreateEventOp,
    CreatePlanItemOp,
)
from backend.services.canon_history_service import CanonHistoryService
from backend.services.conversation_service import (
    ConversationService,
    assistant_turn,
    author_turn,
)
from backend.services.mutation_service import MutationService, story_reader

pytestmark = pytest.mark.integration

WS, PR = "ws_merge", "pr_merge"


def _scope(actor: str = "usr_author") -> AccessScope:
    return AccessScope(
        actor_user_id=actor, auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR
    )


@pytest_asyncio.fixture
async def stack(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    journal = PostgresCanonJournal(factory)
    repository = PostgresCanonRepository(factory)
    mutation = MutationService(journal, repository, local_mode=True)
    history = CanonHistoryService(
        canon=repository, journal=journal, local_mode=True, mutation=mutation
    )
    store = PostgresConversationStore(factory)
    conversations = ConversationService(store, journal=journal, local_mode=True)
    try:
        yield mutation, repository, journal, history, conversations, store
    finally:
        await engine.dispose()


async def _commit(mutation, repository, journal, *operations) -> int:
    branch = await journal.working_branch(workspace_id=WS, project_id=PR)
    head = await repository.get_head(workspace_id=WS, project_id=PR, branch_id=branch.id)
    draft = await mutation.create_draft(
        _scope(), workspace_id=WS, project_id=PR, operations=list(operations), expected_head=head
    )
    result = await mutation.commit_author_change(_scope(), draft.id, acknowledge_high_impact=True)
    return int(result.revision)


def _knows(fact_id: str, subject: str, target: str) -> AssertFactOp:
    return AssertFactOp(
        id=fact_id,
        proposition=Proposition(
            subject_id=subject,
            predicate="KNOWS",
            object=EntityRefObject(entity_id=target, project_id=PR),
        ),
    )


@pytest.mark.asyncio
async def test_a_merge_is_worked_out_in_the_commit_and_undone_in_one_step(stack) -> None:
    mutation, repository, journal, history, _conversations, _store = stack
    await _commit(
        mutation,
        repository,
        journal,
        CreateEntityOp(id="ent_mara", type="Character", name="Mara"),
        CreateEntityOp(id="ent_twin", type="Character", name="Mara V.", aliases=["The Fox"]),
        CreateEntityOp(id="ent_tobin", type="Character", name="Tobin"),
        CreateEntityOp(id="ent_quay", type="Location", name="The quay"),
    )
    await _commit(
        mutation,
        repository,
        journal,
        _knows("fct_tobin_twin", "ent_tobin", "ent_twin"),
        CreateEventOp(
            id="evt_meet",
            kind="meeting",
            name="The meeting",
            participants=[EventParticipant(entity_id="ent_twin", role="participant")],
            location_ids=["ent_quay"],
        ),
        CreateEventOp(id="evt_fire", kind="fire", name="The fire", cause_ids=["ent_twin"]),
        CreatePlanItemOp(
            id="pln_scene",
            kind=PlanKind.SCENE,
            title="On the quay",
            refs=[PlanRef(entity_id="ent_twin", role="point_of_view")],
        ),
    )
    records = await read_records(story_reader(repository, WS, PR), "ent_mara", "ent_twin")
    assert records is not None
    merged_at = await _commit(mutation, repository, journal, plan_merge(records, CORE_PREDICATES))

    twin = await repository.get_entity(workspace_id=WS, project_id=PR, entity_id="ent_twin")
    assert twin.lifecycle.value == "retired"
    mara = await repository.get_entity(workspace_id=WS, project_id=PR, entity_id="ent_mara")
    assert mara.aliases == ["Mara V.", "The Fox"]
    event = await repository.get_event(workspace_id=WS, project_id=PR, event_id="evt_meet")
    assert [p.entity_id for p in event.participants] == ["ent_mara"]
    fire = await repository.get_event(workspace_id=WS, project_id=PR, event_id="evt_fire")
    assert fire.cause_ids == ["ent_mara"]
    scene = await repository.get_plan_item(workspace_id=WS, project_id=PR, plan_id="pln_scene")
    assert [ref.entity_id for ref in scene.refs] == ["ent_mara"]
    knows = await repository.list_facts(workspace_id=WS, project_id=PR, subject_id="ent_tobin")
    assert [fact.proposition.object.entity_id for fact in knows] == ["ent_mara"]
    # The change log holds only changes every reader already understands.
    versions = await repository.list_record_versions(
        workspace_id=WS, project_id=PR, min_revision=merged_at - 1, max_revision=merged_at
    )
    assert "merge_entities" not in {version.operation_type for version in versions}
    retired = next(v for v in versions if v.operation_type == "retire_entity")
    assert retired.change["merged_into"] == "ent_mara"

    await history.commit_revert(
        _scope(),
        WS,
        PR,
        from_revision=merged_at - 1,
        expected_head=merged_at,
        acknowledge_high_impact=True,
    )
    twin = await repository.get_entity(workspace_id=WS, project_id=PR, entity_id="ent_twin")
    assert twin.is_active and twin.retired_revision is None
    event = await repository.get_event(workspace_id=WS, project_id=PR, event_id="evt_meet")
    assert [p.entity_id for p in event.participants] == ["ent_twin"]
    fire = await repository.get_event(workspace_id=WS, project_id=PR, event_id="evt_fire")
    assert fire.cause_ids == ["ent_twin"]
    knows = await repository.list_facts(workspace_id=WS, project_id=PR, subject_id="ent_tobin")
    assert [fact.proposition.object.entity_id for fact in knows] == ["ent_twin"]
    mara = await repository.get_entity(workspace_id=WS, project_id=PR, entity_id="ent_mara")
    assert mara.aliases == []


@pytest.mark.asyncio
async def test_threads_are_kept_per_author_and_cleared_and_runs_settle(stack) -> None:
    mutation, repository, journal, _history, conversations, store = stack
    await conversations.record(
        _scope(),
        WS,
        PR,
        ThreadKind.CHANGE,
        [author_turn("Mara is Tobin's sister."), assistant_turn("Siblings.", questions=["Sure?"])],
    )
    await conversations.record(
        _scope(), WS, PR, ThreadKind.CHANGE, [author_turn("Yes."), assistant_turn("Done.")]
    )
    thread = await conversations.thread(_scope(), WS, PR, ThreadKind.CHANGE)
    assert [turn.body for turn in thread.turns] == [
        "Mara is Tobin's sister.",
        "Siblings.",
        "Yes.",
        "Done.",
    ]
    assert thread.turns[1].questions == ["Sure?"]
    await conversations.count_tokens(thread.id, WS, PR, 120)
    assert (await conversations.thread(_scope(), WS, PR, ThreadKind.CHANGE)).tokens_used == 120
    # Another author has a thread of their own, never this one.
    assert await conversations.thread(_scope("usr_other"), WS, PR, ThreadKind.CHANGE) is None
    assert await conversations.clear(_scope(), WS, PR, ThreadKind.CHANGE) is True
    assert await conversations.thread(_scope(), WS, PR, ThreadKind.CHANGE) is None

    branch = await journal.working_branch(workspace_id=WS, project_id=PR)
    draft = await mutation.create_draft(
        _scope(),
        workspace_id=WS,
        project_id=PR,
        operations=[CreateEntityOp(id="tmp:a", type="Character", name="Ada")],
        expected_head=await repository.get_head(
            workspace_id=WS, project_id=PR, branch_id=branch.id
        ),
        origin="model",
    )
    await mutation.submit(_scope(), draft.id, "test")
    await conversations.record_run(
        _scope(), WS, PR, kind=RunKind.LINKS, suggested=1, mutation_id=draft.id
    )
    (run,) = await conversations.runs(_scope(), WS, PR, kind=RunKind.LINKS)
    assert run.kept is None and run.settled_at is None
    await mutation.approve(_scope(), draft.id)
    await mutation.commit(_scope(), draft.id)
    assert (
        await journal.get_mutation(workspace_id=WS, project_id=PR, mutation_id=draft.id)
    ).status is MutationStatus.COMMITTED
    (run,) = await conversations.runs(_scope(), WS, PR, kind=RunKind.LINKS)
    assert (run.kept, run.precision) == (1, 1.0) and run.settled_at <= datetime.now(UTC)


@pytest.mark.asyncio
async def test_threads_and_runs_in_postgres_keep_what_they_must(stack) -> None:
    from backend.domain.branching import working_in
    from backend.domain.conversations import MAX_TURNS
    from backend.domain.delegation import kept_note

    mutation, repository, journal, _history, conversations, store = stack
    # A premise thread never lets go of the turns that tell the story.
    await conversations.record(
        _scope(), WS, PR, ThreadKind.PREMISE, [author_turn("Wren lights the lamps.")]
    )
    for n in range(MAX_TURNS // 2):
        await conversations.record(
            _scope(),
            WS,
            PR,
            ThreadKind.PREMISE,
            [author_turn("", [(f"Q{n}?", "A.")]), assistant_turn("Ok.")],
        )
    premise = await conversations.thread(_scope(), WS, PR, ThreadKind.PREMISE)
    assert premise.turns[0].body == "Wren lights the lamps."
    assert len(premise.turns) == MAX_TURNS + 1
    # A removed member's threads go, in every story of the workspace; no one else's.
    await conversations.record(_scope("usr_other"), WS, PR, ThreadKind.CHANGE, [author_turn("x")])
    assert await store.delete_member_threads(workspace_id=WS, actor_user_id="usr_other") == 1
    assert await conversations.thread(_scope("usr_other"), WS, PR, ThreadKind.CHANGE) is None
    assert await conversations.thread(_scope(), WS, PR, ThreadKind.PREMISE) is not None

    # Runs are told per line, and what a rule kept is counted apart.
    branch = await journal.working_branch(workspace_id=WS, project_id=PR)
    draft = await mutation.create_draft(
        _scope(),
        workspace_id=WS,
        project_id=PR,
        operations=[CreateEntityOp(id="tmp:b", type="Character", name="Bo")],
        expected_head=await repository.get_head(
            workspace_id=WS, project_id=PR, branch_id=branch.id
        ),
        origin="model",
    )
    await mutation.submit(_scope(), draft.id, "test")
    await conversations.record_run(
        _scope(), WS, PR, kind=RunKind.LINKS, suggested=1, mutation_id=draft.id, details={"x": 2}
    )
    with working_in("branch_elsewhere"):
        await conversations.record_run(_scope(), WS, PR, kind=RunKind.LINKS, suggested=9)
        assert [run.suggested for run in await conversations.runs(_scope(), WS, PR)] == [9]
    await mutation.approve(_scope(), draft.id, kept_note(["new_entry"]))
    await mutation.commit(_scope(), draft.id)
    (run,) = await conversations.runs(_scope(), WS, PR)
    assert (run.kept, run.kept_for_you, run.precision) == (0, 1, None)
    assert run.details == {"x": 2, "kept_for_you": 1}
