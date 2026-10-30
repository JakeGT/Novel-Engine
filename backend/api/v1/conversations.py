"""The assistant's kept threads, starting from a premise, and how suggestions fared.

- ``/assist/threads/{kind}``: the caller's own running conversation of a kind ("change"
  for Change anything, "premise" for starting from a premise), and clearing it to start
  again. Nobody else's thread is ever shown.
- ``/assist/premise``: one round of starting from a premise (AST-EXP-005). Spends the
  workspace's own provider allowance; the result is open questions and a set of
  suggested entries to review. Nothing enters the story without review.
- ``/assist/runs``: each run of suggestions in the line the author works in, with how
  many were suggested, how many the author kept once decided (precision, AST-EXP-007;
  those a keep-for-me rule kept are counted apart) and how many quote the author's own
  material (groundedness, AST-EXP-013). Counts only.
"""

from __future__ import annotations

import contextlib
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import (
    get_assist_session_service,
    get_canon_character_workflow,
    get_conversation_service,
    get_credential_service,
    get_settings,
    get_usage_service,
)
from backend.api.v1.assistant_settings import (
    confirmed_tokens,
    get_assist_budget,
    get_assistant_settings,
)
from backend.api.v1.canon_assist import (
    SchemaSuggestionModel,
    _suggestions,
    reading,
)
from backend.api.v1.canon_characters import DiscardedItemModel, ProposalIssueModel
from backend.api.v1.router import _provider_context
from backend.core.config import Settings
from backend.domain.assist import AssistAnswer, AssistKind, AssistQuestion
from backend.domain.conversations import (
    RunKind,
    SuggestionRun,
    Thread,
    ThreadKind,
    TurnRole,
    recall_text,
    recalled,
)
from backend.domain.errors import ValidationError
from backend.domain.identity import AccessScope
from backend.domain.prompts import prompt_version
from backend.domain.usage_controls import require_within_conversation
from backend.services.assist_session_service import AssistSessionService
from backend.services.assistant_settings_service import AssistantSettingsService, AssistBudget
from backend.services.canon_creation_workflow import (
    MAX_PREMISE_ANSWERS,
    MAX_PREMISE_LENGTH,
    CanonCreationWorkflow,
)
from backend.services.conversation_service import (
    ConversationService,
    assistant_turn,
    author_turn,
)
from backend.services.credential_service import CredentialService
from backend.services.usage_service import ProviderUsageService

router = APIRouter(prefix="/projects/{project_id}/assist", tags=["canon-assist"])


class TurnAnswerModel(BaseModel):
    question: str
    answer: str


class TurnModel(BaseModel):
    position: int
    # author or assistant.
    role: str
    # What the author said, or the assistant's restatement of it.
    body: str
    questions: list[str]
    answers: list[TurnAnswerModel]
    notes: list[str]
    # The set of suggested changes the turn led to, if any, and how many changes it held.
    mutation_id: str | None
    operation_count: int
    created_at: datetime | None


class ThreadModel(BaseModel):
    # change or premise.
    kind: str
    # Null when there is no thread yet.
    id: str | None
    turns: list[TurnModel]
    # The questions from the last reply that are still to be answered (premise only).
    open_questions: list[str] = Field(default_factory=list)
    # What the thread's replies used so far, as the provider reported it.
    tokens_used: int = 0
    updated_at: datetime | None = None


def _thread(kind: ThreadKind, thread: Thread | None) -> ThreadModel:
    if thread is None:
        return ThreadModel(kind=kind.value, id=None, turns=[])
    last = next((turn for turn in reversed(thread.turns) if turn.role is TurnRole.ASSISTANT), None)
    return ThreadModel(
        kind=kind.value,
        id=thread.id,
        turns=[
            TurnModel(
                position=turn.position,
                role=turn.role.value,
                body=turn.body,
                questions=turn.questions,
                answers=[TurnAnswerModel(**item.model_dump()) for item in turn.answers],
                notes=turn.notes,
                mutation_id=turn.mutation_id,
                operation_count=turn.operation_count,
                created_at=turn.created_at,
            )
            for turn in thread.turns
        ],
        open_questions=list(last.questions) if last is not None else [],
        tokens_used=thread.tokens_used,
        updated_at=thread.updated_at,
    )


def _kind(kind: str) -> ThreadKind:
    try:
        return ThreadKind(kind)
    except ValueError as error:
        raise ValidationError("There is no conversation of that kind.") from error


@router.get("/threads/{kind}", response_model=ThreadModel)
async def get_thread(
    project_id: str,
    kind: str,
    threads: ConversationService = Depends(get_conversation_service),
    scope: AccessScope = Depends(get_project_scope),
) -> ThreadModel:
    """The caller's own kept conversation of this kind, in the line they work in."""
    which = _kind(kind)
    return _thread(which, await threads.thread(scope, scope.workspace_id or "", project_id, which))


class ClearedModel(BaseModel):
    # False when there was nothing to clear.
    cleared: bool


