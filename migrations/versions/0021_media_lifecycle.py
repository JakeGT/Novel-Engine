"""immutable media lifecycle audit

Revision ID: 0021_media_lifecycle
Revises: 0020_entity_identity
Create Date: 2026-10-09

Asset and asset-version registration, import, and retirement append an event
recording who did it and at which canon revision. Events are written in the
same transaction as the state change and can never be updated; they are removed
only when the owning project is deleted.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021_media_lifecycle"
down_revision: str | None = "0020_entity_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS media_lifecycle_events (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            asset_version_id TEXT,
            action TEXT NOT NULL CHECK (action IN (
                'asset_registered', 'asset_imported', 'asset_retired',
                'version_registered', 'version_imported', 'version_retired'
            )),
            actor_user_id TEXT,
            canon_revision BIGINT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            seq BIGSERIAL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS media_lifecycle_events_asset_idx "
        "ON media_lifecycle_events (workspace_id, project_id, asset_id, seq)"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION media_lifecycle_events_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'media lifecycle events are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER media_lifecycle_events_no_update BEFORE UPDATE ON media_lifecycle_events "
        "FOR EACH ROW EXECUTE FUNCTION media_lifecycle_events_immutable()"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS media_lifecycle_events")
    op.execute("DROP FUNCTION IF EXISTS media_lifecycle_events_immutable()")
