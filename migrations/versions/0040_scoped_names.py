"""name answers that hold only in part of the story, with the author's examples

Revision ID: 0040_scoped_names
Revises: 0039_check_passes
Create Date: 2026-10-18

CTX-016. A remembered answer ("'the captain' is Roe") can now hold only in one what-if
(``branch_id``), from a scene on or until a scene in reading order (``from_scene_id``,
``until_scene_id``), or in scenes seen by one character (``viewpoint_id``), as well as in
one text (``source_id``, unchanged). Every column is null for an answer that holds
everywhere, so every existing answer keeps meaning what it meant. ``examples`` keeps the
passages the author confirmed, where the phrase does or does not mean the entry.

The scene and viewpoint columns are plain ids, not foreign keys: plan items are kept per
line of the story, so a scene id names a scene in whichever line is being read, and an
answer whose scene has gone simply stops applying.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0040_scoped_names"
down_revision: str | None = "0039_check_passes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE alias_bindings "
        "ADD COLUMN IF NOT EXISTS branch_id TEXT, "
        "ADD COLUMN IF NOT EXISTS from_scene_id TEXT, "
        "ADD COLUMN IF NOT EXISTS until_scene_id TEXT, "
        "ADD COLUMN IF NOT EXISTS viewpoint_id TEXT, "
        "ADD COLUMN IF NOT EXISTS examples JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    # Dropped first so running this again replaces the check instead of failing on it.
    op.execute("ALTER TABLE alias_bindings DROP CONSTRAINT IF EXISTS alias_bindings_examples_list")
    op.execute(
        "ALTER TABLE alias_bindings ADD CONSTRAINT alias_bindings_examples_list "
        "CHECK (jsonb_typeof(examples) = 'array')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE alias_bindings DROP CONSTRAINT IF EXISTS alias_bindings_examples_list")
    op.execute(
        "ALTER TABLE alias_bindings "
        "DROP COLUMN IF EXISTS examples, "
        "DROP COLUMN IF EXISTS viewpoint_id, "
        "DROP COLUMN IF EXISTS until_scene_id, "
        "DROP COLUMN IF EXISTS from_scene_id, "
        "DROP COLUMN IF EXISTS branch_id"
    )
