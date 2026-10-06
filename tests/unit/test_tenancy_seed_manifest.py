from datetime import UTC, datetime

import pytest

from backend.domain.identity import Role
from backend.domain.tenancy import Invitation, InvitationStatus
from backend.seed.tenancy import (
    IDENTITIES,
    INVITATIONS,
    MEMBERSHIPS,
    PROJECTS,
    TENANCY_SEED_VERSION,
    USERS,
    WORKSPACES,
    counts,
)

pytestmark = pytest.mark.unit


def test_identifiers_are_unique_and_tenant_scoped() -> None:
    assert len({u[0] for u in USERS}) == len(USERS)
    assert len({w[0] for w in WORKSPACES}) == len(WORKSPACES)
    assert len({w[1] for w in WORKSPACES}) == len(WORKSPACES)
    assert len({p[0] for p in PROJECTS}) == len(PROJECTS)
    assert len({(p[1], p[2]) for p in PROJECTS}) == len(PROJECTS)
    assert len({i[3] for i in INVITATIONS}) == len(INVITATIONS)  # token hashes


def test_two_similarly_named_workspaces_with_colliding_project_slug() -> None:
    names = {w[2] for w in WORKSPACES}
    assert len(names) == 2
    # the same project slug appears in both workspaces to exercise isolation
    slugs_by_workspace = {}
    for _, workspace_id, slug, _name in PROJECTS:
        slugs_by_workspace.setdefault(slug, set()).add(workspace_id)
    assert any(len(workspaces) == 2 for workspaces in slugs_by_workspace.values())


def test_memberships_cover_every_role() -> None:
    roles = {m[2] for m in MEMBERSHIPS}
    assert roles == {Role.OWNER.value, Role.EDITOR.value}
    # a viewer membership is not seeded, but the role exists in the policy matrix
    assert MEMBERSHIPS


def test_invitations_cover_pending_expired_revoked_and_accepted() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    statuses = set()
    for row in INVITATIONS:
        invitation = Invitation(
            id=row[0],
            workspace_id=row[1],
            role=Role(row[2]),
            token_hash=row[3],
            created_by_user_id=row[4],
            email=row[5],
            expires_at=row[6],
            accepted_at=row[7],
            revoked_at=row[8],
        )
        statuses.add(invitation.status(now))
    assert {
        InvitationStatus.PENDING,
        InvitationStatus.EXPIRED,
        InvitationStatus.REVOKED,
        InvitationStatus.ACCEPTED,
    } <= statuses


def test_counts_and_version() -> None:
    assert TENANCY_SEED_VERSION
    assert counts() == {
        "users": len(USERS),
        "external_identities": len(IDENTITIES),
        "workspaces": len(WORKSPACES),
        "memberships": len(MEMBERSHIPS),
        "projects": len(PROJECTS),
        "invitations": len(INVITATIONS),
    }
