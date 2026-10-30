"""media remainders: picture time windows, two-step uploads, notes removed, carried steps

Revision ID: 0058_media_remainders
Revises: 0057_check_remainders
Create Date: 2026-10-21

AST-001. ``asset_links`` gains ``applies_until`` (a story time, as JSON, from which a
picture no longer applies; NULL is to the end) beside batch 6's ``applies_from``, and
``shows_version``: true when the link shows the version it names rather than the
picture's newest. Both are written only by a reviewed ``link_asset`` operation.

AST-002. ``media_uploads`` holds a two-step upload: the intent (who asked, what it is for,
the size and SHA-256 the sender promised, when it expires), the staged file once the
bytes arrived, and what finishing it made. No story words: the name is the author's label
for the picture, as ``media_assets`` keeps it. Deleted with its project; an archive does
not carry it (an unfinished upload is not part of the story).

AST-008. ``media_lifecycle_events`` may record ``notes_removed``: what a model said about
a deleted picture (its observations and their search rows) was removed, with how many
(``item_count``). Counts only, never the words.

EXT-007. ``scene_readings.steps`` keeps the thread steps a reading found but has not yet
suggested, so a reading stopped part way passes them to the next one instead of losing
them. Each step names a thread and where in the scene's text it is; a thread the scene
opens also keeps the title and summary the assistant suggested for it (shown only in review).

Chained after 0057_check_remainders.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0058_media_remainders"
down_revision: str | None = "0057_check_remainders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIONS_BEFORE = (
    "'asset_registered', 'asset_imported', 'asset_retired', "
    "'version_registered', 'version_imported', 'version_retired', "
    "'asset_deleted', 'original_deleted'"
)
_ACTIONS_AFTER = _ACTIONS_BEFORE + ", 'notes_removed'"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE asset_links "
        "ADD COLUMN IF NOT EXISTS applies_until JSONB, "
        "ADD COLUMN IF NOT EXISTS shows_version BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS media_uploads (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            branch_id TEXT,
            actor_user_id TEXT,
            target_type TEXT NOT NULL CHECK (target_type IN ('entity', 'plan_item', 'asset')),
            target_id TEXT NOT NULL,
            name TEXT,
            expected_bytes BIGINT NOT NULL CHECK (expected_bytes > 0),
            expected_sha256 TEXT NOT NULL CHECK (expected_sha256 ~ '^[0-9a-f]{64}$'),
            state TEXT NOT NULL DEFAULT 'waiting'
                CHECK (state IN ('waiting', 'received', 'finishing', 'finished', 'expired')),
            staged_key TEXT,
            content_type TEXT,
            asset_id TEXT,
            version_id TEXT,
            link_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            finished_at TIMESTAMPTZ,
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS media_uploads_expiry_idx "
        "ON media_uploads (expires_at) WHERE state IN ('waiting', 'received', 'finishing')"
    )
    op.execute("ALTER TABLE media_lifecycle_events ADD COLUMN IF NOT EXISTS item_count INTEGER")
    op.execute(
        "ALTER TABLE media_lifecycle_events "
        "DROP CONSTRAINT IF EXISTS media_lifecycle_events_action_check"
    )
    op.execute(
        "ALTER TABLE media_lifecycle_events ADD CONSTRAINT media_lifecycle_events_action_check "
        f"CHECK (action IN ({_ACTIONS_AFTER}))"
    )
    op.execute(
        "ALTER TABLE scene_readings "
        "ADD COLUMN IF NOT EXISTS steps JSONB NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE scene_readings DROP COLUMN IF EXISTS steps")
    op.execute(
        "ALTER TABLE media_lifecycle_events "
        "DROP CONSTRAINT IF EXISTS media_lifecycle_events_action_check"
    )
    # Events of the new kind cannot satisfy the old check; NOT VALID keeps them while
    # refusing new ones.
    op.execute(
        "ALTER TABLE media_lifecycle_events ADD CONSTRAINT media_lifecycle_events_action_check "
        f"CHECK (action IN ({_ACTIONS_BEFORE})) NOT VALID"
    )
    op.execute("ALTER TABLE media_lifecycle_events DROP COLUMN IF EXISTS item_count")
    op.execute("DROP TABLE IF EXISTS media_uploads")
    op.execute(
        "ALTER TABLE asset_links "
        "DROP COLUMN IF EXISTS shows_version, DROP COLUMN IF EXISTS applies_until"
    )
