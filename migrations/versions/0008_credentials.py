"""provider credentials (envelope-encrypted)

Revision ID: 0008_credentials
Revises: 0007_workspace_scope
Create Date: 2026-10-06

Workspace-scoped provider credentials. Only ciphertext, the wrapped data key, and
non-secret metadata columns are stored; plaintext keys never enter the database.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_credentials"
down_revision: str | None = "0007_workspace_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS credentials (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            label TEXT NOT NULL,
            key_fingerprint TEXT NOT NULL,
            key_last4 TEXT NOT NULL,
            key_version INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            wrapped_dek TEXT NOT NULL,
            nonce TEXT NOT NULL,
            ciphertext TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            rotated_at TIMESTAMPTZ,
            last_used_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS credentials_workspace_idx ON credentials (workspace_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS credentials")
