"""Retention purge for ephemeral operational rows.

Deletes expired sessions, login attempts, invitations, and stale rate-limit hits.
It never touches canonical graph/prose data or the users/workspaces records.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

import psycopg
from psycopg.rows import tuple_row

from backend.core.config import settings as default_settings

PURGE_STATEMENTS: dict[str, str] = {
    "sessions": "DELETE FROM sessions WHERE expires_at < now()",
    "login_attempts": "DELETE FROM login_attempts WHERE expires_at < now()",
    "invitations": "DELETE FROM invitations WHERE expires_at < now() AND accepted_at IS NULL",
    "rate_limit_hits": "DELETE FROM rate_limit_hits WHERE occurred_at < now() - interval '1 day'",
}


def purge_expired(dsn: str) -> dict[str, int]:
    """Return per-table deleted-row counts; tolerate un-migrated stores."""
    deleted: dict[str, int] = {}
    with psycopg.connect(dsn, autocommit=True, row_factory=tuple_row) as connection:
        for table, statement in PURGE_STATEMENTS.items():
            try:
                deleted[table] = connection.execute(statement).rowcount
            except psycopg.errors.UndefinedTable:
                deleted[table] = 0
    return deleted


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backend.devtools.retention")
    parser.parse_args(argv)
    counts = purge_expired(default_settings.postgres_dsn_psycopg)
    for table, count in counts.items():
        print(f"retention: {table}: deleted {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
