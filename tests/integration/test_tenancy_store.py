import pytest
import pytest_asyncio

from backend.adapters.identity_store import PostgresIdentityStore
from backend.adapters.tenancy_store import PostgresTenancyStore
from backend.core.database import create_engine, create_session_factory
from backend.domain.identity import IdentityClaims, Role
from backend.domain.tenancy import (
    InvitationNotAcceptable,
    MembershipConflict,
    ProjectSlugConflict,
    RoleTransitionError,
    WorkspaceSlugConflict,
)

pytestmark = pytest.mark.integration

ISSUER = "https://idp.test/application/o/novel-engine/"


@pytest_asyncio.fixture
async def session_factory(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _make_user(session_factory, subject: str) -> str:
    store = PostgresIdentityStore(session_factory)
    user = await store.get_or_create_user(IdentityClaims(issuer=ISSUER, subject=subject))
    return user.id


@pytest.mark.asyncio
async def test_workspace_and_membership_invariants(session_factory) -> None:
    store = PostgresTenancyStore(session_factory)
    owner = await _make_user(session_factory, "owner")
    editor = await _make_user(session_factory, "editor")

    workspace = await store.create_workspace(slug="harbor", name="Harbor")
    assert await store.get_workspace_by_slug("harbor") is not None
    assert [w.id for w in await store.list_workspaces_for_user(owner)] == []

    await store.add_membership(workspace_id=workspace.id, user_id=owner, role=Role.OWNER)
    await store.add_membership(workspace_id=workspace.id, user_id=editor, role=Role.EDITOR)
    assert [w.id for w in await store.list_workspaces_for_user(owner)] == [workspace.id]

    with pytest.raises(WorkspaceSlugConflict):
        await store.create_workspace(slug="harbor", name="Duplicate")
    with pytest.raises(MembershipConflict):
        await store.add_membership(workspace_id=workspace.id, user_id=owner, role=Role.OWNER)

    roles = {m.user_id: m.role for m in await store.list_memberships(workspace.id)}
    assert roles == {owner: Role.OWNER, editor: Role.EDITOR}


@pytest.mark.asyncio
async def test_project_slug_unique_within_workspace(session_factory) -> None:
    store = PostgresTenancyStore(session_factory)
    ws_a = await store.create_workspace(slug="a", name="A")
    ws_b = await store.create_workspace(slug="b", name="B")

    await store.create_project(workspace_id=ws_a.id, slug="novel", name="Novel")
    await store.create_project(workspace_id=ws_b.id, slug="novel", name="Novel")
    with pytest.raises(ProjectSlugConflict):
        await store.create_project(workspace_id=ws_a.id, slug="novel", name="Novel again")

    assert len(await store.list_projects(ws_a.id)) == 1


@pytest.mark.asyncio
async def test_last_owner_protection_and_transfer(session_factory) -> None:
    store = PostgresTenancyStore(session_factory)
    owner = await _make_user(session_factory, "owner")
    editor = await _make_user(session_factory, "editor")
    workspace = await store.create_workspace(slug="w", name="W")
    await store.add_membership(workspace_id=workspace.id, user_id=owner, role=Role.OWNER)
    await store.add_membership(workspace_id=workspace.id, user_id=editor, role=Role.EDITOR)

    from backend.domain.tenancy import LastOwnerRemoval

    with pytest.raises(LastOwnerRemoval):
        await store.remove_membership(workspace_id=workspace.id, user_id=owner)
    with pytest.raises(LastOwnerRemoval):
        await store.set_role(workspace_id=workspace.id, user_id=owner, role=Role.VIEWER)

    await store.transfer_ownership(workspace_id=workspace.id, from_user_id=owner, to_user_id=editor)
    roles = {m.user_id: m.role for m in await store.list_memberships(workspace.id)}
    assert roles == {owner: Role.EDITOR, editor: Role.OWNER}

    # The former owner can now be removed; the new owner is protected.
    assert await store.remove_membership(workspace_id=workspace.id, user_id=owner) is True
    assert await store.remove_membership(workspace_id=workspace.id, user_id="missing") is False


@pytest.mark.asyncio
async def test_invitation_accept_and_authority_recheck(session_factory) -> None:
    store = PostgresTenancyStore(session_factory)
    owner = await _make_user(session_factory, "owner")
    owner2 = await _make_user(session_factory, "owner2")
    invitee = await _make_user(session_factory, "invitee")
    workspace = await store.create_workspace(slug="w", name="W")
    await store.add_membership(workspace_id=workspace.id, user_id=owner, role=Role.OWNER)
    await store.add_membership(workspace_id=workspace.id, user_id=owner2, role=Role.OWNER)

    invitation = await store.create_invitation(
        workspace_id=workspace.id,
        role=Role.EDITOR,
        token_hash="hash-1",
        created_by_user_id=owner2,
        ttl_seconds=3600,
    )
    fetched = await store.get_invitation_by_token_hash("hash-1")
    assert fetched is not None and fetched.id == invitation.id

    membership = await store.accept_invitation(token_hash="hash-1", user_id=invitee)
    assert membership.role == Role.EDITOR

    # Consumed invitations are not acceptable.
    with pytest.raises(InvitationNotAcceptable):
        await store.accept_invitation(token_hash="hash-1", user_id=invitee)

    # Expired invitation.
    await store.create_invitation(
        workspace_id=workspace.id,
        role=Role.VIEWER,
        token_hash="hash-expired",
        created_by_user_id=owner2,
        ttl_seconds=-1,
    )
    with pytest.raises(InvitationNotAcceptable):
        await store.accept_invitation(token_hash="hash-expired", user_id=invitee)

    # An inviter who has lost authority cannot be accepted.
    await store.create_invitation(
        workspace_id=workspace.id,
        role=Role.VIEWER,
        token_hash="hash-orphan",
        created_by_user_id=owner2,
        ttl_seconds=3600,
    )
    await store.remove_membership(workspace_id=workspace.id, user_id=owner2)
    other = await _make_user(session_factory, "other")
    with pytest.raises(RoleTransitionError):
        await store.accept_invitation(token_hash="hash-orphan", user_id=other)


@pytest.mark.asyncio
async def test_invitation_revoke_and_audit(session_factory) -> None:
    store = PostgresTenancyStore(session_factory)
    owner = await _make_user(session_factory, "owner")
    workspace = await store.create_workspace(slug="w", name="W")
    await store.add_membership(workspace_id=workspace.id, user_id=owner, role=Role.OWNER)

    invitation = await store.create_invitation(
        workspace_id=workspace.id,
        role=Role.EDITOR,
        token_hash="hash-revoke",
        created_by_user_id=owner,
        ttl_seconds=3600,
    )
    assert (
        await store.revoke_invitation(workspace_id=workspace.id, invitation_id=invitation.id)
        is True
    )
    assert (
        await store.revoke_invitation(workspace_id=workspace.id, invitation_id=invitation.id)
        is False
    )

    event = await store.record_audit(
        workspace_id=workspace.id,
        actor_user_id=owner,
        action="invitation.revoked",
        target_type="invitation",
        target_id=invitation.id,
        detail={"role": "editor"},
        correlation_id="cid-1",
    )
    assert event.detail == {"role": "editor"}
    assert event.correlation_id == "cid-1"


@pytest.mark.asyncio
async def test_delete_workspace_cascades_memberships_and_projects(session_factory) -> None:
    store = PostgresTenancyStore(session_factory)
    owner = await _make_user(session_factory, "owner-del")
    workspace = await store.create_workspace(slug="del-ws", name="Delete Me")
    await store.add_membership(workspace_id=workspace.id, user_id=owner, role=Role.OWNER)
    await store.create_project(workspace_id=workspace.id, slug="p", name="P")

    assert await store.delete_workspace(workspace.id) is True
    assert await store.get_workspace(workspace.id) is None
    assert await store.list_memberships(workspace.id) == []
    assert await store.list_projects(workspace.id) == []
    assert await store.delete_workspace(workspace.id) is False


@pytest.mark.asyncio
async def test_delete_workspace_leaves_other_workspaces_intact(session_factory) -> None:
    store = PostgresTenancyStore(session_factory)
    owner = await _make_user(session_factory, "owner-two")
    keep = await store.create_workspace(slug="keep-ws", name="Keep")
    drop = await store.create_workspace(slug="drop-ws", name="Drop")
    await store.add_membership(workspace_id=keep.id, user_id=owner, role=Role.OWNER)
    await store.add_membership(workspace_id=drop.id, user_id=owner, role=Role.OWNER)

    assert await store.delete_workspace(drop.id) is True
    assert await store.get_workspace(keep.id) is not None
    assert len(await store.list_memberships(keep.id)) == 1


@pytest.mark.asyncio
async def test_concurrent_accept_only_one_wins(session_factory) -> None:
    import asyncio

    store = PostgresTenancyStore(session_factory)
    owner = await _make_user(session_factory, "owner-conc")
    accepter_a = await _make_user(session_factory, "accepter-a")
    accepter_b = await _make_user(session_factory, "accepter-b")

    workspace = await store.create_workspace(slug="concurrent", name="Concurrent")
    await store.add_membership(workspace_id=workspace.id, user_id=owner, role=Role.OWNER)
    await store.create_invitation(
        workspace_id=workspace.id,
        role=Role.EDITOR,
        token_hash="hash-concurrent",
        created_by_user_id=owner,
        ttl_seconds=3600,
    )

    async def accept(user_id: str):
        try:
            return await store.accept_invitation(token_hash="hash-concurrent", user_id=user_id)
        except InvitationNotAcceptable:
            return None

    results = await asyncio.gather(accept(accepter_a), accept(accepter_b))
    assert sum(result is not None for result in results) == 1