@router.delete("/threads/{kind}", response_model=ClearedModel)
async def clear_thread(
    project_id: str,
    kind: str,
    threads: ConversationService = Depends(get_conversation_service),
    sessions: AssistSessionService = Depends(get_assist_session_service),
    scope: AccessScope = Depends(require_project_scope),
) -> ClearedModel:
    """Erase the caller's kept conversation and start again. Suggested changes it led to
    stay in review, and saved ones stay in the story; the words its conversations kept
    (the premise, the instructions, the answers) are erased with it."""
    which = _kind(kind)
    workspace_id = scope.workspace_id or ""
    thread = await threads.thread(scope, workspace_id, project_id, which)
    if thread is not None:
        await sessions.erase_words(scope, workspace_id, project_id, thread.session_ids())
    return ClearedModel(cleared=await threads.clear(scope, workspace_id, project_id, which))


class SuggestionRunModel(BaseModel):
    id: str
    # links, plan_draft, plan_next or premise.
    kind: str
    # meaning (candidates found by search by meaning first) or listing.
    source: str
    suggested: int
    # How many the author kept, once the suggestions were decided; null until then.
    kept: int | None
    # How many a keep-for-me rule kept without the author deciding; null when none did.
    # Never counted as kept by the author.
    kept_for_you: int | None = None
    # Kept by the author out of suggested, 0 to 1; null until decided, and null when a
    # rule kept them.
    precision: float | None
    # How many quote the author's material, and how many are the assistant's own.
    grounded: int | None
    invented: int | None
    groundedness: float | None
    decided: bool
    mutation_id: str | None
    prompt_version: str | None
    created_at: datetime
    # Counts only: candidates found by meaning, suggestions among them, items left out.
    details: dict[str, int]


class SuggestionRunListModel(BaseModel):
    items: list[SuggestionRunModel]


def _run(run: SuggestionRun) -> SuggestionRunModel:
    return SuggestionRunModel(
        id=run.id,
        kind=run.kind.value,
        source=run.source.value,
        suggested=run.suggested,
        kept=run.kept,
        kept_for_you=run.kept_for_you,
        precision=run.precision,
        grounded=run.grounded,
        invented=run.invented,
        groundedness=run.groundedness,
        decided=run.settled_at is not None,
        mutation_id=run.mutation_id,
        prompt_version=run.prompt_version,
        created_at=run.created_at,
        details=run.details,
    )


@router.get("/runs", response_model=SuggestionRunListModel)
async def list_runs(
    project_id: str,
    kind: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=10, ge=1, le=50),
    runs: ConversationService = Depends(get_conversation_service),
    scope: AccessScope = Depends(get_project_scope),
) -> SuggestionRunListModel:
    """Recent runs of suggestions, newest first, each with what became of it."""
    which: RunKind | None = None
    if kind is not None:
        try:
            which = RunKind(kind)
        except ValueError as error:
            raise ValidationError("There are no suggestions of that kind.") from error
    return SuggestionRunListModel(
        items=[
            _run(run)
            for run in await runs.runs(
                scope, scope.workspace_id or "", project_id, kind=which, limit=limit
            )
        ]
    )


class PremiseAnswerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(default="", max_length=2000)


class PremiseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The author's story in their own words. Needed to start; later, more to add to it.
    premise: str = Field(default="", max_length=MAX_PREMISE_LENGTH)
    # Answers to the assistant's open questions; an empty answer skips that question.
    answers: list[PremiseAnswerModel] = Field(default_factory=list, max_length=MAX_PREMISE_ANSWERS)
    expected_revision: int = Field(ge=0)


class PremiseRoundModel(BaseModel):
    # The kept thread this round belongs to.
    thread_id: str
    understood: str
    # What the assistant still needs to know, to answer in turn.
    questions: list[str]
    # Set when the round suggested entries to review; null when it only asked.
    mutation_id: str | None
    status: str | None
    operation_count: int
    discarded: list[DiscardedItemModel]
    suggestions: list[SchemaSuggestionModel] = Field(default_factory=list)
    validation_ok: bool
    validation_issues: list[ProposalIssueModel]
    # The conversation kept as the evidence behind the suggested entries.
    session_id: str | None


