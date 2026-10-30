"""Every SQL statement the recovery runbooks tell an operator to run is valid (OPS-004).

Each ``psql ... -c "..."`` in ``plan/runbooks/*.md`` runs against a database migrated to
head, with each ``<placeholder>`` replaced by a made-up id, inside a transaction that is
rolled back, so a runbook can never send someone a query for a table or column that is
not there any more.
"""

from __future__ import annotations

import re
from pathlib import Path

import psycopg
import pytest

pytestmark = pytest.mark.integration

RUNBOOKS = Path(__file__).resolve().parents[2] / "plan" / "runbooks"
STATEMENT = re.compile(r'psql[^\n]*?-c\s*(?:\\\s*\n\s*)?"((?:[^"\\]|\\.)*)"', re.S)
PLACEHOLDER = re.compile(r"<[^<>']+>")


def _statements() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for path in sorted(RUNBOOKS.glob("*.md")):
        for match in STATEMENT.finditer(path.read_text(encoding="utf-8")):
            sql = PLACEHOLDER.sub("not-a-real-id", " ".join(match.group(1).split()))
            found.append((path.name, sql))
    return found


STATEMENTS = _statements()


def test_the_runbooks_have_queries_to_check() -> None:
    names = {name for name, _ in STATEMENTS}
    assert {"spend-spike.md", "commit-recovery.md", "data-deletion.md"} <= names


@pytest.mark.parametrize(
    ("runbook", "sql"), STATEMENTS, ids=[f"{n}:{i}" for i, (n, _) in enumerate(STATEMENTS)]
)
def test_a_runbook_query_runs_on_the_current_schema(disposable_database, runbook, sql) -> None:
    with psycopg.connect(disposable_database.psycopg_dsn) as connection:
        try:
            connection.execute(sql)
        finally:
            connection.rollback()
