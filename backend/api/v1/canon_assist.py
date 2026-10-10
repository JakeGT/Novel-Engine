"""Assisted creation API: describe something, answer a few questions, review the draft.

The endpoints that call a model spend the workspace's own provider budget. A
*proposal* is a model-origin mutation set in the review queue; canon changes only
when a reviewer commits it through ``/mutations``.

Each conversation is kept (``/assist/sessions``): while open, its author can pick
it up again; once it has led to a proposal it is the evidence behind that proposal.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import require_project_scope
from backend.api.dependencies import (
    get_assist_session_service,
    get_canon_character_workflow,
    get_credential_service,
    get_settings,
    get_usage_service,
)
from backend.api.v1.canon_characters import (
    CharacterAnswerModel,
    CharacterProposalModel,
    CharacterQuestionModel,
    DiscardedItemModel,
    ProposalIssueModel,
)
from backend.api.v1.router import _provider_context
from backend.core.config import Settings
from backend.domain.assist import AssistAnswer, AssistKind, AssistQuestion, AssistSession
from backend.domain.identity import AccessScope
from backend.services.assist_session_service import AssistSessionService
from backend.services.canon_change_workflow import MAX_INSTRUCTION_LENGTH, CanonChangeWorkflow
from backend.services.canon_character_workflow import MAX_ANSWERS, CharacterAnswer
from backend.services.canon_creation_workflow import (
    MAX_DESCRIPTION_LENGTH,
    CanonCreationWorkflow,
    CreationBrief,
)
from backend.services.credential_service import CredentialService
from backend.services.usage_service import ProviderUsageService

router = APIRouter(prefix="/projects/{project_id}/assist", tags=["canon-assist"])


class AssistBriefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)
    # Optional: a brief may describe one named thing or a whole idea.
    entity_type: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=200)
    # Set to fill in an entry that already exists instead of adding a new one.
    entity_id: str | None = Field(default=None, max_length=200)

    def brief(self) -> CreationBrief:
        return CreationBrief(
            description=self.description,
            entity_type=self.entity_type,
            name=self.name,
            entity_id=self.entity_id,
        )


class AssistQuestionSheetModel(BaseModel):
    # The conversation these questions belong to; send it back with the answers.
    session_id: str
    questions: list[CharacterQuestionModel]


class AssistProposalRequest(AssistBriefRequest):
    # The open conversation this continues, when questions were asked first.
    session_id: str | None = Field(default=None, max_length=80)
    # Answers are optional: a draft can be made from the description alone.
    answers: list[CharacterAnswerModel] = Field(default_factory=list, max_length=MAX_ANSWERS)
    expected_revision: int = Field(ge=0)


@router.post("/questions", response_model=AssistQuestionSheetModel)
async def assist_questions(
    project_id: str,
    body: AssistBriefRequest,
    workflow: CanonCreationWorkflow = Depends(get_canon_character_workflow),
    sessions: AssistSessionService = Depends(get_assist_session_service),
    credentials: CredentialService = Depends(get_credential_service),
    usage: ProviderUsageService = Depends(get_usage_service),
    settings: Settings = Depends(get_settings),
    scope: AccessScope = Depends(require_project_scope),
) -> AssistQuestionSheetModel:
    credential_id, api_key, provider_base_url, usage_sink = await _provider_context(
        settings=settings, credentials=credentials, usage=usage, scope=scope
    )
    sheet = await workflow.brief_questions(
        scope,
        scope.workspace_id or "",
        project_id,
        body.brief(),
        credential=api_key,
        provider_base_url=provider_base_url,
        usage_sink=usage_sink,
    )
    if credential_id:
        await credentials.mark_used(scope.workspace_id or "", credential_id)
    session = await sessions.begin(
        scope,
        scope.workspace_id or "",
        project_id,
        kind=_brief_kind(body),
        description=body.description,
        entity_type=body.entity_type,
        name=body.name,
        entity_id=body.entity_id,
        questions=[
            AssistQuestion(id=item.id, question=item.question, category=item.category)
            for item in sheet.questions
        ],
    )
    return AssistQuestionSheetModel(
        session_id=session.id,
        questions=[
            CharacterQuestionModel(id=item.id, question=item.question, category=item.category)
            for item in sheet.questions
        ],
    )


@router.post("/proposals", response_model=CharacterProposalModel, status_code=201)
async def assist_proposal(
    project_id: str,
    body: AssistProposalRequest,
    workflow: CanonCreationWorkflow = Depends(get_canon_character_workflow),
    sessions: AssistSessionService = Depends(get_assist_session_service),
    credentials: CredentialService = Depends(get_credential_service),
    usage: ProviderUsageService = Depends(get_usage_service),
    settings: Settings = Depends(get_settings),
    scope: AccessScope = Depends(require_project_scope),
) -> CharacterProposalModel:
    credential_id, api_key, provider_base_url, usage_sink = await _provider_context(
        settings=settings, credentials=credentials, usage=usage, scope=scope
    )
    # Kept before the model is called: if drafting fails, the answers are not lost.
    session = await sessions.begin(
        scope,
        scope.workspace_id or "",
        project_id,
        kind=_brief_kind(body),
        session_id=body.session_id,
        description=body.description,
        entity_type=body.entity_type,
        name=body.name,
        entity_id=body.entity_id,
        answers=[
            AssistAnswer(
                question_id=answer.question_id, question=answer.question, answer=answer.answer
            )
            for answer in body.answers
        ],
    )
    result = await workflow.propose_from_brief(
        scope,
        scope.workspace_id or "",
        project_id,
        body.brief(),
        answers=[
            CharacterAnswer(
                question_id=answer.question_id, question=answer.question, answer=answer.answer
            )
            for answer in body.answers
        ],
        expected_revision=body.expected_revision,
        session_id=session.id,
        credential=api_key,
        provider_base_url=provider_base_url,
        usage_sink=usage_sink,
    )
    if credential_id:
        await credentials.mark_used(scope.workspace_id or "", credential_id)
    await sessions.proposed(
        scope, scope.workspace_id or "", project_id, session.id, mutation_id=result.mutation.id
    )
    return CharacterProposalModel(
        mutation_id=result.mutation.id,
        status=result.mutation.status.value,
        operation_count=result.operation_count,
        session_id=result.session_id,
        discarded=[
            DiscardedItemModel(kind=item.kind, reason=item.reason) for item in result.discarded
        ],
        validation_ok=result.validation.ok,
        validation_issues=[
            ProposalIssueModel(
                ordinal=issue.ordinal,
                code=issue.code,
                message=issue.message,
                severity=issue.severity,
            )
            for issue in result.validation.issues
        ],
    )


class ChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # What should change, in the author's words.
    instruction: str = Field(min_length=1, max_length=MAX_INSTRUCTION_LENGTH)
    # The entry the author is looking at, if any, so "she" and "here" can be resolved.
    entity_id: str | None = Field(default=None, max_length=200)
    # The open conversation this continues, when the assistant asked something first.
    session_id: str | None = Field(default=None, max_length=80)
    expected_revision: int = Field(ge=0)


class ChangeProposalModel(BaseModel):
    # Null when the assistant asked questions instead of proposing; nothing was created.
    mutation_id: str | None
    status: str | None
    operation_count: int
    session_id: str
    # One sentence restating what will change.
    understood: str
    # Asked instead of guessing when the instruction was ambiguous.
    questions: list[str]
    # Other things in the story this affects, for the author to consider.
    notes: list[str]
    discarded: list[DiscardedItemModel]
    validation_ok: bool
    validation_issues: list[ProposalIssueModel]


@router.post("/changes", response_model=ChangeProposalModel)
async def assist_change(
    project_id: str,
    body: ChangeRequest,
    workflow: CanonChangeWorkflow = Depends(get_canon_character_workflow),
    sessions: AssistSessionService = Depends(get_assist_session_service),
    credentials: CredentialService = Depends(get_credential_service),
    usage: ProviderUsageService = Depends(get_usage_service),
    settings: Settings = Depends(get_settings),
    scope: AccessScope = Depends(require_project_scope),
) -> ChangeProposalModel:
    """The God Hand: one instruction, every change it implies, as a proposal for review."""
    credential_id, api_key, provider_base_url, usage_sink = await _provider_context(
        settings=settings, credentials=credentials, usage=usage, scope=scope
    )
    if body.session_id is not None:
        # Refuses a conversation that is finished or is not this author's.
        await sessions.begin(
            scope,
            scope.workspace_id or "",
            project_id,
            kind=AssistKind.CHANGE,
            session_id=body.session_id,
            description=body.instruction,
            entity_id=body.entity_id,
        )
    result = await workflow.change_by_instruction(
        scope,
        scope.workspace_id or "",
        project_id,
        instruction=body.instruction,
        focus_entity_id=body.entity_id,
        expected_revision=body.expected_revision,
        session_id=body.session_id,
        credential=api_key,
        provider_base_url=provider_base_url,
        usage_sink=usage_sink,
    )
    if credential_id:
        await credentials.mark_used(scope.workspace_id or "", credential_id)
    # Kept either way: with questions it stays open to be picked up again; with a
    # proposal it becomes the evidence behind it.
    session = await sessions.begin(
        scope,
        scope.workspace_id or "",
        project_id,
        kind=AssistKind.CHANGE,
        session_id=body.session_id,
        description=body.instruction,
        entity_id=body.entity_id,
        questions=[
            AssistQuestion(id=f"q{index + 1}", question=question)
            for index, question in enumerate(result.questions)
        ],
        understood=result.understood,
        notes=result.notes,
    )
    if result.mutation is not None:
        await sessions.proposed(
            scope,
            scope.workspace_id or "",
            project_id,
            session.id,
            mutation_id=result.mutation.id,
        )
    issues = result.validation.issues if result.validation is not None else []
    return ChangeProposalModel(
        mutation_id=result.mutation.id if result.mutation is not None else None,
        status=result.mutation.status.value if result.mutation is not None else None,
        operation_count=result.operation_count,
        session_id=session.id,
        understood=result.understood,
        questions=result.questions,
        notes=result.notes,
        discarded=[
            DiscardedItemModel(kind=item.kind, reason=item.reason) for item in result.discarded
        ],
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
    )


def _brief_kind(body: AssistBriefRequest) -> AssistKind:
    return AssistKind.FILL_IN if body.entity_id else AssistKind.CREATE


class AssistSessionModel(BaseModel):
    id: str
    kind: str
    status: str
    actor_user_id: str
    # The author's description, or their instruction for a change.
    description: str
    entity_type: str | None
    name: str | None
    entity_id: str | None
    questions: list[CharacterQuestionModel]
    answers: list[CharacterAnswerModel]
    understood: str
    notes: list[str]
    mutation_id: str | None
    created_at: datetime
    updated_at: datetime


class AssistSessionListModel(BaseModel):
    items: list[AssistSessionModel]


class SaveAnswersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[CharacterAnswerModel] = Field(default_factory=list, max_length=MAX_ANSWERS)


def _session(session: AssistSession) -> AssistSessionModel:
    return AssistSessionModel(
        id=session.id,
        kind=session.kind.value,
        status=session.status.value,
        actor_user_id=session.actor_user_id,
        description=session.description,
        entity_type=session.entity_type,
        name=session.name,
        entity_id=session.entity_id,
        questions=[
            CharacterQuestionModel(id=item.id, question=item.question, category=item.category)
            for item in session.questions
        ],
        answers=[
            CharacterAnswerModel(
                question_id=item.question_id, question=item.question, answer=item.answer
            )
            for item in session.answers
        ],
        understood=session.understood,
        notes=session.notes,
        mutation_id=session.mutation_id,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.get("/sessions", response_model=AssistSessionListModel)
async def list_assist_sessions(
    project_id: str,
    mutation_id: str | None = Query(default=None, max_length=200),
    sessions: AssistSessionService = Depends(get_assist_session_service),
    scope: AccessScope = Depends(require_project_scope),
) -> AssistSessionListModel:
    """The caller's unfinished conversations; or, with ``mutation_id``, the
    conversation that set of suggested changes came from."""
    workspace_id = scope.workspace_id or ""
    if mutation_id is not None:
        found = await sessions.for_mutation(scope, workspace_id, project_id, mutation_id)
        return AssistSessionListModel(items=[_session(found)] if found is not None else [])
    return AssistSessionListModel(
        items=[_session(item) for item in await sessions.list_open(scope, workspace_id, project_id)]
    )


@router.get("/sessions/{session_id}", response_model=AssistSessionModel)
async def get_assist_session(
    project_id: str,
    session_id: str,
    sessions: AssistSessionService = Depends(get_assist_session_service),
    scope: AccessScope = Depends(require_project_scope),
) -> AssistSessionModel:
    return _session(await sessions.get(scope, scope.workspace_id or "", project_id, session_id))


@router.put("/sessions/{session_id}/answers", response_model=AssistSessionModel)
async def save_assist_answers(
    project_id: str,
    session_id: str,
    body: SaveAnswersRequest,
    sessions: AssistSessionService = Depends(get_assist_session_service),
    scope: AccessScope = Depends(require_project_scope),
) -> AssistSessionModel:
    """Keep the answers typed so far. Calls no model and changes nothing in the story."""
    return _session(
        await sessions.save_answers(
            scope,
            scope.workspace_id or "",
            project_id,
            session_id,
            [
                AssistAnswer(
                    question_id=answer.question_id, question=answer.question, answer=answer.answer
                )
                for answer in body.answers
            ],
        )
    )


@router.post("/sessions/{session_id}/close", response_model=AssistSessionModel)
async def close_assist_session(
    project_id: str,
    session_id: str,
    sessions: AssistSessionService = Depends(get_assist_session_service),
    scope: AccessScope = Depends(require_project_scope),
) -> AssistSessionModel:
    """Put an unfinished conversation away."""
    return _session(await sessions.close(scope, scope.workspace_id or "", project_id, session_id))
