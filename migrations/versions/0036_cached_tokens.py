"""prompt tokens the provider read from its cache, on each usage record and job

Revision ID: 0036_cached_tokens
Revises: 0035_scene_readings
Create Date: 2026-10-17

CTX-015. A provider that keeps the start of a prompt it has seen before reports how
many of the prompt's tokens it read back rather than processed again (OpenAI and
OpenRouter: ``usage.prompt_tokens_details.cached_tokens``). They are part of the
prompt tokens already counted, never added to them. Added with a default, so no
existing row is rewritten.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0036_cached_tokens"
down_revision: str | None = "0035_scene_readings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("provider_usage", "jobs"):
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS cached_prompt_tokens INTEGER "
            "NOT NULL DEFAULT 0 CHECK (cached_prompt_tokens >= 0)"
        )


def downgrade() -> None:
    for table in ("jobs", "provider_usage"):
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS cached_prompt_tokens")
