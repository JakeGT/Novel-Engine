from collections.abc import Iterator

import pytest

from backend.adapters.graph import Neo4jGraphRepository
from backend.devtools.reset import reset_neo4j, reset_vector_documents
from backend.graph.manager import GraphManager
from backend.seed.apply import SEED_PROJECT_ID, SEED_WORKSPACE_ID, apply_manifest
from backend.seed.ledger import clear_seed, read_seed, record_seed
from backend.seed.world import REFERENCE_WORLD

pytestmark = pytest.mark.integration


@pytest.fixture
def repository(neo4j_driver, neo4j_settings) -> Iterator[tuple[Neo4jGraphRepository, GraphManager]]:
    manager = GraphManager(neo4j_settings)
    try:
        yield Neo4jGraphRepository(manager), manager
    finally:
        manager.close()


def test_seed_is_idempotent_and_verifies(repository) -> None:
    graph, _manager = repository

    first = apply_manifest(graph, REFERENCE_WORLD)
    second = apply_manifest(graph, REFERENCE_WORLD)

    assert first.ok, first.problems
    assert second.ok, second.problems
    assert len(graph.get_nodes(workspace_id=SEED_WORKSPACE_ID, project_id=SEED_PROJECT_ID)) == len(
        REFERENCE_WORLD.nodes
    )


def test_reset_then_reseed_restores_reference_world(repository, disposable_database) -> None:
    graph, manager = repository
    dsn = disposable_database.psycopg_dsn

    apply_manifest(graph, REFERENCE_WORLD)
    record_seed(dsn, REFERENCE_WORLD.version, REFERENCE_WORLD.checksum(), REFERENCE_WORLD.counts())

    assert read_seed(dsn)["seed.version"] == REFERENCE_WORLD.version

    deleted = reset_neo4j(manager)
    assert deleted == len(REFERENCE_WORLD.nodes)
    assert graph.get_nodes(workspace_id=SEED_WORKSPACE_ID, project_id=SEED_PROJECT_ID) == []
    assert clear_seed(dsn) == 4
    assert read_seed(dsn) == {}

    reset_vector_documents(dsn)

    reseeded = apply_manifest(graph, REFERENCE_WORLD)
    assert reseeded.ok, reseeded.problems
    assert len(graph.get_nodes(workspace_id=SEED_WORKSPACE_ID, project_id=SEED_PROJECT_ID)) == len(
        REFERENCE_WORLD.nodes
    )
