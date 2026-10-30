"""voice and audits: the author's usual voice, audit schedules and correction plans

Revision ID: 0055_voice_and_audits
Revises: 0054_conversations
Create Date: 2026-10-21

CON-018. ``voice_baselines`` keeps the scenes the author left out of their usual voice
(one row per story). The usual itself is worked out on read from the scenes' words and
never stored; only the author's choice is. Author data: it travels with the archive.

CON-011. ``audit_schedules`` holds the audits an author opted into running on a schedule:
the part of the plan, the passes, how often (in days), the most each run may use, and
who asked (the run is theirs, checked again against their access and limits each time).
``next_run_at`` is claimed with a conditional update, so two servers never start the same
run. ``last_inputs`` is a digest of the exact versions the last audit that succeeded
read, so a run with nothing changed since is skipped and spends nothing (one that failed
or stopped part of the way is run again). No author words. A schedule belongs to one line of
the story (``branch_id``, NULL for the main story). Not carried by an archive or a
what-if: a restored or forked story never starts spending on its own.

``correction_plans`` keeps a plan of which scenes to change and why, written by the
assistant from an audit's findings: each item cites the findings it answers, and waits
for the author to keep or skip it. Nothing in the story changes. It holds some of the
story: each item keeps its scene's title and the words of the findings it cites, which
quote up to 160 characters of the scene, and the summary, change and why are the
assistant's words about it. It belongs with the audit it came from (its job), which an
archive does not carry, so neither does it; it is never logged.

Every table is deleted with its project.

Chained after 0054_conversations.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0055_voice_and_audits"
down_revision: str | None = "0054_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS voice_baselines (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            left_out JSONB NOT NULL DEFAULT '[]'::jsonb,
            updated_by TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_schedules (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            branch_id TEXT,
            plan_id TEXT NOT NULL DEFAULT '',
            passes JSONB NOT NULL DEFAULT '[]'::jsonb,
            every_days INTEGER NOT NULL CHECK (every_days BETWEEN 1 AND 90),
            max_tokens INTEGER NOT NULL CHECK (max_tokens >= 0),
            actor_user_id TEXT NOT NULL,
            auth_mode TEXT NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            next_run_at TIMESTAMPTZ NOT NULL,
            last_run_at TIMESTAMPTZ,
            last_job_id TEXT,
            last_outcome TEXT,
            last_inputs TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS audit_schedules_one_per_part ON audit_schedules "
        "(workspace_id, project_id, COALESCE(branch_id, ''), plan_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS audit_schedules_due ON audit_schedules (next_run_at) "
        "WHERE enabled"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS correction_plans (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            branch_id TEXT,
            audit_job_id TEXT NOT NULL,
            job_id TEXT,
            summary TEXT NOT NULL DEFAULT '',
            items JSONB NOT NULL DEFAULT '[]'::jsonb,
            model TEXT,
            prompt_version TEXT,
            dropped INTEGER NOT NULL DEFAULT 0,
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS correction_plans_by_audit ON correction_plans "
        "(workspace_id, project_id, audit_job_id, created_at DESC)"
    )


def downgrade() -> None:
    for table in ("correction_plans", "audit_schedules", "voice_baselines"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
