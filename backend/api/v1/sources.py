"""Texts an author brings in, the names found in them, and what each phrase means."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_container, get_source_service
from backend.container import Container
from backend.domain.identity import AccessScope
from backend.domain.sources import (
    MAX_PHRASE_LENGTH,
    MAX_SOURCE_LENGTH,
    MAX_TITLE_LENGTH,
    AliasBinding,
    SourceKind,
    SourceText,
    Verdict,
)
from backend.services.source_service import SourceReading, SourceService

router = APIRouter(prefix="/projects/{project_id}", tags=["texts"])


class TextModel(BaseModel):
    id: str
    title: str
    # paste or markdown.
    kind: str
    created_at: datetime
    versions: int
    latest_version_id: str | None
    # Characters in the latest version.
    length: int
    # For a scene's text: the scene it belongs to, written in its studio (ADR-022).
    scene_id: str | None = None


class TextListModel(BaseModel):
    items: list[TextModel]


class AddTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    content: str = Field(min_length=1, max_length=MAX_SOURCE_LENGTH + 10_000)
    kind: SourceKind = SourceKind.PASTE


class ReviseTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # New words become a new version; earlier versions stay exactly as they were.
    content: str | None = Field(default=None, max_length=MAX_SOURCE_LENGTH + 10_000)
    title: str | None = Field(default=None, max_length=MAX_TITLE_LENGTH)


class VersionModel(BaseModel):
    id: str
    number: int
    created_at: datetime
    length: int


class SectionModel(BaseModel):
    index: int
    title: str
    start: int
    end: int


class MentionModel(BaseModel):
    start: int
    end: int
    phrase: str
    # True when the author marked it (@Mara, [[the Old Mill]]).
    marked: bool
    # known, unclear (more than one entry answers to it) or unknown.
    status: str
    entity_ids: list[str]


class NameQuestionModel(BaseModel):
    phrase: str
    count: int
    first_start: int
    first_end: int
    candidates: list[str]
    # True when the candidates are the assistant's guess, not a matching name.
    guessed: bool


class TextDetailModel(BaseModel):
    text: TextModel
    version: VersionModel
    versions: list[VersionModel]
    content: str
    sections: list[SectionModel]
    mentions: list[MentionModel]
    # Who is this? One per phrase, most frequent first.
    questions: list[NameQuestionModel]
    # Entry id → name, for every entry mentioned above.
    names: dict[str, str]


class PassageModel(BaseModel):
    source_id: str
    title: str
    version_number: int | None
    before: str
    quote: str
    after: str


class NameDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phrase: str = Field(min_length=1, max_length=MAX_PHRASE_LENGTH)
    # is, is_not or ignore (not a name at all).
    verdict: Verdict
    entity_id: str | None = Field(default=None, max_length=200)
    # Set to keep the answer to one text; leave out to remember it everywhere.
    source_id: str | None = Field(default=None, max_length=200)


class NameDecisionModel(BaseModel):
    id: str
    phrase: str
    verdict: str
    entity_id: str | None
    source_id: str | None
    decided_at: datetime


class NameMemoryModel(BaseModel):
    items: list[NameDecisionModel]
    names: dict[str, str]


class AddEntryFromTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phrase: str = Field(min_length=1, max_length=MAX_PHRASE_LENGTH)
    entity_type: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0)


class AddedEntryModel(BaseModel):
    id: str
    name: str
    type: str
    revision: int


def _text(source: SourceText, scenes: dict[str, str] | None = None) -> TextModel:
    return TextModel(
        scene_id=(scenes or {}).get(source.id),
        id=source.id,
        title=source.title,
        kind=source.kind.value,
        created_at=source.created_at,
        versions=source.versions,
        latest_version_id=source.latest_version_id,
        length=source.length,
    )


def _decision(binding: AliasBinding) -> NameDecisionModel:
    return NameDecisionModel(
        id=binding.id,
        phrase=binding.phrase,
        verdict=binding.verdict.value,
        entity_id=binding.entity_id,
        source_id=binding.source_id,
        decided_at=binding.decided_at,
    )


def _detail(reading: SourceReading, scenes: dict[str, str] | None = None) -> TextDetailModel:
    def version(item: object) -> VersionModel:
        return VersionModel(
            id=item.id,  # type: ignore[attr-defined]
            number=item.number,  # type: ignore[attr-defined]
            created_at=item.created_at,  # type: ignore[attr-defined]
            length=len(item.content),  # type: ignore[attr-defined]
        )

    return TextDetailModel(
        text=_text(reading.source, scenes),
        version=version(reading.version),
        versions=[version(item) for item in reading.versions],
        content=reading.version.content,
        sections=[
            SectionModel(index=item.index, title=item.title, start=item.start, end=item.end)
            for item in reading.sections
        ],
        mentions=[
            MentionModel(
                start=item.start,
                end=item.end,
                phrase=item.phrase,
                marked=item.marked,
                status=item.status.value,
                entity_ids=list(item.entity_ids),
            )
            for item in reading.mentions
        ],
        questions=[
            NameQuestionModel(
                phrase=item.phrase,
                count=item.count,
                first_start=item.first_start,
                first_end=item.first_end,
                candidates=item.candidates,
                guessed=item.guessed,
            )
            for item in reading.questions
        ],
        names=reading.names,
    )


@router.get("/texts", response_model=TextListModel)
async def list_texts(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: SourceService = Depends(get_source_service),
    container: Container = Depends(get_container),
) -> TextListModel:
    items = await service.texts(scope, scope.workspace_id or "", project_id)
    scenes = await container.studio_service.scene_texts(scope.workspace_id or "", project_id)
    # A scene's text from another line of the story is not this line's writing.
    shown = [item for item in items if item.kind is not SourceKind.SCENE or item.id in scenes]
    return TextListModel(items=[_text(item, scenes) for item in shown])


@router.post("/texts", response_model=TextModel, status_code=201)
async def add_text(
    project_id: str,
    body: AddTextRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: SourceService = Depends(get_source_service),
) -> TextModel:
    source = await service.add(
        scope,
        scope.workspace_id or "",
        project_id,
        title=body.title,
        content=body.content,
        kind=body.kind,
    )
    return _text(source)


@router.get("/texts/passage", response_model=PassageModel)
async def read_passage(
    project_id: str,
    version_id: str = Query(min_length=1, max_length=200),
    location: str | None = Query(default=None, max_length=120),
    scope: AccessScope = Depends(get_project_scope),
    service: SourceService = Depends(get_source_service),
) -> PassageModel:
    """The words a kept change points back to."""
    found = await service.evidence(
        scope, scope.workspace_id or "", project_id, version_id, location
    )
    passage = found.passage
    return PassageModel(
        source_id=found.source_id,
        title=found.title,
        version_number=found.version_number,
        before=passage.before if passage else "",
        quote=passage.quote if passage else "",
        after=passage.after if passage else "",
    )


@router.get("/texts/{source_id}", response_model=TextDetailModel)
async def read_text(
    project_id: str,
    source_id: str,
    version_id: str | None = Query(default=None, max_length=200),
    scope: AccessScope = Depends(get_project_scope),
    service: SourceService = Depends(get_source_service),
    container: Container = Depends(get_container),
) -> TextDetailModel:
    reading = await service.read(
        scope, scope.workspace_id or "", project_id, source_id, version_id=version_id
    )
    scenes = await container.studio_service.scene_texts(scope.workspace_id or "", project_id)
    return _detail(reading, scenes)


@router.patch("/texts/{source_id}", response_model=TextModel)
async def revise_text(
    project_id: str,
    source_id: str,
    body: ReviseTextRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: SourceService = Depends(get_source_service),
) -> TextModel:
    source = await service.revise(
        scope,
        scope.workspace_id or "",
        project_id,
        source_id,
        content=body.content,
        title=body.title,
    )
    return _text(source)


@router.delete("/texts/{source_id}", status_code=204)
async def remove_text(
    project_id: str,
    source_id: str,
    scope: AccessScope = Depends(require_project_scope),
    service: SourceService = Depends(get_source_service),
) -> Response:
    await service.remove(scope, scope.workspace_id or "", project_id, source_id)
    return Response(status_code=204)


@router.post("/texts/{source_id}/entries", response_model=AddedEntryModel, status_code=201)
async def add_entry_from_text(
    project_id: str,
    source_id: str,
    body: AddEntryFromTextRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: SourceService = Depends(get_source_service),
) -> AddedEntryModel:
    """Add a name the text uses as a new entry, pointing back at where it first appears."""
    entity = await service.add_entry(
        scope,
        scope.workspace_id or "",
        project_id,
        source_id,
        phrase=body.phrase,
        entity_type=body.entity_type,
        expected_revision=body.expected_revision,
    )
    return AddedEntryModel(
        id=entity.id, name=entity.name, type=entity.type, revision=entity.created_revision
    )


@router.get("/names", response_model=NameMemoryModel)
async def list_name_memory(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: SourceService = Depends(get_source_service),
) -> NameMemoryModel:
    bindings, names = await service.bindings(scope, scope.workspace_id or "", project_id)
    return NameMemoryModel(items=[_decision(item) for item in bindings], names=names)


@router.post("/names", response_model=NameDecisionModel, status_code=201)
async def decide_name(
    project_id: str,
    body: NameDecisionRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: SourceService = Depends(get_source_service),
) -> NameDecisionModel:
    binding = await service.decide(
        scope,
        scope.workspace_id or "",
        project_id,
        phrase=body.phrase,
        verdict=body.verdict,
        entity_id=body.entity_id,
        source_id=body.source_id,
    )
    return _decision(binding)


@router.delete("/names/{binding_id}", status_code=204)
async def forget_name(
    project_id: str,
    binding_id: str,
    scope: AccessScope = Depends(require_project_scope),
    service: SourceService = Depends(get_source_service),
) -> Response:
    await service.forget(scope, scope.workspace_id or "", project_id, binding_id)
    return Response(status_code=204)
