"""Continuity across story time and place, for the timeline, the plan and the studio.

Reads the story bible, the plan and the author's "how long things take" table, and runs
the deterministic checks in ``backend/domain/continuity.py``. Never calls a model and
never writes canon; the table is the author's own setting beside the story's unit of
time, shared by every line of the story (ADR-020).
"""

from __future__ import annotations

from typing import Any

from backend.domain.canon.plans import PlanKind
from backend.domain.continuity import (
    ContinuityFindings,
    HowLong,
    StoryParts,
    continuity_findings,
    known_records,
    read_how_long,
)
from backend.domain.errors import ConflictError, ValidationError
from backend.domain.knowledge import reading_order
from backend.ports import CanonRepositoryPort
from backend.services.story_time_service import DEFAULT_UNIT, MAX_RECORDS, Story, read_story

MAX_PLAN_ITEMS = 2000


async def how_long_of(clocks: Any, workspace_id: str, project_id: str) -> tuple[HowLong, int]:
    if clocks is None or not hasattr(clocks, "get_how_long"):
        return HowLong(), 0
    raw, version = await clocks.get_how_long(workspace_id=workspace_id, project_id=project_id)
    return read_how_long(raw), version


async def unit_of(clocks: Any, workspace_id: str, project_id: str) -> str:
    if clocks is None:
        return DEFAULT_UNIT
    stored = await clocks.get_unit(workspace_id=workspace_id, project_id=project_id)
    return stored or DEFAULT_UNIT


async def story_continuity(
    reader: CanonRepositoryPort,
    clocks: Any,
    workspace_id: str,
    project_id: str,
    *,
    story: Story | None = None,
    items: list[Any] | None = None,
) -> ContinuityFindings:
    """Every continuity finding in this line of the story, worked out now.

    Pass ``story`` and ``items`` when they are already read, so nothing is read twice.
    ``not_shown`` on the result counts findings past the cap.
    """
    if story is None:
        story = await read_story(reader, workspace_id, project_id)
    if items is None:
        items = list(
            await reader.list_plan_items(
                workspace_id=workspace_id, project_id=project_id, limit=MAX_PLAN_ITEMS
            )
        )
    how_long, _ = await how_long_of(clocks, workspace_id, project_id)
    return continuity_findings(
        StoryParts(
            entities=story.entities,
            facts=story.facts,
            events=story.events,
            scenes=[item for item in reading_order(items) if item.kind is PlanKind.SCENE],
            unit=await unit_of(clocks, workspace_id, project_id),
            how_long=how_long,
        )
    )


async def save_how_long(
    reader: CanonRepositoryPort,
    clocks: Any,
    workspace_id: str,
    project_id: str,
    table: HowLong,
    expected_version: int,
) -> int:
    """Replace the table, refusing places, events and facts this line does not have.

    The table is shared by every line, so only rows that name something new are checked
    against this line: a row another line added (a what-if's place) is kept as it is.
    """
    kept, _ = await how_long_of(clocks, workspace_id, project_id)
    entities = await reader.list_entities(
        workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
    )
    events = await reader.list_events(
        workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
    )
    facts = await reader.list_facts(
        workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
    )
    problems = known_records(
        table,
        {entity.id: entity for entity in entities},
        [event.id for event in events if event.retired_revision is None],
        [fact.id for fact in facts if fact.closed_revision is None],
        kept=kept,
    )
    if problems:
        raise ValidationError(" ".join(problems))
    version = await clocks.set_how_long(
        workspace_id=workspace_id,
        project_id=project_id,
        table=table.model_dump(mode="json"),
        expected_version=expected_version,
    )
    if version is None:
        raise ConflictError(
            "Someone else changed how long things take since you opened it. Reload to see "
            "their changes, then make yours again."
        )
    return int(version)


__all__ = ["how_long_of", "save_how_long", "story_continuity", "unit_of"]
