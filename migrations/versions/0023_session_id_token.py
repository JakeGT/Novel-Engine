"""keep the ID token with a hosted session

Revision ID: 0023_session_id_token
Revises: 0022_archive_imports
Create Date: 2026-10-10

The identity provider only honours a return address at sign-out when it is sent
with the ID token that proves whose session is ending. The token is stored with
the session row, is removed with it, and is never logged or returned by the API.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023_session_id_token"
down_revision: str | None = "0022_archive_imports"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS id_token TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS id_token")
