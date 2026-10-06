"""workspaces, memberships, projects, invitations, and audit events

Revision ID: 0005_tenancy
Revises: 0004_identity_and_sessions
Create Date: 2026-10-06

Tenancy tables. Memberships are keyed by ``(workspace_id, user_id)``; project
slugs are unique within a workspace; invitation tokens are stored only as a
SHA-256 hash. Audit events carry identifiers and outcomes, never raw tokens.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005_tenancy"
down_revision: str | None = "0004_identity_and_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS memberships (
            workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (workspace_id, user_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS memberships_user_id_idx ON memberships (user_id)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            slug TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            UNIQUE (workspace_id, slug)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS projects_workspace_id_idx ON projects (workspace_id)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS invitations (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            email TEXT,
            role TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_by_user_id TEXT NOT NULL REFERENCES users(id),
            created_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            accepted_at TIMESTAMPTZ,
            accepted_by_user_id TEXT REFERENCES users(id),
            revoked_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS invitations_workspace_id_idx ON invitations (workspace_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_events (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            actor_user_id TEXT NOT NULL,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            detail JSONB NOT NULL DEFAULT '{}'::jsonb,
            correlation_id TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS audit_events_workspace_created_idx "
        "ON audit_events (workspace_id, created_at)"
    )


def downgrade() -> None:
    for table in ("audit_events", "invitations", "projects", "memberships", "workspaces"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
