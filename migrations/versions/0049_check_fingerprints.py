"""incremental checks: input fingerprints on runs, a check cache, finding states, dismissals

Revision ID: 0049_check_fingerprints
Revises: 0048_media_observations
Create Date: 2026-10-20

CON-007, CON-001, CON-016.

``consistency_runs`` gains ``profile`` (how much the story checks on its own and the
version of the checks and of the review-bundle policy when the run was made) and
``fingerprints`` (a SHA-256 digest of each input the checks read: the words, the names
they resolve, each slice of the story bible a check reads, the travel table and clock,
the story rules; then one digest per check from the inputs it reads, and which checks
were worked out again or reused). Never the words themselves.

A run is now one line of the story's own record, so a what-if starts with the runs of
the line it was made from (``consistency_runs`` is forked): its key gains ``branch_id``,
and one run per job becomes one run per job in each line.

``check_cache`` keeps what each check that needs no model found for one set of inputs,
keyed by that check's fingerprint. The fingerprint covers everything the check read
(the line of the story is part of it wherever the story is), so it is shared by every
line; it is derived and is never carried by an archive.

``finding_dismissals`` gains ``depends_on``: a digest of what the finding said when it
was dismissed (worked out from the facts, times and places it read). When those change,
the finding comes back.

``review_dismissals`` keeps the author's dismissal of an advisory finding on a proposal
in review, keyed by the change as it stands (its operation, revision and content) and
what the finding said; an edited change or a changed story brings it back.

Downgrading is for development only and loses data: the old key cannot hold a run in
more than one line of the story, so it deletes every run kept in a what-if (every line
but the root), with the check cache, the review-screen dismissals and what each scene
dismissal depended on.

Chained after 0048_media_observations.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0049_check_fingerprints"
down_revision: str | None = "0048_media_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE consistency_runs ADD COLUMN IF NOT EXISTS profile JSONB NOT NULL "
        "DEFAULT '{}'::jsonb"
    )
    op.execute(
        "ALTER TABLE consistency_runs ADD COLUMN IF NOT EXISTS fingerprints JSONB NOT NULL "
        "DEFAULT '{}'::jsonb"
    )
    op.execute("ALTER TABLE consistency_runs DROP CONSTRAINT IF EXISTS consistency_runs_pkey")
    op.execute(
        "ALTER TABLE consistency_runs ADD CONSTRAINT consistency_runs_pkey "
        "PRIMARY KEY (workspace_id, project_id, branch_id, id)"
    )
    op.execute("DROP INDEX IF EXISTS consistency_runs_job")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS consistency_runs_job "
        "ON consistency_runs (workspace_id, project_id, branch_id, job_id) "
        "WHERE job_id IS NOT NULL"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS check_cache (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            check_name TEXT NOT NULL,
            fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
            findings JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, plan_id, check_name, fingerprint)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS check_cache_scene "
        "ON check_cache (workspace_id, project_id, plan_id, check_name, created_at)"
    )
    op.execute(
        "ALTER TABLE finding_dismissals ADD COLUMN IF NOT EXISTS depends_on TEXT "
        "CHECK (depends_on IS NULL OR length(depends_on) = 64)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS review_dismissals (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            mutation_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
            depends_on TEXT NOT NULL CHECK (length(depends_on) = 64),
            code TEXT NOT NULL,
            reason TEXT NOT NULL CHECK (reason IN ('intended', 'not_a_problem', 'other')),
            note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 200),
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            withdrawn_at TIMESTAMPTZ,
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS review_dismissals_change "
        "ON review_dismissals (workspace_id, project_id, mutation_id, created_at)"
    )
    # At most one standing dismissal of the same finding on a proposal.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS review_dismissals_standing "
        "ON review_dismissals (workspace_id, project_id, mutation_id, fingerprint) "
        "WHERE withdrawn_at IS NULL"
    )


def downgrade() -> None:
    """For development only: deletes every run in a what-if (see above)."""
    op.execute("DROP TABLE IF EXISTS review_dismissals")
    op.execute("ALTER TABLE finding_dismissals DROP COLUMN IF EXISTS depends_on")
    op.execute("DROP TABLE IF EXISTS check_cache")
    # Runs copied into a what-if share their id with the original: only the line each was
    # made in can stay under the old key.
    op.execute(
        "DELETE FROM consistency_runs r USING branches b WHERE b.id = r.branch_id AND NOT b.is_root"
    )
    op.execute("DROP INDEX IF EXISTS consistency_runs_job")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS consistency_runs_job "
        "ON consistency_runs (workspace_id, project_id, job_id) WHERE job_id IS NOT NULL"
    )
    op.execute("ALTER TABLE consistency_runs DROP CONSTRAINT IF EXISTS consistency_runs_pkey")
    op.execute(
        "ALTER TABLE consistency_runs ADD CONSTRAINT consistency_runs_pkey "
        "PRIMARY KEY (workspace_id, project_id, id)"
    )
    op.execute("ALTER TABLE consistency_runs DROP COLUMN IF EXISTS fingerprints")
    op.execute("ALTER TABLE consistency_runs DROP COLUMN IF EXISTS profile")
