import uuid

import psycopg
import pytest
from alembic import command
from sqlalchemy import create_engine, text

from backend.core.config import settings
from backend.migrate import alembic_config

pytestmark = pytest.mark.integration


def _dsn(database: str, driver: str = "postgresql") -> str:
    return (
        f"{driver}://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{database}"
    )


@pytest.fixture
def disposable_database() -> str:
    name = f"novel_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(_dsn("postgres"), autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}"')
    try:
        yield name
    finally:
        with psycopg.connect(_dsn("postgres"), autocommit=True) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_upgrade_from_empty_is_idempotent_and_downgrades(disposable_database: str) -> None:
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", _dsn(disposable_database, "postgresql+psycopg"))
    engine = create_engine(_dsn(disposable_database, "postgresql+psycopg"))

    try:
        command.upgrade(config, "head")
        command.upgrade(config, "head")

        with engine.connect() as connection:
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
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                == "0001_initial"
            )

        command.downgrade(config, "base")

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT to_regclass('public.infrastructure_metadata')")
                ).scalar()
                is None
            )
    finally:
        engine.dispose()
