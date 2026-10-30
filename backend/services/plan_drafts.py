"""The shape of a drafted piece of plan, shared by planning and by Change anything.

Kept apart from ``plan_workflow`` so Change anything (which the planning workflow builds
on) can draft plan items too without a circular import. Model output: untrusted, typed
and checked by ``plan_workflow.plan_operations`` before it becomes a change.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DraftPlanRef(BaseModel):
    ref: str = Field(description="The id of an existing entry, or the ref of a new one")
    role: str = Field(description="One of: point_of_view, place, participant, about")


class DraftPlanItem(BaseModel):
    ref: str = Field(description="A short local handle for this item")
    kind: str = Field(description="One of: story, arc, beat, scene, thread")
    title: str
    summary: str = Field(default="", description="What is meant to happen, in a sentence or two")
    parent_ref: str | None = Field(
        default=None, description="The id of an existing plan item, or the ref of a drafted one"
    )
    entries: list[DraftPlanRef] = Field(default_factory=list)
    sets_up: list[str] = Field(default_factory=list, description="Threads this opens")
    pays_off: list[str] = Field(default_factory=list, description="Threads this resolves")
    quote: str = Field(
        default="",
        description=(
            "The author's own words this item comes from, copied exactly from what you were "
            "given; empty when it is your own suggestion"
        ),
    )


class DraftPlanEntry(BaseModel):
    ref: str
    type: str = Field(description="One of the allowed entry types")
    name: str


__all__ = ["DraftPlanEntry", "DraftPlanItem", "DraftPlanRef"]
