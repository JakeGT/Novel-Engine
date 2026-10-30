"""Having the assistant read a passage of the author's text into suggested changes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import require_project_scope
from backend.api.dependencies import (
    get_canon_character_workflow,
    get_credential_service,
    get_settings,
    get_source_service,
    get_usage_service,
)
from backend.api.v1.assistant_settings import confirmed_tokens, get_assist_budget
from backend.api.v1.canon_assist import ContextUsedModel, DiscardedItemModel, context_used
from backend.api.v1.router import _provider_context
from backend.core.config import Settings
from backend.domain.branching import working_branch_id
from backend.domain.errors import ValidationError
from backend.domain.identity import AccessScope
from backend.domain.sources import Verdict, find_mentions
from backend.services.assistant_settings_service import AssistBudget
from backend.services.credential_service import CredentialService
from backend.services.intake_workflow import MAX_PASSAGE, IntakeWorkflow
from backend.services.scene_reading import passage_tokens
from backend.services.source_service import SourceService
from backend.services.usage_service import ProviderUsageService

router = APIRouter(prefix="/projects/{project_id}/assist/texts", tags=["texts-assist"])


class ReadPassageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str | None = Field(default=None, max_length=200)
    # Where to read from and to, in characters. A long stretch is read a part at a time.
    start: int = Field(default=0, ge=0)
    end: int | None = Field(default=None, ge=1)
    expected_revision: int = Field(ge=0)
    # The scene of the plan this text is the prose of, when the author says so (EXT-001):
    # the reading then also suggests the steps of the plan's threads it shows, as one
    # change to that scene's plan per passage read.
    scene_id: str | None = Field(default=None, max_length=200)


class MissingModel(BaseModel):
    # What the story bible has no way to record yet, in the assistant's words.
    name: str
    reason: str


class ReadPassageModel(BaseModel):
    # Null when the passage held nothing new.
    mutation_id: str | None
    operation_count: int
    validation_ok: bool
    # What was actually read, and whether the text goes on past it.
    start: int
    end: int
    more: bool
    # How many suggested changes the passage implies rather than states.
    implied: int
    # New "who is this?" questions the assistant raised. Never acted on unasked.
    guesses: int
    discarded: list[DiscardedItemModel]
    missing: list[MissingModel]
    # How many of the suggested changes are events, and steps of the plan's threads (a
    # scene's reading, or a text read as a scene's prose); they wait for review with the
    # rest (EXT-001).
    events: int = 0
    thread_steps: int = 0
    # How many are changes of state (a thing held or lost, a place reached or left, a
    # status or a relationship beginning or ending), and new threads a scene opens.
    state_changes: int = 0
    new_threads: int = 0
    # What part of the story bible the assistant was shown (CTX-002).
    context: ContextUsedModel | None = None
    # The longest stretch read in one go.
    limit: int = MAX_PASSAGE


async def _check_scene_text(
    sources: SourceService, workspace_id: str, project_id: str, source_id: str, scene_id: str
) -> None:
    """A text read as a scene's prose is that scene's own text in this line of the story,
    or a text no scene holds (pasted). A scene's text in another line (a what-if's own
    words, or the main story's once a what-if has its own) is read there, never here."""
    studio: Any = sources.studio
    if studio is None:
        return
    holder = (await studio.scene_texts(workspace_id, project_id)).get(source_id)
    if holder is not None:
        if holder != scene_id:
            raise ValidationError(
                "This text is the prose of another scene of the plan; read it as that scene."
            )
        return
    branch_id = await studio._branch_id(workspace_id, project_id)  # noqa: SLF001
    if await studio._store.linked_elsewhere(  # noqa: SLF001
        workspace_id=workspace_id, project_id=project_id, branch_id=branch_id, source_id=source_id
    ):
        raise ValidationError(
            "This text is a scene's prose in another line of the story; read it there."
        )


@router.post("/{source_id}/read", response_model=ReadPassageModel)
async def read_passage_with_assistant(
    project_id: str,
    source_id: str,
    body: ReadPassageRequest,
    workflow: IntakeWorkflow = Depends(get_canon_character_workflow),
    sources: SourceService = Depends(get_source_service),
    credentials: CredentialService = Depends(get_credential_service),
    usage: ProviderUsageService = Depends(get_usage_service),
    settings: Settings = Depends(get_settings),
    scope: AccessScope = Depends(require_project_scope),
    budget: AssistBudget = Depends(get_assist_budget),
    confirmed: int | None = Depends(confirmed_tokens),
) -> ReadPassageModel:
    workspace_id = scope.workspace_id or ""
    # Reading the text is authorized here, before any provider is contacted.
    reading = await sources.read(
        scope, workspace_id, project_id, source_id, version_id=body.version_id
    )
    scene = (
        await workflow.scene_for_reading(workspace_id, project_id, body.scene_id)
        if body.scene_id
        else None
    )
    if scene is not None:
        await _check_scene_text(sources, workspace_id, project_id, source_id, scene.id)
    # Names are read as that scene reads them, when the text is its prose (CTX-016).
    index, entities, _ = await sources.index_for(
        workspace_id, project_id, source_id, scene_id=scene.id if scene else None
    )
    length = len(reading.version.content)
    stretch = max(0, min(body.end or length, length, body.start + MAX_PASSAGE) - body.start)

    async def reads() -> int:
        return passage_tokens(stretch)

    await budget.check(
        scope,
        workspace_id,
        project_id,
        reads=reads,
        doing="Reading this passage",
        confirmed_tokens=confirmed,
    )
    credential_id, api_key, provider_base_url, usage_sink = await _provider_context(
        settings=settings, credentials=credentials, usage=usage, scope=scope
    )
    result = await workflow.read_passage(
        scope,
        workspace_id,
        project_id,
        version=reading.version,
        entities=entities,
        mentions=find_mentions(reading.version.content, index),
        index=index,
        start=body.start,
        end=body.end,
        expected_revision=body.expected_revision,
        credential=api_key,
        provider_base_url=provider_base_url,
        usage_sink=usage_sink,
        scene=scene,
    )
    if credential_id:
        await credentials.mark_used(workspace_id, credential_id)
    # A guess is kept only as a question for this text; it resolves nothing.
    for guess in result.guesses:
        await sources.remember(
            workspace_id,
            project_id,
            phrase=guess.phrase,
            verdict=Verdict.SUGGESTED,
            entity_id=guess.entity_id,
            source_id=source_id,
            actor_user_id=None,
            # A guess made reading in a what-if stays in that what-if.
            branch_id=working_branch_id(),
        )
    return ReadPassageModel(
        mutation_id=result.mutation.id if result.mutation is not None else None,
        operation_count=result.operation_count,
        validation_ok=result.validation.ok if result.validation is not None else True,
        start=result.start,
        end=result.end,
        more=result.more,
        implied=result.implied,
        guesses=len(result.guesses),
        discarded=[
            DiscardedItemModel(kind=item.kind, reason=item.reason) for item in result.discarded
        ],
        missing=[
            MissingModel(name=item.name, reason=item.reason or item.description)
            for item in result.suggestions
        ],
        events=result.events,
        thread_steps=result.thread_steps,
        state_changes=result.state_changes,
        new_threads=result.new_threads,
        context=context_used(result.context),
    )
