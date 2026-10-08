"""outbox delivery tracking

Revision ID: 0015_outbox_delivery
Revises: 0014_canon_storage
Create Date: 2026-10-08

Adds delivery bookkeeping to the transactional outbox so a projector/consumer can
mark messages published or record a retryable failure with an attempt count, while
keeping projection lag visible.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_outbox_delivery"
down_revision: str | None = "0014_canon_storage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_messages",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("outbox_messages", sa.Column("last_error", sa.Text(), nullable=True))
    op.execute(
        "CREATE INDEX IF NOT EXISTS outbox_messages_pending_idx "
        "ON outbox_messages (workspace_id, project_id, published_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS outbox_messages_pending_idx")
    op.drop_column("outbox_messages", "last_error")
    op.drop_column("outbox_messages", "attempts")