"""Seed ledger persistence in the operational PostgreSQL store.

The ledger records which seed version/checksum was applied and the resulting
record counts so reseeding and reset can be verified and repeated idempotently.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import psycopg

SEED_VERSION_KEY = "seed.version"
SEED_CHECKSUM_KEY = "seed.checksum"
SEED_COUNTS_KEY = "seed.counts"
SEED_APPLIED_AT_KEY = "seed.applied_at"
SEED_KEYS = (SEED_VERSION_KEY, SEED_CHECKSUM_KEY, SEED_COUNTS_KEY, SEED_APPLIED_AT_KEY)


def record_seed(
    dsn: str,
    version: str,
    checksum: str,
    counts: dict[str, Any],
    applied_at: datetime | None = None,
) -> None:
    applied = applied_at or datetime.now(UTC)
    rows = (
        (SEED_VERSION_KEY, version),
        (SEED_CHECKSUM_KEY, checksum),
        (SEED_COUNTS_KEY, json.dumps(counts, sort_keys=True)),
        (SEED_APPLIED_AT_KEY, applied.isoformat()),
    )
    with psycopg.connect(dsn, autocommit=True) as connection:
        for key, value in rows:
            connection.execute(
                """
                INSERT INTO infrastructure_metadata (key, value) VALUES (%s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """,
                (key, value),
            )


def read_seed(dsn: str) -> dict[str, str]:
    with psycopg.connect(dsn, autocommit=True) as connection:
        rows = connection.execute(
            "SELECT key, value FROM infrastructure_metadata WHERE key = ANY(%s)",
            (list(SEED_KEYS),),
        ).fetchall()
    return {row[0]: row[1] for row in rows}


def clear_seed(dsn: str) -> int:
    try:
        with psycopg.connect(dsn, autocommit=True) as connection:
            cursor = connection.execute(
                "DELETE FROM infrastructure_metadata WHERE key LIKE 'seed.%'"
            )
            return cursor.rowcount
    except psycopg.errors.UndefinedTable:
        return 0
