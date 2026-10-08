"""one active credential per workspace and provider

Revision ID: 0011_active_credential_unique
Revises: 0010_vector_project_scope
Create Date: 2026-10-08

The credential vault serializes replacement with a PostgreSQL advisory lock. This
partial unique index is the durable backstop: concurrent writers or future code
paths cannot leave credential selection ambiguous.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_active_credential_unique"
down_revision: str | None = "0010_vector_project_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Retain the newest active credential if prototype data already contains
    # duplicates, and revoke the rest before creating the invariant.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY workspace_id, provider
                       ORDER BY created_at DESC, id DESC
                   ) AS position
            FROM credentials
            WHERE status = 'active'
        )
        UPDATE credentials AS credential
        SET status = 'revoked', revoked_at = COALESCE(credential.revoked_at, now())
        FROM ranked
        WHERE credential.id = ranked.id AND ranked.position > 1
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS credentials_one_active_provider_idx "
        "ON credentials (workspace_id, provider) WHERE status = 'active'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS credentials_one_active_provider_idx")
