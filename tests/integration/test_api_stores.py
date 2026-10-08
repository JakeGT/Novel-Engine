from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.adapters.artifacts import NullArtifactStore
from backend.adapters.graph import Neo4jGraphRepository, Neo4jMigrationVerifier
from backend.adapters.vector import PgVectorStore
from backend.api.auth import get_project_scope, require_local_graph_write, require_project_scope
from backend.container import Container
from backend.domain.identity import AccessScope, AuthMode
from backend.graph.manager import GraphManager
from backend.graph.migrations import Neo4jMigrationRunner
from backend.graph.schema import SchemaRegistry
from backend.main import create_app
from backend.services.character_service import CharacterService
from tests.doubles import (
    FakeClock,
    FakeDatabase,
    FakeEmbeddingProvider,
    OrderedIdGenerator,
    ScriptedAIGateway,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def container(neo4j_driver, neo4j_settings, disposable_database, tmp_path) -> Iterator[Container]:
    manager = GraphManager(neo4j_settings)
    Neo4jMigrationRunner(manager.driver).run()
    embeddings = FakeEmbeddingProvider()
    graph = Neo4jGraphRepository(manager)
    registry = SchemaRegistry(config_path=str(tmp_path / "custom_schema.json"))
    container = Container(
        settings=disposable_database.settings(),
        graph=graph,
        vectors=PgVectorStore(disposable_database.psycopg_dsn, embeddings),
        embeddings=embeddings,
        ai=ScriptedAIGateway(),
        schema_registry=registry,
        clock=FakeClock(),
        ids=OrderedIdGenerator(),
        artifacts=NullArtifactStore(),
        neo4j_migrations=Neo4jMigrationVerifier(Neo4jMigrationRunner(manager.driver)),
        database=FakeDatabase(ready=True),
        character_service=CharacterService(
            graph=graph,
            ai=ScriptedAIGateway(),
            embeddings=embeddings,
            schema_registry=registry,
            artifacts=NullArtifactStore(),
        ),
    )
    try:
        yield container
    finally:
        manager.close()


@pytest.fixture
def client(container: Container) -> Iterator[TestClient]:
    app = create_app(container=container)
    scope = AccessScope(
        actor_user_id="tester",
        auth_mode=AuthMode.LOCAL,
        workspace_id="ws-test",
        project_id="prj-test",
    )

    async def _scope() -> AccessScope:
        return scope

    app.dependency_overrides[get_project_scope] = _scope
    app.dependency_overrides[require_project_scope] = _scope
    # Generic graph writes are a local/admin escape hatch; this integration app is
    # local mode, so allow them without a platform-admin session.
    app.dependency_overrides[require_local_graph_write] = _scope
    with TestClient(app) as test_client:
        yield test_client


def test_entity_lifecycle_against_real_neo4j(client: TestClient) -> None:
    created = client.post(
        "/api/v1/entities",
        json={"id": "char_mara", "type": "Character", "properties": {"name": "Mara Voss"}},
    )
    assert created.status_code == 201
    assert created.json()["id"] == "char_mara"
    assert created.json()["type"] == "Character"

    fetched = client.get("/api/v1/entities/char_mara")
    assert fetched.status_code == 200
    assert fetched.json()["properties"]["name"] == "Mara Voss"

    searched = client.get("/api/v1/entities/search", params={"q": "mara"})
    assert searched.status_code == 200
    assert [item["id"] for item in searched.json()["items"]] == ["char_mara"]

    listed = client.get("/api/v1/entities", params={"type": "Character"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    updated = client.put(
        "/api/v1/entities/char_mara",
        json={"type": "Character", "properties": {"name": "Mara Voss", "role": "guard"}},
    )
    assert updated.status_code == 200
    assert updated.json()["properties"]["role"] == "guard"

    deleted = client.delete("/api/v1/entities/char_mara")
    assert deleted.status_code == 204
    assert client.get("/api/v1/entities/char_mara").status_code == 404


def test_edges_against_real_neo4j(client: TestClient) -> None:
    for entity_id, entity_type, name in (
        ("char_mara", "Character", "Mara"),
        ("loc_harbor", "Location", "Harbor"),
    ):
        client.post(
            "/api/v1/entities",
            json={"id": entity_id, "type": entity_type, "properties": {"name": name}},
        )

    created = client.post(
        "/api/v1/edges",
        json={"source": "char_mara", "target": "loc_harbor", "type": "LOCATED_AT"},
    )
    assert created.status_code == 201

    edges = client.get("/api/v1/entities/char_mara/edges")
    assert edges.status_code == 200
    assert edges.json()["items"][0]["type"] == "LOCATED_AT"


def test_health_reports_real_stores(client: TestClient) -> None:
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"

    capabilities = client.get("/health/capabilities")
    assert capabilities.status_code == 200
