"""entity handles (slug/alias/external id) and per-operation evidence

Revision ID: 0020_entity_identity
Revises: 0019_review_workflow
Create Date: 2026-10-09

Names are never identity. A slug is unique among the *active* entities of a
branch; an external identifier resolves to at most one entity per branch. Every
operation may cite bounded evidence references, stored per canonical record.
Record versions gain the operation ordinal so history is ordered within a commit.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020_entity_identity"
down_revision: str | None = "0019_review_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCOPE = "workspace_id TEXT NOT NULL, project_id TEXT NOT NULL, branch_id TEXT NOT NULL"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE canon_entities "
        "ADD COLUMN IF NOT EXISTS aliases JSONB NOT NULL DEFAULT '[]'::jsonb, "
        "ADD COLUMN IF NOT EXISTS external_ids JSONB NOT NULL DEFAULT '{}'::jsonb"
    )
    # Slugs were free text before this revision. Keep the earliest holder of a
    # duplicated active slug and clear the rest so the unique index can be built;
    # nothing is deleted and the entities keep their identity.
    op.execute(
        """
        UPDATE canon_entities e SET slug = NULL
        WHERE e.slug IS NOT NULL AND e.lifecycle = 'active' AND EXISTS (
            SELECT 1 FROM canon_entities o
            WHERE o.workspace_id = e.workspace_id AND o.project_id = e.project_id
              AND o.branch_id = e.branch_id AND o.slug = e.slug AND o.lifecycle = 'active'
              AND (o.created_revision, o.id) < (e.created_revision, e.id)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS canon_entities_active_slug_uq "
        "ON canon_entities (workspace_id, project_id, branch_id, slug) "
        "WHERE slug IS NOT NULL AND lifecycle = 'active'"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS canon_entity_external_ids (
            {_SCOPE},
            namespace TEXT NOT NULL,
            value TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            PRIMARY KEY (workspace_id, project_id, branch_id, namespace, value)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS canon_entity_external_ids_entity_idx "
        "ON canon_entity_external_ids (workspace_id, project_id, branch_id, entity_id)"
    )
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS canon_evidence (
            {_SCOPE},
            commit_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            position INTEGER NOT NULL,
            record_type TEXT NOT NULL,
            record_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            source_artefact_id TEXT NOT NULL,
            location TEXT,
            PRIMARY KEY (workspace_id, project_id, branch_id, commit_id, ordinal, position)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS canon_evidence_record_idx "
        "ON canon_evidence (workspace_id, project_id, record_type, record_id, revision)"
    )

    # History within one commit needs a stable order (edits and reverts depend on it).
    op.execute(
        "ALTER TABLE canon_record_versions ADD COLUMN IF NOT EXISTS ordinal INTEGER NOT NULL "
        "DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE canon_record_versions DROP COLUMN IF EXISTS ordinal")
    op.execute("DROP TABLE IF EXISTS canon_evidence")
    op.execute("DROP TABLE IF EXISTS canon_entity_external_ids")
    op.execute("DROP INDEX IF EXISTS canon_entities_active_slug_uq")
    op.execute(
        "ALTER TABLE canon_entities DROP COLUMN IF EXISTS aliases, "
        "DROP COLUMN IF EXISTS external_ids"
    )
