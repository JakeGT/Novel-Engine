import pytest

from backend.graph.schema import Edge, Node
from tests.doubles import (
    FakeClock,
    FakeDatabase,
    FakeEmbeddingProvider,
    FakeMigrationChecker,
    FakeMigrationVerifier,
    InMemoryGraphRepository,
    InMemoryVectorStore,
    OrderedIdGenerator,
    ScriptedAIClient,
    ScriptedAIGateway,
)

pytestmark = pytest.mark.unit


def test_fake_clock_is_fixed_until_advanced() -> None:
    clock = FakeClock()
    start = clock.now()

    assert clock.now() == start
    assert clock.advance(60) > start
    assert (clock.now() - start).total_seconds() == 60


def test_ordered_id_generator_is_deterministic() -> None:
    generator = OrderedIdGenerator()
    first = OrderedIdGenerator()

    assert generator.new_id("char") == "char_0001"
    assert generator.new_id("char") == "char_0002"
    assert first.new_id("char") == "char_0001"


def test_in_memory_graph_repository_node_and_edge_roundtrip() -> None:
    graph = InMemoryGraphRepository()
    graph.add_node(
        Node(
            id="char_arin",
            type="Character",
            properties={"name": "Arin"},
            workspace_id="ws",
            project_id="prj",
        )
    )
    graph.add_node(
        Node(
            id="loc_gate",
            type="Location",
            properties={"name": "Gate"},
            workspace_id="ws",
            project_id="prj",
        )
    )
    graph.add_edge(
        Edge(
            source="char_arin",
            target="loc_gate",
            type="LOCATED_AT",
            workspace_id="ws",
            project_id="prj",
        )
    )

    stored = graph.get_node("char_arin", workspace_id="ws", project_id="prj")
    assert stored is not None
    assert stored.type == "Character"
    assert stored.properties["name"] == "Arin"
    # Cross-workspace reads do not resolve.
    assert graph.get_node("char_arin", workspace_id="other", project_id="prj") is None

    edges = graph.get_edges("char_arin", workspace_id="ws", project_id="prj")
    assert edges[0].target == "loc_gate"
    assert edges[0].type == "LOCATED_AT"

    assert graph.get_neighbors("char_arin", workspace_id="ws", project_id="prj")[0].id == "loc_gate"


def test_in_memory_graph_repository_updates_and_searches() -> None:
    graph = InMemoryGraphRepository()
    graph.add_node(
        Node(
            id="char_arin",
            type="Character",
            properties={"name": "Arin"},
            workspace_id="ws",
            project_id="prj",
        )
    )
    graph.update_node(
        Node(
            id="char_arin",
            type="Character",
            properties={"role": "scout"},
            workspace_id="ws",
            project_id="prj",
        )
    )

    stored = graph.get_node("char_arin", workspace_id="ws", project_id="prj")
    assert stored is not None
    assert stored.properties["role"] == "scout"
    assert (
        graph.search_nodes_by_name("ari", workspace_id="ws", project_id="prj")[0]["id"]
        == "char_arin"
    )
    assert (
        graph.get_nodes(workspace_id="ws", project_id="prj", node_type="Character")[0].id
        == "char_arin"
    )


def test_in_memory_vector_store_returns_scripted_results() -> None:
    store = InMemoryVectorStore(results=[{"id": "doc1", "score": 0.9}])
    store.add_documents(
        ["hello"], [{"kind": "prose"}], ["doc1"], workspace_id="ws", project_id="prj"
    )

    assert store.query("hello", workspace_id="ws", project_id="prj") == [
        {"id": "doc1", "score": 0.9}
    ]
    assert store.queries == ["hello"]
    assert store.health()


def test_fake_embedding_provider_is_deterministic_and_fixed_dimension() -> None:
    provider = FakeEmbeddingProvider(dimension=8)

    first = provider.embed("Arin")
    second = provider.embed("Arin")

    assert first == second
    assert len(first) == 8
    assert provider.calls == ["Arin", "Arin"]


@pytest.mark.asyncio
async def test_scripted_ai_gateway_returns_queued_parsed_response() -> None:
    gateway = ScriptedAIGateway(responses=[{"answer": 42}])

    response = await gateway.generate_structured(messages=[], response_format=dict)

    assert response.parsed == {"answer": 42}
    assert gateway.remaining == 0


@pytest.mark.asyncio
async def test_scripted_ai_gateway_raises_queued_error() -> None:
    gateway = ScriptedAIGateway(responses=[TimeoutError("provider timeout")])

    with pytest.raises(TimeoutError):
        await gateway.generate_structured(messages=[], response_format=dict)


@pytest.mark.asyncio
async def test_scripted_ai_client_returns_queued_parsed_response() -> None:
    client = ScriptedAIClient(responses=[{"answer": 42}])

    response = await client.beta.chat.completions.parse(model="test")

    assert response.choices[0].message.parsed == {"answer": 42}


def test_fake_migration_checker_reports_pending_versions() -> None:
    checker = FakeMigrationChecker(expected={"001", "002"}, applied={"001"})

    assert checker.pending() == {"002"}
    assert not checker.is_current()

    checker.applied.add("002")
    assert checker.is_current()


@pytest.mark.asyncio
async def test_fake_migration_verifier_and_database() -> None:
    verifier = FakeMigrationVerifier(current=True)
    database = FakeDatabase(ready=True, current=False, detail="pending")

    assert verifier.status().current
    assert await database.ready()
    assert not (await database.migration_status()).current

    await database.aclose()
    assert database.closed
