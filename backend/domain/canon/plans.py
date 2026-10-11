"""Story plan records: intent, never fact.

A plan says what the author *means* to happen: the story, its arcs, the beats of
an arc, the scenes that carry a beat, and the threads (setups, promises, questions)
that run through them. A planned scene is not an event that happened, and nothing
here is ever read as true in the story. That is kept by construction, not by a
filter: plan items live in their own table, are never projected into the graph,
and are not returned by any entry, fact, belief or event query.

They are still canon-governed. A plan item is created, changed and removed only
through reviewed operations, has history, and can be undone like anything else.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class PlanKind(StrEnum):
    STORY = "story"
    ARC = "arc"
    BEAT = "beat"
    SCENE = "scene"
    THREAD = "thread"


# What each kind may sit under. Every level is optional: an author may plan scenes
# with no beats, or beats with no arc. A thread runs across the plan and has no parent.
ALLOWED_PARENTS: dict[PlanKind, frozenset[PlanKind]] = {
    PlanKind.STORY: frozenset(),
    PlanKind.ARC: frozenset({PlanKind.STORY}),
    PlanKind.BEAT: frozenset({PlanKind.ARC, PlanKind.STORY}),
    PlanKind.SCENE: frozenset({PlanKind.BEAT, PlanKind.ARC, PlanKind.STORY}),
    PlanKind.THREAD: frozenset(),
}

# How an entry takes part in a plan item. A fixed vocabulary, so a role can be
# relied on ("the scene's point of view") and is never free text from a model.
PLAN_ROLES: frozenset[str] = frozenset(
    {
        "point_of_view",  # whose eyes a scene is seen through
        "place",  # where it happens
        "participant",  # who or what is in it
        "about",  # who or what an arc or thread belongs to
    }
)
# Roles that may be filled by one entry only, and what that entry must be.
SINGLE_ROLES: dict[str, str | None] = {"point_of_view": "Character", "place": "Location"}

MAX_TITLE_LENGTH = 200
MAX_SUMMARY_LENGTH = 4000
MAX_REFS = 40
MAX_THREAD_LINKS = 20
MAX_DETAILS = 20
MAX_DETAIL_LENGTH = 2000


class PlanRef(BaseModel):
    """An entry a plan item involves, and how."""

    model_config = {"extra": "forbid"}

    entity_id: str
    role: str


class PlanItem(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    workspace_id: str
    project_id: str
    branch_id: str
    kind: PlanKind
    title: str
    # What is meant to happen, in the author's words. Intent, not a record of events.
    summary: str = ""
    parent_id: str | None = None
    # Order among the items that share a parent.
    position: int = 0
    refs: list[PlanRef] = Field(default_factory=list)
    # Threads this beat or scene opens, and threads it resolves (plan item ids).
    sets_up: list[str] = Field(default_factory=list)
    pays_off: list[str] = Field(default_factory=list)
    # Anything else the author wants to keep with it: stakes, what changes, a turning point.
    details: dict[str, str] = Field(default_factory=dict)
    provenance_id: str
    created_revision: int
    retired_revision: int | None = None

    @property
    def is_active(self) -> bool:
        return self.retired_revision is None

    def refs_as(self, role: str) -> list[str]:
        return [ref.entity_id for ref in self.refs if ref.role == role]


def plan_item_from_create(
    change: Any,
    *,
    workspace_id: str,
    project_id: str,
    branch_id: str,
    provenance_id: str,
    revision: int,
) -> PlanItem:
    """The item a ``create_plan_item`` operation (or its stored payload) brings into being."""
    data = change if isinstance(change, dict) else change.model_dump(mode="json")
    return PlanItem(
        id=data["id"],
        workspace_id=workspace_id,
        project_id=project_id,
        branch_id=branch_id,
        kind=PlanKind(data["kind"]),
        title=data["title"],
        summary=data.get("summary") or "",
        parent_id=data.get("parent_id"),
        position=data.get("position") or 0,
        refs=[PlanRef.model_validate(ref) for ref in data.get("refs") or []],
        sets_up=list(data.get("sets_up") or []),
        pays_off=list(data.get("pays_off") or []),
        details=dict(data.get("details") or {}),
        provenance_id=provenance_id,
        created_revision=revision,
    )


def apply_plan_update(item: PlanItem, change: Any) -> PlanItem:
    """``item`` after an ``update_plan_item`` operation (or its stored payload).

    One definition of what an update means, shared by the store, the history replay
    and the validator, so they can never disagree.
    """
    data = change if isinstance(change, dict) else change.model_dump(mode="json")
    update: dict[str, Any] = {}
    for key in ("title", "summary", "position", "sets_up", "pays_off", "details"):
        if data.get(key) is not None:
            update[key] = data[key]
    if data.get("refs") is not None:
        update["refs"] = [PlanRef.model_validate(ref) for ref in data["refs"]]
    if data.get("detach"):
        update["parent_id"] = None
    elif data.get("parent_id"):
        update["parent_id"] = data["parent_id"]
    return item.model_copy(update=update)


__all__ = [
    "ALLOWED_PARENTS",
    "MAX_DETAILS",
    "MAX_DETAIL_LENGTH",
    "MAX_REFS",
    "MAX_SUMMARY_LENGTH",
    "MAX_THREAD_LINKS",
    "MAX_TITLE_LENGTH",
    "PLAN_ROLES",
    "SINGLE_ROLES",
    "PlanItem",
    "PlanKind",
    "PlanRef",
    "apply_plan_update",
    "plan_item_from_create",
]
