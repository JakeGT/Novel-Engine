"""Access keys for other assistants (QRY-003): made, listed, withdrawn, and checked each time."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from backend.core.security import hash_token
from backend.domain.access_keys import AccessKeyStatus, RevokedBecause, generate_key
from backend.domain.errors import (
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
    ValidationError,
)
from backend.domain.identity import AccessScope, AuthMode, IdentityClaims, Role, utcnow
from backend.services.access_key_service import AccessKeyService
from backend.services.tenancy_service import TenancyService
from tests.doubles import (
    InMemoryAccessKeyStore,
    InMemoryIdentityStore,
    InMemoryRateLimiter,
    InMemoryTenancyStore,
)

pytestmark = pytest.mark.application


class _Clock:
    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self) -> datetime:
        return self.now


@dataclass
class World:
    service: AccessKeyService
    store: InMemoryAccessKeyStore
    tenancy: TenancyService
    tenancy_store: InMemoryTenancyStore
    identity: InMemoryIdentityStore
    clock: _Clock
    owner: AccessScope
    editor: AccessScope
    viewer: AccessScope
    workspace_id: str
    project_id: str
    other_project_id: str


async def _world(settings_factory, *, calls_per_minute: int = 120, failed: int = 30) -> World:
    settings = settings_factory(
        APP_ENV="test",
        AUTH_MODE="hosted",
        OIDC_ISSUER="https://auth.example.test/application/o/novel/",
        OIDC_CLIENT_ID="client",
        OIDC_CLIENT_SECRET="secret",
        OIDC_REDIRECT_URI="https://novel.example.test/auth/callback",
    )
    tenancy_store = InMemoryTenancyStore()
    identity = InMemoryIdentityStore()
    tenancy = TenancyService(tenancy_store, identity_store=identity, settings=settings)
    users = {}
    for name in ("owner", "editor", "viewer"):
        user = await identity.get_or_create_user(
            IdentityClaims(issuer="local", subject=name, display_name=name.title())
        )
        users[name] = AccessScope(actor_user_id=user.id, auth_mode=AuthMode.HOSTED)
    workspace = await tenancy_store.create_workspace(slug="ws", name="WS")
    await tenancy_store.add_membership(
        workspace_id=workspace.id, user_id=users["owner"].actor_user_id, role=Role.OWNER
    )
    await tenancy_store.add_membership(
        workspace_id=workspace.id, user_id=users["editor"].actor_user_id, role=Role.EDITOR
    )
    await tenancy_store.add_membership(
        workspace_id=workspace.id, user_id=users["viewer"].actor_user_id, role=Role.VIEWER
    )
    project = await tenancy_store.create_project(workspace_id=workspace.id, slug="a", name="A")
    other = await tenancy_store.create_project(workspace_id=workspace.id, slug="b", name="B")
    store = InMemoryAccessKeyStore()
    clock = _Clock()
    service = AccessKeyService(
        store,
        tenancy=tenancy,
        identity_store=identity,
        rate_limiter=InMemoryRateLimiter(),
        settings=settings,
        calls_per_minute=calls_per_minute,
        failed_per_minute=failed,
        now=clock,
    )
    return World(
        service=service,
        store=store,
        tenancy=tenancy,
        tenancy_store=tenancy_store,
        identity=identity,
        clock=clock,
        owner=users["owner"],
        editor=users["editor"],
        viewer=users["viewer"],
        workspace_id=workspace.id,
        project_id=project.id,
        other_project_id=other.id,
    )


async def _make(world: World, scope: AccessScope, **overrides) -> tuple:
    values = {"name": "My assistant", "project_id": world.project_id, "may_suggest": False}
    values["lifetime_days"] = overrides.pop("lifetime_days", None)
    values.update(overrides)
    return await world.service.create(scope, world.workspace_id, **values)


@pytest.mark.asyncio
async def test_a_key_is_shown_once_and_only_its_digest_is_kept(settings_factory) -> None:
    world = await _world(settings_factory)
    key, raw = await _make(world, world.editor)

    stored = world.store.keys[key.id]
    assert stored.token_hash == hash_token(raw)
    assert raw not in stored.model_dump_json()
    assert stored.hint == raw[-4:]
    assert stored.expires_at - stored.created_at == timedelta(days=90)
    listed = await world.service.list_keys(world.editor, world.workspace_id)
    assert [item.key.id for item in listed] == [key.id]
    assert all(raw not in item.key.model_dump_json() for item in listed)


@pytest.mark.asyncio
async def test_lifetime_name_and_suggesting_are_checked(settings_factory) -> None:
    world = await _world(settings_factory)
    with pytest.raises(ValidationError):
        await _make(world, world.editor, lifetime_days=366)
    with pytest.raises(ValidationError):
        await _make(world, world.editor, name="   ")
    # Someone who can only read cannot make a key that suggests changes.
    with pytest.raises(ForbiddenError):
        await _make(world, world.viewer, may_suggest=True)
    key, _ = await _make(world, world.viewer)
    assert key.may_suggest is False
    # A story in another workspace is not there.
    with pytest.raises(NotFoundError):
        await _make(world, world.editor, project_id="prj_elsewhere")


@pytest.mark.asyncio
async def test_a_person_sees_their_own_keys_and_an_owner_sees_everyone_s(settings_factory) -> None:
    world = await _world(settings_factory)
    editor_key, _ = await _make(world, world.editor)
    owner_key, _ = await _make(world, world.owner)

    editor_sees = await world.service.list_keys(world.editor, world.workspace_id)
    assert [item.key.id for item in editor_sees] == [editor_key.id]
    owner_sees = {
        item.key.id: item for item in await world.service.list_keys(world.owner, world.workspace_id)
    }
    assert set(owner_sees) == {editor_key.id, owner_key.id}
    assert owner_sees[editor_key.id].holder_name == "Editor"
    assert owner_sees[editor_key.id].mine is False

    # Someone else's key is simply not there for a non-owner.
    with pytest.raises(NotFoundError):
        await world.service.revoke(world.editor, world.workspace_id, owner_key.id)
    withdrawn = await world.service.revoke(world.owner, world.workspace_id, editor_key.id)
    assert withdrawn.revoked_reason is RevokedBecause.OWNER
    assert withdrawn.status() is AccessKeyStatus.REVOKED


@pytest.mark.asyncio
async def test_a_working_key_authenticates_as_its_holder(settings_factory) -> None:
    world = await _world(settings_factory)
    key, raw = await _make(world, world.editor, may_suggest=True)

    caller = await world.service.authenticate(raw, client="1.2.3.4")
    assert caller.key.id == key.id
    assert caller.role is Role.EDITOR
    assert caller.can_suggest is True
    scope = caller.scope(world.project_id)
    assert (scope.actor_user_id, scope.workspace_id) == (
        world.editor.actor_user_id,
        world.workspace_id,
    )
    assert world.store.keys[key.id].last_used_at == world.clock.now


@pytest.mark.asyncio
async def test_a_key_stops_suggesting_when_its_holder_can_no_longer_write(settings_factory) -> None:
    world = await _world(settings_factory)
    _key, raw = await _make(world, world.editor, may_suggest=True)
    await world.tenancy_store.set_role(
        workspace_id=world.workspace_id, user_id=world.editor.actor_user_id, role=Role.VIEWER
    )
    caller = await world.service.authenticate(raw, client="1.2.3.4")
    assert caller.role is Role.VIEWER
    assert caller.can_suggest is False


@pytest.mark.asyncio
async def test_expired_withdrawn_and_unknown_keys_are_refused(settings_factory) -> None:
    world = await _world(settings_factory)
    key, raw = await _make(world, world.editor, lifetime_days=1)

    world.clock.now = world.clock.now + timedelta(days=1, seconds=1)
    with pytest.raises(UnauthorizedError, match="expired"):
        await world.service.authenticate(raw, client="1.2.3.4")

    other, other_raw = await _make(world, world.editor)
    await world.service.revoke(world.editor, world.workspace_id, other.id)
    with pytest.raises(UnauthorizedError, match="withdrawn"):
        await world.service.authenticate(other_raw, client="1.2.3.4")

    with pytest.raises(UnauthorizedError):
        await world.service.authenticate(generate_key(), client="1.2.3.4")
    with pytest.raises(UnauthorizedError):
        await world.service.authenticate("nvlpat_not-a-key", client="1.2.3.4")
    with pytest.raises(UnauthorizedError):
        await world.service.authenticate(None, client="1.2.3.4")
    assert key.id in world.store.keys


@pytest.mark.asyncio
async def test_a_key_is_withdrawn_for_good_when_its_holder_loses_access(settings_factory) -> None:
    world = await _world(settings_factory)
    key, raw = await _make(world, world.editor)
    await world.service.authenticate(raw, client="1.2.3.4")

    # Checked on the next request, not cached.
    await world.tenancy_store.remove_membership(
        workspace_id=world.workspace_id, user_id=world.editor.actor_user_id
    )
    with pytest.raises(UnauthorizedError, match="no longer has access"):
        await world.service.authenticate(raw, client="1.2.3.4")
    assert world.store.keys[key.id].revoked_reason is RevokedBecause.ACCESS_LOST

    # Coming back does not bring the key back.
    await world.tenancy_store.add_membership(
        workspace_id=world.workspace_id, user_id=world.editor.actor_user_id, role=Role.EDITOR
    )
    with pytest.raises(UnauthorizedError, match="withdrawn"):
        await world.service.authenticate(raw, client="1.2.3.4")


@pytest.mark.asyncio
async def test_a_key_for_a_removed_story_or_a_closed_account_is_withdrawn(settings_factory) -> None:
    world = await _world(settings_factory)
    story_key, story_raw = await _make(world, world.editor)
    await world.tenancy_store.delete_project(world.workspace_id, world.project_id)
    with pytest.raises(UnauthorizedError):
        await world.service.authenticate(story_raw, client="1.2.3.4")
    assert world.store.keys[story_key.id].revoked_reason is RevokedBecause.ACCESS_LOST

    account_key, account_raw = await _make(world, world.owner, project_id=None)
    world.identity.users[world.owner.actor_user_id].is_active = False
    with pytest.raises(UnauthorizedError):
        await world.service.authenticate(account_raw, client="1.2.3.4")
    assert world.store.keys[account_key.id].revoked_reason is RevokedBecause.ACCOUNT_CLOSED


@pytest.mark.asyncio
async def test_calls_with_a_key_and_failed_attempts_are_rate_limited(settings_factory) -> None:
    world = await _world(settings_factory, calls_per_minute=3, failed=2)
    _key, raw = await _make(world, world.editor)
    for _ in range(3):
        await world.service.authenticate(raw, client="1.2.3.4")
    with pytest.raises(RateLimitedError):
        await world.service.authenticate(raw, client="1.2.3.4")

    for _ in range(2):
        with pytest.raises(UnauthorizedError):
            await world.service.authenticate(generate_key(), client="9.9.9.9")
    with pytest.raises(RateLimitedError):
        await world.service.authenticate(generate_key(), client="9.9.9.9")


@pytest.mark.asyncio
async def test_use_is_counted_by_tool_without_content(settings_factory) -> None:
    world = await _world(settings_factory)
    key, raw = await _make(world, world.editor)
    caller = await world.service.authenticate(raw, client="1.2.3.4")
    await world.service.record_use(caller, "read_entry", refused=False)
    await world.service.record_use(caller, "read_entry", refused=True)
    await world.service.record_use(caller, "list_plan", refused=False)

    uses = await world.service.uses(world.editor, world.workspace_id, key.id)
    assert {(use.tool, use.calls, use.refused) for use in uses} == {
        ("read_entry", 2, 1),
        ("list_plan", 1, 0),
    }


@pytest.mark.asyncio
async def test_a_member_has_a_bounded_number_of_working_keys(settings_factory) -> None:
    world = await _world(settings_factory)
    from backend.domain.access_keys import MAX_ACTIVE_KEYS_PER_MEMBER

    for _ in range(MAX_ACTIVE_KEYS_PER_MEMBER):
        await _make(world, world.editor)
    with pytest.raises(ValidationError, match="Withdraw one"):
        await _make(world, world.editor)


@pytest.mark.asyncio
async def test_removing_a_member_withdraws_their_keys_before_they_are_used(
    settings_factory,
) -> None:
    world = await _world(settings_factory)
    key, raw = await _make(world, world.editor)
    owner_key, owner_raw = await _make(world, world.owner)

    assert await world.tenancy.remove_member(
        world.owner, world.workspace_id, world.editor.actor_user_id
    )
    # Re-added before the key was ever used again: it still does not come back.
    await world.tenancy_store.add_membership(
        workspace_id=world.workspace_id, user_id=world.editor.actor_user_id, role=Role.EDITOR
    )
    with pytest.raises(UnauthorizedError, match="withdrawn"):
        await world.service.authenticate(raw, client="1.2.3.4")
    assert world.store.keys[key.id].revoked_reason is RevokedBecause.ACCESS_LOST
    audited = [
        event
        for event in world.tenancy_store.audit
        if event.action == "access_key.revoked" and event.target_id == key.id
    ]
    assert len(audited) == 1
    assert audited[0].detail == {"by": "access_lost"}
    assert audited[0].actor_user_id == world.owner.actor_user_id
    # Nobody else's key is touched.
    assert world.store.keys[owner_key.id].revoked_at is None
    assert (await world.service.authenticate(owner_raw, client="1.2.3.4")).key.id == owner_key.id


@pytest.mark.asyncio
async def test_withdrawn_and_expired_keys_count_against_the_address(settings_factory) -> None:
    world = await _world(settings_factory, failed=2)
    withdrawn, withdrawn_raw = await _make(world, world.editor)
    await world.service.revoke(world.editor, world.workspace_id, withdrawn.id)
    _expiring, expiring_raw = await _make(world, world.editor, lifetime_days=1)
    world.clock.now = world.clock.now + timedelta(days=1, seconds=1)

    with pytest.raises(UnauthorizedError, match="withdrawn"):
        await world.service.authenticate(withdrawn_raw, client="5.5.5.5")
    with pytest.raises(UnauthorizedError, match="expired"):
        await world.service.authenticate(expiring_raw, client="5.5.5.5")
    with pytest.raises(RateLimitedError):
        await world.service.authenticate(withdrawn_raw, client="5.5.5.5")
