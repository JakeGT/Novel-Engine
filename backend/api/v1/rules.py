"""Story rules: an author's own continuity rules, checked without a model.

A rule warns by default; the author may set it to block (ADR-013, CON-015)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import (
    get_canon_character_workflow,
    get_credential_service,
    get_rule_service,
    get_settings,
    get_usage_service,
)
from backend.api.v1.assistant_settings import confirmed_tokens, get_assist_budget
from backend.api.v1.router import _provider_context
from backend.core.config import Settings
from backend.domain.identity import AccessScope
from backend.domain.rules import (
    MAX_EXAMPLES,
    MAX_SENTENCE_LENGTH,
    MAX_TITLE_LENGTH,
    ExampleResult,
    RuleBody,
    RuleExample,
    RuleStatus,
    Violation,
)
from backend.domain.validation import RuleEnforcement
from backend.services.assistant_settings_service import AssistBudget
from backend.services.canon_revise_workflow import estimate_reading
from backend.services.credential_service import CredentialService
from backend.services.rule_service import RuleCheck, RuleReading, RuleService
from backend.services.rule_workflow import RuleWorkflow
from backend.services.usage_service import ProviderUsageService

router = APIRouter(prefix="/projects/{project_id}", tags=["rules"])


class BrokenByModel(BaseModel):
    entity_id: str
    name: str


class ExampleResultModel(BaseModel):
    description: str
    # caught or allowed: what the example says should happen.
    expect: str
    # What the rule actually does with it.
    outcome: str
    agrees: bool


class RuleModel(BaseModel):
    id: str
    title: str
    # The rule as the author wrote it.
    sentence: str
    # The rule as it is checked, in ordinary words.
    reading: str
    # on or paused.
    status: str
    # warning (the default) or blocking: whether a change that breaks it can be added.
    enforcement: RuleEnforcement = RuleEnforcement.WARNING
    version: int
    body: RuleBody
    examples: list[RuleExample]
    example_results: list[ExampleResultModel]
    # Entries it does not hold for right now.
    broken_by: list[BrokenByModel]


class RuleListModel(BaseModel):
    items: list[RuleModel]


class RuleCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: RuleBody
    examples: list[RuleExample] = Field(default_factory=list, max_length=MAX_EXAMPLES)


class RuleCheckModel(BaseModel):
    reading: str
    # Why it cannot be kept as written. Empty when it can.
    problems: list[str]
    example_results: list[ExampleResultModel]
    broken_by: list[BrokenByModel]


class AddRuleRequest(RuleCheckRequest):
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    sentence: str = Field(default="", max_length=MAX_SENTENCE_LENGTH)


class ChangeRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=MAX_TITLE_LENGTH)
    sentence: str | None = Field(default=None, max_length=MAX_SENTENCE_LENGTH)
    status: RuleStatus | None = None
    # Warn about a change that breaks the rule, or block it until it is fixed.
    enforcement: RuleEnforcement | None = None
    # New wording becomes a new version; the earlier one is kept.
    body: RuleBody | None = None
    examples: list[RuleExample] | None = Field(default=None, max_length=MAX_EXAMPLES)


class DraftRuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sentence: str = Field(min_length=1, max_length=MAX_SENTENCE_LENGTH)


class RuleDraftModel(BaseModel):
    title: str
    understood: str
    # Null when the sentence could not be made into a rule.
    body: RuleBody | None
    examples: list[RuleExample]
    check: RuleCheckModel | None
    # What a rule cannot say that this sentence needs, if anything.
    cannot_express: str


def _broken(found: list[Violation]) -> list[BrokenByModel]:
    return [BrokenByModel(entity_id=item.entity_id, name=item.entity_name) for item in found]


def _results(found: list[ExampleResult]) -> list[ExampleResultModel]:
    return [
        ExampleResultModel(
            description=item.description,
            expect=item.expect,
            outcome=item.outcome,
            agrees=item.agrees,
        )
        for item in found
    ]


def _check(found: RuleCheck) -> RuleCheckModel:
    return RuleCheckModel(
        reading=found.reading,
        problems=found.problems,
        example_results=_results(found.examples),
        broken_by=_broken(found.broken_by),
    )


def _model(found: RuleReading) -> RuleModel:
    rule = found.rule
    return RuleModel(
        id=rule.id,
        title=rule.title,
        sentence=rule.sentence,
        reading=found.reading,
        status=rule.status.value,
        enforcement=rule.enforcement,
        version=rule.version,
        body=rule.body,
        examples=rule.examples,
        example_results=_results(found.examples),
        broken_by=_broken(found.broken_by),
    )


@router.get("/rules", response_model=RuleListModel)
async def list_rules(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: RuleService = Depends(get_rule_service),
) -> RuleListModel:
    found = await service.rules(scope, scope.workspace_id or "", project_id)
    return RuleListModel(items=[_model(item) for item in found])


@router.post("/rules/check", response_model=RuleCheckModel)
async def check_rule(
    project_id: str,
    body: RuleCheckRequest,
    scope: AccessScope = Depends(get_project_scope),
    service: RuleService = Depends(get_rule_service),
) -> RuleCheckModel:
    """What a rule would do, before it is kept: its reading, its examples, who breaks it now."""
    return _check(
        await service.check(scope, scope.workspace_id or "", project_id, body.body, body.examples)
    )


@router.post("/rules", response_model=RuleModel, status_code=201)
async def add_rule(
    project_id: str,
    body: AddRuleRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: RuleService = Depends(get_rule_service),
) -> RuleModel:
    found = await service.add(
        scope,
        scope.workspace_id or "",
        project_id,
        title=body.title,
        sentence=body.sentence,
        body=body.body,
        examples=body.examples,
    )
    return _model(found)


@router.patch("/rules/{rule_id}", response_model=RuleModel)
async def change_rule(
    project_id: str,
    rule_id: str,
    body: ChangeRuleRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: RuleService = Depends(get_rule_service),
) -> RuleModel:
    found = await service.change(
        scope,
        scope.workspace_id or "",
        project_id,
        rule_id,
        title=body.title,
        sentence=body.sentence,
        status=body.status,
        body=body.body,
        examples=body.examples,
        enforcement=body.enforcement,
    )
    return _model(found)


@router.delete("/rules/{rule_id}", status_code=204)
async def remove_rule(
    project_id: str,
    rule_id: str,
    scope: AccessScope = Depends(require_project_scope),
    service: RuleService = Depends(get_rule_service),
) -> Response:
    await service.remove(scope, scope.workspace_id or "", project_id, rule_id)
    return Response(status_code=204)


@router.post("/assist/rules/draft", response_model=RuleDraftModel)
async def draft_rule(
    project_id: str,
    body: DraftRuleRequest,
    workflow: RuleWorkflow = Depends(get_canon_character_workflow),
    service: RuleService = Depends(get_rule_service),
    credentials: CredentialService = Depends(get_credential_service),
    usage: ProviderUsageService = Depends(get_usage_service),
    settings: Settings = Depends(get_settings),
    scope: AccessScope = Depends(require_project_scope),
    budget: AssistBudget = Depends(get_assist_budget),
    confirmed: int | None = Depends(confirmed_tokens),
) -> RuleDraftModel:
    """Draft a rule from a sentence. Keeps nothing: the author decides whether to add it."""
    workspace_id = scope.workspace_id or ""

    async def reads() -> int:
        # The sentence, the kinds of fact and the names of what exists.
        sizes = await estimate_reading(workflow, scope, workspace_id, project_id)
        return int(sizes["entry"]) + len(body.sentence) // 4

    await budget.check(
        scope,
        workspace_id,
        project_id,
        reads=reads,
        doing="Drafting this rule",
        confirmed_tokens=confirmed,
    )
    credential_id, api_key, provider_base_url, usage_sink = await _provider_context(
        settings=settings, credentials=credentials, usage=usage, scope=scope
    )
    draft = await workflow.draft_rule(
        scope,
        workspace_id,
        project_id,
        sentence=body.sentence,
        credential=api_key,
        provider_base_url=provider_base_url,
        usage_sink=usage_sink,
    )
    if credential_id:
        await credentials.mark_used(workspace_id, credential_id)
    check = (
        _check(await service.checked(workspace_id, project_id, draft.body, draft.examples))
        if draft.body is not None
        else None
    )
    return RuleDraftModel(
        title=draft.title,
        understood=draft.understood,
        body=draft.body,
        examples=draft.examples,
        check=check,
        cannot_express=draft.cannot_express,
    )
