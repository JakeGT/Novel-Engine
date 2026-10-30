"""how much the assistant may do and spend on its own

Revision ID: 0038_usage_controls
Revises: 0037_finding_decisions
Create Date: 2026-10-18

CON-009. ``assistant_settings`` holds, per story, how much checking runs on its own
(``check_profile``: only when asked, the default; balanced; thorough), the tokens
automatic checks may use for one scene in a day, and how often a thorough check may
run after saving. A row whose ``project_id`` is empty is the workspace's own: an owner's
ceilings for everyone in it.

AST-EXP-011 and OPS-002. The same rows hold the size above which a single request must
be confirmed before it is sent (``confirm_above_tokens``) and the most one conversation
with the assistant may use (``conversation_tokens``); both are off when null, and the
lower of the story's and the workspace's applies. ``assist_sessions.tokens_used`` counts
what each conversation has used. ``jobs.provider_cost_usd`` keeps the cost a provider
reported for a job's calls, when it reports one (OpenRouter does); no price is ever
worked out here.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0038_usage_controls"
down_revision: str | None = "0037_finding_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_settings (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL DEFAULT '',
            check_profile TEXT NOT NULL DEFAULT 'on_request'
                CHECK (check_profile IN ('on_request', 'balanced', 'thorough')),
            scene_tokens INTEGER NOT NULL DEFAULT 40000
                CHECK (scene_tokens BETWEEN 1000 AND 10000000),
            check_minutes INTEGER NOT NULL DEFAULT 15 CHECK (check_minutes BETWEEN 1 AND 1440),
            confirm_above_tokens INTEGER CHECK (confirm_above_tokens > 0),
            conversation_tokens INTEGER CHECK (conversation_tokens > 0),
            updated_by TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id)
        )
        """
    )
    op.execute(
        "ALTER TABLE assist_sessions ADD COLUMN IF NOT EXISTS tokens_used BIGINT NOT NULL "
        "DEFAULT 0 CHECK (tokens_used >= 0)"
    )
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS provider_cost_usd DOUBLE PRECISION")


def downgrade() -> None:
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS provider_cost_usd")
    op.execute("ALTER TABLE assist_sessions DROP COLUMN IF EXISTS tokens_used")
    op.execute("DROP TABLE IF EXISTS assistant_settings")
