"""Keep the safe ones for me (AST-EXP-015, OSS-007): the author's rules for which of the
assistant's suggestions are kept without asking, what was kept, and undoing one.

Off by default. A kind can be switched on only after the author has approved that kind
by hand ``hand_approvals_needed`` times; a workspace owner can turn the feature off for
every story (``/ai/kept-for-you``). Switching a kind off takes effect at once, including
for a suggestion that is being kept at that moment.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_project_scope, require_project_scope, require_workspace_scope
from backend.api.dependencies import get_container, get_credential_service
from backend.container import Container
from backend.domain.delegation import (
    ALWAYS_WAITS,
    DAILY_LIMIT,
    HAND_APPROVALS_NEEDED,
    KIND_DESCRIPTIONS,
    SIZE_LIMIT,
    kept_note,
    kind_name,
)
from backend.domain.identity import AccessScope
from backend.services.credential_service import CredentialService
from backend.services.delegation_service import DelegationService, DelegationView

router = APIRouter(tags=["kept-for-you"])


def get_delegation_service(container: Container = Depends(get_container)) -> Any:
    return container.delegation_service


class KeepRuleModel(BaseModel):
    # new_entry, first_details or new_fact.
    kind: str
    name: str
    description: str
    on: bool
    # How many suggestions of this kind the author has approved by hand.
    hand_approvals: int
    # When it was switched on; null while off.
    since: datetime | None


class KeptSettingsModel(BaseModel):
    rules: list[KeepRuleModel]
    # Always false: suggestions from assistants connected with an access key always wait
    # (QRY-003, ADR-024). Kept in the answer so older clients keep working.
    allow_clients: bool
    # A workspace owner turned this off for every story.
    workspace_forbidden: bool
    hand_approvals_needed: int
    size_limit: int
    daily_limit: int
    # What always waits for the author, whatever the rules say.
    always_waits: list[str]
    # Kept since this person last looked at what was kept.
    kept_since_seen: int


class SetKeptSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The kinds to keep; an empty list switches everything off.
    kinds: list[str] = Field(default_factory=list, max_length=10)
    allow_clients: bool = False


class KeptOperationModel(BaseModel):
    ordinal: int
    operation_type: str
    payload: dict[str, Any]


class KeptItemModel(BaseModel):
    mutation_id: str
    kinds: list[str]
    # The decision as history shows it: "Kept for you under your rule “New entries”."
    note: str
    kept_at: datetime
    # The version of the story it became.
    revision: int | None
    operations: list[KeptOperationModel]
    # The suggestion to undo it, once asked for, and where that stands.
    undo_mutation_id: str | None
    undo_status: str | None


class KeptListModel(BaseModel):
    items: list[KeptItemModel]


class KeepDecisionModel(BaseModel):
    """How one suggestion was decided, when the rules looked at it."""

    mutation_id: str
    # by_hand, kept or waited; null when the rules never looked at it.
    how: str | None = None
    kinds: list[str] = Field(default_factory=list)
    note: str | None = None
    # Why it waited, in plain words.
    reason_code: str | None = None
    reason: str | None = None


class UndoModel(BaseModel):
    # The suggestion to undo it, waiting in the review queue.
    undo_mutation_id: str


class WorkspaceKeptModel(BaseModel):
    forbidden: bool


class SetWorkspaceKeptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    forbidden: bool


def _settings(view: DelegationView) -> KeptSettingsModel:
    return KeptSettingsModel(
        rules=[
            KeepRuleModel(
                kind=rule.kind,
                name=rule.name,
                description=KIND_DESCRIPTIONS[rule.kind],
                on=rule.on,
                hand_approvals=rule.hand_approvals,
                since=rule.since,
            )
            for rule in view.rules
        ],
        allow_clients=view.allow_clients,
        workspace_forbidden=view.workspace_forbidden,
        hand_approvals_needed=HAND_APPROVALS_NEEDED,
        size_limit=SIZE_LIMIT,
        daily_limit=DAILY_LIMIT,
        always_waits=list(ALWAYS_WAITS),
        kept_since_seen=view.kept_since_seen,
    )


@router.get("/projects/{project_id}/kept-for-you/settings")
async def get_kept_settings(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: DelegationService = Depends(get_delegation_service),
) -> KeptSettingsModel:
    """Which kinds of suggestion are kept without asking in this story."""
    return _settings(await service.view(scope, scope.workspace_id or "", project_id))


@router.put("/projects/{project_id}/kept-for-you/settings")
async def set_kept_settings(
    project_id: str,
    body: SetKeptSettingsRequest,
    scope: AccessScope = Depends(require_project_scope),
    service: DelegationService = Depends(get_delegation_service),
) -> KeptSettingsModel:
    """Choose the kinds to keep. A kind not yet approved by hand often enough is refused
    (400); switching off is never refused and takes effect at once."""
    return _settings(
        await service.set_story(
            scope,
            scope.workspace_id or "",
            project_id,
            kinds=body.kinds,
            allow_clients=body.allow_clients,
        )
    )


@router.get("/projects/{project_id}/kept-for-you")
async def list_kept(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: DelegationService = Depends(get_delegation_service),
) -> KeptListModel:
    """What was kept for the author, newest first."""
    items = await service.kept(scope, scope.workspace_id or "", project_id)
    return KeptListModel(
        items=[
            KeptItemModel(
                mutation_id=item.record.mutation_id,
                kinds=list(item.record.kinds),
                note=kept_note(item.record.kinds),
                kept_at=item.record.created_at,
                revision=item.record.revision,
                operations=[
                    KeptOperationModel(
                        ordinal=operation.ordinal,
                        operation_type=operation.operation_type,
                        payload=operation.payload,
                    )
                    for operation in item.operations
                ],
                undo_mutation_id=item.record.undo_mutation_id,
                undo_status=item.undo_status,
            )
            for item in items
        ]
    )


@router.post("/projects/{project_id}/kept-for-you/seen", status_code=204)
async def mark_kept_seen(
    project_id: str,
    scope: AccessScope = Depends(require_project_scope),
    service: DelegationService = Depends(get_delegation_service),
) -> None:
    """The person has looked at what was kept; the review queue counts from now."""
    await service.seen(scope, scope.workspace_id or "", project_id)


@router.get("/projects/{project_id}/kept-for-you/decisions/{mutation_id}")
async def get_keep_decision(
    project_id: str,
    mutation_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: DelegationService = Depends(get_delegation_service),
) -> KeepDecisionModel:
    """Whether a suggestion was kept for the author, approved by hand, or why it waited."""
    record = await service.record(scope, scope.workspace_id or "", project_id, mutation_id)
    if record is None:
        return KeepDecisionModel(mutation_id=mutation_id)
    return KeepDecisionModel(
        mutation_id=mutation_id,
        how=record.how,
        kinds=list(record.kinds),
        note=kept_note(record.kinds) if record.how == "kept" else None,
        reason_code=record.reason_code,
        reason=record.reason,
    )


@router.post("/projects/{project_id}/kept-for-you/{mutation_id}/undo", status_code=201)
async def undo_kept(
    project_id: str,
    mutation_id: str,
    scope: AccessScope = Depends(require_project_scope),
    service: DelegationService = Depends(get_delegation_service),
) -> UndoModel:
    """Suggest undoing something that was kept. The undo waits in the review queue."""
    return UndoModel(
        undo_mutation_id=await service.undo(
            scope, scope.workspace_id or "", project_id, mutation_id
        )
    )


@router.get("/ai/kept-for-you")
async def get_workspace_kept(
    scope: AccessScope = Depends(require_workspace_scope),
    service: DelegationService = Depends(get_delegation_service),
) -> WorkspaceKeptModel:
    """Whether a workspace owner turned off keeping suggestions for every story."""
    assert scope.workspace_id is not None
    return WorkspaceKeptModel(forbidden=await service.workspace_forbidden(scope.workspace_id))


@router.put("/ai/kept-for-you")
async def set_workspace_kept(
    body: SetWorkspaceKeptRequest,
    scope: AccessScope = Depends(require_workspace_scope),
    credentials: CredentialService = Depends(get_credential_service),
    service: DelegationService = Depends(get_delegation_service),
) -> WorkspaceKeptModel:
    """An owner turns keeping suggestions off, or back on, for every story here."""
    assert scope.workspace_id is not None
    await credentials.require_admin(scope, scope.workspace_id)
    return WorkspaceKeptModel(
        forbidden=await service.set_workspace_forbidden(
            scope, scope.workspace_id, forbidden=body.forbidden
        )
    )


__all__ = ["get_delegation_service", "kind_name", "router"]
