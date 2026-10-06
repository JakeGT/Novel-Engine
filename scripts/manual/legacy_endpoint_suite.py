import pytest
import requests

BASE_URL = "http://localhost:8000/api"

# Helper to ensure we format things cleanly
def cleanup_graph_data():
    """Wipes test nodes, edges, and schemas to leave the DB clean."""
    try:
        # We need a robust cleanup. For tests, we can just delete the known types.
        # It's better to delete edges first. But in our case, Neo4j cascade deletes edges if nodes are deleted via pure Detach Delete.
        # But our API doesn't have a detach delete endpoint right now.
        # We can delete the schemas, but that just removes them from the registry.
        requests.delete(f"{BASE_URL}/schema/nodes/TestCustomNode")
        requests.delete(f"{BASE_URL}/schema/edges/TEST_EDGE_TYPE")
        
        # If we need to forcefully delete test_node_01 and test_node_02 via API, we lack a DELETE /nodes/ endpoint.
        # Let's add a quick cleanup function if needed, but for now we'll assume neo4j needs a manual wipe or we add an endpoint.
        # Let's try to just use the Neo4j Python driver directly here to ensure a truly clean state since it's an integration test.
    except Exception as e:
        print(f"Cleanup failed: {e}")

@pytest.fixture(scope="module", autouse=True)
def setup_and_teardown():
    # Setup - nothing to do, assuming API is running
    yield
    # Teardown
    cleanup_graph_data()
    from backend.graph.manager import graph_manager
    graph_manager.execute_cypher("MATCH (n:TestCustomNode) DETACH DELETE n")
    graph_manager.execute_cypher("MATCH (n:Location {id: 'test_node_02'}) DETACH DELETE n")

class TestEndpoints:
    def test_01_custom_node_schema(self):
        # 1. Add schema
        res = requests.post(f"{BASE_URL}/schema/nodes/", json={"type": "TestCustomNode"})
        assert res.status_code == 200, res.text
        assert "TestCustomNode" in res.json().get("types", [])

        # 2. Get schema
        res = requests.get(f"{BASE_URL}/schema/nodes/")
        assert res.status_code == 200
        assert "TestCustomNode" in res.json().get("types", [])

    def test_02_graph_node_creation_and_retrieval(self):
        # 1. Create Node 1
        node_data = {
            "id": "test_node_01",
            "type": "TestCustomNode",
            "properties": {"name": "Test Character Alpha", "power": 100}
        }
        res = requests.post(f"{BASE_URL}/graph/nodes/", json=node_data)
        assert res.status_code == 200, res.text

        # 2. Retrieve Node 1
        res = requests.get(f"{BASE_URL}/graph/nodes/test_node_01")
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == "test_node_01"
        assert data["power"] == 100

        # 3. Update Node 1
        node_update_data = {
            "id": "test_node_01",
            "type": "TestCustomNode",
            "properties": {"name": "Test Character Alpha Update", "power": 150}
        }
        res = requests.put(f"{BASE_URL}/graph/nodes/test_node_01", json=node_update_data)
        assert res.status_code == 200
        
        res = requests.get(f"{BASE_URL}/graph/nodes/test_node_01")
        assert res.json()["power"] == 150

        # 4. Create Node 2 (Location)
        node_data_2 = {
            "id": "test_node_02",
            "type": "Location",
            "properties": {"name": "Test Location Beta"}
        }
        res = requests.post(f"{BASE_URL}/graph/nodes/", json=node_data_2)
        assert res.status_code == 200

    def test_03_custom_edge_schema(self):
        res = requests.post(f"{BASE_URL}/schema/edges/", json={"type": "TEST_EDGE_TYPE"})
        assert res.status_code == 200
        
        res = requests.get(f"{BASE_URL}/schema/edges/")
        assert res.status_code == 200
        assert "TEST_EDGE_TYPE" in res.json().get("types", [])

    def test_04_graph_edge_creation_and_retrieval(self):
        # Ensure nodes exist
        requests.post(f"{BASE_URL}/graph/nodes/", json={"id": "test_node_01", "type": "TestCustomNode", "properties": {}})
        requests.post(f"{BASE_URL}/graph/nodes/", json={"id": "test_node_02", "type": "Location", "properties": {}})

        # 1. Create Edge
        edge_data = {
            "source": "test_node_01",
            "target": "test_node_02",
            "type": "TEST_EDGE_TYPE",
            "properties": {"strength": 10}
        }
        res = requests.post(f"{BASE_URL}/graph/edges/", json=edge_data)
        assert res.status_code == 200

        # 2. Get Node Edges
        res = requests.get(f"{BASE_URL}/graph/nodes/test_node_01/edges")
        assert res.status_code == 200
        data = res.json()
        assert len(data) >= 1
        assert data[0]["type"] == "TEST_EDGE_TYPE"
        
        # 3. Get Specific Edge Between Two Nodes
        res = requests.get(f"{BASE_URL}/graph/edges/?source_id=test_node_01&target_id=test_node_02")
        assert res.status_code == 200
        assert len(res.json()) >= 1
        
        # 4. Get Specific Edge with Type Filter
        res = requests.get(f"{BASE_URL}/graph/edges/?source_id=test_node_01&target_id=test_node_02&type=TEST_EDGE_TYPE")
        assert res.status_code == 200
        assert len(res.json()) >= 1

    def test_05_entities_and_search(self):
        # Ensure node exists
        requests.post(f"{BASE_URL}/graph/nodes/", json={"id": "test_node_01", "type": "TestCustomNode", "properties": {"name": "Test Character Alpha"}})

        # 1. Get Entities
        res = requests.get(f"{BASE_URL}/graph/nodes/entities/?type=TestCustomNode")
        assert res.status_code == 200
        data = res.json()
        assert len(data) >= 1
        assert data[0]["n"]["id"] == "test_node_01"

        # 2. Fuzzy Search
        res = requests.get(f"{BASE_URL}/graph/nodes/search/?query=Alpha")
        assert res.status_code == 200
        data = res.json()
        assert len(data) >= 1
        assert data[0]["id"] == "test_node_01"

    def test_06_schema_cleanup(self):
        # The teardown fixture handles DB wipe, this just tests the schema remove endpoint
        res = requests.delete(f"{BASE_URL}/schema/nodes/TestCustomNode")
        assert res.status_code == 200
        
        res = requests.delete(f"{BASE_URL}/schema/edges/TEST_EDGE_TYPE")
        assert res.status_code == 200
