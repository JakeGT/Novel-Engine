"""site search setup: the site's search-by-meaning choice, kept on a page

Revision ID: 0063_site_search_setup
Revises: 0062_audits_and_impact
Create Date: 2026-10-30

The site's search-by-meaning setup (its embedding service, model and size) moves from
operator-only settings to a page a platform administrator fills in. Two tables hold it:

- ``site_search_setup``: one row — provider, model, dimension, an overridden address
  (custom or upstream override only), a version bumped on every change, who changed it
  and when, and whether the provider accepted the key when it was saved.
- ``site_search_secrets``: the site's key, sealed exactly like workspace credentials
  (``envelope_encrypt`` under ``CREDENTIAL_KEK``), with its last four characters, its
  fingerprint, and the provider and host it was saved for. The key is never returned.

Sessions also gain ``authenticated_at`` (when the person actually signed in), carried
unchanged through session rotation and backfilled from ``created_at``, so "signed in
recently" stays meaningful however often the session id turns over.

Chained after 0062_audits_and_impact.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0063_site_search_setup"
down_revision: str | None = "0062_audits_and_impact"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS authenticated_at TIMESTAMPTZ")
    op.execute("UPDATE sessions SET authenticated_at = created_at WHERE authenticated_at IS NULL")
    op.execute("ALTER TABLE sessions ALTER COLUMN authenticated_at SET NOT NULL")

    # A login attempt remembers whether it asked the provider to authenticate the person
    # again (``fresh``) and where to return afterwards, so the callback cannot add either.
    op.execute(
        "ALTER TABLE login_attempts ADD COLUMN IF NOT EXISTS fresh BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute("ALTER TABLE login_attempts ADD COLUMN IF NOT EXISTS return_to TEXT")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS site_search_setup (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            dimension INTEGER NOT NULL CHECK (dimension BETWEEN 1 AND 2000),
            base_url TEXT,
            version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
            updated_by TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            verified BOOLEAN
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS site_search_secrets (
            id TEXT PRIMARY KEY,
            key_fingerprint TEXT NOT NULL,
            key_last4 TEXT NOT NULL,
            key_version INTEGER NOT NULL,
            wrapped_dek TEXT NOT NULL,
            nonce TEXT NOT NULL,
            ciphertext TEXT NOT NULL,
            provider TEXT NOT NULL,
            host TEXT NOT NULL,
            created_by TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS site_search_secrets")
    op.execute("DROP TABLE IF EXISTS site_search_setup")
    op.execute("ALTER TABLE login_attempts DROP COLUMN IF EXISTS return_to")
    op.execute("ALTER TABLE login_attempts DROP COLUMN IF EXISTS fresh")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS authenticated_at")
