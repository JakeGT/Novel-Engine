import pytest
from alembic import command
from sqlalchemy import text

from backend.adapters.postgres import PostgresDatabase
from backend.migrate import alembic_config, postgres_head_revision

pytestmark = pytest.mark.integration


def test_upgrade_is_idempotent_and_downgrades(disposable_database, pg_engine) -> None:
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", disposable_database.sqlalchemy_dsn)

    command.upgrade(config, "head")

    with pg_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
            ).scalar()
            == "vector"
        )
        assert (
            connection.execute(
                text("SELECT to_regclass('public.infrastructure_metadata')")
            ).scalar()
            is not None
        )
        assert (
            connection.execute(text("SELECT to_regclass('public.embedding_vectors')")).scalar()
            is not None
        )
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
            == postgres_head_revision()
        )

    command.downgrade(config, "base")

    with pg_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT to_regclass('public.infrastructure_metadata')")
            ).scalar()
            is None
        )
        assert (
            connection.execute(text("SELECT to_regclass('public.embedding_vectors')")).scalar()
            is None
        )


@pytest.mark.asyncio
async def test_migration_status_uses_owned_settings(disposable_database) -> None:
    """A database adapter verifies the database it owns, not process-global settings."""
    database = PostgresDatabase(disposable_database.settings())
    try:
        status = await database.migration_status()
    finally:
        await database.aclose()

    assert status.current is True
    assert status.detail == postgres_head_revision()


@pytest.mark.asyncio
async def test_migration_status_reports_unreachable_owned_database(disposable_database) -> None:
    """Global settings point at a migrated database; a missing owned database must not pass."""
    database = PostgresDatabase(disposable_database.settings(POSTGRES_DB="novel_missing_db_xyz"))
    try:
        status = await database.migration_status()
    finally:
        await database.aclose()

    assert status.current is False
    assert "migration check failed" in status.detail
