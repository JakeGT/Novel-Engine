import psycopg
import pytest

from backend.seed.tenancy import apply_tenancy, clear_tenancy, counts

pytestmark = pytest.mark.integration

_TABLES = (
    "users",
    "external_identities",
    "workspaces",
    "memberships",
    "projects",
    "invitations",
)
_TOTAL = sum(counts().values())


def _table_counts(dsn: str) -> dict[str, int]:
    with psycopg.connect(dsn) as connection:
        return {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in _TABLES
        }


def test_tenancy_seed_is_idempotent_and_clearable(disposable_database) -> None:
    dsn = disposable_database.psycopg_dsn

    first = apply_tenancy(dsn)
    assert first.inserted == _TOTAL
    assert _table_counts(dsn) == counts()

    second = apply_tenancy(dsn)
    assert second.inserted == 0
    assert _table_counts(dsn) == counts()

    assert clear_tenancy(dsn) is True
    assert _table_counts(dsn) == {table: 0 for table in _TABLES}

    third = apply_tenancy(dsn)
    assert third.inserted == _TOTAL
    assert _table_counts(dsn) == counts()
