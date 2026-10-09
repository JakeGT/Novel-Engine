"""Serve the API for end-to-end browser tests against disposable state.

Creates a uniquely named PostgreSQL database, migrates it to head, makes sure the
disposable ``neo4j-test`` graph schema is current, and runs uvicorn in local
auth mode against them with a temporary object store. On exit (SIGINT/SIGTERM or
server stop) the database and object store are removed. Development databases
and volumes are never touched.

    uv run --locked python -m scripts.e2e.serve --port 8010
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Sequence

import psycopg
from alembic import command
from neo4j import GraphDatabase

from backend.core.config import settings
from backend.graph.migrations import Neo4jMigrationRunner
from backend.migrate import alembic_config

NEO4J_TEST_URI = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7688")


def _dsn(database: str, *, driver: str = "postgresql") -> str:
    return (
        f"{driver}://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{database}"
    )


def _create_database(name: str) -> None:
    with psycopg.connect(_dsn("postgres"), autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}"')
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", _dsn(name, driver="postgresql+psycopg"))
    command.upgrade(config, "head")


def _drop_database(name: str) -> None:
    with psycopg.connect(_dsn("postgres"), autocommit=True) as connection:
        connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _migrate_test_graph() -> None:
    driver = GraphDatabase.driver(
        NEO4J_TEST_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
    )
    try:
        driver.verify_connectivity()
        Neo4jMigrationRunner(driver).run()
    finally:
        driver.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.e2e.serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args(argv)

    database = f"novel_e2e_{uuid.uuid4().hex[:10]}"
    object_dir = tempfile.mkdtemp(prefix="novel-e2e-objects-")
    process: subprocess.Popen[bytes] | None = None

    def _stop(_signum: int, _frame: object) -> None:
        if process is not None and process.poll() is None:
            process.terminate()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    try:
        _create_database(database)
        _migrate_test_graph()
        env = {
            **os.environ,
            "APP_ENV": "test",
            "AUTH_MODE": "local",
            "POSTGRES_DB": database,
            "NEO4J_URI": NEO4J_TEST_URI,
            "OBJECT_STORE_DIR": object_dir,
            # No provider key: the no-provider path is what the e2e run exercises.
            "OPENAI_API_KEY": "",
            "SENTRY_DSN_BACKEND": "",
        }
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "backend.main:app",
                "--host",
                args.host,
                "--port",
                str(args.port),
                "--log-level",
                "warning",
            ],
            env=env,
        )
        return process.wait()
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        _drop_database(database)
        shutil.rmtree(object_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
