"""Deterministic identity/tenancy seed data.

Two users, two similarly named workspaces, projects with colliding slugs across
workspaces, memberships covering each role, and pending/expired/revoked/accepted
invitations. All identifiers and timestamps are fixed so apply/reset is stable
and a "similar ID" isolation test is meaningful. Applied idempotently with
``ON CONFLICT DO NOTHING``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import psycopg

TENANCY_SEED_VERSION = "2026-10-06.1"
FIXED_TIMESTAMP = datetime(2024, 1, 1, tzinfo=UTC)
FAR_FUTURE = datetime(2099, 1, 1, tzinfo=UTC)
LONG_PAST = datetime(2023, 1, 1, tzinfo=UTC)

USER_OWNER = "usr_seed_owner"
USER_EDITOR = "usr_seed_editor"
WS_HARBOR = "ws_seed_harbor"
WS_ALT = "ws_seed_harbor_alt"
LOCAL_ISSUER = "local"
TEST_ISSUER = "https://idp.test/application/o/novel-engine/"

USERS: tuple[tuple[str, str, str, bool], ...] = (
    (USER_OWNER, "owner@example.test", "Seed Owner", True),
    (USER_EDITOR, "editor@example.test", "Seed Editor", True),
)

IDENTITIES: tuple[tuple[str, str, str, str, str], ...] = (
    (LOCAL_ISSUER, "seed-owner", USER_OWNER, "owner@example.test", "Seed Owner"),
    (TEST_ISSUER, "seed-editor", USER_EDITOR, "editor@example.test", "Seed Editor"),
)

WORKSPACES: tuple[tuple[str, str, str], ...] = (
    (WS_HARBOR, "seed-harbor", "Seed Harbor"),
    (WS_ALT, "seed-harbor-alt", "Seed Harbor Alt"),
)

MEMBERSHIPS: tuple[tuple[str, str, str], ...] = (
    (WS_HARBOR, USER_OWNER, "owner"),
    (WS_HARBOR, USER_EDITOR, "editor"),
    (WS_ALT, USER_EDITOR, "owner"),
)

PROJECTS: tuple[tuple[str, str, str, str], ...] = (
    ("prj_seed_harbor", WS_HARBOR, "seed-novel", "Seed Novel"),
    ("prj_seed_harbor_alt", WS_HARBOR, "seed-novel-alt", "Seed Novel Alt"),
    ("prj_seed_alt", WS_ALT, "seed-novel", "Seed Novel"),
)

# (id, workspace_id, role, token_hash, created_by, email, expires_at, accepted_at, revoked_at)
INVITATIONS: tuple[
    tuple[str, str, str, str, str, str | None, datetime, datetime | None, datetime | None], ...
] = (
    (
        "inv_seed_pending",
        WS_HARBOR,
        "editor",
        "seed-hash-pending",
        USER_OWNER,
        None,
        FAR_FUTURE,
        None,
        None,
    ),
    (
        "inv_seed_expired",
        WS_HARBOR,
        "viewer",
        "seed-hash-expired",
        USER_OWNER,
        None,
        LONG_PAST,
        None,
        None,
    ),
    (
        "inv_seed_revoked",
        WS_ALT,
        "editor",
        "seed-hash-revoked",
        USER_EDITOR,
        None,
        FAR_FUTURE,
        None,
        FIXED_TIMESTAMP,
    ),
    (
        "inv_seed_accepted",
        WS_ALT,
        "viewer",
        "seed-hash-accepted",
        USER_EDITOR,
        None,
        FAR_FUTURE,
        FIXED_TIMESTAMP,
        None,
    ),
)


@dataclass
class TenancySeedReport:
    version: str
    inserted: int = 0
    counts: dict[str, int] = field(default_factory=dict)


def counts() -> dict[str, int]:
    return {
        "users": len(USERS),
        "external_identities": len(IDENTITIES),
        "workspaces": len(WORKSPACES),
        "memberships": len(MEMBERSHIPS),
        "projects": len(PROJECTS),
        "invitations": len(INVITATIONS),
    }


def _insert(connection: psycopg.Connection, sql: str, rows: list[tuple]) -> int:
    inserted = 0
    for row in rows:
        cursor = connection.execute(sql, row)
        inserted += cursor.rowcount or 0
    return inserted


def apply_tenancy(dsn: str) -> TenancySeedReport:
    """Idempotently insert the deterministic tenancy rows."""
    report = TenancySeedReport(version=TENANCY_SEED_VERSION)
    with psycopg.connect(dsn, autocommit=True) as connection:
        report.inserted += _insert(
            connection,
            "INSERT INTO users (id, email, display_name, is_active, created_at) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            [(u[0], u[1], u[2], u[3], FIXED_TIMESTAMP) for u in USERS],
        )
        report.inserted += _insert(
            connection,
            "INSERT INTO external_identities "
            "(issuer, subject, user_id, email, display_name, linked_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (issuer, subject) DO NOTHING",
            [(i[0], i[1], i[2], i[3], i[4], FIXED_TIMESTAMP) for i in IDENTITIES],
        )
        report.inserted += _insert(
            connection,
            "INSERT INTO workspaces (id, slug, name, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            [(w[0], w[1], w[2], FIXED_TIMESTAMP, FIXED_TIMESTAMP) for w in WORKSPACES],
        )
        report.inserted += _insert(
            connection,
            "INSERT INTO memberships (workspace_id, user_id, role, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (workspace_id, user_id) DO NOTHING",
            [(m[0], m[1], m[2], FIXED_TIMESTAMP, FIXED_TIMESTAMP) for m in MEMBERSHIPS],
        )
        report.inserted += _insert(
            connection,
            "INSERT INTO projects (id, workspace_id, slug, name, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            [(p[0], p[1], p[2], p[3], FIXED_TIMESTAMP, FIXED_TIMESTAMP) for p in PROJECTS],
        )
        report.inserted += _insert(
            connection,
            "INSERT INTO invitations "
            "(id, workspace_id, role, token_hash, created_by_user_id, email, created_at, "
            " expires_at, accepted_at, revoked_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            [
                (i[0], i[1], i[2], i[3], i[4], i[5], FIXED_TIMESTAMP, i[6], i[7], i[8])
                for i in INVITATIONS
            ],
        )
    report.counts = counts()
    return report


def clear_tenancy(dsn: str) -> bool:
    """Remove all identity/tenancy rows. ``False`` when the tables are unmigrated."""
    try:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(
                "TRUNCATE TABLE outbox_messages, canon_revisions, commit_attempts, "
                "commit_envelopes, review_decisions, mutation_transitions, operation_decisions, "
                "mutation_operation_revisions, mutation_operations, mutation_sets, "
                "branch_reservations, branches, schema_versions, audit_events, invitations, "
                "projects, memberships, workspaces, external_identities, sessions, "
                "login_attempts, users RESTART IDENTITY CASCADE"
            )
        return True
    except psycopg.errors.UndefinedTable:
        return False
