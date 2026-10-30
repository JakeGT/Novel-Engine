"""Who knows what, telling, claims, trust, and what the reader is shown (ADR-020)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_container
from backend.api.v1.canon_assertions import (
    BeliefModel,
    EventModel,
    FactModel,
    PropositionRequest,
    _belief_model,
    _event_model,
    _fact_model,
)
from backend.container import Container
from backend.domain.errors import ValidationError
from backend.domain.identity import AccessScope
from backend.domain.trust import Stance, TrustPolicy
from backend.domain.worldtime import WorldTime
from backend.services.knowledge_service import KnowledgeService, LearnedView, Telling

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


class PremiseModel(BaseModel):
    id: str
    # "fact", "belief", or "gone" when it is no longer in the story.
    kind: Literal["fact", "belief", "gone"]
    sentence: str


class LearnedModel(BaseModel):
    """How and when a character came to hold one belief (KNW-002)."""

    belief_id: str
    how: Literal["told", "overheard", "saw", "worked_out", "heard_said", "event", "recorded"]
    # The same, in plain words: "Told by Mara", "Saw it: The fire at the docks".
    words: str
    event_id: str | None = None
    # Who told them or said it.
    source_id: str | None = None
    scene_id: str | None = None
    scene_title: str | None = None
    # The story time it was learned, in the story's words; null when not placed in time.
    when: str | None = None
    # What it was worked out from, for something worked out.
    premises: list[PremiseModel] = []
    # The fact it agrees with, when the story says it is so.
    fact_id: str | None = None


class CharacterKnowledgeModel(BaseModel):
    entity_id: str
    knows: list[KnownFactModel]
    wrong: list[WrongBeliefModel]
    unconfirmed: list[BeliefModel]
    unaware: list[FactModel]
    unrecorded: int
    # How and when each of their beliefs was learned.
    learned: list[LearnedModel] = []


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
    # Where and when it is told, so what the hearers learn can be placed (KNW-002).
    scene_id: str | None = Field(default=None, max_length=200)
    when: WorldTime | None = None


class ProposedBeliefModel(BaseModel):
    holder_id: str
    how: Literal["told", "overheard", "saw"]
    # How firmly they would believe it; null when no belief is proposed.
    confidence: float | None
    # Why, in plain words: their trust, or that they saw it.
    why: str
    already: bool = False


class TellResult(BaseModel):
    """A telling or a witnessed event, waiting in review with a belief for each hearer.

    Nothing is in the story until the proposal is approved."""

    mutation_id: str
    event_id: str
    beliefs: int
    already_knew: list[str]
    proposals: list[ProposedBeliefModel]


class WitnessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # What happens, in the author's words.
    name: str = Field(min_length=1, max_length=200)
    # Who is there; each perceives it unless listed in not_noticed.
    present_ids: list[str] = Field(min_length=1, max_length=12)
    not_noticed: list[str] = Field(default_factory=list, max_length=12)
    # What it shows them: an existing fact, or a statement of its own.
    fact_id: str | None = None
    proposition: PropositionRequest | None = None
    expected_revision: int
    scene_id: str | None = Field(default=None, max_length=200)
    when: WorldTime | None = None


class InferRequest(BaseModel):
    """A character works something out from facts and their own beliefs (KNW-002)."""

    model_config = ConfigDict(extra="forbid")
    holder_id: str
    # What they work out: an existing fact, or a statement of its own (which may be wrong).
    fact_id: str | None = None
    proposition: PropositionRequest | None = None
    # The facts and the holder's own beliefs it was worked out from.
    premises: list[str] = Field(min_length=1, max_length=12)
    confidence: float = Field(default=1.0, gt=0, le=1)
    scene_id: str | None = Field(default=None, max_length=200)
    when: WorldTime | None = None
    expected_revision: int


class InferResult(BaseModel):
    revision: int
    event_id: str


class TrustModel(BaseModel):
    """How a character takes what others tell them."""

    entity_id: str
    default: Stance
    speakers: dict[str, Stance]


class TrustRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default: Stance = "believes"
    speakers: dict[str, Stance] = Field(default_factory=dict, max_length=50)
    expected_revision: int


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposition: PropositionRequest
    modality: Literal["states", "suggests", "wonders", "denies"] = "states"
    # Who says it; leave out for the narration itself.
    speaker_id: str | None = None
    # The speaker is narrating (perhaps unreliably) rather than speaking in a scene.
    narrates: bool = False
    perceiver_ids: list[str] = Field(default_factory=list, max_length=12)
    scene_id: str | None = Field(default=None, max_length=200)
    # Propose, for review, what each hearer comes to believe by their trust in the speaker.
    hearers_believe: bool = False
    expected_revision: int


class ClaimModel(BaseModel):
    """Something said in the story. Not necessarily true; never counted as a fact."""

    event: EventModel
    speaker_id: str | None
    # "speaker" (a character says it) or "narrator" (a character narrates it); null for the
    # narration itself.
    speaker_role: Literal["speaker", "narrator"] | None
    perceiver_ids: list[str]
    # What the facts say about it.
    standing: Literal["borne_out", "contradicted", "unconfirmed"]


class ClaimList(BaseModel):
    items: list[ClaimModel]


class RecordedClaimModel(BaseModel):
    revision: int
    event_id: str
    # What the hearers would believe, waiting in review; null unless asked for.
    beliefs: TellResult | None = None


class PromoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int


class PromotionModel(BaseModel):
    """A proposal to make a claim a fact, waiting in review."""

    mutation_id: str


def _telling(telling: Telling) -> TellResult:
    return TellResult(
        mutation_id=telling.mutation_id,
        event_id=telling.event_id,
        beliefs=telling.beliefs,
        already_knew=telling.already_knew,
        proposals=[
            ProposedBeliefModel(
                holder_id=item.holder_id,
                how=item.how,
                confidence=item.confidence,
                why=item.why,
                already=item.already,
            )
            for item in telling.proposals
        ],
    )


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
    # Which part of the guide this is (QRY-002).
    part: Literal["everything", "characters", "places", "timeline", "recap"] = "everything"


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
        learned=[
            _learned_model(view)
            for view in await service.learned(
                scope, scope.workspace_id or "", project_id, entity_id
            )
        ],
    )


def _learned_model(view: LearnedView) -> LearnedModel:
    learned = view.learned
    return LearnedModel(
        belief_id=learned.belief_id,
        how=learned.how,
        words=view.words,
        event_id=learned.event_id,
        source_id=learned.source_id,
        scene_id=learned.scene_id,
        scene_title=view.scene_title,
        when=view.when,
        premises=[
            PremiseModel(id=item.id, kind=item.kind, sentence=item.sentence)  # type: ignore[arg-type]
            for item in view.premises
        ],
        fact_id=view.fact_id,
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
    scope: AccessScope = Depends(require_project_scope),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> TellResult:
    """Propose a telling: the event, and what each hearer comes to believe by their trust.

    It waits in review; nothing is in the story until it is approved."""
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
        scene_id=body.scene_id,
        when=body.when,
    )
    return _telling(telling)


@router.post("/knowledge/witness", status_code=status.HTTP_201_CREATED)
async def witness(
    project_id: str,
    body: WitnessRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> TellResult:
    """Propose something seen: the event, and a belief for each person who notices it."""
    if (body.fact_id is None) == (body.proposition is None):
        raise ValidationError("Say what they see: one fact, or a statement of its own.")
    if body.fact_id is not None:
        known = await service.fact(scope, scope.workspace_id or "", project_id, body.fact_id)
        proposition = known.fact.proposition
    else:
        assert body.proposition is not None
        proposition = body.proposition.to_proposition()
    telling = await service.witness(
        scope,
        scope.workspace_id or "",
        project_id,
        name=body.name,
        present_ids=body.present_ids,
        not_noticed=body.not_noticed,
        proposition=proposition,
        expected_revision=body.expected_revision,
        scene_id=body.scene_id,
        when=body.when,
    )
    return _telling(telling)


@router.post("/knowledge/infer", status_code=status.HTTP_201_CREATED)
async def infer(
    project_id: str,
    body: InferRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> InferResult:
    """Record that a character worked something out, and from what. Saved at once: it is
    the author's own change, like recording a claim."""
    if (body.fact_id is None) == (body.proposition is None):
        raise ValidationError("Say what they work out: one fact, or a statement of its own.")
    if body.fact_id is not None:
        known = await service.fact(scope, scope.workspace_id or "", project_id, body.fact_id)
        proposition = known.fact.proposition
    else:
        assert body.proposition is not None
        proposition = body.proposition.to_proposition()
    result, event_id = await service.infer(
        scope,
        scope.workspace_id or "",
        project_id,
        holder_id=body.holder_id,
        proposition=proposition,
        premises=body.premises,
        scene_id=body.scene_id,
        when=body.when,
        confidence=body.confidence,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return InferResult(revision=result.revision, event_id=event_id)


@router.get("/knowledge/characters/{entity_id}/trust")
async def character_trust(
    project_id: str,
    entity_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> TrustModel:
    policy = await service.trust(scope, scope.workspace_id or "", project_id, entity_id)
    return TrustModel(entity_id=entity_id, default=policy.default, speakers=policy.speakers)


@router.put("/knowledge/characters/{entity_id}/trust")
async def set_character_trust(
    project_id: str,
    entity_id: str,
    body: TrustRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> TrustModel:
    """Say how a character takes what others tell them, in general and from particular people."""
    result, policy = await service.set_trust(
        scope,
        scope.workspace_id or "",
        project_id,
        entity_id,
        policy=TrustPolicy(default=body.default, speakers=dict(body.speakers)),
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return TrustModel(entity_id=entity_id, default=policy.default, speakers=policy.speakers)


@router.get("/knowledge/claims")
async def list_claims(
    project_id: str,
    entity_id: str | None = Query(default=None, max_length=200),
    scene_id: str | None = Query(default=None, max_length=200),
    scope: AccessScope = Depends(get_project_scope),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> ClaimList:
    """What is said in the story: about or by an entry, or in a scene. Never as facts."""
    found = await service.claims(
        scope, scope.workspace_id or "", project_id, entity_id=entity_id, scene_id=scene_id
    )
    return ClaimList(
        items=[
            ClaimModel(
                event=_event_model(record.event),
                speaker_id=record.speaker_id,
                speaker_role=record.speaker_role,
                perceiver_ids=record.perceiver_ids,
                standing=record.standing,
            )
            for record in found
        ]
    )


@router.post("/knowledge/claims", status_code=status.HTTP_201_CREATED)
async def record_claim(
    project_id: str,
    body: ClaimRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> RecordedClaimModel:
    """Record something said, by the narration, a character or a narrator, as a claim."""
    recorded = await service.record_claim(
        scope,
        scope.workspace_id or "",
        project_id,
        proposition=body.proposition.to_proposition(),
        modality=body.modality,
        speaker_id=body.speaker_id,
        narrates=body.narrates,
        perceiver_ids=body.perceiver_ids,
        scene_id=body.scene_id,
        hearers_believe=body.hearers_believe,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{recorded.result.revision}"'
    return RecordedClaimModel(
        revision=recorded.result.revision,
        event_id=recorded.event_id,
        beliefs=_telling(recorded.beliefs) if recorded.beliefs is not None else None,
    )


@router.post("/knowledge/claims/{event_id}/promote", status_code=status.HTTP_201_CREATED)
async def promote_claim(
    project_id: str,
    event_id: str,
    body: PromoteRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> PromotionModel:
    """Propose making what a claim says a fact. It waits in review."""
    promotion = await service.promote_claim(
        scope,
        scope.workspace_id or "",
        project_id,
        event_id,
        expected_revision=body.expected_revision,
    )
    return PromotionModel(mutation_id=promotion.mutation_id)


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
    part: Literal["everything", "characters", "places", "timeline", "recap"] = Query(
        default="everything"
    ),
    scope: AccessScope = Depends(get_project_scope),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> GuideModel:
    """The story bible a reader could see after a scene, as the story stood at a version.

    Without ``version`` it is the latest one; the guide always names the version it shows.
    ``part`` makes one part of it on its own: the characters, the places, a timeline of
    the scenes read so far and what happened in them, or a recap scene by scene.
    """
    guide = await service.guide(
        scope,
        scope.workspace_id or "",
        project_id,
        up_to_scene=scene,
        viewpoint=viewpoint,
        version=version,
        part=part,
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
        part=part,
    )
