"""Planning the story with the assistant: questions, a drafted plan, and directions to choose from.

Everything here ends in a set of suggested changes to review, or in options to
choose between. Nothing is added to the plan, or to the story, by asking.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import require_project_scope
from backend.api.dependencies import (
    get_assist_session_service,
    get_canon_character_workflow,
    get_credential_service,
    get_settings,
    get_usage_service,
)
from backend.api.v1.assistant_settings import confirmed_tokens, get_assist_budget
from backend.api.v1.canon_assist import (
    AssistQuestionSheetModel,
    DiscardedItemModel,
    reading,
)
from backend.api.v1.canon_characters import CharacterAnswerModel, CharacterQuestionModel
from backend.api.v1.router import _provider_context
from backend.core.config import Settings
from backend.domain.assist import AssistAnswer, AssistKind, AssistQuestion
from backend.domain.identity import AccessScope
from backend.services.assist_session_service import AssistSessionService
from backend.services.assistant_settings_service import AssistBudget
from backend.services.canon_character_workflow import MAX_ANSWERS, CharacterAnswer
from backend.services.credential_service import CredentialService
from backend.services.plan_workflow import (
    MAX_DESCRIPTION_LENGTH,
    PlanDraft,
    PlanProposalResult,
    PlanWorkflow,
)
from backend.services.usage_service import ProviderUsageService

router = APIRouter(prefix="/projects/{project_id}/assist/plan", tags=["plan-assist"])


class PlanBriefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # What the author means to happen, in their own words. Optional when answering questions.
    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)
    # How far this round goes: arcs, beats or scenes.
    depth: str = Field(default="arcs", max_length=20)
    # The part of the plan being worked on, if any ("plan this arc's beats").
    about_id: str | None = Field(default=None, max_length=200)


class PlanProposalRequest(PlanBriefRequest):
    session_id: str | None = Field(default=None, max_length=80)
    answers: list[CharacterAnswerModel] = Field(default_factory=list, max_length=MAX_ANSWERS)
    expected_revision: int = Field(ge=0)


class PlanProposalModel(BaseModel):
    mutation_id: str
    operation_count: int
    session_id: str
    understood: str
    # What the author has not decided: said, never filled in.
    notes: list[str]
    discarded: list[DiscardedItemModel]
    validation_ok: bool


class DirectionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    about_id: str | None = Field(default=None, max_length=200)


class DirectionModel(BaseModel):
    title: str
    what_happens: str
    what_it_changes: list[str]
    # How many suggested changes picking it would lead to.
    adds: int
    # Sent back as it is to choose this direction; checked again when it is.
    draft: PlanDraft


class DirectionsModel(BaseModel):
    directions: list[DirectionModel]


class ChooseDirectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft: PlanDraft
    expected_revision: int = Field(ge=0)


def _proposal(result: PlanProposalResult) -> PlanProposalModel:
    return PlanProposalModel(
        mutation_id=result.mutation.id,
        operation_count=result.operation_count,
        session_id=result.session_id,
        understood=result.understood,
        notes=result.notes,
        discarded=[
            DiscardedItemModel(kind=item.kind, reason=item.reason) for item in result.discarded
        ],
        validation_ok=result.validation.ok,
    )


@router.post("/questions", response_model=AssistQuestionSheetModel)
async def plan_questions(
    project_id: str,
    body: PlanBriefRequest,
    workflow: PlanWorkflow = Depends(get_canon_character_workflow),
    sessions: AssistSessionService = Depends(get_assist_session_service),
    credentials: CredentialService = Depends(get_credential_service),
    usage: ProviderUsageService = Depends(get_usage_service),
    settings: Settings = Depends(get_settings),
    scope: AccessScope = Depends(require_project_scope),
    budget: AssistBudget = Depends(get_assist_budget),
    confirmed: int | None = Depends(confirmed_tokens),
) -> AssistQuestionSheetModel:
    guard = await budget.check(
        scope,
        scope.workspace_id or "",
        project_id,
        reads=reading(workflow, scope, project_id, "story"),
        doing="Asking planning questions",
        confirmed_tokens=confirmed,
    )
    credential_id, api_key, provider_base_url, usage_sink = await _provider_context(
        settings=settings, credentials=credentials, usage=usage, scope=scope
    )
    usage_sink = guard.counting(usage_sink)
    sheet = await workflow.plan_questions(
        scope,
        scope.workspace_id or "",
        project_id,
        description=body.description,
        depth=body.depth,
        about_id=body.about_id,
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
        kind=AssistKind.PLAN,
        description=body.description,
        questions=[
            AssistQuestion(id=item.id, question=item.question, category=item.category)
            for item in sheet.questions
        ],
    )
    await guard.record(session.id)
    return AssistQuestionSheetModel(
        session_id=session.id,
        questions=[
            CharacterQuestionModel(id=item.id, question=item.question, category=item.category)
            for item in sheet.questions
        ],
    )


@router.post("/proposals", response_model=PlanProposalModel, status_code=201)
async def plan_proposal(
    project_id: str,
    body: PlanProposalRequest,
    workflow: PlanWorkflow = Depends(get_canon_character_workflow),
    sessions: AssistSessionService = Depends(get_assist_session_service),
    credentials: CredentialService = Depends(get_credential_service),
    usage: ProviderUsageService = Depends(get_usage_service),
    settings: Settings = Depends(get_settings),
    scope: AccessScope = Depends(require_project_scope),
    budget: AssistBudget = Depends(get_assist_budget),
    confirmed: int | None = Depends(confirmed_tokens),
) -> PlanProposalModel:
    """One round of planning, as plan items to review. The plan itself is not changed."""

    async def guarded() -> Any:
        return await budget.check(
            scope,
            scope.workspace_id or "",
            project_id,
            reads=reading(workflow, scope, project_id, "story"),
            doing="Drafting this part of the plan",
            confirmed_tokens=confirmed,
            session_id=body.session_id,
        )

    # A new conversation is not opened for a request that is then refused.
    guard = None if body.session_id else await guarded()
    credential_id, api_key, provider_base_url, usage_sink = await _provider_context(
        settings=settings, credentials=credentials, usage=usage, scope=scope
    )
    answers = [
        AssistAnswer(question_id=a.question_id, question=a.question, answer=a.answer)
        for a in body.answers
    ]
    # Kept before the model is asked, so a failed draft loses nothing the author wrote.
    session = await sessions.begin(
        scope,
        scope.workspace_id or "",
        project_id,
        kind=AssistKind.PLAN,
        session_id=body.session_id,
        description=body.description,
        answers=answers,
    )
    # Checked once the answers are kept, so a refusal loses nothing the author wrote.
    guard = guard or await guarded()
    try:
        result = await workflow.plan_proposal(
            scope,
            scope.workspace_id or "",
            project_id,
            description=body.description,
            depth=body.depth,
            about_id=body.about_id,
            answers=[
                CharacterAnswer(question_id=a.question_id, question=a.question, answer=a.answer)
                for a in body.answers
            ],
            expected_revision=body.expected_revision,
            session_id=session.id,
            credential=api_key,
            provider_base_url=provider_base_url,
            usage_sink=guard.counting(usage_sink),
        )
    finally:
        await guard.record(session.id)
    if credential_id:
        await credentials.mark_used(scope.workspace_id or "", credential_id)
    await sessions.proposed(
        scope,
        scope.workspace_id or "",
        project_id,
        session.id,
        mutation_id=result.mutation.id,
    )
    return _proposal(result)


@router.post("/directions", response_model=DirectionsModel)
async def plan_directions(
    project_id: str,
    body: DirectionsRequest,
    workflow: PlanWorkflow = Depends(get_canon_character_workflow),
    credentials: CredentialService = Depends(get_credential_service),
    usage: ProviderUsageService = Depends(get_usage_service),
    settings: Settings = Depends(get_settings),
    scope: AccessScope = Depends(require_project_scope),
    budget: AssistBudget = Depends(get_assist_budget),
    confirmed: int | None = Depends(confirmed_tokens),
) -> DirectionsModel:
    """What could happen next: two or three options. Nothing is proposed or changed."""
    await budget.check(
        scope,
        scope.workspace_id or "",
        project_id,
        reads=reading(workflow, scope, project_id, "story"),
        doing="Suggesting what could happen next",
        confirmed_tokens=confirmed,
    )
    credential_id, api_key, provider_base_url, usage_sink = await _provider_context(
        settings=settings, credentials=credentials, usage=usage, scope=scope
    )
    offered = await workflow.plan_directions(
        scope,
        scope.workspace_id or "",
        project_id,
        about_id=body.about_id,
        credential=api_key,
        provider_base_url=provider_base_url,
        usage_sink=usage_sink,
    )
    if credential_id:
        await credentials.mark_used(scope.workspace_id or "", credential_id)
    return DirectionsModel(
        directions=[
            DirectionModel(
                title=item.title,
                what_happens=item.what_happens,
                what_it_changes=item.what_it_changes,
                adds=item.adds,
                draft=item.draft,
            )
            for item in offered
        ]
    )


@router.post("/directions/choose", response_model=PlanProposalModel, status_code=201)
async def choose_direction(
    project_id: str,
    body: ChooseDirectionRequest,
    workflow: PlanWorkflow = Depends(get_canon_character_workflow),
    scope: AccessScope = Depends(require_project_scope),
) -> PlanProposalModel:
    """The direction the author picked, checked again, as plan items to review. No model call."""
    result = await workflow.plan_from_direction(
        scope,
        scope.workspace_id or "",
        project_id,
        body.draft,
        expected_revision=body.expected_revision,
    )
    return _proposal(result)
