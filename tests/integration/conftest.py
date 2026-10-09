"""Disposable integration fixtures.

These fixtures require external services and are only exercised by tests marked
``integration``. They create per-run PostgreSQL databases and use the separate
``neo4j-test`` service so ordinary development volumes are never touched.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
import pytest
from alembic import command
from neo4j import Driver, GraphDatabase
from sqlalchemy import create_engine

from backend.core.config import Settings, settings
from backend.migrate import alembic_config

NEO4J_TEST_URI = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7688")


def _psycopg_dsn(database: str) -> str:
    return (
        f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{database}"
    )


def _sqlalchemy_dsn(database: str) -> str:
    return (
        f"postgresql+psycopg://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{database}"
    )


@dataclass
class DisposableDatabase:
    name: str
    psycopg_dsn: str
    sqlalchemy_dsn: str

    def settings(self, **overrides: object) -> Settings:
        values: dict[str, object] = {
            "APP_ENV": "test",
            "POSTGRES_DB": self.name,
            "POSTGRES_HOST": settings.POSTGRES_HOST,
            "POSTGRES_PORT": settings.POSTGRES_PORT,
            "POSTGRES_USER": settings.POSTGRES_USER,
            "POSTGRES_PASSWORD": settings.POSTGRES_PASSWORD,
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)


@pytest.fixture
def disposable_database() -> Iterator[DisposableDatabase]:
    """Create a uniquely named PostgreSQL database and apply migrations to head."""
    name = f"novel_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(_psycopg_dsn("postgres"), autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}"')

    # Everything after CREATE DATABASE is inside the try, so a failing migration
    # cannot leave the disposable database behind.
    try:
        config = alembic_config()
        config.set_main_option("sqlalchemy.url", _sqlalchemy_dsn(name))
        command.upgrade(config, "head")
        yield DisposableDatabase(
            name=name,
            psycopg_dsn=_psycopg_dsn(name),
            sqlalchemy_dsn=_sqlalchemy_dsn(name),
        )
    finally:
        with psycopg.connect(_psycopg_dsn("postgres"), autocommit=True) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def pg_engine(disposable_database: DisposableDatabase):
    engine = create_engine(disposable_database.sqlalchemy_dsn)
    try:
        yield engine
    finally:
        engine.dispose()


def _clear_entities(driver: Driver) -> None:
    with driver.session() as session:
        session.run(
            "MATCH (n) WHERE n:Entity OR n:CanonEntity OR n:CanonFact OR n:CanonBelief "
            "OR n:CanonEvent OR n:CanonProvenance OR n:CanonAsset OR n:CommitMarker "
            "DETACH DELETE n"
        )


@pytest.fixture
def neo4j_driver() -> Iterator[Driver]:
    """Connect to the disposable neo4j-test service, or skip when unavailable."""
    candidate = GraphDatabase.driver(
        NEO4J_TEST_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
    )
    try:
        candidate.verify_connectivity()
    except Exception:
        candidate.close()
        pytest.skip(f"neo4j-test service not reachable at {NEO4J_TEST_URI}")

    _clear_entities(candidate)
    try:
        yield candidate
    finally:
        _clear_entities(candidate)
        candidate.close()


@pytest.fixture
def neo4j_settings() -> Settings:
    return Settings(
        _env_file=None,
        APP_ENV="test",
        NEO4J_URI=NEO4J_TEST_URI,
        NEO4J_USER=settings.NEO4J_USER,
        NEO4J_PASSWORD=settings.NEO4J_PASSWORD,
    )
