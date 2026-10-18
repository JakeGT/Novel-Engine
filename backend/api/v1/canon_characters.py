"""Guided character workflow API.

Both endpoints spend the workspace's own provider budget. The second one
returns a *proposal* (a model-origin mutation set in the review queue); canon
changes only when a reviewer commits it through ``/mutations``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import require_project_scope
from backend.api.dependencies import (
    get_canon_character_workflow,
    get_credential_service,
    get_settings,
    get_usage_service,
)
from backend.api.v1.assistant_settings import confirmed_tokens, get_assist_budget
from backend.api.v1.router import _provider_context
from backend.core.config import Settings
from backend.domain.identity import AccessScope
from backend.services.assistant_settings_service import AssistBudget
from backend.services.canon_character_workflow import (
    MAX_ANSWER_LENGTH,
    MAX_ANSWERS,
    CanonCharacterWorkflow,
    CharacterAnswer,
)
from backend.services.canon_revise_workflow import estimate_reading
from backend.services.credential_service import CredentialService
from backend.services.usage_service import ProviderUsageService

router = APIRouter(prefix="/projects/{project_id}/characters", tags=["canon-characters"])


def _reads(
    workflow: Any, scope: AccessScope, project_id: str, extra: int = 0
) -> Callable[[], Awaitable[int]]:
    """What asking about a character reads, worked out only when a limit asks for it: at
    most the story bible, plus ``extra`` characters the author sent."""

    async def reads() -> int:
        sizes = await estimate_reading(workflow, scope, scope.workspace_id or "", project_id)
        return int(sizes["story"]) + extra // 4

    return reads


class CharacterQuestionModel(BaseModel):
    id: str
    question: str
    category: str


class CharacterQuestionSheetModel(BaseModel):
    entity_id: str
    questions: list[CharacterQuestionModel]


class CharacterAnswerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1, max_length=60)
    question: str = Field(min_length=1, max_length=1000)
    answer: str = Field(min_length=1, max_length=MAX_ANSWER_LENGTH)


class CharacterProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[CharacterAnswerModel] = Field(min_length=1, max_length=MAX_ANSWERS)
    expected_revision: int = Field(ge=0)


class DiscardedItemModel(BaseModel):
    kind: str
    reason: str


class ProposalIssueModel(BaseModel):
    ordinal: int
    code: str
    message: str
    severity: str


class CharacterProposalModel(BaseModel):
    # Review and commit this proposal through /projects/{project_id}/mutations/{mutation_id}.
    mutation_id: str
    status: str
    operation_count: int
    # Evidence source id cited by every proposed operation.
    session_id: str
    # Model output that could not be expressed as a valid typed operation.
    discarded: list[DiscardedItemModel]
    validation_ok: bool
    validation_issues: list[ProposalIssueModel]


@router.post("/{entity_id}/questions", response_model=CharacterQuestionSheetModel)
async def character_questions(
    project_id: str,
    entity_id: str,
    workflow: CanonCharacterWorkflow = Depends(get_canon_character_workflow),
    credentials: CredentialService = Depends(get_credential_service),
    usage: ProviderUsageService = Depends(get_usage_service),
    settings: Settings = Depends(get_settings),
    scope: AccessScope = Depends(require_project_scope),
    budget: AssistBudget = Depends(get_assist_budget),
    confirmed: int | None = Depends(confirmed_tokens),
) -> CharacterQuestionSheetModel:
    await budget.check(
        scope,
        scope.workspace_id or "",
        project_id,
        reads=_reads(workflow, scope, project_id),
        doing="Asking about this character",
        confirmed_tokens=confirmed,
    )
    credential_id, api_key, provider_base_url, usage_sink = await _provider_context(
        settings=settings, credentials=credentials, usage=usage, scope=scope
    )
    sheet = await workflow.generate_questions(
        scope,
        scope.workspace_id or "",
        project_id,
        entity_id,
        credential=api_key,
        provider_base_url=provider_base_url,
        usage_sink=usage_sink,
    )
    if credential_id:
        await credentials.mark_used(scope.workspace_id or "", credential_id)
    return CharacterQuestionSheetModel(
        entity_id=entity_id,
        questions=[
            CharacterQuestionModel(id=item.id, question=item.question, category=item.category)
            for item in sheet.questions
        ],
    )


@router.post("/{entity_id}/proposals", response_model=CharacterProposalModel, status_code=201)
async def character_proposal(
    project_id: str,
    entity_id: str,
    body: CharacterProposalRequest,
    workflow: CanonCharacterWorkflow = Depends(get_canon_character_workflow),
    credentials: CredentialService = Depends(get_credential_service),
    usage: ProviderUsageService = Depends(get_usage_service),
    settings: Settings = Depends(get_settings),
    scope: AccessScope = Depends(require_project_scope),
    budget: AssistBudget = Depends(get_assist_budget),
    confirmed: int | None = Depends(confirmed_tokens),
) -> CharacterProposalModel:
    answered = sum(len(item.question) + len(item.answer) for item in body.answers)
    await budget.check(
        scope,
        scope.workspace_id or "",
        project_id,
        reads=_reads(workflow, scope, project_id, answered),
        doing="Suggesting changes from these answers",
        confirmed_tokens=confirmed,
    )
    credential_id, api_key, provider_base_url, usage_sink = await _provider_context(
        settings=settings, credentials=credentials, usage=usage, scope=scope
    )
    result = await workflow.propose_from_answers(
        scope,
        scope.workspace_id or "",
        project_id,
        entity_id,
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
