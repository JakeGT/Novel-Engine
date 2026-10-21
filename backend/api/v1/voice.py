"""The author's voice in numbers, and proposals from findings (CON-018, CON-010).

Voice is worked out from the words with no model: a scene against the author's usual,
the whole book scene by scene, and which scenes count towards the usual. A proposal
from a finding is drafted by the assistant, its cost shown first, and waits for review.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_container
from backend.api.v1.assistant_settings import confirmed_tokens
from backend.container import Container
from backend.domain.identity import AccessScope
from backend.domain.voice import (
    BANDS,
    MIN_BASELINE_SCENES,
    MIN_BASELINE_WORDS,
    MIN_SCENE_WORDS,
    VOICE_VERSION,
    Baseline,
    VoiceNote,
    VoiceStats,
)
from backend.services.finding_proposals import FindingProposalService
from backend.services.voice_service import SceneVoice, Usual, VoiceService

router = APIRouter(prefix="/projects/{project_id}", tags=["voice"])


def get_voice(container: Container = Depends(get_container)) -> VoiceService:
    return container.voice_service  # type: ignore[no-any-return]


def get_finding_proposals(
    container: Container = Depends(get_container),
) -> FindingProposalService:
    return container.finding_proposals  # type: ignore[no-any-return]


class EchoModel(BaseModel):
    word: str
    times: int
    start: int
    end: int


class VoiceStatsModel(BaseModel):
    words: int
    sentences: int
    # Whether the words read as English: the measures that need English words are null
    # (or "unclear") otherwise.
    english: bool
    sentence_mean: float
    sentence_median: float
    sentence_long: float
    sentence_spread: float
    # How many sentences fall in each band of ``band_labels``.
    bands: list[int]
    dialogue_share: float
    adverb_rate: float | None
    adverbs: list[str]
    echoes: list[EchoModel]
    # first, second, third or unclear.
    person: str
    # past, present, mixed or unclear.
    tense: str
    past_share: float | None


class VoiceNoteModel(BaseModel):
    code: str
    message: str
    start: int | None = None
    end: int | None = None


class UsualModel(BaseModel):
    """The author's usual, and what it was made from."""

    enough: bool
    scenes: int
    words: int
    # Only scenes the author wrote themselves (no kept drafts) were used.
    own_only: bool
    drafts_left_out: int
    left_out: list[str]
    sentence_mean: float | None
    dialogue_share: float | None
    adverb_rate: float | None
    person: str
    tense: str
    needs_scenes: int = MIN_BASELINE_SCENES
    needs_words: int = MIN_BASELINE_WORDS


class SceneVoiceModel(BaseModel):
    plan_id: str
    title: str
    version_id: str | None
    version_number: int | None
    stats: VoiceStatsModel | None
    usual: UsualModel
    notes: list[VoiceNoteModel]
    counted: bool
    left_out: bool
    not_compared: str | None
    band_labels: list[str]
    min_words: int = MIN_SCENE_WORDS
    measures_version: str = VOICE_VERSION


class BookSceneModel(BaseModel):
    plan_id: str
    title: str
    version_number: int
    own: bool
    stats: VoiceStatsModel
    notes: list[VoiceNoteModel]


class BookVoiceModel(BaseModel):
    scenes: list[BookSceneModel]
    usual: UsualModel
    unwritten: int
    stopped: bool
    band_labels: list[str]


def band_labels() -> list[str]:
    return [f"{low}–{high} words" if high else f"{low} or more" for low, high in BANDS]


def stats_model(stats: VoiceStats) -> VoiceStatsModel:
    return VoiceStatsModel(
        words=stats.words,
        sentences=stats.sentences,
        english=stats.english,
        sentence_mean=stats.sentence_mean,
        sentence_median=stats.sentence_median,
        sentence_long=stats.sentence_long,
        sentence_spread=stats.sentence_spread,
        bands=list(stats.bands),
        dialogue_share=stats.dialogue_share,
        adverb_rate=stats.adverb_rate,
        adverbs=[word for word, _ in stats.adverbs],
        echoes=[
            EchoModel(word=item.word, times=item.times, start=item.start, end=item.end)
            for item in stats.echoes
        ],
        person=stats.person,
        tense=stats.tense,
        past_share=stats.past_share,
    )


def note_model(note: VoiceNote) -> VoiceNoteModel:
    return VoiceNoteModel(code=note.code, message=note.message, start=note.start, end=note.end)


def _middle(baseline: Baseline, name: str) -> float | None:
    spread = getattr(baseline, name)
    return round(spread.middle, 3) if spread is not None else None


def usual_model(usual: Usual) -> UsualModel:
    baseline = usual.baseline
    return UsualModel(
        enough=usual.enough,
        scenes=baseline.scenes,
        words=baseline.words,
        own_only=usual.own_only,
        drafts_left_out=usual.drafts_left_out,
        left_out=list(usual.left_out),
        sentence_mean=_middle(baseline, "sentence_mean"),
        dialogue_share=_middle(baseline, "dialogue_share"),
        adverb_rate=_middle(baseline, "adverb_rate"),
        person=baseline.person,
        tense=baseline.tense,
    )


