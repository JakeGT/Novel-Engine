"""normalized canonical PostgreSQL storage and projection watermarks

Revision ID: 0014_canon_storage
Revises: 0013_canon_journal
Create Date: 2026-10-08

ADR-012: PostgreSQL owns normalized, versioned canonical state alongside the
immutable journal, committed in one transaction. Neo4j becomes a rebuildable
projection tracked by a per-project/branch watermark. Prototype rows may be
reset; this migration is deterministic from an empty database.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_canon_storage"
down_revision: str | None = "0013_canon_journal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCOPE = "workspace_id TEXT NOT NULL, project_id TEXT NOT NULL, branch_id TEXT NOT NULL"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS canon_entities (
            {_SCOPE},
            id TEXT NOT NULL,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            slug TEXT,
            lifecycle TEXT NOT NULL DEFAULT 'active',
            schema_version_id TEXT,
            created_revision INTEGER NOT NULL,
            retired_revision INTEGER,
            attributes JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            PRIMARY KEY (workspace_id, project_id, branch_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS canon_entities_name_idx "
        "ON canon_entities (workspace_id, project_id, name)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS canon_facts (
            {_SCOPE},
            id TEXT NOT NULL,
            proposition JSONB NOT NULL,
            status TEXT NOT NULL DEFAULT 'asserted',
            confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
            provenance_id TEXT NOT NULL,
            created_revision INTEGER NOT NULL,
            closed_revision INTEGER,
            valid_from TIMESTAMPTZ,
            valid_until TIMESTAMPTZ,
            PRIMARY KEY (workspace_id, project_id, branch_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS canon_facts_subject_idx "
        "ON canon_facts (workspace_id, project_id, (proposition->>'subject_id'))"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS canon_beliefs (
            {_SCOPE},
            id TEXT NOT NULL,
            holder_entity_id TEXT NOT NULL,
            proposition JSONB NOT NULL,
            confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
            acquisition_provenance_id TEXT NOT NULL,
            source_ref TEXT,
            created_revision INTEGER NOT NULL,
            closed_revision INTEGER,
            PRIMARY KEY (workspace_id, project_id, branch_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS canon_beliefs_holder_idx "
        "ON canon_beliefs (workspace_id, project_id, holder_entity_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS canon_events (
            {_SCOPE},
            id TEXT NOT NULL,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            participants JSONB NOT NULL DEFAULT '[]'::jsonb,
            location_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            cause_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            outcome_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            world_position TEXT,
            provenance_id TEXT NOT NULL,
            created_revision INTEGER NOT NULL,
            retired_revision INTEGER,
            PRIMARY KEY (workspace_id, project_id, branch_id, id)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS canon_provenance (
            {_SCOPE},
            id TEXT NOT NULL,
            origin TEXT NOT NULL,
            actor_user_id TEXT,
            job_id TEXT,
            source_ref TEXT,
            note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, branch_id, id)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS canon_record_versions (
            {_SCOPE},
            record_type TEXT NOT NULL,
            record_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            operation_type TEXT NOT NULL,
            commit_id TEXT NOT NULL,
            change JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS canon_record_versions_lookup_idx "
        "ON canon_record_versions (workspace_id, project_id, record_type, record_id, revision)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS projection_watermarks (
            projection TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0,
            commit_id TEXT,
            envelope_checksum TEXT,
            projection_schema_version TEXT NOT NULL DEFAULT '1',
            status TEXT NOT NULL DEFAULT 'current',
            last_error TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (projection, workspace_id, project_id, branch_id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS projection_watermarks")
    op.execute("DROP TABLE IF EXISTS canon_record_versions")
    op.execute("DROP TABLE IF EXISTS canon_provenance")
    op.execute("DROP TABLE IF EXISTS canon_events")
    op.execute("DROP TABLE IF EXISTS canon_beliefs")
    op.execute("DROP TABLE IF EXISTS canon_facts")
    op.execute("DROP TABLE IF EXISTS canon_entities")
