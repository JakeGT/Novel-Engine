"""context layers: a waiting list for the passage index, and summaries of scenes

Revision ID: 0052_context_layers
Revises: 0051_planning_help
Create Date: 2026-10-20

CTX-005. A change to a text no longer re-reads it into the passage index inline: it
puts the text on ``passage_index_waiting``, one row per text, and the background worker
reads it in. A row is coalescing and idempotent: asking again for a text already
waiting raises its ``generation``, and a delivery removes the row only when the
generation it read is still the latest, so a change made while a text is being read is
never lost. ``claimed_until`` keeps two servers from reading the same text at once and
spaces out retries after a failure (``attempts``, ``last_error``). How many rows wait,
and since when, is the index's lag.

CTX-006. ``scene_summaries`` keeps a summary of one exact version of a scene's prose:
written by the author, or proposed by the assistant when the author asks and kept only
once the author accepts (or corrects) it. Each row records who or what wrote it, the
model, prompt version and job, and the version of the prose it summarises, so it is
known to be out of date when the prose changes. A summary is a derived layer, never a
story fact. Rows belong to the text and its version, and to the line of the story they
were written in (``branch_id``, NULL for the main story): a what-if shares its scene
texts until it first changes one, so a summary written in a what-if is that what-if's
alone and never replaces the main story's; until the what-if keeps one of its own, it
uses the main story's. After the first change the copy has its own versions, so a
summary never crosses lines; the context reads only the summaries of the line's own
scene texts.

Chained after 0051_planning_help.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0052_context_layers"
down_revision: str | None = "0051_planning_help"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS passage_index_waiting (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            gone BOOLEAN NOT NULL DEFAULT false,
            generation BIGINT NOT NULL DEFAULT 1,
            requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            first_requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            claimed_until TIMESTAMPTZ,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            PRIMARY KEY (workspace_id, project_id, source_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS passage_index_waiting_oldest_idx "
        "ON passage_index_waiting (first_requested_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS scene_summaries (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            version_id TEXT NOT NULL,
            body TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('proposed', 'accepted', 'rejected', 'replaced')),
            origin TEXT NOT NULL CHECK (origin IN ('author', 'model', 'model_corrected')),
            model TEXT,
            prompt_version TEXT,
            job_id TEXT,
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            decided_by TEXT,
            decided_at TIMESTAMPTZ,
            branch_id TEXT,
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )
    # A database that ran this revision before summaries knew their line.
    op.execute("ALTER TABLE scene_summaries ADD COLUMN IF NOT EXISTS branch_id TEXT")
    op.execute(
        "CREATE INDEX IF NOT EXISTS scene_summaries_source_idx "
        "ON scene_summaries (workspace_id, project_id, source_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scene_summaries")
    op.execute("DROP TABLE IF EXISTS passage_index_waiting")
