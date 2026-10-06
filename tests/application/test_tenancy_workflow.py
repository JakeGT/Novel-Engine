import pytest

from backend.core.security import hash_token
from backend.domain.errors import ForbiddenError
from backend.domain.identity import AccessScope, AuthMode, IdentityClaims, Role
from backend.domain.tenancy import (
    InvitationNotAcceptable,
    LastOwnerRemoval,
    RoleTransitionError,
)
from backend.services.tenancy_service import TenancyService
from tests.doubles import InMemoryIdentityStore, InMemoryTenancyStore

pytestmark = pytest.mark.application


def _scope(user_id: str) -> AccessScope:
    return AccessScope(actor_user_id=user_id, auth_mode=AuthMode.LOCAL)


async def _users():
    store = InMemoryIdentityStore()
    owner = await store.get_or_create_user(
        IdentityClaims(issuer="local", subject="owner", email="owner@example.test")
    )
    editor = await store.get_or_create_user(
        IdentityClaims(issuer="local", subject="editor", email="editor@example.test")
    )
    return store, owner, editor


@pytest.mark.asyncio
async def test_invitation_vertical_slice() -> None:
    identity_store, owner, invitee = await _users()
    store = InMemoryTenancyStore()
    service = TenancyService(store, identity_store=identity_store)
    owner_scope = _scope(owner.id)
    invitee_scope = _scope(invitee.id)

    workspace = await service.create_workspace(owner_scope, slug="harbor", name="Harbor")
    invitation, token = await service.create_invitation(owner_scope, workspace.id, role=Role.EDITOR)

    membership = await service.accept_invitation(invitee_scope, token=token)
    assert membership.role == Role.EDITOR
    # The invitee can now read the workspace.
    assert (await service.get_workspace(invitee_scope, workspace.id)).id == workspace.id

    # Replay is rejected.
    with pytest.raises(InvitationNotAcceptable):
        await service.accept_invitation(_scope("someone-else"), token=token)

    # Owner changes role then removes the member; access is revoked.
    await service.set_role(owner_scope, workspace.id, invitee.id, Role.VIEWER)
    assert await service.remove_member(owner_scope, workspace.id, invitee.id) is True
    with pytest.raises(ForbiddenError):
        await service.get_workspace(invitee_scope, workspace.id)

    actions = [event.action for event in store.audit]
    assert "workspace.created" in actions
    assert "invitation.created" in actions
    assert "invitation.accepted" in actions
    assert "membership.role_changed" in actions
    assert "membership.removed" in actions
    # Audit records never contain the raw invitation token.
    assert all(token not in str(event.detail) for event in store.audit)


@pytest.mark.asyncio
async def test_revoked_and_expired_invitations_are_rejected() -> None:
    identity_store, owner, invitee = await _users()
    store = InMemoryTenancyStore()
    service = TenancyService(store, identity_store=identity_store)
    owner_scope = _scope(owner.id)
    invitee_scope = _scope(invitee.id)
    workspace = await service.create_workspace(owner_scope, slug="w", name="W")

    invitation, token = await service.create_invitation(owner_scope, workspace.id, role=Role.EDITOR)
    assert await service.revoke_invitation(owner_scope, workspace.id, invitation.id) is True
    with pytest.raises(InvitationNotAcceptable):
        await service.accept_invitation(invitee_scope, token=token)

    expired = await store.create_invitation(
        workspace_id=workspace.id,
        role=Role.VIEWER,
        token_hash=hash_token("expired-token"),
        created_by_user_id=owner.id,
        ttl_seconds=-1,
    )
    assert expired.id
    with pytest.raises(InvitationNotAcceptable):
        await service.accept_invitation(invitee_scope, token="expired-token")


@pytest.mark.asyncio
async def test_removed_inviter_cannot_be_accepted() -> None:
    identity_store, owner, invitee = await _users()
    store = InMemoryTenancyStore()
    service = TenancyService(store, identity_store=identity_store)
    owner_scope = _scope(owner.id)
    workspace = await service.create_workspace(owner_scope, slug="w", name="W")
    await store.add_membership(workspace_id=workspace.id, user_id="owner2", role=Role.OWNER)

    invitation, token = await service.create_invitation(
        _scope("owner2"), workspace.id, role=Role.EDITOR
    )
    assert invitation.id
    assert await service.remove_member(owner_scope, workspace.id, "owner2") is True

    with pytest.raises(RoleTransitionError):
        await service.accept_invitation(_scope(invitee.id), token=token)


@pytest.mark.asyncio
async def test_email_bound_invitation_rejects_other_account() -> None:
    identity_store, owner, invitee = await _users()
    store = InMemoryTenancyStore()
    service = TenancyService(store, identity_store=identity_store)
    owner_scope = _scope(owner.id)
    workspace = await service.create_workspace(owner_scope, slug="w", name="W")

    _, token = await service.create_invitation(
        owner_scope, workspace.id, role=Role.EDITOR, email="someone-else@example.test"
    )
    with pytest.raises(ForbiddenError):
        await service.accept_invitation(_scope(invitee.id), token=token)


@pytest.mark.asyncio
async def test_last_owner_cannot_be_removed_through_service() -> None:
    identity_store, owner, _ = await _users()
    store = InMemoryTenancyStore()
    service = TenancyService(store, identity_store=identity_store)
    owner_scope = _scope(owner.id)
    workspace = await service.create_workspace(owner_scope, slug="w", name="W")
    with pytest.raises(LastOwnerRemoval):
        await service.remove_member(owner_scope, workspace.id, owner.id)