@router.post("/premise", response_model=PremiseRoundModel)
async def premise_round(
    project_id: str,
    body: PremiseRequest,
    workflow: CanonCreationWorkflow = Depends(get_canon_character_workflow),
    threads: ConversationService = Depends(get_conversation_service),
    sessions: AssistSessionService = Depends(get_assist_session_service),
    credentials: CredentialService = Depends(get_credential_service),
    usage: ProviderUsageService = Depends(get_usage_service),
    settings: Settings = Depends(get_settings),
    controls: AssistantSettingsService = Depends(get_assistant_settings),
    scope: AccessScope = Depends(require_project_scope),
    budget: AssistBudget = Depends(get_assist_budget),
    confirmed: int | None = Depends(confirmed_tokens),
) -> PremiseRoundModel:
    """One round of starting from a premise: open questions, and entries to review."""
    workspace_id = scope.workspace_id or ""
    thread = await threads.thread(scope, workspace_id, project_id, ThreadKind.PREMISE)
    told = [
        turn.body
        for turn in (thread.turns if thread is not None else [])
        if turn.role is TurnRole.AUTHOR and turn.body
    ]
    added = body.premise.strip()
    premise = "\n\n".join([*told, *([added] if added else [])])
    if not premise:
        raise ValidationError("Describe your story first.")
    answers = [(item.question.strip(), item.answer.strip()) for item in body.answers]
    answered = [(q, a) for q, a in answers if q and a]
    if thread is not None and not added and not answered:
        raise ValidationError("Answer a question, or say more about your story.")
    guard = await budget.check(
        scope,
        workspace_id,
        project_id,
        reads=reading(workflow, scope, project_id, "entry"),
        doing="Reading your story",
        confirmed_tokens=confirmed,
    )
    # The whole premise conversation is one conversation for its budget.
    limits = await controls.limits(workspace_id, project_id)
    if thread is not None and guard.estimated_tokens:
        require_within_conversation(
            used=thread.tokens_used,
            estimated_tokens=guard.estimated_tokens,
            budget=limits.conversation_tokens,
        )
    credential_id, api_key, provider_base_url, usage_sink = await _provider_context(
        settings=settings, credentials=credentials, usage=usage, scope=scope
    )
    earlier = recall_text(recalled(thread.turns)) if thread is not None else ""
    # The evidence behind any suggested entries: the story and the answers. Begun first,
    # so the suggestions cite the conversation that is kept, by its own id.
    answers_kept = [
        AssistAnswer(question_id=f"a{index + 1}", question=q, answer=a)
        for index, (q, a) in enumerate(answered)
    ]
    session = await sessions.begin(
        scope,
        workspace_id,
        project_id,
        kind=AssistKind.CREATE,
        description=premise[:MAX_PREMISE_LENGTH],
        answers=answers_kept,
    )
    result: Any = None
    try:
        result = await workflow.premise_round(
            scope,
            workspace_id,
            project_id,
            premise=premise,
            answers=answered,
            expected_revision=body.expected_revision,
            earlier=earlier or None,
            waiting=thread.mutation_ids() if thread is not None else [],
            session_id=session.id,
            credential=api_key,
            provider_base_url=provider_base_url,
            usage_sink=guard.counting(usage_sink),
        )
    except Exception:
        # Nothing came of it: the words are not kept.
        with contextlib.suppress(Exception):
            await sessions.close(scope, workspace_id, project_id, session.id)
        raise
    finally:
        spent = guard.used
        if thread is not None:
            await threads.count_tokens(thread.id, workspace_id, project_id, spent)
    if credential_id:
        await credentials.mark_used(workspace_id, credential_id)
    session_id: str | None = None
    if result.mutation is None:
        # Only questions: the thread keeps them, so this conversation is not kept.
        await sessions.close(scope, workspace_id, project_id, session.id)
    else:
        session = await sessions.begin(
            scope,
            workspace_id,
            project_id,
            kind=AssistKind.CREATE,
            session_id=session.id,
            description=premise[:MAX_PREMISE_LENGTH],
            questions=[
                AssistQuestion(id=f"q{index + 1}", question=question)
                for index, question in enumerate(result.questions)
            ],
            answers=answers_kept,
            understood=result.understood,
        )
        session_id = session.id
        await sessions.proposed(
            scope,
            workspace_id,
            project_id,
            session.id,
            mutation_id=result.mutation.id,
            suggestions=result.suggestions,
        )
    recorded = await threads.record(
        scope,
        workspace_id,
        project_id,
        ThreadKind.PREMISE,
        [
            author_turn(added, answered),
            assistant_turn(
                result.understood,
                questions=result.questions,
                notes=[item.reason for item in result.discarded][:8],
                mutation_id=result.mutation.id if result.mutation is not None else None,
                operation_count=result.operation_count,
                session_id=session_id,
            ),
        ],
    )
    if thread is None:
        await threads.count_tokens(recorded.id, workspace_id, project_id, spent)
    if result.mutation is not None:
        await threads.record_run(
            scope,
            workspace_id,
            project_id,
            kind=RunKind.PREMISE,
            suggested=result.operation_count,
            mutation_id=result.mutation.id,
            prompt_version=prompt_version("premise-round"),
            details={"questions": len(result.questions), "discarded": len(result.discarded)},
        )
    issues = result.validation.issues if result.validation is not None else []
    return PremiseRoundModel(
        thread_id=recorded.id,
        understood=result.understood,
        questions=result.questions,
        mutation_id=result.mutation.id if result.mutation is not None else None,
        status=result.mutation.status.value if result.mutation is not None else None,
        operation_count=result.operation_count,
        discarded=[
            DiscardedItemModel(kind=item.kind, reason=item.reason) for item in result.discarded
        ],
        suggestions=_suggestions(result.suggestions),
        validation_ok=result.validation.ok if result.validation is not None else True,
        validation_issues=[
            ProposalIssueModel(
                ordinal=issue.ordinal,
                code=issue.code,
                message=issue.message,
                severity=issue.severity,
            )
            for issue in issues
        ],
        session_id=session_id,
    )
