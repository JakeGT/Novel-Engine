"""local connector: a model on the author's own computer

Revision ID: 0053_local_connector
Revises: 0052_context_layers
Create Date: 2026-10-21

SCN-002A, ADR-025. A person on a hosted site runs a small connector program on their own
computer, next to a model server (Ollama, LM Studio, llama.cpp). The connector only ever
makes outbound HTTPS requests; nothing on the person's computer is opened to the internet.

``connector_codes`` holds registrations in progress, in the shape of the OAuth device
authorization grant (RFC 8628): the connector asks for a code, shows the person a short
code, and the person confirms it in the app while signed in to a workspace. The long
device code the connector polls with is stored only as its SHA-256 digest. A code lasts
ten minutes and is used once.

``connector_devices`` holds the computers a workspace connected: the digest of the
device's credential (never the credential), the previous digest for a short grace window
after the connector rotates it, when it was last seen, which connector version it runs,
and what its model server said it can do (model names, context sizes, whether it keeps
to an answer shape). ``in_use`` marks the one computer the workspace's assistant uses.
``credential_last_used_at`` is empty until the current credential is first used: rotating
with the replaced one after that is treated as a copied credential (RFC 9700 reuse
detection) and disconnects the computer.

``connector_requests`` is the outbound channel: a model request waiting for the device,
taken by it, and answered. The request body (which holds the author's words) and the
answer are cleared as soon as the waiting request has read the answer, or when it gives
up; what stays is who asked, when, the model and token counts, for usage and audit. A
sweep clears words a crash left behind: a request still open after the answer window, or a
finished one whose answer was never read after a few minutes. ``project_id`` names the
story a request was for, so deleting the story removes its requests.

Workspace data: deleting a workspace removes all three. Not story data, so not in the
archive or a what-if.

Chained after 0052_context_layers.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0053_local_connector"
down_revision: str | None = "0052_context_layers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS connector_codes (
            device_code_hash TEXT PRIMARY KEY,
            user_code TEXT NOT NULL UNIQUE,
            device_name TEXT NOT NULL,
            connector_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'denied', 'issued')),
            workspace_id TEXT,
            decided_by TEXT,
            decided_at TIMESTAMPTZ,
            device_id TEXT,
            interval_seconds INTEGER NOT NULL DEFAULT 5,
            last_polled_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS connector_codes_expiry_idx ON connector_codes (expires_at)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS connector_devices (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            name TEXT NOT NULL,
            registered_by TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
            revoked_reason TEXT,
            revoked_by TEXT,
            revoked_at TIMESTAMPTZ,
            credential_hash TEXT NOT NULL UNIQUE,
            previous_hash TEXT,
            previous_valid_until TIMESTAMPTZ,
            credential_issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            credential_last_used_at TIMESTAMPTZ,
            connector_version TEXT NOT NULL DEFAULT '',
            capabilities JSONB NOT NULL DEFAULT '{}'::jsonb,
            last_seen_at TIMESTAMPTZ,
            in_use BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS connector_devices_workspace_idx "
        "ON connector_devices (workspace_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS connector_devices_previous_idx "
        "ON connector_devices (previous_hash) WHERE previous_hash IS NOT NULL"
    )
    # One computer in use per workspace.
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS connector_devices_in_use_idx "
        "ON connector_devices (workspace_id) WHERE in_use"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS connector_requests (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            project_id TEXT,
            device_id TEXT NOT NULL,
            actor_user_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK (status IN ('waiting', 'taken', 'done', 'failed', 'expired')),
            payload JSONB,
            result JSONB,
            result_status INTEGER,
            failure TEXT,
            model TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            taken_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS connector_requests_waiting_idx "
        "ON connector_requests (device_id, created_at) WHERE status = 'waiting'"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS connector_requests_workspace_idx "
        "ON connector_requests (workspace_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS connector_requests_project_idx "
        "ON connector_requests (workspace_id, project_id) WHERE project_id IS NOT NULL"
    )
    # Rows still holding words, for the sweep that clears ones a crash left behind.
    op.execute(
        "CREATE INDEX IF NOT EXISTS connector_requests_words_idx "
        "ON connector_requests (created_at) WHERE payload IS NOT NULL OR result IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS connector_requests")
    op.execute("DROP TABLE IF EXISTS connector_devices")
    op.execute("DROP TABLE IF EXISTS connector_codes")
