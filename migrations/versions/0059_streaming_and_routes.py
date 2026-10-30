"""streaming and routes: drafts as they are written, and what each model can take

Revision ID: 0059_streaming_and_routes
Revises: 0058_media_remainders
Create Date: 2026-10-21

SCN-002. ``job_streams`` holds the words of a draft while the model is still writing
it, one row per draft of a job, so the studio can show them as they arrive whichever
server process runs the job. It is a passing copy: the words are cleared as soon as the
whole draft is stored (or the work stops or fails), the sweep deletes the rows of work
that has ended, and a row goes with its job. The job still records the whole answer and
its usage once, as before.

CTX-013. ``model_capabilities`` keeps what a provider's own model list says each model
can take (pictures, the context it reads, the longest answer, whether it keeps to a
JSON schema), so a route is negotiated per model rather than per route. Install-wide:
the lists are public metadata, never a workspace's data, and hold no story words.

Chained after 0058_media_remainders.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0059_streaming_and_routes"
down_revision: str | None = "0058_media_remainders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS job_streams (
            workspace_id TEXT NOT NULL,
            project_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            number INTEGER NOT NULL CHECK (number >= 1),
            -- The attempt at the job that writes this draft: a worker that lost the job
            -- to another never adds to, or ends, the words the newer one is writing.
            attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
            text TEXT NOT NULL DEFAULT '',
            chars INTEGER NOT NULL DEFAULT 0 CHECK (chars >= 0),
            done BOOLEAN NOT NULL DEFAULT false,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (workspace_id, project_id, job_id, number),
            FOREIGN KEY (workspace_id, project_id, job_id)
                REFERENCES jobs (workspace_id, project_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS model_capabilities (
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            sees_images BOOLEAN,
            context_tokens INTEGER CHECK (context_tokens IS NULL OR context_tokens > 0),
            max_output_tokens INTEGER
                CHECK (max_output_tokens IS NULL OR max_output_tokens > 0),
            structured BOOLEAN,
            source TEXT NOT NULL CHECK (source IN ('provider', 'computer', 'operator')),
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (provider, model)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS model_capabilities")
    op.execute("DROP TABLE IF EXISTS job_streams")
