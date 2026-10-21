"""views of the story kept ready to read, and deleting one picture's file

Revision ID: 0050_read_models
Revises: 0049_check_fingerprints
Create Date: 2026-10-20

QRY-001. ``read_models`` holds, per line of the story (``branch_id``) and per view
(location, possession, relationship, timeline, disclosure, dependency), rows built from
the story's records after each commit, the canon revision they reflect (the
watermark), the builder version, a checksum, and the outcome of the last replay
verification. The rows are derived: they are purged with the project, never archived,
never forked into a what-if (each line builds its own), and rebuilt after a restore.
An index on the outbox lets the builder find lines with commits past its watermark.

AST-008. A picture's original can be deleted before its story is: the version row is
kept as the tombstone (checksum, size, type) with ``deleted_at``; the lifecycle audit
gains ``asset_deleted`` and ``original_deleted``. Deleted versions no longer count
toward the workspace's room for pictures.

Downgrading drops ``deleted_at``, so a deleted version looks like a kept one again: it
counts toward the workspace's room once more although its file is gone, and opening it
finds no file (the picture itself stays ``deleted``, and the events of the new kinds stay
under a ``NOT VALID`` check). Files are never brought back; only an archive saved before
the deletion still holds them.

Chained after 0049_check_fingerprints.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0050_read_models"
down_revision: str | None = "0049_check_fingerprints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTIONS_BEFORE = (
    "'asset_registered', 'asset_imported', 'asset_retired', "
    "'version_registered', 'version_imported', 'version_retired'"
)
_ACTIONS_AFTER = _ACTIONS_BEFORE + ", 'asset_deleted', 'original_deleted'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS read_models (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            branch_id TEXT NOT NULL,
            view TEXT NOT NULL CHECK (view IN (
                'location', 'possession', 'relationship', 'timeline', 'disclosure',
                'dependency'
            )),
            revision BIGINT NOT NULL CHECK (revision >= 0),
            builder_version INTEGER NOT NULL,
            rows JSONB NOT NULL,
            row_count INTEGER NOT NULL,
            checksum TEXT NOT NULL,
            built_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            verified_revision BIGINT,
            verified_builder INTEGER,
            verified_at TIMESTAMPTZ,
            verify_outcome TEXT CHECK (verify_outcome IN ('matched', 'differed')),
            differences INTEGER,
            PRIMARY KEY (workspace_id, project_id, branch_id, view)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS outbox_messages_line_revision_idx "
        "ON outbox_messages (workspace_id, project_id, branch_id, revision) "
        "WHERE topic = 'canon.commit'"
    )
    op.execute("ALTER TABLE media_asset_versions ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
    op.execute(
        "ALTER TABLE media_lifecycle_events "
        "DROP CONSTRAINT IF EXISTS media_lifecycle_events_action_check"
    )
    op.execute(
        "ALTER TABLE media_lifecycle_events ADD CONSTRAINT media_lifecycle_events_action_check "
        f"CHECK (action IN ({_ACTIONS_AFTER}))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE media_lifecycle_events "
        "DROP CONSTRAINT IF EXISTS media_lifecycle_events_action_check"
    )
    # Events of the new kinds cannot satisfy the old check; NOT VALID keeps them while
    # refusing new ones.
    op.execute(
        "ALTER TABLE media_lifecycle_events ADD CONSTRAINT media_lifecycle_events_action_check "
        f"CHECK (action IN ({_ACTIONS_BEFORE})) NOT VALID"
    )
    op.execute("ALTER TABLE media_asset_versions DROP COLUMN IF EXISTS deleted_at")
    op.execute("DROP INDEX IF EXISTS outbox_messages_line_revision_idx")
    op.execute("DROP TABLE IF EXISTS read_models")
