"""Compensating revert proposals: edit inverses and dependency conflicts (2H)."""

import pytest

from backend.domain.canon.propositions import EntityRefObject, Proposition, StringObject
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import (
    AmendEventOp,
    AssertFactOp,
    CloseFactOp,
    CreateEntityOp,
    CreateEventOp,
    RetireEntityOp,
    UpdateEntityOp,
)
from backend.services.canon_history_service import CanonHistoryService
from backend.services.mutation_service import MutationService
from tests.doubles import InMemoryCanonJournal, InMemoryCanonRepository

pytestmark = pytest.mark.unit

WS, PR = "ws", "pr"
SCOPE = AccessScope(actor_user_id="u1", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR)


class _World:
    def __init__(self) -> None:
        self.journal = InMemoryCanonJournal()
        self.repo = InMemoryCanonRepository(journal=self.journal)
        self.mutation = MutationService(self.journal, self.repo, local_mode=True)
        self.history = CanonHistoryService(
            canon=self.repo, journal=self.journal, local_mode=True, mutation=self.mutation
        )
        self.head = 0

    async def commit(self, *operations) -> int:
        draft = await self.mutation.create_draft(
            SCOPE,
            workspace_id=WS,
            project_id=PR,
            operations=list(operations),
            expected_head=self.head,
        )
        result = await self.mutation.commit_author_change(
            SCOPE, draft.id, acknowledge_high_impact=True
        )
        self.head = result.revision
        return result.revision

    async def revert(self, from_revision: int, to_revision: int | None = None) -> int:
        result = await self.history.commit_revert(
            SCOPE,
            WS,
            PR,
            from_revision=from_revision,
            to_revision=to_revision,
            expected_head=self.head,
            acknowledge_high_impact=True,
        )
        self.head = result.revision
        return result.revision


def _name_fact(fact_id: str, subject: str, value: str) -> AssertFactOp:
    return AssertFactOp(
        id=fact_id,
        proposition=Proposition(
            subject_id=subject,
            predicate="HAS_NAME",
            object=StringObject(kind="string", value=value),
        ),
    )


def _located(fact_id: str, subject: str, place: str) -> AssertFactOp:
    return AssertFactOp(
        id=fact_id,
        proposition=Proposition(
            subject_id=subject,
            predicate="LOCATED_AT",
            object=EntityRefObject(kind="entity_ref", entity_id=place, project_id=PR),
        ),
    )


@pytest.mark.asyncio
async def test_entity_edit_is_reverted_to_its_exact_prior_state() -> None:
    world = _World()
    await world.commit(
        CreateEntityOp(
            id="ent_1", type="Character", name="Mara", slug="mara", attributes={"age": 30}
        )
    )
    await world.commit(
        UpdateEntityOp(id="ent_1", name="Mara Venn", attributes={"age": 31}, aliases=["Fox"])
    )

    proposal = await world.history.propose_revert(SCOPE, WS, PR, from_revision=1)
    assert proposal.operations == [
        {
            "op": "update_entity",
            "id": "ent_1",
            "name": "Mara",
            "attributes": {"age": 30},
            "aliases": [],
        }
    ]
    assert proposal.conflicts == []

    await world.revert(1)
    entity = world.repo.entities[(WS, PR, "ent_1")]
    assert (entity.name, entity.attributes, entity.aliases, entity.slug) == (
        "Mara",
        {"age": 30},
        [],
        "mara",
    )
    # History is extended, never rewritten.
    assert world.head == 3
    assert len([v for v in world.repo.record_versions if v.record_id == "ent_1"]) == 3


@pytest.mark.asyncio
async def test_stacked_edits_unwind_newest_first() -> None:
    world = _World()
    await world.commit(CreateEntityOp(id="ent_1", type="Character", name="A"))
    await world.commit(UpdateEntityOp(id="ent_1", name="B"))
    await world.commit(UpdateEntityOp(id="ent_1", name="C"))

    proposal = await world.history.propose_revert(SCOPE, WS, PR, from_revision=1)
    assert [op["name"] for op in proposal.operations] == ["B", "A"]
    await world.revert(1)
    assert world.repo.entities[(WS, PR, "ent_1")].name == "A"


@pytest.mark.asyncio
async def test_closed_fact_is_reasserted_as_a_new_record() -> None:
    world = _World()
    await world.commit(
        CreateEntityOp(id="ent_1", type="Character", name="Mara"),
        _name_fact("fct_1", "ent_1", "Mara"),
    )
    await world.commit(CloseFactOp(id="fct_1"))

    proposal = await world.history.propose_revert(SCOPE, WS, PR, from_revision=1)
    assert proposal.operations[0]["op"] == "assert_fact"
    assert proposal.operations[0]["proposition"]["object"]["value"] == "Mara"

    await world.revert(1)
    facts = list(world.repo.facts.values())
    assert world.repo.facts[(WS, PR, "fct_1")].status.value == "closed"
    reasserted = [f for f in facts if f.id != "fct_1"]
    assert len(reasserted) == 1 and reasserted[0].status.value == "asserted"
    assert reasserted[0].proposition == world.repo.facts[(WS, PR, "fct_1")].proposition


