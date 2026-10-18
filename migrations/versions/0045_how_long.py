"""how long things take: the author's travel times and lengths

Revision ID: 0045_how_long
Revises: 0044_consistency_runs
Create Date: 2026-10-18

CON-003. Beside the unit a story counts time in, a small table the author fills: how
long it takes to get from one place to another, and how long an event or a fact lasts.
The continuity checks read it; nothing ever fills it in by guessing. Like the unit it
is shared by every line of the story. ``how_long_version`` goes up with every save so
two people changing the table at once cannot overwrite each other unnoticed.

Chained after 0044_consistency_runs, the head when this was written.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0045_how_long"
down_revision: str | None = "0044_consistency_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE story_clocks "
        "ADD COLUMN IF NOT EXISTS how_long JSONB NOT NULL DEFAULT '{}'::jsonb, "
        "ADD COLUMN IF NOT EXISTS how_long_version INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE story_clocks "
        "DROP COLUMN IF EXISTS how_long_version, DROP COLUMN IF EXISTS how_long"
    )