def scene_model(found: SceneVoice) -> SceneVoiceModel:
    reading = found.reading
    return SceneVoiceModel(
        plan_id=found.scene.id,
        title=found.scene.title,
        version_id=reading.version_id if reading else None,
        version_number=reading.version_number if reading else None,
        stats=stats_model(reading.stats) if reading else None,
        usual=usual_model(found.usual),
        notes=[note_model(note) for note in found.notes],
        counted=found.counted,
        left_out=found.left_out,
        not_compared=found.not_compared,
        band_labels=band_labels(),
    )


@router.get("/plan/{plan_id}/voice")
async def scene_voice(
    project_id: str,
    plan_id: str,
    scope: AccessScope = Depends(get_project_scope),
    voice: VoiceService = Depends(get_voice),
) -> SceneVoiceModel:
    """How the scene's saved words sound against the author's usual. Calls no model."""
    return scene_model(await voice.scene(scope, scope.workspace_id or "", project_id, plan_id))


class CountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # True leaves the scene out of the author's usual; false counts it again.
    left_out: bool


@router.put("/plan/{plan_id}/voice/usual")
async def count_scene(
    project_id: str,
    plan_id: str,
    body: CountRequest,
    scope: AccessScope = Depends(require_project_scope),
    voice: VoiceService = Depends(get_voice),
) -> SceneVoiceModel:
    """Leave the scene out of the author's usual voice, or count it again."""
    await voice.leave_out(
        scope, scope.workspace_id or "", project_id, plan_id, left_out=body.left_out
    )
    return scene_model(await voice.scene(scope, scope.workspace_id or "", project_id, plan_id))


@router.get("/voice/book")
async def book_voice(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    voice: VoiceService = Depends(get_voice),
) -> BookVoiceModel:
    """Every written scene's voice in reading order, against the author's usual."""
    found = await voice.book(scope, scope.workspace_id or "", project_id)
    return BookVoiceModel(
        scenes=[
            BookSceneModel(
                plan_id=reading.plan_id,
                title=reading.title,
                version_number=reading.version_number,
                own=reading.own,
                stats=stats_model(reading.stats),
                notes=[note_model(note) for note in notes],
            )
            for reading, notes in found.rows
        ],
        usual=usual_model(found.usual),
        unwritten=found.unwritten,
        stopped=found.stopped,
        band_labels=band_labels(),
    )


# --------------------------------------------------------------- finding proposals


class FindingRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # A check without the assistant (by its key) or a note from the assistant (by its id).
    kind: Literal["check", "note"]
    key: str = Field(min_length=1, max_length=200)


class FindingProposalRequest(FindingRef):
    # make_true: add what the passage says. change_bible: change the story bible's records
    # the finding is about so they agree with the passage.
    choice: Literal["make_true", "change_bible"]


class FindingProposalEstimateModel(BaseModel):
    total_tokens: int
    # Whether the finding points at a passage ("Make this true" needs one).
    has_passage: bool


class FindingProposalModel(BaseModel):
    # Null when the assistant asked questions instead; nothing was created then.
    mutation_id: str | None
    understood: str
    questions: list[str]
    notes: list[str]
    operation_count: int


@router.post("/plan/{plan_id}/findings/proposal-estimate")
async def finding_proposal_estimate(
    project_id: str,
    plan_id: str,
    body: FindingRef,
    scope: AccessScope = Depends(require_project_scope),
    proposals: FindingProposalService = Depends(get_finding_proposals),
) -> FindingProposalEstimateModel:
    """What drafting a story-bible change from this finding would use. Calls no model."""
    found = await proposals.find(
        scope, scope.workspace_id or "", project_id, plan_id, kind=body.kind, key=body.key
    )
    return FindingProposalEstimateModel(
        total_tokens=await proposals.estimate(scope, scope.workspace_id or "", project_id),
        has_passage=bool(found.passage),
    )


@router.post("/plan/{plan_id}/findings/proposal")
async def finding_proposal(
    project_id: str,
    plan_id: str,
    body: FindingProposalRequest,
    scope: AccessScope = Depends(require_project_scope),
    proposals: FindingProposalService = Depends(get_finding_proposals),
    confirmed: int | None = Depends(confirmed_tokens),
) -> FindingProposalModel:
    """Turn a finding into a story-bible change for review: make what the passage says true,
    or change the story bible's records instead. Above the size the author asked to confirm,
    refused with ``409 needs_confirmation`` until confirmed. Nothing changes until approved."""
    result = await proposals.propose(
        scope,
        scope.workspace_id or "",
        project_id,
        plan_id,
        kind=body.kind,
        key=body.key,
        choice=body.choice,
        confirmed_tokens=confirmed,
    )
    return FindingProposalModel(
        mutation_id=result.mutation.id if result.mutation is not None else None,
        understood=result.understood,
        questions=list(result.questions),
        notes=list(result.notes),
        operation_count=result.operation_count,
    )


__all__ = ["router"]
