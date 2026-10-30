"""context remainders: summaries of whole arcs and beats

Revision ID: 0056_context_remainders
Revises: 0055_voice_and_audits
Create Date: 2026-10-21

CTX-006. ``part_summaries`` keeps a summary of a whole arc or beat (or a story): written
by the author, or proposed by the assistant from the kept summaries of the scenes in it
(never the prose) and kept only once the author accepts or corrects it. Each row records
who or what wrote it, the model, prompt version and job, and ``basis``: which kept scene
summaries it was written from (scene id and summary id, in reading order), with their
fingerprint ``basis_digest``, so it is known to be out of date as soon as a scene summary
in the part is kept anew, replaced or dropped. A summary is a derived layer, never a story
fact. Rows belong to the line of the story they were written in (``branch_id``, NULL for
the main story); a what-if uses the main story's until it keeps one of its own. Kept rows
travel in the project archive with the scene summaries, and are deleted with the project.
At most one summary of a part is kept in each line (a partial unique index).
No author prose is stored: the body is the author's or a suggestion the author kept.

Chained after 0055_voice_and_audits.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0056_context_remainders"
down_revision: str | None = "0055_voice_and_audits"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS part_summaries (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            body TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('proposed', 'accepted', 'rejected', 'replaced')),
            origin TEXT NOT NULL CHECK (origin IN ('author', 'model', 'model_corrected')),
            basis JSONB NOT NULL DEFAULT '[]'::jsonb,
            basis_digest TEXT NOT NULL,
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
    op.execute(
        "CREATE INDEX IF NOT EXISTS part_summaries_plan_idx "
        "ON part_summaries (workspace_id, project_id, plan_id, created_at)"
    )
    # At most one kept summary of a part in each line of the story (the main story's
    # branch_id is NULL, so it is compared as '').
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS part_summaries_one_kept_idx "
        "ON part_summaries (workspace_id, project_id, plan_id, COALESCE(branch_id, '')) "
        "WHERE state = 'accepted'"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS part_summaries")
