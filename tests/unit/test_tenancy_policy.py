from datetime import timedelta

import pytest

from backend.domain.identity import Role, utcnow
from backend.domain.tenancy import (
    Invitation,
    InvitationStatus,
    LastOwnerRemoval,
    Membership,
    Permission,
    RoleTransitionError,
    ensure_member_removable,
    ensure_ownership_transfer,
    ensure_role_change_allowed,
    is_last_owner,
    role_allows,
)

pytestmark = pytest.mark.unit


def _member(user_id: str, role: Role) -> Membership:
    return Membership(workspace_id="ws_1", user_id=user_id, role=role)


def test_owner_has_all_permissions() -> None:
    for permission in Permission:
        assert role_allows(Role.OWNER, permission)


def test_editor_can_write_projects_but_not_administer() -> None:
    assert role_allows(Role.EDITOR, Permission.WORKSPACE_READ)
    assert role_allows(Role.EDITOR, Permission.PROJECT_WRITE)
    assert not role_allows(Role.EDITOR, Permission.WORKSPACE_ADMIN)
    assert not role_allows(Role.EDITOR, Permission.MEMBER_INVITE)


def test_viewer_is_read_only() -> None:
    assert role_allows(Role.VIEWER, Permission.PROJECT_READ)
    assert not role_allows(Role.VIEWER, Permission.PROJECT_WRITE)
    assert not role_allows(Role.VIEWER, Permission.MEMBER_REMOVE)


def test_last_owner_detection() -> None:
    solo = [_member("u1", Role.OWNER)]
    assert is_last_owner(solo, "u1") is True
    two = [_member("u1", Role.OWNER), _member("u2", Role.OWNER)]
    assert is_last_owner(two, "u1") is False
    assert is_last_owner([_member("u1", Role.OWNER), _member("u2", Role.EDITOR)], "u2") is False


def test_cannot_remove_or_demote_last_owner() -> None:
    memberships = [_member("u1", Role.OWNER), _member("u2", Role.EDITOR)]
    with pytest.raises(LastOwnerRemoval):
        ensure_member_removable(memberships, "u1")
    with pytest.raises(LastOwnerRemoval):
        ensure_role_change_allowed(memberships, "u1", Role.VIEWER)


def test_can_remove_non_owner_and_promote_to_owner() -> None:
    memberships = [_member("u1", Role.OWNER), _member("u2", Role.EDITOR)]
    ensure_member_removable(memberships, "u2")
    ensure_role_change_allowed(memberships, "u2", Role.OWNER)


def test_ownership_transfer_rules() -> None:
    memberships = [_member("u1", Role.OWNER), _member("u2", Role.EDITOR)]
    ensure_ownership_transfer(memberships, "u1", "u2")

    with pytest.raises(RoleTransitionError):
        ensure_ownership_transfer(memberships, "u1", "u1")
    with pytest.raises(RoleTransitionError):
        ensure_ownership_transfer(memberships, "u2", "u1")  # u2 is not an owner
    with pytest.raises(RoleTransitionError):
        ensure_ownership_transfer(memberships, "u1", "u3")  # not a member


def _invitation(**overrides) -> Invitation:
    values = {
        "id": "inv_1",
        "workspace_id": "ws_1",
        "role": Role.EDITOR,
        "token_hash": "hash",
        "created_by_user_id": "u1",
        "expires_at": utcnow() + timedelta(hours=1),
    }
    values.update(overrides)
    return Invitation(**values)


def test_invitation_status_lifecycle() -> None:
    now = utcnow()
    pending = _invitation()
    assert pending.status(now) == InvitationStatus.PENDING
    assert pending.is_acceptable(now) is True

    expired = _invitation(expires_at=now - timedelta(seconds=1))
    assert expired.status(now) == InvitationStatus.EXPIRED
    assert expired.is_acceptable(now) is False

    accepted = _invitation(accepted_at=now)
    assert accepted.status(now) == InvitationStatus.ACCEPTED
    assert accepted.is_acceptable(now) is False

    revoked = _invitation(revoked_at=now)
    assert revoked.status(now) == InvitationStatus.REVOKED
    assert revoked.is_acceptable(now) is False
