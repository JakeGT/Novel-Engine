"""0040_scoped_names can run again over itself (a half-finished or repeated upgrade)."""

import psycopg
import pytest
from alembic import command

from backend.migrate import alembic_config

pytestmark = pytest.mark.integration

REVISION = "0040_scoped_names"


def test_scoped_names_runs_again_and_keeps_its_check(disposable_database) -> None:
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", disposable_database.sqlalchemy_dsn)
    command.upgrade(config, REVISION)
    # Marked as not yet run although everything it adds is there: it runs again cleanly.
    command.stamp(config, "0039_check_passes")
    command.upgrade(config, REVISION)

    with psycopg.connect(disposable_database.psycopg_dsn, autocommit=True) as connection:
        checks = connection.execute(
            "SELECT count(*) FROM pg_constraint WHERE conname = 'alias_bindings_examples_list'"
        ).fetchone()
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert checks == (1,)
    assert version == (REVISION,)
