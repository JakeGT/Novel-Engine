"""the scene studio: scene prose, drafts, jobs and how the author writes

Revision ID: 0034_scene_studio
Revises: 0033_context
Create Date: 2026-10-17

Phase 5 (ADR-022). A scene's prose is one of the author's texts (kind ``scene``),
linked to the scene per line of the story in ``scene_manuscripts``. Each text version
says where it came from. Drafts from the assistant wait in ``scene_drafts`` until kept
or discarded. ``jobs`` holds work done in the background, claimed under a lease.
``draft_findings`` keeps what a check with the assistant found; checks without a model
are worked out each time and never stored. ``story_voices`` is the author's note on how
they write.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0034_scene_studio"
down_revision: str | None = "0033_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE source_texts DROP CONSTRAINT IF EXISTS source_texts_kind_check")
    op.execute(
        "ALTER TABLE source_texts ADD CONSTRAINT source_texts_kind_check "
        "CHECK (kind IN ('paste', 'markdown', 'scene'))"
    )
    # Where a version came from. Added with defaults, so no existing row is rewritten.
    op.execute(
        "ALTER TABLE source_versions ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL "
        "DEFAULT 'author' CHECK (origin IN ('author', 'assistant'))"
    )
    op.execute("ALTER TABLE source_versions ADD COLUMN IF NOT EXISTS parent_version_id TEXT")
    op.execute("ALTER TABLE source_versions ADD COLUMN IF NOT EXISTS draft_id TEXT")
    op.execute("ALTER TABLE source_versions ADD COLUMN IF NOT EXISTS manifest_id TEXT")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS scene_manuscripts (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            PRIMARY KEY (workspace_id, project_id, branch_id, plan_id),
            FOREIGN KEY (workspace_id, project_id, source_id)
                REFERENCES source_texts (workspace_id, project_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
            stage TEXT NOT NULL DEFAULT '',
            progress INTEGER NOT NULL DEFAULT 0,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            result JSONB NOT NULL DEFAULT '{}'::jsonb,
            actor_user_id TEXT NOT NULL,
            auth_mode TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT '',
            correlation_id TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT NOT NULL DEFAULT '',
            cancel_requested BOOLEAN NOT NULL DEFAULT false,
            attempts INTEGER NOT NULL DEFAULT 0,
            lease_owner TEXT,
            lease_expires_at TIMESTAMPTZ,
            error_code TEXT,
            error_message TEXT,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ,
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS jobs_claimable ON jobs (status, lease_expires_at, created_at)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS jobs_idempotency ON jobs "
        "(workspace_id, project_id, actor_user_id, idempotency_key) WHERE idempotency_key <> ''"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS scene_drafts (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            branch_id TEXT,
            plan_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            manifest_id TEXT,
            number INTEGER NOT NULL,
            content TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'proposed'
                CHECK (status IN ('proposed', 'kept')),
            kept_version_id TEXT,
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS scene_drafts_scene ON scene_drafts "
        "(workspace_id, project_id, plan_id, created_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS draft_findings (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            target_kind TEXT NOT NULL CHECK (target_kind IN ('draft', 'version')),
            target_id TEXT NOT NULL,
            job_id TEXT,
            category TEXT NOT NULL,
            message TEXT NOT NULL,
            suggestion TEXT NOT NULL DEFAULT '',
            start_char INTEGER,
            end_char INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS draft_findings_target ON draft_findings "
        "(workspace_id, project_id, target_kind, target_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS story_voices (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id)
        )
        """
    )


def downgrade() -> None:
    for table in ("story_voices", "draft_findings", "scene_drafts", "jobs", "scene_manuscripts"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    for column in ("manifest_id", "draft_id", "parent_version_id", "origin"):
        op.execute(f"ALTER TABLE source_versions DROP COLUMN IF EXISTS {column}")
    op.execute("ALTER TABLE source_texts DROP CONSTRAINT IF EXISTS source_texts_kind_check")
    op.execute(
        "ALTER TABLE source_texts ADD CONSTRAINT source_texts_kind_check "
        "CHECK (kind IN ('paste', 'markdown'))"
    )
