"""what is said in the story, kept apart from what is true

Revision ID: 0041_claims
Revises: 0040_scoped_names
Create Date: 2026-10-18

KNW-005. A claim is something the text, a character or a narrator says: an event of
kind ``claim`` whose ``claim`` column holds what is said (a proposition), how it is said
(states, suggests, wonders, denies) and the scene it is said in. It is never a fact; a
fact is only ever made from it by a reviewed change. The column is null for every other
event, so every existing event keeps its meaning.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0041_claims"
down_revision: str | None = "0040_scoped_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE canon_events ADD COLUMN IF NOT EXISTS claim JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE canon_events DROP COLUMN IF EXISTS claim")
