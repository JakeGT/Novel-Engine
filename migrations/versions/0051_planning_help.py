"""planning help: the story's shared scales

Revision ID: 0051_planning_help
Revises: 0050_read_models
Create Date: 2026-10-20

STY-006. Beside the unit a story counts time in and its table of how long things take,
the story keeps a short list of scales many characters share (trust, standing,
loyalty): each a name and named levels, in order or not. An arc's stages may say where
their character stands on one. Like the unit and the table, the list is shared by every
line of the story, so it is never forked into a what-if, and ``scales_version`` goes up
with every save so two people changing it at once cannot overwrite each other
unnoticed. It travels in the project archive with the story's time.

STY-003, STY-004 and the checks against the prose (STY-005, SCN-007, CON-004, CON-014)
need no schema: suggestions are reviewed plan changes and the checks are derived on read.

Chained after 0050_read_models.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0051_planning_help"
down_revision: str | None = "0050_read_models"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE story_clocks "
        "ADD COLUMN IF NOT EXISTS scales JSONB NOT NULL DEFAULT '{}'::jsonb, "
        "ADD COLUMN IF NOT EXISTS scales_version INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE story_clocks DROP COLUMN IF EXISTS scales_version, DROP COLUMN IF EXISTS scales"
    )
