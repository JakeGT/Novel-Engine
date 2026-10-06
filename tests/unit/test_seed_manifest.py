from backend.graph.schema import SchemaRegistry
from backend.seed.manifest import SEED_VERSION
from backend.seed.world import REFERENCE_WORLD


def test_checksum_is_deterministic() -> None:
    assert REFERENCE_WORLD.checksum() == REFERENCE_WORLD.checksum()
    assert len(REFERENCE_WORLD.checksum()) == 64
    assert REFERENCE_WORLD.version == SEED_VERSION


def test_counts_match_definition() -> None:
    counts = REFERENCE_WORLD.counts()
    assert counts["nodes"] == len(REFERENCE_WORLD.nodes)
    assert counts["edges"] == len(REFERENCE_WORLD.edges)
    assert counts["documents"] == len(REFERENCE_WORLD.documents)
    assert counts["queries"] == len(REFERENCE_WORLD.queries)


def test_identifiers_unique_and_edges_reference_known_nodes() -> None:
    node_ids = [node.id for node in REFERENCE_WORLD.nodes]
    assert len(node_ids) == len(set(node_ids))
    known = set(node_ids)
    for edge in REFERENCE_WORLD.edges:
        assert edge.source in known, edge.source
        assert edge.target in known, edge.target


def test_all_types_are_registered(tmp_path) -> None:
    registry = SchemaRegistry(config_path=str(tmp_path / "custom_schema.json"))
    for node in REFERENCE_WORLD.nodes:
        assert registry.is_valid_node_type(node.type), node.type
    for edge in REFERENCE_WORLD.edges:
        assert registry.is_valid_edge_type(edge.type), edge.type


def test_required_reference_elements_present() -> None:
    kinds = {node.properties.get("kind") for node in REFERENCE_WORLD.nodes}
    assert {"workspace", "project", "branch"} <= kinds

    assert len(REFERENCE_WORLD.nodes_of_type("Character")) >= 2
    assert len(REFERENCE_WORLD.nodes_of_type("Location")) >= 2
    assert REFERENCE_WORLD.nodes_of_type("Object")
    assert REFERENCE_WORLD.nodes_of_type("Resource")
    assert REFERENCE_WORLD.nodes_of_type("Faction")
    assert REFERENCE_WORLD.nodes_of_type("System")
    assert REFERENCE_WORLD.nodes_of_type("Law")
    assert REFERENCE_WORLD.nodes_of_type("Flashback")
    assert REFERENCE_WORLD.nodes_of_type("Constraint")

    public_facts = [
        node
        for node in REFERENCE_WORLD.nodes
        if node.type == "Event" and node.properties.get("visibility") == "public"
    ]
    assert public_facts

    secrets = [node for node in REFERENCE_WORLD.nodes if node.type == "Secret"]
    assert any(node.properties.get("visibility") == "author" for node in secrets)

    beliefs = [node for node in REFERENCE_WORLD.nodes if node.type == "Belief"]
    assert any(node.properties.get("truth_value") is False for node in beliefs)

    threads = [node for node in REFERENCE_WORLD.nodes if node.type == "PlotArc"]
    assert any(node.properties.get("status") == "unresolved" for node in threads)

    beats = {node.properties.get("kind") for node in REFERENCE_WORLD.nodes_of_type("Beat")}
    assert {"setup", "payoff"} <= beats


def test_branches_have_conflicting_fact() -> None:
    branches = [node for node in REFERENCE_WORLD.nodes if node.properties.get("kind") == "branch"]
    assert len(branches) == 2
    branch_ids = {node.id for node in branches}
    conflicts = [
        node
        for node in REFERENCE_WORLD.nodes
        if node.properties.get("branch_id") in branch_ids and "conflicts_with" in node.properties
    ]
    assert conflicts
    for conflict in conflicts:
        assert conflict.properties["conflicts_with"] in {node.id for node in REFERENCE_WORLD.nodes}


def test_documents_declare_expected_entities() -> None:
    assert REFERENCE_WORLD.documents
    known = REFERENCE_WORLD.node_ids()
    for document in REFERENCE_WORLD.documents:
        expected = document.metadata.get("expected_entities")
        assert expected
        assert set(expected) <= known


def test_flashback_narrative_ordering() -> None:
    flashback = REFERENCE_WORLD.nodes_of_type("Flashback")[0]
    assert flashback.properties["narrative_order"] < 3
    assert "depicts" in flashback.properties
