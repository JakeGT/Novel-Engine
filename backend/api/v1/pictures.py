"""One picture, what a model saw in it, and how it is used in the assistant's context
(AST-005, AST-007, CTX-013).

``GET /pictures/{asset_id}`` is the picture inspector: the picture's newest version, where
it is shown (with each link's secrecy and the story time it applies from), the
observations a model proposed and the author's decisions, and the picture's choice for the
assistant's context. Describing is model work and is asked about first: the estimate says
what it would use and whether the assistant in use can see pictures; starting it above
the story's threshold answers ``409 needs_confirmation`` until ``X-Confirm-Tokens`` is
sent, and a route that cannot see pictures answers ``409 cannot_see_pictures`` before
anything is queued or spent. Accepting an observation changes nothing in the story;
offering one as a fact makes an ordinary proposal for the review queue.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_container
from backend.api.v1.assistant_settings import confirmed_tokens
from backend.api.v1.studio import JobModel, _job
from backend.container import Container
from backend.domain.context_parts import CHOICE_WORDS, ContextChoice
from backend.domain.identity import AccessScope
from backend.domain.media_observations import (
    MAX_OBSERVATION_TEXT,
    MediaObservation,
    ReviewDecision,
)
from backend.domain.worldtime import describe, parse_world_time
from backend.services.media_observations import (
    LinkView,
    MediaObservationService,
    PictureView,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["pictures"])

ROLE_WORDS = {
    "DEPICTS": "a picture of it",
    "REFERENCES": "a reference",
    "SET_AT": "where it is set",
    "MAP_OF": "a map of it",
    "EVIDENCE_FOR": "a reference",
    "VOICE_OF": "a voice",
}


def get_observations(container: Container = Depends(get_container)) -> MediaObservationService:
    return container.media_observations  # type: ignore[no-any-return]


class PictureVersionModel(BaseModel):
    id: str
    version: int
    content_type: str
    byte_length: int
    source: str | None = None
    licence: str | None = None
    attribution: str | None = None
    created_at: datetime


class PictureLinkModel(BaseModel):
    id: str
    role: str
    # In plain words: "a picture of it", "a reference".
    role_words: str
    # entity or plan_item (a scene).
    target_record_type: str
    target_record_id: str
    target_name: str
    # character, location, scene, ...
    target_kind: str
    # Only the characters in ``known_by`` may know what a secret picture shows.
    secret: bool
    known_by: list[str]
    known_by_names: list[str]
    # The story time from which the link applies, in words; null for always.
    applies_from: str | None = None


class ObservationRegionModel(BaseModel):
    x: float
    y: float
    width: float
    height: float


class ObservationModel(BaseModel):
    id: str
    asset_version_id: str
    # caption or appearance
    kind: str
    # What the model proposed.
    text: str
    # The words that stand: the author's correction, else what was proposed.
    words: str
    corrected_text: str | None = None
    subject_record_id: str | None = None
    subject_name: str | None = None
    region: ObservationRegionModel | None = None
    confidence: float | None = None
    model: str | None = None
    prompt_version: str | None = None
    job_id: str | None = None
    # proposed, accepted, corrected, rejected or superseded
    state: str
    reviewed_at: datetime | None = None
    # The reviewed proposal it was offered as a fact through; null until it was.
    proposal_mutation_set_id: str | None = None
    created_at: datetime
    # Whether it is about the picture's newest version.
    current: bool


class PictureModel(BaseModel):
    asset_id: str
    name: str
    version: PictureVersionModel
    versions: list[PictureVersionModel]
    width: int | None = None
    height: int | None = None
    links: list[PictureLinkModel]
    observations: list[ObservationModel]
    # send, describe or leave_out
    context_choice: ContextChoice
    context_choice_words: str
    # The accepted words about the newest version, given in place of the picture.
    description: str
    # The story's revision, to send with a change to where it is shown.
    revision: int


class DescribeEstimateModel(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    # Whether the assistant in use can see pictures; when false, describing is refused.
    sees_pictures: bool
    reason: str | None = None


class ObservationReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: ReviewDecision
    corrected_text: str | None = Field(default=None, max_length=MAX_OBSERVATION_TEXT * 2)


class ObservationProposeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Who or what it is about, when the model did not say or said wrongly.
    entity_id: str | None = Field(default=None, max_length=200)


class ProposedFactModel(BaseModel):
    observation: ObservationModel
    mutation_id: str


class ContextChoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choice: ContextChoice


class ContextChoiceModel(BaseModel):
    asset_id: str
    choice: ContextChoice
    words: str


def _version(version: Any) -> PictureVersionModel:
    return PictureVersionModel(
        id=version.id,
        version=version.version,
        content_type=version.content_type,
        byte_length=version.byte_length,
        source=version.source,
        licence=version.licence,
        attribution=version.attribution,
        created_at=version.created_at,
    )


def _applies(link: Any) -> str | None:
    if link.applies_from is None:
        return None
    when = parse_world_time(link.applies_from)
    return describe(when) if when is not None else None


def _link(view: LinkView) -> PictureLinkModel:
    link = view.link
    return PictureLinkModel(
        id=link.id,
        role=link.role.value,
        role_words=ROLE_WORDS.get(link.role.value, "a reference"),
        target_record_type=link.target_record_type,
        target_record_id=link.target_record_id,
        target_name=view.target_name,
        target_kind=view.target_kind,
        secret=link.is_secret,
        known_by=list(link.known_by),
        known_by_names=list(view.known_by_names),
        applies_from=_applies(link),
    )


def _observation(
    observation: MediaObservation, *, current_version: str, names: dict[str, str]
) -> ObservationModel:
    region = observation.region
    return ObservationModel(
        id=observation.id,
        asset_version_id=observation.asset_version_id,
        kind=observation.kind.value,
        text=observation.text,
        words=observation.words,
        corrected_text=observation.corrected_text,
        subject_record_id=observation.subject_record_id,
        subject_name=names.get(observation.subject_record_id or ""),
        region=ObservationRegionModel(**region.model_dump()) if region is not None else None,
        confidence=observation.confidence,
        model=observation.model,
        prompt_version=observation.prompt_version,
        job_id=observation.job_id,
        state=observation.state.value,
        reviewed_at=observation.reviewed_at,
        proposal_mutation_set_id=observation.proposal_mutation_set_id,
        created_at=observation.created_at,
        current=observation.asset_version_id == current_version,
    )


def _names(view: PictureView) -> dict[str, str]:
    return {
        item.link.target_record_id: item.target_name
        for item in view.links
        if item.link.target_record_type != "plan_item"
    }


def _picture(view: PictureView) -> PictureModel:
    names = _names(view)
    return PictureModel(
        asset_id=view.asset.id,
        name=view.asset.name,
        version=_version(view.version),
        versions=[_version(version) for version in view.versions],
        width=view.width,
        height=view.height,
        links=[_link(item) for item in view.links],
        observations=[
            _observation(item, current_version=view.version.id, names=names)
            for item in view.observations
        ],
        context_choice=view.choice,
        context_choice_words=CHOICE_WORDS[view.choice],
        description=view.description,
        revision=view.revision,
    )


@router.get("/pictures/{asset_id}")
async def get_picture(
    project_id: str,
    asset_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: MediaObservationService = Depends(get_observations),
) -> PictureModel:
    """The picture inspector: details, where it is shown, what a model saw, the choice."""
    view = await service.picture(scope, scope.workspace_id or "", project_id, asset_id)
    return _picture(view)


@router.get("/pictures/{asset_id}/describe/estimate")
async def describe_estimate(
    project_id: str,
    asset_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: MediaObservationService = Depends(get_observations),
) -> DescribeEstimateModel:
    """What describing the picture would use, before anything is sent."""
    estimate = await service.describe_estimate(
        scope, scope.workspace_id or "", project_id, asset_id
    )
    return DescribeEstimateModel(
        prompt_tokens=estimate.prompt_tokens,
        completion_tokens=estimate.completion_tokens,
        total_tokens=estimate.total,
        sees_pictures=estimate.sees_pictures,
        reason=estimate.reason,
    )


@router.post("/pictures/{asset_id}/describe", status_code=status.HTTP_202_ACCEPTED)
async def describe_picture(
    project_id: str,
    asset_id: str,
    scope: AccessScope = Depends(require_project_scope),
    service: MediaObservationService = Depends(get_observations),
    confirmed: int | None = Depends(confirmed_tokens),
) -> JobModel:
    """Ask the assistant to describe the picture, as a background job (``media_describe``).
    What it sees comes back as observations to accept, correct or reject."""
    job = await service.describe(
        scope, scope.workspace_id or "", project_id, asset_id, confirmed_tokens=confirmed
    )
    return _job(job)


@router.post("/observations/{observation_id}/review")
async def review_observation(
    project_id: str,
    observation_id: str,
    body: ObservationReviewRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: MediaObservationService = Depends(get_observations),
) -> ObservationModel:
    """Accept, correct or reject what a model saw in a picture. The story is unchanged."""
    observation = await service.review(
        scope,
        scope.workspace_id or "",
        project_id,
        observation_id,
        body.decision,
        corrected_text=body.corrected_text,
    )
    return _observation(observation, current_version=observation.asset_version_id, names={})


@router.post("/observations/{observation_id}/propose", status_code=status.HTTP_201_CREATED)
async def propose_observation(
    project_id: str,
    observation_id: str,
    body: ObservationProposeRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: MediaObservationService = Depends(get_observations),
) -> ProposedFactModel:
    """Offer an accepted appearance detail as a fact about its entry. It goes to the
    review queue as a suggestion from the assistant and changes nothing until approved."""
    observation, mutation_id = await service.propose_fact(
        scope,
        scope.workspace_id or "",
        project_id,
        observation_id,
        entity_id=body.entity_id,
    )
    return ProposedFactModel(
        observation=_observation(
            observation, current_version=observation.asset_version_id, names={}
        ),
        mutation_id=mutation_id,
    )


class LinkChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # A secret picture reaches a point of view only when that character knows it.
    secret: bool = False
    known_by: list[str] = Field(default_factory=list, max_length=50)
    # A story time (as the story-time API takes it) from which the link applies.
    applies_from: dict[str, Any] | None = None
    expected_revision: int = Field(ge=0)


@router.put("/pictures/{asset_id}/links/{link_id}")
async def change_picture_link(
    project_id: str,
    asset_id: str,
    link_id: str,
    body: LinkChangeRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: MediaObservationService = Depends(get_observations),
) -> PictureModel:
    """Say who may know what the picture shows here, and from when it applies (AST-004).
    The link is replaced by a new one in one change; the story's history keeps both."""
    link = await service.change_link(
        scope,
        scope.workspace_id or "",
        project_id,
        asset_id,
        link_id,
        secret=body.secret,
        known_by=body.known_by,
        applies_from=body.applies_from,
        expected_revision=body.expected_revision,
    )
    response.headers["X-Link-Id"] = link.id
    view = await service.picture(scope, scope.workspace_id or "", project_id, asset_id)
    return _picture(view)


@router.put("/pictures/{asset_id}/context")
async def set_context_choice(
    project_id: str,
    asset_id: str,
    body: ContextChoiceRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: MediaObservationService = Depends(get_observations),
) -> ContextChoiceModel:
    """Send the picture, use its description, or leave it out of the assistant's context."""
    choice = await service.set_choice(
        scope, scope.workspace_id or "", project_id, asset_id, body.choice
    )
    return ContextChoiceModel(asset_id=asset_id, choice=choice, words=CHOICE_WORDS[choice])


__all__ = ["router"]
