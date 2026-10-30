"""mutation review workflow: transitions, operation revisions, operation decisions

Revision ID: 0019_review_workflow
Revises: 0018_commit_integrity
Create Date: 2026-10-09

Mutation-set state changes become an append-only transition log (actor, time,
reason, correlation/idempotency ids). Editing a proposed operation appends an
immutable operation revision instead of mutating reviewed evidence, and
per-operation accept/reject decisions bind to the exact revision reviewed.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019_review_workflow"
down_revision: str | None = "0018_commit_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = (
    "'draft', 'validating', 'needs_review', 'approved', 'committing', 'committed', "
    "'rejected', 'recovery_required'"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE mutation_sets ADD COLUMN IF NOT EXISTS intent TEXT NOT NULL DEFAULT 'edit'"
    )
    op.execute(
        "ALTER TABLE mutation_sets ADD CONSTRAINT mutation_sets_status_ck "
        f"CHECK (status IN ({_STATUSES}))"
    )
    op.execute(
        "ALTER TABLE mutation_sets ADD CONSTRAINT mutation_sets_intent_ck "
        "CHECK (intent IN ('edit', 'revert'))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS mutation_sets_scope_status_idx "
        "ON mutation_sets (workspace_id, project_id, status, created_at)"
    )
    op.execute(
        "ALTER TABLE mutation_operations ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL "
        "DEFAULT 1 CHECK (revision >= 1)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mutation_operation_revisions (
            mutation_id TEXT NOT NULL REFERENCES mutation_sets (id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            revision INTEGER NOT NULL CHECK (revision >= 1),
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            operation_type TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            actor_user_id TEXT,
            reason TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (mutation_id, ordinal, revision)
        )
        """
    )
    # Existing proposals keep their only revision as immutable history.
    op.execute(
        """
        INSERT INTO mutation_operation_revisions (
            mutation_id, ordinal, revision, workspace_id, project_id, operation_type, payload
        )
        SELECT mutation_id, ordinal, revision, workspace_id, project_id, operation_type, payload
        FROM mutation_operations
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS operation_decisions (
            id TEXT PRIMARY KEY,
            mutation_id TEXT NOT NULL REFERENCES mutation_sets (id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            operation_revision INTEGER NOT NULL,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            decision TEXT NOT NULL CHECK (decision IN ('accepted', 'rejected')),
            actor_user_id TEXT,
            note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            seq BIGSERIAL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS operation_decisions_mutation_idx "
        "ON operation_decisions (mutation_id, seq)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mutation_transitions (
            id TEXT PRIMARY KEY,
            mutation_id TEXT NOT NULL REFERENCES mutation_sets (id) ON DELETE CASCADE,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            from_status TEXT NOT NULL,
            to_status TEXT NOT NULL,
            actor_user_id TEXT,
            reason TEXT NOT NULL CHECK (reason <> ''),
            correlation_id TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            seq BIGSERIAL,
            CHECK (from_status <> to_status)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS mutation_transitions_mutation_idx "
        "ON mutation_transitions (mutation_id, seq)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mutation_transitions")
    op.execute("DROP TABLE IF EXISTS operation_decisions")
    op.execute("DROP TABLE IF EXISTS mutation_operation_revisions")
    op.execute("ALTER TABLE mutation_operations DROP COLUMN IF EXISTS revision")
    op.execute("DROP INDEX IF EXISTS mutation_sets_scope_status_idx")
    op.execute("ALTER TABLE mutation_sets DROP CONSTRAINT IF EXISTS mutation_sets_intent_ck")
    op.execute("ALTER TABLE mutation_sets DROP CONSTRAINT IF EXISTS mutation_sets_status_ck")
    op.execute("ALTER TABLE mutation_sets DROP COLUMN IF EXISTS intent")
