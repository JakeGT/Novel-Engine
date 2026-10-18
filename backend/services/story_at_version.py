"""The story as it stood at one saved version (QRY-002).

Every change is kept as a record version (``canon_record_versions``), so the story at
any version is rebuilt by replaying them, oldest first, the same way history shows one
record at a version. A what-if carries the versions it was started from, so this works
on any line of the story. Nothing is stored: the replay is derived each time.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.domain.canon.assertions import Belief, Fact
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import Event
from backend.domain.canon.plans import PlanItem
from backend.domain.errors import ValidationError
from backend.domain.projection import RecordVersion
from backend.ports import CanonRepositoryPort
from backend.services.canon_history_service import (
    _replay_belief,
    _replay_entity,
    _replay_event,
    _replay_fact,
    _replay_plan_item,
)
from backend.services.story_time_service import Story

_PAGE = 2_000
# A story far larger than any novel; past this the replay is refused rather than run away
# (or stop part of the way and show a story that never was).
MAX_VERSIONS = 200_000
TOO_LONG = "This story's history is too long to show an earlier version of it."


@dataclass(frozen=True)
class StoryAt:
    """Everything current in one story at one version, as the guide and journey read it."""

    story: Story
    beliefs: list[Belief]
    plan_items: list[PlanItem]


async def _versions_up_to(
    canon: CanonRepositoryPort, workspace_id: str, project_id: str, revision: int
) -> list[RecordVersion]:
    versions: list[RecordVersion] = []
    upper = revision
    while upper > 0:
        page = await canon.list_record_versions(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id, max_revision=upper, limit=_PAGE
        )
        if len(versions) + len(page) > MAX_VERSIONS:
            raise ValidationError(TOO_LONG)
        versions.extend(page)
        if len(page) < _PAGE:
            break
        # Newest first: the next page starts below the oldest revision seen, and that
        # revision is read again whole so none of its records is cut in two.
        oldest = min(version.revision for version in page)
        versions = [version for version in versions if version.revision != oldest]
        if oldest == upper:
            # One version with more records than a page: read it on its own.
            versions.extend(await _one_revision(canon, workspace_id, project_id, oldest))
            if len(versions) > MAX_VERSIONS:
                raise ValidationError(TOO_LONG)
            upper = oldest - 1
        else:
            upper = oldest
    unique = {
        (version.revision, version.ordinal, version.record_type, version.record_id): version
        for version in versions
    }
    return sorted(unique.values(), key=lambda version: (version.revision, version.ordinal))


async def _one_revision(
    canon: CanonRepositoryPort, workspace_id: str, project_id: str, revision: int
) -> list[RecordVersion]:
    found: list[RecordVersion] = []
    for record_type in ("entity", "fact", "belief", "event", "plan_item"):
        found.extend(
            await canon.list_record_versions(  # type: ignore[attr-defined]
                workspace_id=workspace_id,
                project_id=project_id,
                record_type=record_type,
                min_revision=revision - 1,
                max_revision=revision,
                limit=MAX_VERSIONS,
            )
        )
    return found


async def story_at(
    canon: CanonRepositoryPort, workspace_id: str, project_id: str, revision: int
) -> StoryAt:
    """The entries, facts, events, beliefs and plan as they stood at ``revision``."""
    entities: dict[str, CanonEntity | None] = {}
    facts: dict[str, Fact | None] = {}
    beliefs: dict[str, Belief | None] = {}
    events: dict[str, Event | None] = {}
    plan: dict[str, PlanItem | None] = {}
    for version in await _versions_up_to(canon, workspace_id, project_id, revision):
        key = version.record_id
        if version.record_type == "entity":
            entities[key] = _replay_entity(version, entities.get(key))
        elif version.record_type == "fact":
            facts[key] = _replay_fact(version, facts.get(key))
        elif version.record_type == "belief":
            beliefs[key] = _replay_belief(version, beliefs.get(key))
        elif version.record_type == "event":
            events[key] = _replay_event(version, events.get(key))
        elif version.record_type == "plan_item":
            plan[key] = _replay_plan_item(version, plan.get(key))
    return StoryAt(
        story=Story(
            entities={
                entity.id: entity
                for entity in entities.values()
                if entity is not None and entity.is_active
            },
            facts=[
                fact for fact in facts.values() if fact is not None and fact.closed_revision is None
            ],
            events=[e for e in events.values() if e is not None and e.retired_revision is None],
        ),
        beliefs=[b for b in beliefs.values() if b is not None and b.closed_revision is None],
        plan_items=[p for p in plan.values() if p is not None and p.retired_revision is None],
    )


__all__ = ["MAX_VERSIONS", "StoryAt", "story_at"]
