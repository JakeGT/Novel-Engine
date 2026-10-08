"""Canon events.

An event records participants/locations/causes/outcomes and provenance. Phase 2
stores author-supplied world-position input verbatim (which may be fuzzy) and
never fabricates exact chronology; Phase 3 owns chronology evaluation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class EventParticipant(BaseModel):
    model_config = {"extra": "forbid"}

    entity_id: str
    role: str


class Event(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    workspace_id: str
    project_id: str
    branch_id: str
    kind: str
    name: str
    participants: list[EventParticipant] = Field(default_factory=list)
    location_ids: list[str] = Field(default_factory=list)
    cause_ids: list[str] = Field(default_factory=list)
    outcome_ids: list[str] = Field(default_factory=list)
    # Author-supplied, possibly fuzzy; stored as given, never normalized here.
    world_position: str | None = None
    provenance_id: str
    created_revision: int
    retired_revision: int | None = None
