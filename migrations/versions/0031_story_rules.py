"""story rules and their versions

Revision ID: 0031_story_rules
Revises: 0030_source_texts
Create Date: 2026-10-11

An author's own continuity rule, kept as checkable data (ADR-013). Changing a
rule adds a version; earlier versions stay as they were, so a warning can always
say which wording of the rule raised it.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0031_story_rules"
down_revision: str | None = "0030_source_texts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS story_rules (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            title TEXT NOT NULL,
            sentence TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'on' CHECK (status IN ('on', 'paused')),
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS story_rules_project "
        "ON story_rules (workspace_id, project_id, created_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS story_rule_versions (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            rule_id TEXT NOT NULL,
            number INTEGER NOT NULL,
            body JSONB NOT NULL,
            examples JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, rule_id, number),
            FOREIGN KEY (workspace_id, project_id, rule_id)
                REFERENCES story_rules (workspace_id, project_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION story_rule_versions_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'story rule versions are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER story_rule_versions_no_update BEFORE UPDATE ON story_rule_versions "
        "FOR EACH ROW EXECUTE FUNCTION story_rule_versions_immutable()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS story_rule_versions_no_update ON story_rule_versions")
    op.execute("DROP FUNCTION IF EXISTS story_rule_versions_immutable()")
    op.execute("DROP TABLE IF EXISTS story_rule_versions")
    op.execute("DROP TABLE IF EXISTS story_rules")
