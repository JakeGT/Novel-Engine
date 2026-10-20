"""What a model saw in a picture, as observations the author reviews (AST-005, ADR-010).

A vision model may propose a caption for a picture and appearance details of what it
shows. Each observation belongs to one exact picture version, keeps the model, the prompt
version and the job that produced it, an optional region of the picture as evidence, and a
confidence. The author accepts it, corrects it (their words win) or rejects it. A newer
description of the same picture supersedes the proposals nobody reviewed.

An observation is never a story fact. Only an accepted or corrected appearance detail
about an entry can be turned into one, and only by an ordinary proposal that goes through
review like any other change (``propose_fact`` in the service). The accepted words are
what a model is given in place of the picture when it cannot see pictures.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.domain.errors import ValidationError
from backend.domain.identity import utcnow

MAX_OBSERVATION_TEXT = 600
MAX_DETAILS = 8


class ObservationKind(StrEnum):
    CAPTION = "caption"
    APPEARANCE = "appearance"


class ObservationState(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    CORRECTED = "corrected"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ReviewDecision(StrEnum):
    ACCEPT = "accept"
    CORRECT = "correct"
    REJECT = "reject"


class Region(BaseModel):
    """A rectangle of the picture, as fractions of its width and height (0 to 1), so it
    holds for the original and every smaller copy alike."""

    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _inside(self) -> Region:
        if self.x + self.width > 1.0001 or self.y + self.height > 1.0001:
            raise ValueError("the region runs off the picture")
        return self

    def location(self) -> str:
        """An evidence location: ``region:x,y,w,h`` to three places."""
        return "region:" + ",".join(f"{v:.3f}" for v in (self.x, self.y, self.width, self.height))


class MediaObservation(BaseModel):
    workspace_id: str
    project_id: str
    id: str
    asset_id: str
    asset_version_id: str
    segment_id: str | None = None
    kind: ObservationKind
    text: str
    # The entry the detail is about, when the model named one of the picture's entries.
    subject_record_type: str | None = None
    subject_record_id: str | None = None
    region: Region | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    model: str | None = None
    prompt_name: str | None = None
    prompt_version: str | None = None
    job_id: str | None = None
    state: ObservationState = ObservationState.PROPOSED
    corrected_text: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    superseded_by: str | None = None
    # The reviewed proposal it was offered as a fact through, once it was.
    proposal_mutation_set_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def accepted(self) -> bool:
        return self.state in (ObservationState.ACCEPTED, ObservationState.CORRECTED)

    @property
    def words(self) -> str:
        """The words that stand: the author's correction, else what was proposed."""
        return (self.corrected_text or self.text).strip()


def reviewed(
    observation: MediaObservation,
    decision: ReviewDecision,
    *,
    corrected_text: str | None,
    actor_user_id: str | None,
    now: datetime | None = None,
) -> MediaObservation:
    """The observation after the author's decision. Only a proposal is decided once;
    an accepted one may be corrected again; a rejected or superseded one stays as it is."""
    if observation.state in (ObservationState.REJECTED, ObservationState.SUPERSEDED):
        raise ValidationError("This observation was already set aside.")
    if observation.accepted and decision is ReviewDecision.ACCEPT:
        raise ValidationError("This observation was already accepted.")
    update: dict[str, object] = {"reviewed_by": actor_user_id, "reviewed_at": now or utcnow()}
    if decision is ReviewDecision.CORRECT:
        words = " ".join((corrected_text or "").split())
        if not words:
            raise ValidationError("Write the corrected words.")
        if len(words) > MAX_OBSERVATION_TEXT:
            raise ValidationError(f"Keep it under {MAX_OBSERVATION_TEXT} characters.")
        update |= {"state": ObservationState.CORRECTED, "corrected_text": words}
    elif decision is ReviewDecision.ACCEPT:
        update |= {"state": ObservationState.ACCEPTED}
    else:
        if observation.proposal_mutation_set_id:
            raise ValidationError(
                "This was already offered as a fact; decide that in the review queue."
            )
        update |= {"state": ObservationState.REJECTED}
    return observation.model_copy(update=update)


def description_of(observations: list[MediaObservation], version_id: str) -> str:
    """The accepted words about one picture version: its caption, then its details.
    "" when nothing about this version has been accepted."""
    standing = [o for o in observations if o.asset_version_id == version_id and o.accepted]
    captions = [o.words for o in standing if o.kind is ObservationKind.CAPTION]
    details = [o.words for o in standing if o.kind is ObservationKind.APPEARANCE]
    parts = [*captions[:1], *details[:MAX_DETAILS]]
    return " ".join(part.rstrip(".") + "." for part in parts if part)


__all__ = [
    "MAX_DETAILS",
    "MAX_OBSERVATION_TEXT",
    "MediaObservation",
    "ObservationKind",
    "ObservationState",
    "Region",
    "ReviewDecision",
    "description_of",
    "reviewed",
]
