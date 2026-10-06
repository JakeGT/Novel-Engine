from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, pool

from backend.core.config import settings
from backend.graph.manager import graph_manager
from backend.graph.migrations import MigrationDriftError, Neo4jMigrationRunner

REPO_ROOT = Path(__file__).resolve().parents[1]


def alembic_config() -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return config


def postgres_head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def postgres_current_revision() -> str | None:
    engine = create_engine(settings.postgres_dsn, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def postgres_is_current() -> bool:
    head = postgres_head_revision()
    return head is not None and postgres_current_revision() == head


def upgrade_postgres() -> None:
    command.upgrade(alembic_config(), "head")


def upgrade_neo4j() -> list[str]:
    return Neo4jMigrationRunner(graph_manager.driver).run()


def verify_neo4j() -> Neo4jMigrationRunner:
    runner = Neo4jMigrationRunner(graph_manager.driver)
    return runner


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backend.migrate", description="Apply store migrations.")
    parser.add_argument("--store", choices=["all", "postgres", "neo4j"], default="all")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Check migration state without mutating either store.",
    )
    args = parser.parse_args(argv)

    exit_code = 0
    try:
        if args.store in ("all", "postgres"):
            if args.verify:
                if postgres_is_current():
                    print(f"postgres: up to date ({postgres_head_revision()})")
                else:
                    print(
                        "postgres: NOT current "
                        f"(current={postgres_current_revision()}, head={postgres_head_revision()})"
                    )
                    exit_code = 1
            else:
                upgrade_postgres()
                print(f"postgres: migrated to head ({postgres_head_revision()})")

        if args.store in ("all", "neo4j"):
            runner = verify_neo4j()
            if args.verify:
                plan = runner.verify()
                if plan.is_current:
                    print("neo4j: up to date")
                else:
                    print(
                        f"neo4j: NOT current (pending={list(plan.pending)}, drift={list(plan.drift)})"
                    )
                    exit_code = 1
            else:
                applied = upgrade_neo4j()
                print(f"neo4j: applied {applied}" if applied else "neo4j: no pending migrations")
    except MigrationDriftError as error:
        print(f"neo4j: migration drift detected: {error}")
        return 2

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
