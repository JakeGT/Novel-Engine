"""Who knows what, telling, and what the reader is shown (ADR-020)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_container
from backend.api.v1.canon_assertions import (
    BeliefModel,
    FactModel,
    PropositionRequest,
    _belief_model,
    _fact_model,
)
from backend.container import Container
from backend.domain.errors import ValidationError
from backend.domain.identity import AccessScope
from backend.services.knowledge_service import KnowledgeService

router = APIRouter(prefix="/projects/{project_id}", tags=["knowledge"])


def get_knowledge_service(container: Container = Depends(get_container)) -> KnowledgeService:
    return container.knowledge_service  # type: ignore[no-any-return]


class KnownFactModel(BaseModel):
    fact: FactModel
    # "believes": they hold a belief that agrees; "everyone": everyone in the story knows it.
    how: str


class WrongBeliefModel(BaseModel):
    belief: BeliefModel
    fact: FactModel


class CharacterKnowledgeModel(BaseModel):
    entity_id: str
    knows: list[KnownFactModel]
    wrong: list[WrongBeliefModel]
    unconfirmed: list[BeliefModel]
    unaware: list[FactModel]
    unrecorded: int


class FactKnowledgeModel(BaseModel):
    fact: FactModel
    everyone: bool
    holders: list[str]
    mistaken: list[str]


class TellRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speaker_id: str
    listener_ids: list[str] = Field(min_length=1, max_length=12)
    overheard_by: list[str] = Field(default_factory=list, max_length=12)
    # What is told: an existing fact, or a statement of its own (which may be false).
    fact_id: str | None = None
    proposition: PropositionRequest | None = None
    expected_revision: int


class TellResult(BaseModel):
    revision: int
    event_id: str
    beliefs: int
    already_knew: list[str]


class ShownModel(BaseModel):
    fact_id: str
    how: str
    # Whether this is the scene where the fact is meant to come out (STY-007).
    main_reveal: bool = False


class ReaderStepModel(BaseModel):
    scene_id: str
    title: str
    number: int
    point_of_view: str | None = None
    shown: list[ShownModel]
    irony: list[str]
    withheld: list[str]
    repeated: list[str]
    flashback: bool


class JourneyModel(BaseModel):
    steps: list[ReaderStepModel]
    facts: dict[str, FactModel]


class GuideEntryModel(BaseModel):
    name: str
    lines: list[str]


class GuideSectionModel(BaseModel):
    heading: str
    entries: list[GuideEntryModel]


class GuideModel(BaseModel):
    """A spoiler-safe guide, bound to what it shows: the line of the story, its version,
    the scene it runs up to and whose knowledge it keeps."""

    title: str
    # The same guide as a Markdown file, with that binding in its header.
    markdown: str
    story_line: str
    version: int
    scene_id: str
    scene_title: str
    scene_number: int
    scene_count: int
    viewpoint_id: str | None
    viewpoint_name: str | None
    # One sentence naming exactly what the guide shows.
    bound_to: str
    leaves_out: str
    sections: list[GuideSectionModel]
    # The version of the rules that decided what is left out.
    rules: int


@router.get("/knowledge/characters/{entity_id}")
async def character_knowledge(
    project_id: str,
    entity_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> CharacterKnowledgeModel:
    known = await service.character(scope, scope.workspace_id or "", project_id, entity_id)
    return CharacterKnowledgeModel(
        entity_id=entity_id,
        knows=[KnownFactModel(fact=_fact_model(fact), how=how) for fact, how in known.knows],
        wrong=[
            WrongBeliefModel(belief=_belief_model(belief), fact=_fact_model(fact))
            for belief, fact in known.wrong
        ],
        unconfirmed=[_belief_model(belief) for belief in known.unconfirmed],
        unaware=[_fact_model(fact) for fact in known.unaware],
        unrecorded=known.unrecorded,
    )


@router.get("/knowledge/facts/{fact_id}")
async def fact_knowledge(
    project_id: str,
    fact_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> FactKnowledgeModel:
    known = await service.fact(scope, scope.workspace_id or "", project_id, fact_id)
    return FactKnowledgeModel(
        fact=_fact_model(known.fact),
        everyone=known.everyone,
        holders=known.holders,
        mistaken=known.mistaken,
    )


@router.post("/knowledge/tell", status_code=status.HTTP_201_CREATED)
async def tell(
    project_id: str,
    body: TellRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> TellResult:
    if (body.fact_id is None) == (body.proposition is None):
        raise ValidationError("Say what is told: one fact, or a statement of its own.")
    if body.fact_id is not None:
        known = await service.fact(scope, scope.workspace_id or "", project_id, body.fact_id)
        proposition = known.fact.proposition
    else:
        assert body.proposition is not None
        proposition = body.proposition.to_proposition()
    telling = await service.tell(
        scope,
        scope.workspace_id or "",
        project_id,
        speaker_id=body.speaker_id,
        listener_ids=body.listener_ids,
        overheard_by=body.overheard_by,
        proposition=proposition,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{telling.result.revision}"'
    return TellResult(
        revision=telling.result.revision,
        event_id=telling.event_id,
        beliefs=telling.beliefs,
        already_knew=telling.already_knew,
    )


@router.get("/reader")
async def reader_journey(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> JourneyModel:
    journey = await service.journey(scope, scope.workspace_id or "", project_id)
    used = {
        fact_id
        for step in journey.steps
        for fact_id in (
            *(reveal.fact_id for reveal in step.shown),
            *step.irony,
            *step.withheld,
        )
    }
    return JourneyModel(
        steps=[
            ReaderStepModel(
                scene_id=step.scene.id,
                title=step.scene.title,
                number=step.number,
                point_of_view=step.point_of_view,
                shown=[
                    ShownModel(fact_id=r.fact_id, how=r.how, main_reveal=r.main_reveal)
                    for r in step.shown
                ],
                irony=step.irony,
                withheld=step.withheld,
                repeated=step.repeated,
                flashback=step.flashback,
            )
            for step in journey.steps
        ],
        facts={
            fact_id: _fact_model(journey.facts[fact_id])
            for fact_id in sorted(used)
            if fact_id in journey.facts
        },
    )


@router.get("/reader/guide")
async def reader_guide(
    project_id: str,
    scene: str = Query(min_length=1, max_length=200),
    viewpoint: str | None = Query(default=None, max_length=200),
    version: int | None = Query(default=None, ge=1),
    scope: AccessScope = Depends(get_project_scope),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> GuideModel:
    """The story bible a reader could see after a scene, as the story stood at a version.

    Without ``version`` it is the latest one; the guide always names the version it shows.
    """
    guide = await service.guide(
        scope,
        scope.workspace_id or "",
        project_id,
        up_to_scene=scene,
        viewpoint=viewpoint,
        version=version,
    )
    return GuideModel(
        title=guide.title,
        markdown=guide.markdown,
        story_line=guide.story_line,
        version=guide.version,
        scene_id=guide.scene_id,
        scene_title=guide.scene_title,
        scene_number=guide.scene_number,
        scene_count=guide.scene_count,
        viewpoint_id=guide.viewpoint_id,
        viewpoint_name=guide.viewpoint_name,
        bound_to=guide.bound_to,
        leaves_out=guide.leaves_out,
        sections=[
            GuideSectionModel(
                heading=section.heading,
                entries=[
                    GuideEntryModel(name=entry.name, lines=entry.lines) for entry in section.entries
                ],
            )
            for section in guide.sections
        ],
        rules=guide.rules,
    )
