"""commit idempotency and lifecycle integrity

Revision ID: 0018_commit_integrity
Revises: 0017_media
Create Date: 2026-10-09

One mutation set may produce at most one immutable commit envelope. The service
derives the commit id from the mutation id, while this constraint protects the
same invariant against alternate writers and concurrent retries.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018_commit_integrity"
down_revision: str | None = "0017_media"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS commit_envelopes_mutation_id_uq "
        "ON commit_envelopes (mutation_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS commit_envelopes_mutation_id_uq")
