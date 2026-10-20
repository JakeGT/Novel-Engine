"""what a model saw in a picture, as reviewed observations; time and secrecy on picture links

Revision ID: 0048_media_observations
Revises: 0047_embedding_generations
Create Date: 2026-10-18

AST-005, AST-004, AST-007. A vision model may propose a caption or an appearance
observation for one exact picture version. Each observation keeps the model, the
prompt version and the job that produced it, an optional evidence region and a
confidence, and a review state. Nothing here is a story fact: an accepted
observation only becomes a fact through an ordinary reviewed proposal, whose
mutation set id is recorded on the observation when it is made.

Picture links gain an optional story time from which they apply and a secrecy flag
(``visibility``: ``open`` or ``secret``, with ``known_by`` naming the characters who
know what it shows); a secret link reaches a point of view only when that character
is among them. Both are set through the reviewed ``link_asset`` operation.

The per-picture context choice (send the picture / use its description / leave out)
lives on the picture itself and defaults to its description: a picture is sent to a
model only when the author has said so.

Chained after 0047_embedding_generations.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0048_media_observations"
down_revision: str | None = "0047_embedding_generations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS media_observations (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            id TEXT NOT NULL,
            asset_id TEXT NOT NULL,
            asset_version_id TEXT NOT NULL,
            segment_id TEXT,
            kind TEXT NOT NULL CHECK (kind IN ('caption', 'appearance')),
            text TEXT NOT NULL,
            subject_record_type TEXT,
            subject_record_id TEXT,
            region JSONB,
            confidence DOUBLE PRECISION
                CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
            model TEXT,
            prompt_name TEXT,
            prompt_version TEXT,
            job_id TEXT,
            state TEXT NOT NULL DEFAULT 'proposed' CHECK (state IN (
                'proposed', 'accepted', 'corrected', 'rejected', 'superseded'
            )),
            corrected_text TEXT,
            reviewed_by TEXT,
            reviewed_at TIMESTAMPTZ,
            superseded_by TEXT,
            proposal_mutation_set_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            seq BIGSERIAL,
            PRIMARY KEY (workspace_id, project_id, id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS media_observations_asset_idx "
        "ON media_observations (workspace_id, project_id, asset_id, seq)"
    )
    op.execute(
        "ALTER TABLE media_assets "
        "ADD COLUMN IF NOT EXISTS context_choice TEXT NOT NULL DEFAULT 'describe' "
        "CHECK (context_choice IN ('send', 'describe', 'leave_out'))"
    )
    op.execute(
        "ALTER TABLE asset_links "
        "ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'open' "
        "CHECK (visibility IN ('open', 'secret')), "
        "ADD COLUMN IF NOT EXISTS applies_from JSONB, "
        "ADD COLUMN IF NOT EXISTS known_by JSONB NOT NULL DEFAULT '[]'::jsonb"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE asset_links "
        "DROP COLUMN IF EXISTS known_by, DROP COLUMN IF EXISTS applies_from, "
        "DROP COLUMN IF EXISTS visibility"
    )
    op.execute("ALTER TABLE media_assets DROP COLUMN IF EXISTS context_choice")
    op.execute("DROP TABLE IF EXISTS media_observations")
