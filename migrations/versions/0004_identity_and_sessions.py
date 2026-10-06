"""identity, external identities, sessions, and login attempts

Revision ID: 0004_identity_and_sessions
Revises: 0003_embedding_provenance
Create Date: 2026-10-06

Provider-neutral identity tables. External identities are keyed by the immutable
``(issuer, subject)`` pair; raw session tokens are never stored (only a SHA-256
hash). These tables carry no tenancy yet; workspaces/memberships/invitations
arrive in later Phase 1 slices.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_identity_and_sessions"
down_revision: str | None = "0003_embedding_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT,
            display_name TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS external_identities (
            issuer TEXT NOT NULL,
            subject TEXT NOT NULL,
            user_id TEXT NOT NULL REFERENCES users(id),
            email TEXT,
            display_name TEXT,
            linked_at TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (issuer, subject)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS external_identities_user_id_idx "
        "ON external_identities (user_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id),
            csrf_token TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            rotated_from TEXT
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS sessions_user_id_idx ON sessions (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS sessions_expires_idx ON sessions (expires_at)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS login_attempts (
            state TEXT PRIMARY KEY,
            nonce TEXT NOT NULL,
            code_verifier TEXT NOT NULL,
            redirect_uri TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL
        )
        """
    )


def downgrade() -> None:
    for table in ("login_attempts", "sessions", "external_identities", "users"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
