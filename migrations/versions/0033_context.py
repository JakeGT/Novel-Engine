"""the author's writing in passages, and what the assistant was given

Revision ID: 0033_context
Revises: 0032_story_time
Create Date: 2026-10-17

Phase 4 (ADR-021). ``text_passages`` is a derived index of the author's texts: each
row is a pointer (text version and character range) and the entries the passage
mentions, never the words themselves, and it can be dropped and rebuilt at any time.
``context_manifests`` keeps exactly what was given to the assistant for a request,
with why, so a draft can always be traced back to its context.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0033_context"
down_revision: str | None = "0032_story_time"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS text_passages (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            version_id TEXT NOT NULL,
            number INTEGER NOT NULL,
            start_char INTEGER NOT NULL,
            end_char INTEGER NOT NULL,
            section TEXT NOT NULL DEFAULT '',
            entity_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            -- The story's version when the names in this passage were found.
            indexed_revision BIGINT NOT NULL DEFAULT 0,
            indexed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, version_id, number),
            CHECK (end_char > start_char)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS text_passages_source_idx "
        "ON text_passages (workspace_id, project_id, source_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS text_passages_entities_idx "
        "ON text_passages USING gin (entity_ids)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS context_manifests (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            branch_id TEXT,
            purpose TEXT NOT NULL,
            plan_id TEXT,
            policy_version TEXT NOT NULL,
            checksum TEXT NOT NULL,
            words INTEGER NOT NULL,
            budget INTEGER NOT NULL,
            manifest JSONB NOT NULL,
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS context_manifests")
    op.execute("DROP TABLE IF EXISTS text_passages")