@pytest.mark.asyncio
async def test_event_amendment_is_restored_and_unclearable_fields_are_manual() -> None:
    world = _World()
    await world.commit(
        CreateEntityOp(id="ent_1", type="Character", name="Mara"),
        CreateEventOp(id="evt_1", kind="battle", name="Fall of Harbor", world_position="year 12"),
        CreateEventOp(id="evt_2", kind="feast", name="Feast"),
    )
    await world.commit(
        AmendEventOp(id="evt_1", name="The Fall", world_position="winter, year 12"),
        AmendEventOp(id="evt_2", world_position="year 13"),
    )

    proposal = await world.history.propose_revert(SCOPE, WS, PR, from_revision=1)
    assert proposal.operations == [
        {
            "op": "amend_event",
            "id": "evt_1",
            "name": "Fall of Harbor",
            "world_position": "year 12",
        }
    ]
    manual = [c for c in proposal.changes if c.inverse_operation is None]
    assert [(c.record_id, c.reason) for c in manual] == [
        ("evt_2", "the earlier state had no world position and it cannot be cleared")
    ]


@pytest.mark.asyncio
async def test_record_created_and_changed_in_range_is_simply_removed() -> None:
    world = _World()
    await world.commit(CreateEntityOp(id="ent_0", type="Character", name="Anchor"))
    await world.commit(CreateEntityOp(id="ent_1", type="Character", name="A"))
    await world.commit(UpdateEntityOp(id="ent_1", name="B"))

    proposal = await world.history.propose_revert(SCOPE, WS, PR, from_revision=1)
    assert proposal.operations == [{"op": "retire_entity", "id": "ent_1"}]
    await world.revert(1)
    assert world.repo.entities[(WS, PR, "ent_1")].lifecycle.value == "retired"
    assert world.repo.entities[(WS, PR, "ent_0")].lifecycle.value == "active"


@pytest.mark.asyncio
async def test_already_retired_creation_and_retirement_need_no_or_manual_action() -> None:
    world = _World()
    await world.commit(CreateEntityOp(id="ent_0", type="Character", name="Old"))
    await world.commit(CreateEntityOp(id="ent_1", type="Character", name="A"))
    await world.commit(RetireEntityOp(id="ent_1"), RetireEntityOp(id="ent_0"))

    proposal = await world.history.propose_revert(SCOPE, WS, PR, from_revision=1)
    assert proposal.operations == []
    reasons = {(c.record_id, c.operation_type): c.reason for c in proposal.changes}
    assert reasons[("ent_1", "create_entity")] == "already retired or closed; nothing to undo"
    assert reasons[("ent_0", "retire_entity")].startswith("a retired entity cannot be reactivated")


@pytest.mark.asyncio
async def test_removing_an_entity_still_referenced_outside_the_revert_is_a_conflict() -> None:
    world = _World()
    await world.commit(CreateEntityOp(id="ent_mara", type="Character", name="Mara"))
    await world.commit(CreateEntityOp(id="ent_harbor", type="Location", name="Harbor"))
    await world.commit(_located("fct_at", "ent_mara", "ent_harbor"))

    # Reverting only r2 would retire the harbor while r3's fact still points at it.
    proposal = await world.history.propose_revert(SCOPE, WS, PR, from_revision=1, to_revision=2)
    assert proposal.conflicts == ["entity ent_harbor is still referenced by fact fct_at"]
    from backend.domain.errors import ValidationError

    with pytest.raises(ValidationError, match="still referenced"):
        await world.revert(1, 2)
    assert world.repo.entities[(WS, PR, "ent_harbor")].lifecycle.value == "active"

    # Reverting both revisions removes the dependent first: no conflict.
    whole = await world.history.propose_revert(SCOPE, WS, PR, from_revision=1)
    assert whole.conflicts == []
    assert [op["op"] for op in whole.operations] == ["close_fact", "retire_entity"]
    await world.revert(1)
    assert world.repo.facts[(WS, PR, "fct_at")].status.value == "closed"
    assert world.repo.entities[(WS, PR, "ent_harbor")].lifecycle.value == "retired"


@pytest.mark.asyncio
async def test_same_record_changed_after_the_range_is_still_a_conflict() -> None:
    world = _World()
    await world.commit(CreateEntityOp(id="ent_1", type="Character", name="A"))
    await world.commit(UpdateEntityOp(id="ent_1", name="B"))
    await world.commit(UpdateEntityOp(id="ent_1", name="C"))

    proposal = await world.history.propose_revert(SCOPE, WS, PR, from_revision=1, to_revision=2)
    assert proposal.conflicts == ["entity ent_1 changed after r2"]
    assert proposal.operations == []
