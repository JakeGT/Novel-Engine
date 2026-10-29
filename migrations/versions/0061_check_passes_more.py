"""more check passes: why characters act, bodies and cause, the story's own rules

Revision ID: 0061_check_passes_more
Revises: 0060_picture_regions
Create Date: 2026-10-21

CON-008. A check with the assistant gains three optional passes, each its own model
call: ``motivation`` (does what a character does follow from what they want and know),
``physical`` (bodies, objects, travel, cause before effect) and ``world`` (the story's
own rules, such as its magic's limits). ``draft_findings.check_pass`` records them.

The check on that column, written in 0039, also never listed ``pictures``, the pass
CON-014 added in 0048, so a note from the pictures pass could not be stored in
PostgreSQL (the in-memory store used by the tests has no such check). The check is
replaced by one naming every pass. The voice check of flagged passages (CON-018) stores
its notes as the ``voice`` pass, so it needs no new value.

The constraint is given a name this time, so a later pass can replace it by name.

Chained after 0060_picture_regions.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0061_check_passes_more"
down_revision: str | None = "0060_picture_regions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "draft_findings_check_pass_known"
# The name PostgreSQL gave the unnamed column check of 0039.
_OLD = "draft_findings_check_pass_check"
_BEFORE = "'viewpoint', 'continuity', 'plan', 'voice', 'clarity'"
_NOW = _BEFORE + ", 'pictures', 'motivation', 'physical', 'world'"


def upgrade() -> None:
    op.execute(f"ALTER TABLE draft_findings DROP CONSTRAINT IF EXISTS {_OLD}")
    op.execute(f"ALTER TABLE draft_findings DROP CONSTRAINT IF EXISTS {_NAME}")
    op.execute(
        f"ALTER TABLE draft_findings ADD CONSTRAINT {_NAME} "
        f"CHECK (check_pass IS NULL OR check_pass IN ({_NOW}))"
    )


def downgrade() -> None:
    # Notes of a pass the older check does not know keep their words and lose their pass,
    # so they still show (as notes from the earlier all-in-one check did).
    op.execute(
        f"UPDATE draft_findings SET check_pass = NULL "
        f"WHERE check_pass IS NOT NULL AND check_pass NOT IN ({_BEFORE})"
    )
    op.execute(f"ALTER TABLE draft_findings DROP CONSTRAINT IF EXISTS {_NAME}")
    op.execute(
        f"ALTER TABLE draft_findings ADD CONSTRAINT {_OLD} "
        f"CHECK (check_pass IS NULL OR check_pass IN ({_BEFORE}))"
    )
