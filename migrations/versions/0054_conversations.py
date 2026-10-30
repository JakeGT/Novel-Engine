"""conversations: a kept thread per author for the assistant, and suggestion runs

Revision ID: 0054_conversations
Revises: 0053_local_connector
Create Date: 2026-10-21

AST-EXP-017, AST-EXP-005. ``assistant_threads`` keeps one running conversation per
author, per kind ("change" for Change anything, "premise" for starting from a premise)
and per line of the story (``branch_id``, NULL for the main story), so the assistant
remembers the thread across visits. ``assistant_turns`` holds its turns in order: what
the author said, and what the assistant answered (its restatement, questions, notes and
the set of suggested changes it led to, by id). The words are the author's own: never
logged, private to their author, erased when the author clears the thread and with the
story. Clearing deletes the thread and its turns. ``tokens_used`` is what the provider
reported for the thread's replies, for the conversation budget (AST-EXP-011).

AST-EXP-007, AST-EXP-013. ``suggestion_runs`` records one run of suggestions the author
reviews (missing connections, a planning round, directions for what happens next, a
premise round): how many were suggested, how the candidates were found (by meaning, or
from the listing), how many quote the author's material and how many do not, and, once
the suggestions are decided, how many the author kept. Counts only: no words.

Chained after 0053_local_connector.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0054_conversations"
down_revision: str | None = "0053_local_connector"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_threads (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            actor_user_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('change', 'premise')),
            branch_id TEXT,
            tokens_used BIGINT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )
    # One running thread per author, kind and line of the story.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS assistant_threads_one_idx ON assistant_threads "
        "(workspace_id, project_id, actor_user_id, kind, COALESCE(branch_id, ''))"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_turns (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('author', 'assistant')),
            body TEXT NOT NULL DEFAULT '',
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, thread_id, position),
            FOREIGN KEY (workspace_id, project_id, thread_id)
                REFERENCES assistant_threads (workspace_id, project_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS suggestion_runs (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            kind TEXT NOT NULL,
            actor_user_id TEXT,
            branch_id TEXT,
            mutation_id TEXT,
            source TEXT NOT NULL DEFAULT 'listing',
            prompt_version TEXT,
            suggested INTEGER NOT NULL DEFAULT 0,
            grounded INTEGER,
            invented INTEGER,
            kept INTEGER,
            settled_at TIMESTAMPTZ,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS suggestion_runs_kind_idx "
        "ON suggestion_runs (workspace_id, project_id, kind, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS suggestion_runs")
    op.execute("DROP TABLE IF EXISTS assistant_turns")
    op.execute("DROP TABLE IF EXISTS assistant_threads")
