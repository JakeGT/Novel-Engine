import os

import pytest
from neo4j import Driver, GraphDatabase

from backend.core.config import settings
from backend.graph.migrations import (
    NEO4J_MIGRATIONS,
    Neo4jMigrationRunner,
)

pytestmark = pytest.mark.integration

NEO4J_TEST_URI = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7688")


def _clear_ledger(driver: Driver) -> None:
    with driver.session() as session:
        session.run("MATCH (m:_SchemaMigration) DETACH DELETE m")


@pytest.fixture
def driver() -> Driver:
    candidate = GraphDatabase.driver(
        NEO4J_TEST_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
    )
    try:
        candidate.verify_connectivity()
    except Exception:
        candidate.close()
        pytest.skip(f"neo4j-test service not reachable at {NEO4J_TEST_URI}")

    _clear_ledger(candidate)
    try:
        yield candidate
    finally:
        _clear_ledger(candidate)
        candidate.close()


def test_migrations_apply_twice_and_report_current(driver: Driver) -> None:
    runner = Neo4jMigrationRunner(driver, clock=lambda: "t0")

    assert runner.run() == [migration.id for migration in NEO4J_MIGRATIONS]
    assert runner.run() == []
    assert runner.verify().is_current

    with driver.session() as session:
        constraints = {record["name"] for record in session.run("SHOW CONSTRAINTS YIELD name")}
        indexes = {record["name"] for record in session.run("SHOW INDEXES YIELD name")}

    assert "entity_scope_id_unique" in constraints
    assert "entity_id_unique" not in constraints
    # Migration 0004 moved the canon projection to its own labels and dropped the
    # constraints that had been placed on the legacy graph's type labels.
    for label in ("canonentity", "canonfact", "canonbelief", "canonevent", "canonprovenance"):
        assert f"{label}_scope_id_unique" in constraints
        assert f"{label}_scope_branch_idx" in indexes
    assert "canonasset_scope_id_unique" in constraints
    for legacy in ("fact", "belief", "event", "provenance"):
        assert f"{legacy}_scope_id_unique" not in constraints
    assert "commit_marker_unique" in constraints
    assert "entity_embeddings" in indexes
