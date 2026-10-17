"""story time, who may know a fact, and what-ifs

Revision ID: 0032_story_time
Revises: 0031_story_rules
Create Date: 2026-10-17

Phase 3 (ADR-020). Facts gain a span of story time and a visibility; events gain
a story time. Branches gain a parent and the revision they were started from, so
a what-if can be compared with the story it came from. A story names the unit it
counts time in. Every new column is optional, so existing rows keep their meaning.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0032_story_time"
down_revision: str | None = "0031_story_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE canon_facts ADD COLUMN IF NOT EXISTS starts JSONB")
    op.execute("ALTER TABLE canon_facts ADD COLUMN IF NOT EXISTS ends JSONB")
    op.execute(
        "ALTER TABLE canon_facts ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL "
        "DEFAULT 'ordinary' CHECK (visibility IN ('ordinary', 'common', 'secret'))"
    )
    op.execute("ALTER TABLE canon_events ADD COLUMN IF NOT EXISTS world_time JSONB")

    op.execute("ALTER TABLE branches ADD COLUMN IF NOT EXISTS parent_branch_id TEXT")
    op.execute("ALTER TABLE branches ADD COLUMN IF NOT EXISTS fork_revision BIGINT")
    op.execute("ALTER TABLE branches ADD COLUMN IF NOT EXISTS description TEXT")
    op.execute(
        "ALTER TABLE branches ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active' "
        "CHECK (status IN ('active', 'set_aside'))"
    )
    op.execute("ALTER TABLE branches ADD COLUMN IF NOT EXISTS created_by TEXT")
    # A branch cannot be its own parent; deeper cycles are refused by the service,
    # which only ever forks from an existing branch.
    op.execute(
        "ALTER TABLE branches ADD CONSTRAINT branches_not_own_parent "
        "CHECK (parent_branch_id IS NULL OR parent_branch_id <> id)"
    )
    op.execute(
        "ALTER TABLE branches ADD CONSTRAINT branches_root_has_no_parent "
        "CHECK (NOT is_root OR parent_branch_id IS NULL)"
    )

    # Picture links were keyed by project and id only; a what-if keeps its own copy of
    # each link under the same id, so the branch joins the key like every story table.
    op.execute("ALTER TABLE asset_links DROP CONSTRAINT IF EXISTS asset_links_pkey")
    op.execute(
        "ALTER TABLE asset_links ADD CONSTRAINT asset_links_pkey "
        "PRIMARY KEY (workspace_id, project_id, branch_id, id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS story_clocks (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT 'year',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS story_clocks")
    op.execute("ALTER TABLE asset_links DROP CONSTRAINT IF EXISTS asset_links_pkey")
    op.execute(
        "ALTER TABLE asset_links ADD CONSTRAINT asset_links_pkey "
        "PRIMARY KEY (workspace_id, project_id, id)"
    )
    op.execute("ALTER TABLE branches DROP CONSTRAINT IF EXISTS branches_root_has_no_parent")
    op.execute("ALTER TABLE branches DROP CONSTRAINT IF EXISTS branches_not_own_parent")
    for column in ("created_by", "status", "description", "fork_revision", "parent_branch_id"):
        op.execute(f"ALTER TABLE branches DROP COLUMN IF EXISTS {column}")
    op.execute("ALTER TABLE canon_events DROP COLUMN IF EXISTS world_time")
    for column in ("visibility", "ends", "starts"):
        op.execute(f"ALTER TABLE canon_facts DROP COLUMN IF EXISTS {column}")
