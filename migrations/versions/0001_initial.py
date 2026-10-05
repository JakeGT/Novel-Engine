"""initial postgres infrastructure

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-05

Enables the pgvector extension and creates the minimal infrastructure metadata
table used by migration and readiness verification. No domain tables are created
in Phase 0.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS infrastructure_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS infrastructure_metadata")
    op.execute("DROP EXTENSION IF EXISTS vector")
