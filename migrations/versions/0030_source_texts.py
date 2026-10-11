"""source texts, their immutable versions, and name memory

Revision ID: 0030_source_texts
Revises: 0029_plan_conversations
Create Date: 2026-10-11

Prose an author brings in. A source text is not canon: it is the evidence a kept
change can point back to. Each version's words never change, so a pointer at
characters 120-180 of a version always means the same passage. Name memory holds
what the author decided a phrase means ("the Fox" is Mara) or does not mean.

Every key includes the project, so a story restored from an archive beside the
original keeps the same ids without clashing.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0030_source_texts"
down_revision: str | None = "0029_plan_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS source_texts (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            title TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('paste', 'markdown')),
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            removed_at TIMESTAMPTZ,
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS source_texts_project "
        "ON source_texts (workspace_id, project_id, created_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS source_versions (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            number INTEGER NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, id),
            UNIQUE (workspace_id, project_id, source_id, number),
            FOREIGN KEY (workspace_id, project_id, source_id)
                REFERENCES source_texts (workspace_id, project_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION source_versions_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'source versions are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER source_versions_no_update BEFORE UPDATE ON source_versions "
        "FOR EACH ROW EXECUTE FUNCTION source_versions_immutable()"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS alias_bindings (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            phrase TEXT NOT NULL,
            normalized TEXT NOT NULL,
            verdict TEXT NOT NULL CHECK (verdict IN ('is', 'is_not', 'ignore', 'suggested')),
            entity_id TEXT,
            source_id TEXT,
            decided_by TEXT,
            decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, id),
            FOREIGN KEY (workspace_id, project_id, source_id)
                REFERENCES source_texts (workspace_id, project_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS alias_bindings_project "
        "ON alias_bindings (workspace_id, project_id, normalized)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS alias_bindings")
    op.execute("DROP TRIGGER IF EXISTS source_versions_no_update ON source_versions")
    op.execute("DROP FUNCTION IF EXISTS source_versions_immutable()")
    op.execute("DROP TABLE IF EXISTS source_versions")
    op.execute("DROP TABLE IF EXISTS source_texts")
