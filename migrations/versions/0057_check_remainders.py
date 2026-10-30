"""check remainders: dismissals for a character or a stretch of the story, review findings seen

Revision ID: 0057_check_remainders
Revises: 0056_context_remainders
Create Date: 2026-10-21

CON-016. ``scoped_dismissals`` keeps the author's dismissal of one kind of finding wider
than one scene: everywhere it is about one character (``entity_id``), or in every scene
from one to another in reading order (``from_plan_id`` to ``to_plan_id``). Each has a
reason, an optional end, and is never changed, only withdrawn (``withdrawn_at``), so
bringing the findings back keeps the record. It belongs to one line of the story
(``branch_id``) and is forked with a what-if, like the dismissals of one scene. No story
words: the author's reason only, up to 200 characters, which is never logged.

``review_finding_seen`` records each advisory finding the review screen has shown on a
proposal (the proposal, the finding's key and its kind), once, so how often each kind is
dismissed can be told to the author as a share of how often it was found. No words.

Both are deleted with the project. Neither travels with the project archive: a scoped
dismissal names scenes and entries the archive carries, but it is a working decision of
this copy, like a review screen's dismissals.

Chained after 0056_context_remainders.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0057_check_remainders"
down_revision: str | None = "0056_context_remainders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS scoped_dismissals (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('check', 'note')),
            code TEXT NOT NULL CHECK (length(code) BETWEEN 1 AND 120),
            scope TEXT NOT NULL CHECK (scope IN ('character', 'range')),
            entity_id TEXT,
            from_plan_id TEXT,
            to_plan_id TEXT,
            reason TEXT NOT NULL CHECK (reason IN ('intended', 'not_a_problem', 'other')),
            note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 200),
            expires_at TIMESTAMPTZ,
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            withdrawn_at TIMESTAMPTZ,
            PRIMARY KEY (workspace_id, project_id, branch_id, id),
            CHECK (
                (scope = 'character' AND entity_id IS NOT NULL)
                OR (scope = 'range' AND from_plan_id IS NOT NULL AND to_plan_id IS NOT NULL)
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS scoped_dismissals_line "
        "ON scoped_dismissals (workspace_id, project_id, branch_id, created_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS review_finding_seen (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            mutation_id TEXT NOT NULL,
            fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
            code TEXT NOT NULL,
            seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, mutation_id, fingerprint)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS review_finding_seen_code "
        "ON review_finding_seen (workspace_id, project_id, code)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS review_finding_seen")
    op.execute("DROP TABLE IF EXISTS scoped_dismissals")
