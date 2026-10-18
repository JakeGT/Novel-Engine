"""Canon events.

An event records participants/locations/causes/outcomes and provenance. Phase 2
stores author-supplied world-position input verbatim (which may be fuzzy) and
never fabricates exact chronology; Phase 3 owns chronology evaluation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.domain.canon.propositions import Proposition
from backend.domain.worldtime import WorldTime

# An event of this kind is something said, not something that happened to be true
# (KNW-005): its ``claim`` holds what is said. It never becomes a fact by itself.
CLAIM_KIND = "claim"
# How something is said. "denies" says it is not so.
ClaimModality = Literal["states", "suggests", "wonders", "denies"]
CLAIM_MODALITIES: tuple[str, ...] = ("states", "suggests", "wonders", "denies")
# Participant roles on a claim: who says it (a character speaking, or a character
# narrating), and who could perceive it. No speaker or narrator means the narration.
CLAIM_SPEAKER = "speaker"
CLAIM_NARRATOR = "narrator"
CLAIM_PERCEIVER = "perceiver"
# A character working something out (KNW-002): an event of this kind names who did, as
# ``thinker``, and the facts and beliefs they worked it out from, as its ``premises``.
INFERRED_KIND = "inferred"
INFERRED_THINKER = "thinker"
MAX_PREMISES = 12


class EventClaim(BaseModel):
    """What a claim says, how, and where. Never a fact (ADR-020 amendment)."""

    model_config = {"extra": "forbid"}

    proposition: Proposition
    modality: ClaimModality = "states"
    # The scene (a plan item) it is said in, when there is one.
    scene_id: str | None = Field(default=None, max_length=200)


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
    # Story time (ADR-020), when the author has placed it.
    when: WorldTime | None = None
    # Set only on an event of kind ``claim``.
    claim: EventClaim | None = None
    # The scene (a plan item) it happens in, when the author says (KNW-002). A claim keeps
    # its scene in ``claim.scene_id`` instead.
    scene_id: str | None = None
    # On an event of kind ``inferred``: the facts and beliefs it was worked out from.
    premises: list[str] = Field(default_factory=list)
    provenance_id: str
    created_revision: int
    retired_revision: int | None = None
