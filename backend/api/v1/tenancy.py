"""Tenancy HTTP routes: workspaces, members, projects, and invitations.

Authorization lives in ``TenancyService``; these endpoints validate input,
resolve the actor's ``AccessScope``, and translate results to DTOs. Mutations are
CSRF-protected. Invitation raw tokens are returned exactly once at creation.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_access_scope, require_scope
from backend.api.dependencies import get_tenancy_service
from backend.domain.errors import NotFoundError
from backend.domain.identity import AccessScope, Role
from backend.services.tenancy_service import TenancyService

router = APIRouter(tags=["tenancy"])

_SLUG = r"^[a-z0-9][a-z0-9-]{1,62}$"


class WorkspaceModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    created_at: datetime
    updated_at: datetime


class MembershipModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    workspace_id: str
    user_id: str
    role: Role
    created_at: datetime
    updated_at: datetime


class ProjectModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    slug: str
    name: str
    created_at: datetime
    updated_at: datetime


class InvitationModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    role: Role
    email: str | None = None
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None


class CreateWorkspaceRequest(BaseModel):
    slug: str = Field(pattern=_SLUG, examples=["harbor"])
    name: str = Field(min_length=1, max_length=200)


class SetRoleRequest(BaseModel):
    role: Role


class TransferOwnershipRequest(BaseModel):
    to_user_id: str = Field(min_length=1)


class CreateProjectRequest(BaseModel):
    slug: str = Field(pattern=_SLUG, examples=["novel"])
    name: str = Field(min_length=1, max_length=200)


class CreateInvitationRequest(BaseModel):
    role: Role = Role.EDITOR
    email: str | None = None


class InvitationCreatedModel(BaseModel):
    invitation: InvitationModel
    token: str = Field(description="One-time raw invitation token; not stored and not shown again.")


class AcceptInvitationRequest(BaseModel):
    token: str = Field(min_length=1)


@router.get("/workspaces", response_model=list[WorkspaceModel])
async def list_workspaces(
    scope: AccessScope = Depends(get_access_scope),
    service: TenancyService = Depends(get_tenancy_service),
) -> list[WorkspaceModel]:
    workspaces = await service.list_workspaces(scope)
    return [WorkspaceModel.model_validate(workspace) for workspace in workspaces]


@router.post("/workspaces", response_model=WorkspaceModel, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: CreateWorkspaceRequest,
    scope: AccessScope = Depends(require_scope),
    service: TenancyService = Depends(get_tenancy_service),
) -> WorkspaceModel:
    workspace = await service.create_workspace(scope, slug=payload.slug, name=payload.name)
    return WorkspaceModel.model_validate(workspace)


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceModel)
async def get_workspace(
    workspace_id: str,
    scope: AccessScope = Depends(get_access_scope),
    service: TenancyService = Depends(get_tenancy_service),
) -> WorkspaceModel:
    workspace = await service.get_workspace(scope, workspace_id)
    return WorkspaceModel.model_validate(workspace)


@router.delete("/workspaces/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workspace(
    workspace_id: str,
    scope: AccessScope = Depends(require_scope),
    service: TenancyService = Depends(get_tenancy_service),
) -> None:
    await service.delete_workspace(scope, workspace_id)


@router.get("/workspaces/{workspace_id}/members", response_model=list[MembershipModel])
async def list_members(
    workspace_id: str,
    scope: AccessScope = Depends(get_access_scope),
    service: TenancyService = Depends(get_tenancy_service),
) -> list[MembershipModel]:
    members = await service.list_members(scope, workspace_id)
    return [MembershipModel.model_validate(member) for member in members]


@router.patch("/workspaces/{workspace_id}/members/{user_id}", response_model=MembershipModel)
async def set_member_role(
    workspace_id: str,
    user_id: str,
    payload: SetRoleRequest,
    scope: AccessScope = Depends(require_scope),
    service: TenancyService = Depends(get_tenancy_service),
) -> MembershipModel:
    membership = await service.set_role(scope, workspace_id, user_id, payload.role)
    return MembershipModel.model_validate(membership)


@router.delete(
    "/workspaces/{workspace_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_member(
    workspace_id: str,
    user_id: str,
    scope: AccessScope = Depends(require_scope),
    service: TenancyService = Depends(get_tenancy_service),
) -> None:
    if not await service.remove_member(scope, workspace_id, user_id):
        raise NotFoundError("Member not found.")


@router.post("/workspaces/{workspace_id}/ownership", status_code=status.HTTP_204_NO_CONTENT)
async def transfer_ownership(
    workspace_id: str,
    payload: TransferOwnershipRequest,
    scope: AccessScope = Depends(require_scope),
    service: TenancyService = Depends(get_tenancy_service),
) -> None:
    await service.transfer_ownership(scope, workspace_id, payload.to_user_id)


@router.get("/workspaces/{workspace_id}/projects", response_model=list[ProjectModel])
async def list_projects(
    workspace_id: str,
    scope: AccessScope = Depends(get_access_scope),
    service: TenancyService = Depends(get_tenancy_service),
) -> list[ProjectModel]:
    projects = await service.list_projects(scope, workspace_id)
    return [ProjectModel.model_validate(project) for project in projects]


@router.post(
    "/workspaces/{workspace_id}/projects",
    response_model=ProjectModel,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    workspace_id: str,
    payload: CreateProjectRequest,
    scope: AccessScope = Depends(require_scope),
    service: TenancyService = Depends(get_tenancy_service),
) -> ProjectModel:
    project = await service.create_project(
        scope, workspace_id, slug=payload.slug, name=payload.name
    )
    return ProjectModel.model_validate(project)


@router.delete(
    "/workspaces/{workspace_id}/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_project(
    workspace_id: str,
    project_id: str,
    scope: AccessScope = Depends(require_scope),
    service: TenancyService = Depends(get_tenancy_service),
) -> None:
    await service.delete_project(scope, workspace_id, project_id)


@router.get("/workspaces/{workspace_id}/invitations", response_model=list[InvitationModel])
async def list_invitations(
    workspace_id: str,
    scope: AccessScope = Depends(get_access_scope),
    service: TenancyService = Depends(get_tenancy_service),
) -> list[InvitationModel]:
    invitations = await service.list_invitations(scope, workspace_id)
    return [InvitationModel.model_validate(invitation) for invitation in invitations]


@router.post(
    "/workspaces/{workspace_id}/invitations",
    response_model=InvitationCreatedModel,
    status_code=status.HTTP_201_CREATED,
)
async def create_invitation(
    workspace_id: str,
    payload: CreateInvitationRequest,
    scope: AccessScope = Depends(require_scope),
    service: TenancyService = Depends(get_tenancy_service),
) -> InvitationCreatedModel:
    invitation, raw_token = await service.create_invitation(
        scope, workspace_id, role=payload.role, email=payload.email
    )
    return InvitationCreatedModel(
        invitation=InvitationModel.model_validate(invitation), token=raw_token
    )


@router.post("/workspaces/{workspace_id}/invitations/{invitation_id}/revoke")
async def revoke_invitation(
    workspace_id: str,
    invitation_id: str,
    scope: AccessScope = Depends(require_scope),
    service: TenancyService = Depends(get_tenancy_service),
) -> dict[str, bool]:
    revoked = await service.revoke_invitation(scope, workspace_id, invitation_id)
    return {"revoked": revoked}


@router.post("/invitations/accept", response_model=MembershipModel)
async def accept_invitation(
    payload: AcceptInvitationRequest,
    scope: AccessScope = Depends(require_scope),
    service: TenancyService = Depends(get_tenancy_service),
) -> MembershipModel:
    membership = await service.accept_invitation(scope, token=payload.token)
    return MembershipModel.model_validate(membership)
