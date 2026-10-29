"""picture regions: the entry a marked part shows, writing read from a picture, and notes
that point at a part of a picture

Revision ID: 0060_picture_regions
Revises: 0059_streaming_and_routes
Create Date: 2026-10-21

AST-007, AST-003. ``media_segments`` gains ``entity_id``: the entry a part of a picture the
author marked shows (a face in a crowd, a sign on a wall). It is the author's own pointer,
like the part's name; it says nothing is true in the story and changes no fact, so it is
not a reviewed change. The picture's reviewed links still decide where it is shown and
who may see it.

AST-005. ``media_observations.kind`` may be ``text_in_picture``: words a vision model read
inside the picture while describing it (never a local OCR engine), with the region they
are in. Reviewed like any other observation (accept, correct, reject); never a story fact.

CON-014. ``draft_findings.picture_anchor`` keeps, for a note of the pictures check, the
picture and version it was compared with and the part of it (a marked part's id, or a
rectangle as fractions of the picture), so the note can show it drawn on the picture.
NULL for every other note.

Chained after 0059_streaming_and_routes.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0060_picture_regions"
down_revision: str | None = "0059_streaming_and_routes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KINDS_BEFORE = "'caption', 'appearance'"
_KINDS_AFTER = _KINDS_BEFORE + ", 'text_in_picture'"


def upgrade() -> None:
    op.execute("ALTER TABLE media_segments ADD COLUMN IF NOT EXISTS entity_id TEXT")
    op.execute(
        "CREATE INDEX IF NOT EXISTS media_segments_entity_idx "
        "ON media_segments (workspace_id, project_id, entity_id) "
        "WHERE entity_id IS NOT NULL AND retired_at IS NULL"
    )
    op.execute(
        "ALTER TABLE media_observations DROP CONSTRAINT IF EXISTS media_observations_kind_check"
    )
    op.execute(
        "ALTER TABLE media_observations ADD CONSTRAINT media_observations_kind_check "
        f"CHECK (kind IN ({_KINDS_AFTER}))"
    )
    op.execute("ALTER TABLE draft_findings ADD COLUMN IF NOT EXISTS picture_anchor JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE draft_findings DROP COLUMN IF EXISTS picture_anchor")
    op.execute(
        "ALTER TABLE media_observations DROP CONSTRAINT IF EXISTS media_observations_kind_check"
    )
    # Writing read from pictures cannot satisfy the old check; NOT VALID keeps those rows
    # while refusing new ones.
    op.execute(
        "ALTER TABLE media_observations ADD CONSTRAINT media_observations_kind_check "
        f"CHECK (kind IN ({_KINDS_BEFORE})) NOT VALID"
    )
    op.execute("DROP INDEX IF EXISTS media_segments_entity_idx")
    op.execute("ALTER TABLE media_segments DROP COLUMN IF EXISTS entity_id")
