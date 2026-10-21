"""Merging rewrites what characters said (claims) to name the kept entry (AST-EXP-017),
unless that would have the kept entry named twice; undoing the merge puts them back."""

import pytest

from backend.domain.canon.events import CLAIM_KIND, EventClaim, EventParticipant
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.canon.registry import CORE_PREDICATES
from backend.domain.merge import expand, plan_merge, read_records
from backend.domain.operations import (
    AmendEventOp,
    CreateEntityOp,
    CreateEventOp,
    RetireEntityOp,
    UpdateEntityOp,
)
from backend.domain.validation import CanonValidationError
from tests.unit.test_merge import PR, SCOPE, WS, _story, _World

pytestmark = pytest.mark.unit


def _claim(event_id: str, subject: str, predicate: str, target: str) -> CreateEventOp:
    return CreateEventOp(
        id=event_id,
        kind=CLAIM_KIND,
        name=f"Tobin says {subject} {predicate} {target}",
        participants=[EventParticipant(entity_id="tobin", role="speaker")],
        claim=EventClaim(
            proposition=Proposition(
                subject_id=subject,
                predicate=predicate,
                object=EntityRefObject(entity_id=target, project_id=PR),
            ),
            modality="states",
        ),
    )


async def _said(world: _World, event_id: str) -> Proposition:
    event = await world.repo.get_event(workspace_id=WS, project_id=PR, event_id=event_id)
    assert event is not None and event.claim is not None
    return event.claim.proposition


@pytest.mark.asyncio
async def test_what_a_character_said_names_the_kept_entry_after_a_merge() -> None:
    world = _World()
    await _story(world)
    await world.commit(
        _claim("ev_says_guild", "mara_v", "MEMBER_OF", "guild"),
        # "Mara V. knows Mara" would become "Mara knows Mara": kept as said.
        _claim("ev_says_knows", "mara_v", "KNOWS", "mara"),
    )
    records = await read_records(world.reader(), "mara", "mara_v")
    assert records is not None
    merge = plan_merge(records, CORE_PREDICATES)
    changes, preview = expand(merge, records, ordinal=0)
    assert preview.claims_moved == ["ev_says_guild"]
    assert preview.still_naming == [("event", "ev_says_knows")]
    (rewrite,) = [op for op in changes if isinstance(op, AmendEventOp) and op.claim is not None]
    assert rewrite.id == "ev_says_guild" and rewrite.claim.proposition.subject_id == "mara"
    # The speaker and how it was said are unchanged.
    assert rewrite.claim.modality == "states" and rewrite.participants is None

    merged_at = await world.commit(merge)
    assert (await _said(world, "ev_says_guild")).subject_id == "mara"
    assert (await _said(world, "ev_says_knows")).subject_id == "mara_v"

    preview_undo = await world.history.propose_revert(SCOPE, WS, PR, from_revision=merged_at - 1)
    assert not preview_undo.conflicts
    await world.revert(merged_at - 1)
    assert (await _said(world, "ev_says_guild")).subject_id == "mara_v"


@pytest.mark.asyncio
async def test_a_claim_about_something_archived_or_retyped_since_never_blocks_a_merge() -> None:
    world = _World()
    await _story(world)
    await world.commit(
        CreateEntityOp(id="old_inn", type="Location", name="The Old Inn"),
        CreateEntityOp(id="crew", type="Faction", name="The Crew"),
    )
    await world.commit(
        _claim("ev_says_inn", "mara_v", "LOCATED_AT", "old_inn"),
        _claim("ev_says_crew", "mara_v", "MEMBER_OF", "crew"),
    )
    # Since then the inn was archived, and the crew turned out to be a place: what Tobin
    # said is kept as he said it, and neither stops the merge.
    await world.commit(RetireEntityOp(id="old_inn"))
    await world.commit(UpdateEntityOp(id="crew", type="Location"))
    records = await read_records(world.reader(), "mara", "mara_v")
    assert records is not None
    await world.commit(plan_merge(records, CORE_PREDICATES))
    inn = await _said(world, "ev_says_inn")
    assert inn.subject_id == "mara" and inn.object.entity_id == "old_inn"
    crew = await _said(world, "ev_says_crew")
    assert crew.subject_id == "mara" and crew.object.entity_id == "crew"


@pytest.mark.asyncio
async def test_a_claim_changed_to_name_an_archived_entry_is_still_refused() -> None:
    world = _World()
    await _story(world)
    await world.commit(CreateEntityOp(id="old_inn", type="Location", name="The Old Inn"))
    await world.commit(_claim("ev_says_at", "mara_v", "LOCATED_AT", "harbor"))
    await world.commit(RetireEntityOp(id="old_inn"))
    event = await world.repo.get_event(workspace_id=WS, project_id=PR, event_id="ev_says_at")
    assert event is not None and event.claim is not None
    moved = event.claim.model_copy(
        update={
            "proposition": event.claim.proposition.model_copy(
                update={"object": EntityRefObject(entity_id="old_inn", project_id=PR)}
            )
        }
    )
    with pytest.raises(CanonValidationError):
        await world.commit(AmendEventOp(id="ev_says_at", claim=moved))
