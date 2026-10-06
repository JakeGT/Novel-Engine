"""durable rate limiter hits

Revision ID: 0006_rate_limiter
Revises: 0005_tenancy
Create Date: 2026-10-06

Sliding-window rate-limit hits shared across workers (invitation acceptance and
future auth/provider limits). Rows are pruned on each check.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_rate_limiter"
down_revision: str | None = "0005_tenancy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS rate_limit_hits (
            id BIGSERIAL PRIMARY KEY,
            key TEXT NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS rate_limit_hits_key_time_idx "
        "ON rate_limit_hits (key, occurred_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rate_limit_hits")