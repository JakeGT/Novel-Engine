"""provider usage records

Revision ID: 0009_provider_usage
Revises: 0008_credentials
Create Date: 2026-10-06

Immutable provider-usage records for limits, retries, and audit. No platform
balance: costs are provider-reported and belong to the user's own account.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_provider_usage"
down_revision: str | None = "0008_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_usage (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            project_id TEXT,
            actor_user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            requested_route TEXT NOT NULL,
            actual_route TEXT,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            latency_ms INTEGER NOT NULL DEFAULT 0,
            attempt INTEGER NOT NULL DEFAULT 1,
            outcome TEXT NOT NULL,
            provider_cost_usd DOUBLE PRECISION,
            idempotency_key TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS provider_usage_scope_time_idx "
        "ON provider_usage (workspace_id, actor_user_id, created_at)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS provider_usage_idempotency_idx "
        "ON provider_usage (idempotency_key) WHERE idempotency_key IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS provider_usage")
