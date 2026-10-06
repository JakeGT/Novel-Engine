import pytest
from alembic import command
from sqlalchemy import text

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
            connection.execute(text("SELECT to_regclass('public.vector_documents')")).scalar()
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
            connection.execute(text("SELECT to_regclass('public.vector_documents')")).scalar()
            is None
        )
