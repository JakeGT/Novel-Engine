"""Assisted creation API: describe something, answer a few questions, review the draft.

Both endpoints spend the workspace's own provider budget. The second returns a
*proposal* (a model-origin mutation set in the review queue) holding the drafted
entries and the links between them and existing canon. Canon changes only when a
reviewer commits it through ``/mutations``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import require_project_scope
from backend.api.dependencies import (
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
from backend.domain.identity import AccessScope
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
    questions: list[CharacterQuestionModel]


class AssistProposalRequest(AssistBriefRequest):
    # Answers are optional: a draft can be made from the description alone.
    answers: list[CharacterAnswerModel] = Field(default_factory=list, max_length=MAX_ANSWERS)
    expected_revision: int = Field(ge=0)


@router.post("/questions", response_model=AssistQuestionSheetModel)
async def assist_questions(
    project_id: str,
    body: AssistBriefRequest,
    workflow: CanonCreationWorkflow = Depends(get_canon_character_workflow),
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
    return AssistQuestionSheetModel(
        questions=[
            CharacterQuestionModel(id=item.id, question=item.question, category=item.category)
            for item in sheet.questions
        ]
    )


@router.post("/proposals", response_model=CharacterProposalModel, status_code=201)
async def assist_proposal(
    project_id: str,
    body: AssistProposalRequest,
    workflow: CanonCreationWorkflow = Depends(get_canon_character_workflow),
    credentials: CredentialService = Depends(get_credential_service),
    usage: ProviderUsageService = Depends(get_usage_service),
    settings: Settings = Depends(get_settings),
    scope: AccessScope = Depends(require_project_scope),
) -> CharacterProposalModel:
    credential_id, api_key, provider_base_url, usage_sink = await _provider_context(
        settings=settings, credentials=credentials, usage=usage, scope=scope
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
        credential=api_key,
        provider_base_url=provider_base_url,
        usage_sink=usage_sink,
    )
    if credential_id:
        await credentials.mark_used(scope.workspace_id or "", credential_id)
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
    credentials: CredentialService = Depends(get_credential_service),
    usage: ProviderUsageService = Depends(get_usage_service),
    settings: Settings = Depends(get_settings),
    scope: AccessScope = Depends(require_project_scope),
) -> ChangeProposalModel:
    """The God Hand: one instruction, every change it implies, as a proposal for review."""
    credential_id, api_key, provider_base_url, usage_sink = await _provider_context(
        settings=settings, credentials=credentials, usage=usage, scope=scope
    )
    result = await workflow.change_by_instruction(
        scope,
        scope.workspace_id or "",
        project_id,
        instruction=body.instruction,
        focus_entity_id=body.entity_id,
        expected_revision=body.expected_revision,
        credential=api_key,
        provider_base_url=provider_base_url,
        usage_sink=usage_sink,
    )
    if credential_id:
        await credentials.mark_used(scope.workspace_id or "", credential_id)
    issues = result.validation.issues if result.validation is not None else []
    return ChangeProposalModel(
        mutation_id=result.mutation.id if result.mutation is not None else None,
        status=result.mutation.status.value if result.mutation is not None else None,
        operation_count=result.operation_count,
        session_id=result.session_id,
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
