"""The story with a proposed change applied, on a scratch copy that is never saved (CON-017).

Before a change is approved, the review screen shows how the plan's scene conditions and
the reader's guide would read after it. Both are worked out by the same code that works
them out for the story as it stands, over a copy of the story's records with the change
applied the way history replays a saved change (``canon_history_service``'s replay of
one record version, the one definition of what each kind of change does). Nothing is
written: the copy lives only for the read. A merge is expanded into the changes it would
make, read from the copy, as it is when kept (``backend.domain.merge``).

What the copy cannot hold is said by the caller: a retired entry brought back (retired
entries are not read here) stays out of the copy.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from backend.domain.canon.assertions import Belief, Fact
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import Event
from backend.domain.canon.plans import PlanItem
from backend.domain.merge import MAX_RECORDS, expand_merges
from backend.domain.operations import CanonOperation
from backend.domain.projection import RecordVersion
from backend.services.canon_history_service import (
    _replay_belief,
    _replay_entity,
    _replay_event,
    _replay_fact,
    _replay_plan_item,
)
from backend.services.story_at_version import StoryAt
from backend.services.story_time_service import Story

_RECORD_TYPES = {
    "create_entity": "entity",
    "update_entity": "entity",
    "retire_entity": "entity",
    "restore_entity": "entity",
    "assert_fact": "fact",
    "close_fact": "fact",
    "amend_fact": "fact",
    "assert_belief": "belief",
    "close_belief": "belief",
    "create_event": "event",
    "amend_event": "event",
    "retire_event": "event",
    "create_plan_item": "plan_item",
    "update_plan_item": "plan_item",
    "retire_plan_item": "plan_item",
}
SCRATCH_COMMIT = "scratch"


class _Copy:
    """The merge source over the scratch copy: the story as the change would find it."""

    def __init__(self, state: StoryAt) -> None:
        self._state = state

    async def get_entity(self, entity_id: str) -> CanonEntity | None:
        return self._state.story.entities.get(entity_id)

    async def asserted_facts(self, limit: int) -> list[Fact]:
        return list(self._state.story.facts)[:limit]

    async def current_beliefs(self, limit: int) -> list[Belief]:
        return list(self._state.beliefs)[:limit]

    async def live_events(self, limit: int) -> list[Event]:
        return list(self._state.story.events)[:limit]

    async def live_plan_items(self, limit: int) -> list[PlanItem]:
        return list(self._state.plan_items)[:limit]

    async def live_asset_links(self, limit: int) -> list[Any]:
        return []


async def scratch_story(
    state: StoryAt,
    operations: Sequence[CanonOperation],
    *,
    workspace_id: str,
    project_id: str,
    branch_id: str,
    revision: int,
) -> StoryAt:
    """``state`` with ``operations`` applied, as a new copy; ``state`` is left as it was.

    ``revision`` is the version the change would be saved as (the head plus one)."""
    expanded, _origin = await expand_merges(operations, _Copy(state))
    return apply_on_copy(
        state,
        expanded,
        workspace_id=workspace_id,
        project_id=project_id,
        branch_id=branch_id,
        revision=revision,
    )


def apply_on_copy(
    state: StoryAt,
    operations: Sequence[CanonOperation],
    *,
    workspace_id: str,
    project_id: str,
    branch_id: str,
    revision: int,
) -> StoryAt:
    """Pure work over the records (no merge left in ``operations``), so it can run off
    the event loop for a large story."""
    entities: dict[str, CanonEntity | None] = dict(state.story.entities)
    facts: dict[str, Fact | None] = {fact.id: fact for fact in state.story.facts}
    beliefs: dict[str, Belief | None] = {belief.id: belief for belief in state.beliefs}
    events: dict[str, Event | None] = {event.id: event for event in state.story.events}
    plan: dict[str, PlanItem | None] = {item.id: item for item in state.plan_items}
    now = datetime.now(UTC)
    for ordinal, operation in enumerate(operations):
        change = operation.model_dump(mode="json")
        record_type = _RECORD_TYPES.get(str(change.get("op")))
        if record_type is None:
            continue  # pictures' links and anything else read by neither
        version = RecordVersion(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch_id,
            record_type=record_type,
            record_id=operation.id,
            revision=revision,
            operation_type=str(change.get("op")),
            commit_id=SCRATCH_COMMIT,
            change=change,
            created_at=now,
            ordinal=ordinal,
        )
        key = operation.id
        if record_type == "entity":
            entities[key] = _replay_entity(version, entities.get(key))
        elif record_type == "fact":
            facts[key] = _replay_fact(version, facts.get(key))
        elif record_type == "belief":
            beliefs[key] = _replay_belief(version, beliefs.get(key))
        elif record_type == "event":
            events[key] = _replay_event(version, events.get(key))
        else:
            plan[key] = _replay_plan_item(version, plan.get(key))
    return StoryAt(
        story=Story(
            entities={
                entity_id: entity
                for entity_id, entity in entities.items()
                if entity is not None and entity.is_active
            },
            facts=[f for f in facts.values() if f is not None and f.closed_revision is None],
            events=[e for e in events.values() if e is not None and e.retired_revision is None],
        ),
        beliefs=[b for b in beliefs.values() if b is not None and b.closed_revision is None],
        plan_items=[p for p in plan.values() if p is not None and p.retired_revision is None],
    )


__all__ = ["MAX_RECORDS", "SCRATCH_COMMIT", "apply_on_copy", "scratch_story"]
