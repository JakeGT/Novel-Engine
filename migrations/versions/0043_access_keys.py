"""access keys for other assistants, and how often each was used

Revision ID: 0043_access_keys
Revises: 0042_delegation
Create Date: 2026-10-18

QRY-003. A member's personal access key lets an assistant they use read a story
through the MCP endpoint. Only the key's SHA-256 digest is stored (unique, so a
presented key is found by its digest); the key itself is shown once and never kept.
``project_id`` is null for a key that reads every story its holder can read in the
workspace. Use is counted per key, day and tool: numbers only, never what was read
or asked. Workspace-owned like credentials (no foreign key: the local workspace has
no row); workspace and project deletion remove them through the purge adapter.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0043_access_keys"
down_revision: str | None = "0042_delegation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS access_keys (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            project_id TEXT,
            name TEXT NOT NULL,
            hint TEXT NOT NULL,
            may_suggest BOOLEAN NOT NULL DEFAULT FALSE,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            last_used_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            revoked_reason TEXT CHECK (
                revoked_reason IS NULL
                OR revoked_reason IN ('holder', 'owner', 'access_lost', 'account_closed')
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS access_keys_workspace_user "
        "ON access_keys (workspace_id, user_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS access_key_uses (
            key_id TEXT NOT NULL REFERENCES access_keys(id) ON DELETE CASCADE,
            workspace_id TEXT NOT NULL,
            day DATE NOT NULL,
            tool TEXT NOT NULL,
            calls INTEGER NOT NULL DEFAULT 0,
            refused INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (key_id, day, tool)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS access_key_uses")
    op.execute("DROP INDEX IF EXISTS access_keys_workspace_user")
    op.execute("DROP TABLE IF EXISTS access_keys")
