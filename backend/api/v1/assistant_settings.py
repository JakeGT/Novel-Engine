"""How much the assistant may do and spend on its own: a story's settings and a workspace's
ceilings (CON-009, OPS-002, AST-EXP-011).

A request estimated above the confirmation threshold is refused with
``409 needs_confirmation`` until it is sent again with ``X-Confirm-Tokens`` set to at
least the estimate the refusal gave (``field_errors.estimated_tokens``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope, require_workspace_scope
from backend.api.dependencies import get_container, get_credential_service
from backend.container import Container
from backend.domain.identity import AccessScope
from backend.domain.usage_controls import (
    MAX_CHECK_MINUTES,
    MAX_CONTROL_TOKENS,
    MAX_SCENE_TOKENS,
    MIN_CHECK_MINUTES,
    MIN_CONFIRM_TOKENS,
    MIN_CONVERSATION_TOKENS,
    MIN_SCENE_TOKENS,
    AssistantSettings,
    CheckProfile,
)
from backend.services.assistant_settings_service import AssistantSettingsService, AssistBudget
from backend.services.credential_service import CredentialService

router = APIRouter(tags=["assistant-settings"])


def get_assistant_settings(container: Container = Depends(get_container)) -> Any:
    return container.assistant_settings


def get_assist_budget(container: Container = Depends(get_container)) -> Any:
    return container.assist_budget


def confirmed_tokens(
    x_confirm_tokens: int | None = Header(
        default=None, alias="X-Confirm-Tokens", ge=0, le=MAX_CONTROL_TOKENS * 10
    ),
) -> int | None:
    """The size the author agreed to spend on this request, after being asked."""
    return x_confirm_tokens


class AssistantSettingsModel(BaseModel):
    # on_request (the default), balanced or thorough.
    check_profile: CheckProfile
    # The most automatic checks may use for one scene in a day.
    scene_tokens: int
    # Thorough: the least time between automatic checks after saving.
    check_minutes: int
    # A single request estimated above this many tokens is confirmed first; null is off.
    confirm_above_tokens: int | None
    # The most one conversation with the assistant may use; null is no limit.
    conversation_tokens: int | None


class StoryAssistantSettingsModel(AssistantSettingsModel):
    # The workspace owner's ceilings, which win when lower; null when none is set.
    workspace_confirm_above_tokens: int | None = None
    workspace_conversation_tokens: int | None = None
    # What applies in this story: the lower of the story's and the workspace's.
    effective_confirm_above_tokens: int | None = None
    effective_conversation_tokens: int | None = None


class SetAssistantSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_profile: CheckProfile = CheckProfile.ON_REQUEST
    scene_tokens: int = Field(default=40_000, ge=MIN_SCENE_TOKENS, le=MAX_SCENE_TOKENS)
    check_minutes: int = Field(default=15, ge=MIN_CHECK_MINUTES, le=MAX_CHECK_MINUTES)
    confirm_above_tokens: int | None = Field(
        default=None, ge=MIN_CONFIRM_TOKENS, le=MAX_CONTROL_TOKENS
    )
    conversation_tokens: int | None = Field(
        default=None, ge=MIN_CONVERSATION_TOKENS, le=MAX_CONTROL_TOKENS
    )


class WorkspaceLimitsModel(BaseModel):
    confirm_above_tokens: int | None
    conversation_tokens: int | None


class SetWorkspaceLimitsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_above_tokens: int | None = Field(
        default=None, ge=MIN_CONFIRM_TOKENS, le=MAX_CONTROL_TOKENS
    )
    conversation_tokens: int | None = Field(
        default=None, ge=MIN_CONVERSATION_TOKENS, le=MAX_CONTROL_TOKENS
    )


async def _story_model(
    service: AssistantSettingsService, workspace_id: str, project_id: str
) -> StoryAssistantSettingsModel:
    story = await service.for_story(workspace_id, project_id)
    workspace = await service.workspace(workspace_id)
    limits = await service.limits(workspace_id, project_id)
    return StoryAssistantSettingsModel(
        check_profile=story.check_profile,
        scene_tokens=story.scene_tokens,
        check_minutes=story.check_minutes,
        confirm_above_tokens=story.confirm_above_tokens,
        conversation_tokens=story.conversation_tokens,
        workspace_confirm_above_tokens=workspace.confirm_above_tokens,
        workspace_conversation_tokens=workspace.conversation_tokens,
        effective_confirm_above_tokens=limits.confirm_above_tokens,
        effective_conversation_tokens=limits.conversation_tokens,
    )


@router.get("/projects/{project_id}/assistant-settings")
async def get_story_settings(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: AssistantSettingsService = Depends(get_assistant_settings),
) -> StoryAssistantSettingsModel:
    """How much checking runs on its own in this story, and when to ask before spending."""
    await service.story(scope, scope.workspace_id or "", project_id)
    return await _story_model(service, scope.workspace_id or "", project_id)


@router.put("/projects/{project_id}/assistant-settings")
async def set_story_settings(
    project_id: str,
    body: SetAssistantSettingsRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: AssistantSettingsService = Depends(get_assistant_settings),
) -> StoryAssistantSettingsModel:
    await service.set_story(
        scope,
        scope.workspace_id or "",
        project_id,
        AssistantSettings(
            check_profile=body.check_profile,
            scene_tokens=body.scene_tokens,
            check_minutes=body.check_minutes,
            confirm_above_tokens=body.confirm_above_tokens,
            conversation_tokens=body.conversation_tokens,
        ),
    )
    return await _story_model(service, scope.workspace_id or "", project_id)


def _workspace_model(found: AssistantSettings) -> WorkspaceLimitsModel:
    return WorkspaceLimitsModel(
        confirm_above_tokens=found.confirm_above_tokens,
        conversation_tokens=found.conversation_tokens,
    )


@router.get("/ai/limits")
async def get_workspace_limits(
    scope: AccessScope = Depends(require_workspace_scope),
    credentials: CredentialService = Depends(get_credential_service),
    service: AssistantSettingsService = Depends(get_assistant_settings),
) -> WorkspaceLimitsModel:
    """The owner's ceilings for everyone in the workspace."""
    assert scope.workspace_id is not None
    await credentials.require_use(scope, scope.workspace_id)
    return _workspace_model(await service.workspace(scope.workspace_id))


@router.put("/ai/limits")
async def set_workspace_limits(
    body: SetWorkspaceLimitsRequest,
    scope: AccessScope = Depends(require_workspace_scope),
    credentials: CredentialService = Depends(get_credential_service),
    service: AssistantSettingsService = Depends(get_assistant_settings),
) -> WorkspaceLimitsModel:
    """An owner sets, for everyone, when to ask before a large request and how much one
    conversation may use. A story may choose lower; never higher."""
    assert scope.workspace_id is not None
    await credentials.require_admin(scope, scope.workspace_id)
    return _workspace_model(
        await service.set_workspace(
            scope.workspace_id,
            confirm_above_tokens=body.confirm_above_tokens,
            conversation_tokens=body.conversation_tokens,
            actor_user_id=scope.actor_user_id,
        )
    )


__all__ = [
    "AssistBudget",
    "confirmed_tokens",
    "get_assist_budget",
    "get_assistant_settings",
    "router",
]
