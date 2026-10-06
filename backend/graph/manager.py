import logging
import re
from functools import lru_cache
from typing import Any

from neo4j import GraphDatabase

from backend.core.config import Settings
from backend.core.config import settings as default_settings
from backend.graph.schema import Edge, Node

logger = logging.getLogger(__name__)


class GraphManager:
    def __init__(self, settings: Settings | None = None):
        resolved = settings or default_settings
        self.driver = GraphDatabase.driver(
            resolved.NEO4J_URI, auth=(resolved.NEO4J_USER, resolved.NEO4J_PASSWORD)
        )

    def close(self):
        self.driver.close()

    def _validate_identifier(self, identifier: str) -> str:
        """
        Validates that the identifier (label or relationship type) contains only
        alphanumeric characters and underscores to prevent Cypher injection.
        """
        if not re.match(r"^[a-zA-Z0-9_]+$", identifier):
            raise ValueError(
                f"Invalid identifier: {identifier}. Only alphanumeric characters and underscores are allowed."
            )
        return identifier

    def add_node(self, node: Node):
        # Validate and sanitize label
        label = self._validate_identifier(node.type)

        # Merge on generic Entity label + ID to ensure global uniqueness check
        # Then set the specific logic label. created_at is written only on first
        # creation so reads return a stable timestamp.
        query = f"""
        MERGE (n:Entity {{id: $id}})
        ON CREATE SET n.created_at = $created_at
        SET n:{label}, n += $properties
        """

        if node.embedding is not None:
            query += "SET n.embedding = $embedding\n"

        query += "RETURN n"

        with self.driver.session() as session:
            session.run(
                query,
                id=node.id,
                properties=node.properties,
                embedding=node.embedding,
                created_at=node.created_at.isoformat(),
            )

    def add_edge(self, edge: Edge):
        # Validate and sanitize relationship type
        rel_type = self._validate_identifier(edge.type)

        query = f"""
        MATCH (a:Entity {{id: $source}}), (b:Entity {{id: $target}})
        MERGE (a)-[r:{rel_type}]->(b)
        SET r += $properties
        RETURN r
        """
        with self.driver.session() as session:
            session.run(query, source=edge.source, target=edge.target, properties=edge.properties)

    def delete_edge(self, edge_id: str):
        query = """
        MATCH ()-[r]->()
        WHERE id(r) = $edge_id
        DELETE r
        """
        with self.driver.session() as session:
            session.run(query, edge_id=edge_id)

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        query = """
        MATCH (n:Entity {id: $id})
        RETURN n, labels(n) as labels
        """
        with self.driver.session() as session:
            result = session.run(query, id=node_id)
            record = result.single()
            if record:
                node_data = dict(record["n"])
                # filter out the generic 'Entity' label to find the specific type
                labels = [label for label in record["labels"] if label != "Entity"]
                if labels:
                    node_data["type"] = labels[0]
                elif record["labels"]:
                    # Fallback if only 'Entity' label exists
                    node_data["type"] = record["labels"][0]
                return node_data
            return None

    def update_node(self, node: Node) -> dict[str, Any] | None:
        query = """
        MATCH (n:Entity {id: $id})
        SET n += $properties
        """

        if node.embedding is not None:
            query += "SET n.embedding = $embedding\n"

        query += "RETURN n, labels(n) as labels"

        with self.driver.session() as session:
            result = session.run(
                query, id=node.id, properties=node.properties, embedding=node.embedding
            )
            record = result.single()
            if record:
                node_data = dict(record["n"])
                labels = [label for label in record["labels"] if label != "Entity"]
                if labels:
                    node_data["type"] = labels[0]
                elif record["labels"]:
                    node_data["type"] = record["labels"][0]
                return node_data
            return None

    def get_neighbors(self, node_id: str, edge_type: str | None = None) -> list[dict[str, Any]]:
        # Validate edge_type if provided to prevent injection
        if edge_type:
            edge_type = self._validate_identifier(edge_type)

        rel_clause = f":{edge_type}" if edge_type else ""
        query = f"""
        MATCH (n:Entity {{id: $id}})-[r{rel_clause}]->(m)
        RETURN m, labels(m) as labels, type(r) as rel_type, properties(r) as rel_props
        """
        neighbors = []
        with self.driver.session() as session:
            result = session.run(query, id=node_id)
            for record in result:
                neighbor_data = dict(record["m"])
                # Extract type from labels
                labels = [label for label in record["labels"] if label != "Entity"]
                if labels:
                    neighbor_data["type"] = labels[0]
                elif record["labels"]:
                    neighbor_data["type"] = record["labels"][0]

                neighbor_data["relationship"] = {
                    "type": record["rel_type"],
                    "properties": record["rel_props"],
                }
                neighbors.append(neighbor_data)
        return neighbors

    def get_edges(self, node_id: str) -> list[dict[str, Any]]:
        query = """
        MATCH (n:Entity {id: $id})-[r]->(m:Entity)
        RETURN
            n.id AS source,
            m.id AS target,
            type(r) AS type,
            properties(r) AS properties
        UNION
        MATCH (n:Entity {id: $id})<-[r]-(m:Entity)
        RETURN
            m.id AS source,
            n.id AS target,
            type(r) AS type,
            properties(r) AS properties
        """
        edges = []
        with self.driver.session() as session:
            result = session.run(query, id=node_id)
            for record in result:
                edges.append(
                    {
                        "source": record["source"],
                        "target": record["target"],
                        "type": record["type"],
                        "properties": record["properties"],
                    }
                )
        return edges

    def get_edge(
        self, source_id: str, target_id: str, edge_type: str | None = None
    ) -> list[dict[str, Any]]:
        rel_clause = f":{self._validate_identifier(edge_type)}" if edge_type else ""
        query = f"""
        MATCH (a:Entity {{id: $source_id}})-[r{rel_clause}]->(b:Entity {{id: $target_id}})
        RETURN
            a.id AS source,
            b.id AS target,
            type(r) AS type,
            properties(r) AS properties
        """
        edges = []
        with self.driver.session() as session:
            result = session.run(query, source_id=source_id, target_id=target_id)
            for record in result:
                edges.append(
                    {
                        "source": record["source"],
                        "target": record["target"],
                        "type": record["type"],
                        "properties": record["properties"],
                    }
                )
        return edges

    def execute_cypher(self, query: str, params: dict[str, Any] = None):
        if params is None:
            params = {}
        with self.driver.session() as session:
            result = session.run(query, **params)
            return [record.data() for record in result]

    def get_nodes(
        self, node_type: str | None = None, node_id: str | None = None
    ) -> list[dict[str, Any]]:
        label_clause = ""
        if node_type:
            label_clause = ":" + self._validate_identifier(node_type)

        id_clause = ""
        params: dict[str, Any] = {}
        if node_id:
            id_clause = "{id: $node_id}"
            params["node_id"] = node_id

        query = f"""
        MATCH (n:Entity{label_clause} {id_clause})
        RETURN n, labels(n) as labels
        """
        with self.driver.session() as session:
            result = session.run(query, **params)
            nodes = []
            for record in result:
                node_data = dict(record["n"])
                labels = [label for label in record["labels"] if label != "Entity"]
                if labels:
                    node_data["type"] = labels[0]
                elif record["labels"]:
                    node_data["type"] = record["labels"][0]
                nodes.append(node_data)
            return nodes

    def delete_node(self, node_id: str) -> bool:
        query = "MATCH (n:Entity {id: $id}) DETACH DELETE n"
        with self.driver.session() as session:
            result = session.run(query, id=node_id)
            return result.consume().counters.nodes_deleted > 0

    def get_top_10_nodes_by_name_and_type(self, node_type: str, name: str) -> list[dict[str, Any]]:
        # Validate and sanitize node_type to prevent injection
        node_type = self._validate_identifier(node_type)

        query = f"""
        MATCH (n:Entity:{node_type})
        WHERE toLower(n.name) CONTAINS toLower($name)
        RETURN n, labels(n) as labels
        LIMIT 10
        """
        with self.driver.session() as session:
            result = session.run(query, name=name)
            nodes = []
            for record in result:
                node_data = dict(record["n"])
                labels = [label for label in record["labels"] if label != "Entity"]
                if labels:
                    node_data["type"] = labels[0]
                elif record["labels"]:
                    node_data["type"] = record["labels"][0]
                nodes.append(node_data)
            return nodes

    def search_nodes_by_name(
        self, query_string: str, type: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Performs a fast, case-insensitive fuzzy search on node names for frontend @-mentions.
        Returns a lightweight dictionary with only id, name, and type for rendering badges.
        """
        # We search across all Entity nodes where the name or id contains the string
        if type:
            type = self._validate_identifier(type)
            type = f":{type}"
        else:
            type = ""
        query = f"""
        MATCH (n:Entity{type})
        WHERE toLower(n.name) CONTAINS toLower($query_str) 
           OR toLower(n.id) CONTAINS toLower($query_str)
        RETURN n.id AS id, n.name AS name, labels(n) AS labels
        LIMIT 20
        """
        with self.driver.session() as session:
            result = session.run(query, query_str=query_string)
            nodes = []
            for record in result:
                labels = [label for label in record["labels"] if label != "Entity"]
                node_type = labels[0] if labels else "Unknown"

                nodes.append(
                    {"id": record["id"], "name": record["name"] or record["id"], "type": node_type}
                )
            return nodes

    def search_semantic_nodes(
        self, embedding: list[float], top_k: int = 5, min_score: float = 0.5
    ) -> list[str]:
        """
        Uses the native Neo4j vector index to find the most semantically similar nodes.
        Returns a list of node IDs.
        """
        query = """
        CALL db.index.vector.queryNodes('entity_embeddings', $top_k, $embedding)
        YIELD node, score
        WHERE score >= $min_score
        RETURN node.id as id, score
        """
        anchor_ids = []
        try:
            with self.driver.session() as session:
                result = session.run(query, top_k=top_k, embedding=embedding, min_score=min_score)
                for record in result:
                    anchor_ids.append(record["id"])
        except Exception as e:
            from backend.core.logging import log_event

            log_event(
                logger,
                "graph.semantic_search_failed",
                level=logging.ERROR,
                error_type=type(e).__name__,
            )
        return anchor_ids


@lru_cache(maxsize=1)
def get_graph_manager() -> GraphManager:
    return GraphManager()


class _LazyGraphManager:
    """Backwards-compatible, lazily constructed manager. No driver is created at import."""

    def __init__(self) -> None:
        self._instance = None

    def _resolve(self) -> GraphManager:
        if self._instance is None:
            self._instance = get_graph_manager()
        return self._instance

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)


graph_manager = _LazyGraphManager()
