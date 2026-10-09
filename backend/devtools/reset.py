"""Reset development stores in a known order.

Resets Neo4j canonical entities, derived pgvector documents, the seed ledger,
and (when enabled) local debug artifacts. Schema and migration ledgers are
preserved. Production and non-local stores are always refused by the guard, and
artifact cleanup is confined to the repository.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg

from backend.core.config import settings as default_settings
from backend.devtools.guard import (
    ResetNotAllowed,
    ensure_artifact_dir_allowed,
    ensure_reset_allowed,
)
from backend.graph.manager import GraphManager
from backend.seed.ledger import clear_seed
from backend.seed.tenancy import clear_tenancy


def reset_neo4j(manager: GraphManager) -> int:
    with manager.driver.session() as session:
        # Legacy graph entities plus every node kind the canon projection writes.
        result = session.run(
            "MATCH (n) WHERE n:Entity OR n:Fact OR n:Belief OR n:Event OR n:Provenance "
            "OR n:Asset OR n:CommitMarker DETACH DELETE n"
        )
        return result.consume().counters.nodes_deleted


def reset_vector_documents(dsn: str) -> int:
    try:
        with psycopg.connect(dsn, autocommit=True) as connection:
            return connection.execute("DELETE FROM vector_documents").rowcount
    except psycopg.errors.UndefinedTable:
        return 0


def reset_artifacts(directory: str) -> int:
    root = Path(directory)
    if not root.exists():
        return 0
    removed = 0
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
            removed += 1
        elif path.is_dir():
            path.rmdir()
    return removed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backend.devtools.reset", description="Reset dev stores.")
    parser.add_argument("--force", action="store_true", help="Confirm the destructive reset.")
    args = parser.parse_args(argv)

    settings = default_settings
    try:
        environment = ensure_reset_allowed(settings, confirm=args.force, environ=os.environ)
    except Exception as error:
        print(f"reset refused: {error}")
        return 2

    print(f"resetting environment: {environment}")

    manager = GraphManager(settings)
    try:
        nodes_deleted = reset_neo4j(manager)
    finally:
        manager.close()
    print(f"neo4j: deleted {nodes_deleted} entities")

    dsn = settings.postgres_dsn_psycopg
    vector_rows = reset_vector_documents(dsn)
    print(f"postgres: deleted {vector_rows} vector documents")

    ledger_rows = clear_seed(dsn)
    print(f"postgres: cleared {ledger_rows} seed ledger rows")

    if clear_tenancy(dsn):
        print("postgres: cleared identity/tenancy rows")
    else:
        print("postgres: identity/tenancy tables not migrated (skipped)")

    if settings.ENABLE_DEBUG_ARTIFACTS:
        try:
            artifact_root = ensure_artifact_dir_allowed(settings.ARTIFACT_STORE_DIR)
        except ResetNotAllowed as error:
            print(f"artifacts: refused ({error})")
            return 2
        artifacts_removed = reset_artifacts(str(artifact_root))
        print(f"artifacts: removed {artifacts_removed} files")
    else:
        print("artifacts: disabled (ENABLE_DEBUG_ARTIFACTS=false)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
