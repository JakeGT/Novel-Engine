"""canon journal: branches, reservations, mutation sets, envelopes, outbox

Revision ID: 0013_canon_journal
Revises: 0012_schema_versions
Create Date: 2026-10-08

PostgreSQL is the authority for the commit workflow (ADR-011). Every row carries
workspace/project ownership; branch heads advance only from an expected revision,
commit ids are immutable, and the outbox is idempotent by key.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013_canon_journal"
down_revision: str | None = "0012_schema_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS branches (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            is_root BOOLEAN NOT NULL DEFAULT true,
            head_revision BIGINT NOT NULL DEFAULT 0 CHECK (head_revision >= 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, project_id, name)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS branches_one_root_idx "
        "ON branches (workspace_id, project_id) WHERE is_root"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS branch_reservations (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            branch_id TEXT NOT NULL REFERENCES branches (id) ON DELETE CASCADE,
            mutation_id TEXT NOT NULL,
            expected_revision BIGINT NOT NULL,
            reserved_revision BIGINT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK (reserved_revision = expected_revision + 1)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS branch_reservations_active_idx "
        "ON branch_reservations (branch_id) WHERE status = 'active'"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mutation_sets (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            branch_id TEXT NOT NULL REFERENCES branches (id) ON DELETE CASCADE,
            schema_version_id TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            origin TEXT NOT NULL DEFAULT 'author',
            actor_user_id TEXT,
            correlation_id TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT NOT NULL DEFAULT '',
            expected_head BIGINT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS mutation_sets_idempotency_idx "
        "ON mutation_sets (workspace_id, project_id, idempotency_key) "
        "WHERE idempotency_key <> ''"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mutation_operations (
            mutation_id TEXT NOT NULL REFERENCES mutation_sets (id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            operation_type TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            PRIMARY KEY (mutation_id, ordinal)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS review_decisions (
            id TEXT PRIMARY KEY,
            mutation_id TEXT NOT NULL REFERENCES mutation_sets (id) ON DELETE CASCADE,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            decision TEXT NOT NULL,
            actor_user_id TEXT,
            note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS commit_envelopes (
            commit_id TEXT PRIMARY KEY,
            mutation_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            revision BIGINT NOT NULL,
            expected_head BIGINT NOT NULL,
            schema_version_id TEXT,
            canonical TEXT NOT NULL,
            checksum TEXT NOT NULL,
            actor_user_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (branch_id, revision)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS commit_attempts (
            id TEXT PRIMARY KEY,
            commit_id TEXT NOT NULL REFERENCES commit_envelopes (commit_id) ON DELETE CASCADE,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            status TEXT NOT NULL,
            detail TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS canon_revisions (
            id TEXT PRIMARY KEY,
            revision BIGINT NOT NULL,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            commit_id TEXT NOT NULL UNIQUE,
            expected_head BIGINT NOT NULL,
            result_head BIGINT NOT NULL,
            schema_version_id TEXT,
            envelope_checksum TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (branch_id, revision)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS outbox_messages (
            id TEXT PRIMARY KEY,
            topic TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            revision BIGINT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            idempotency_key TEXT NOT NULL UNIQUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            published_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS outbox_messages_scope_idx "
        "ON outbox_messages (workspace_id, project_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS outbox_messages")
    op.execute("DROP TABLE IF EXISTS canon_revisions")
    op.execute("DROP TABLE IF EXISTS commit_attempts")
    op.execute("DROP TABLE IF EXISTS commit_envelopes")
    op.execute("DROP TABLE IF EXISTS review_decisions")
    op.execute("DROP TABLE IF EXISTS mutation_operations")
    op.execute("DROP TABLE IF EXISTS mutation_sets")
    op.execute("DROP TABLE IF EXISTS branch_reservations")
    op.execute("DROP TABLE IF EXISTS branches")
