import logging
import re
from functools import lru_cache
from typing import Any

from neo4j import GraphDatabase

from backend.core.config import Settings
from backend.core.config import settings as default_settings
from backend.graph.schema import Edge, Node

logger = logging.getLogger(__name__)

SEMANTIC_OVERSAMPLE = 10
SEMANTIC_MAX_CANDIDATES = 1000


class GraphManager:
    """Neo4j persistence with mandatory workspace + project scoping.

    Every read and write is filtered by ``workspace_id`` and ``project_id``.
    ``add_node`` will not mutate a node owned by another workspace or project
    (global entity IDs are unique), and ``add_edge`` only creates edges when both
    endpoints are in the same workspace and project.
    """

    def __init__(self, settings: Settings | None = None):
        resolved = settings or default_settings
        self.driver = GraphDatabase.driver(
            resolved.NEO4J_URI, auth=(resolved.NEO4J_USER, resolved.NEO4J_PASSWORD)
        )

    def close(self):
        self.driver.close()

    def _validate_identifier(self, identifier: str) -> str:
        """Validate a label or relationship type to prevent Cypher injection."""
        if not re.match(r"^[a-zA-Z0-9_]+$", identifier):
            raise ValueError(
                f"Invalid identifier: {identifier}. Only alphanumeric characters and underscores are allowed."
            )
        return identifier

    def add_node(self, node: Node):  # noqa: D401
        label = self._validate_identifier(node.type)
        # Identity is (workspace_id, project_id, id): the same id in another project
        # is a distinct node, so writes never silently collide across projects.
        query = f"""
        MERGE (n:Entity {{id: $id, workspace_id: $workspace_id, project_id: $project_id}})
        ON CREATE SET n.created_at = $created_at
        SET n:{label}, n += $properties
        SET n.workspace_id = $workspace_id, n.project_id = $project_id
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
                workspace_id=node.workspace_id,
                project_id=node.project_id,
            )

    def add_edge(self, edge: Edge):
        rel_type = self._validate_identifier(edge.type)
        query = f"""
        MATCH (a:Entity {{id: $source, workspace_id: $workspace_id, project_id: $project_id}}),
              (b:Entity {{id: $target, workspace_id: $workspace_id, project_id: $project_id}})
        MERGE (a)-[r:{rel_type}]->(b)
        SET r += $properties, r.workspace_id = $workspace_id, r.project_id = $project_id
        RETURN r
        """
        with self.driver.session() as session:
            session.run(
                query,
                source=edge.source,
                target=edge.target,
                properties=edge.properties,
                workspace_id=edge.workspace_id,
                project_id=edge.project_id,
            )

    def delete_edge(self, edge_id: str):
        query = """
        MATCH ()-[r]->()
        WHERE id(r) = $edge_id
        DELETE r
        """
        with self.driver.session() as session:
            session.run(query, edge_id=edge_id)

    def get_node(
        self, node_id: str, *, workspace_id: str, project_id: str
    ) -> dict[str, Any] | None:
        query = """
        MATCH (n:Entity {id: $id, workspace_id: $workspace_id, project_id: $project_id})
        RETURN n, labels(n) as labels
        """
        with self.driver.session() as session:
            record = session.run(
                query, id=node_id, workspace_id=workspace_id, project_id=project_id
            ).single()
            if record:
                return self._shape(record, "n", "labels")
            return None

    def update_node(self, node: Node) -> dict[str, Any] | None:
        query = """
        MATCH (n:Entity {id: $id, workspace_id: $workspace_id, project_id: $project_id})
        SET n += $properties
        """
        if node.embedding is not None:
            query += "SET n.embedding = $embedding\n"
        query += "RETURN n, labels(n) as labels"

        with self.driver.session() as session:
            record = session.run(
                query,
                id=node.id,
                properties=node.properties,
                embedding=node.embedding,
                workspace_id=node.workspace_id,
                project_id=node.project_id,
            ).single()
            if record:
                return self._shape(record, "n", "labels")
            return None

    def _shape(self, record: Any, node_key: str, labels_key: str) -> dict[str, Any]:
        node_data = dict(record[node_key])
        labels = [label for label in record[labels_key] if label != "Entity"]
        if labels:
            node_data["type"] = labels[0]
        elif record[labels_key]:
            node_data["type"] = record[labels_key][0]
        return node_data

    def get_neighbors(
        self,
        node_id: str,
        *,
        workspace_id: str,
        project_id: str,
        edge_type: str | None = None,
    ) -> list[dict[str, Any]]:
        if edge_type:
            edge_type = self._validate_identifier(edge_type)
        rel_clause = f":{edge_type}" if edge_type else ""
        query = f"""
        MATCH (n:Entity {{id: $id, workspace_id: $workspace_id, project_id: $project_id}})
              -[r{rel_clause}]->
              (m:Entity {{workspace_id: $workspace_id, project_id: $project_id}})
        RETURN m, labels(m) as labels, type(r) as rel_type, properties(r) as rel_props
        """
        neighbors = []
        with self.driver.session() as session:
            result = session.run(
                query, id=node_id, workspace_id=workspace_id, project_id=project_id
            )
            for record in result:
                neighbor_data = self._shape(record, "m", "labels")
                neighbor_data["relationship"] = {
                    "type": record["rel_type"],
                    "properties": record["rel_props"],
                }
                neighbors.append(neighbor_data)
        return neighbors

    def get_edges(
        self, node_id: str, *, workspace_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        query = """
        MATCH (n:Entity {id: $id, workspace_id: $workspace_id, project_id: $project_id})-[r]->
              (m:Entity {workspace_id: $workspace_id, project_id: $project_id})
        RETURN n.id AS source, m.id AS target, type(r) AS type, properties(r) AS properties
        UNION
        MATCH (n:Entity {id: $id, workspace_id: $workspace_id, project_id: $project_id})<-[r]-
              (m:Entity {workspace_id: $workspace_id, project_id: $project_id})
        RETURN m.id AS source, n.id AS target, type(r) AS type, properties(r) AS properties
        """
        edges = []
        with self.driver.session() as session:
            result = session.run(
                query, id=node_id, workspace_id=workspace_id, project_id=project_id
            )
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
        self,
        source_id: str,
        target_id: str,
        *,
        workspace_id: str,
        project_id: str,
        edge_type: str | None = None,
    ) -> list[dict[str, Any]]:
        rel_clause = f":{self._validate_identifier(edge_type)}" if edge_type else ""
        query = f"""
        MATCH (a:Entity {{id: $source_id, workspace_id: $workspace_id, project_id: $project_id}})
              -[r{rel_clause}]->
              (b:Entity {{id: $target_id, workspace_id: $workspace_id, project_id: $project_id}})
        RETURN a.id AS source, b.id AS target, type(r) AS type, properties(r) AS properties
        """
        edges = []
        with self.driver.session() as session:
            result = session.run(
                query,
                source_id=source_id,
                target_id=target_id,
                workspace_id=workspace_id,
                project_id=project_id,
            )
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

    def execute_cypher(self, query: str, params: dict[str, Any] | None = None):
        if params is None:
            params = {}
        with self.driver.session() as session:
            result = session.run(query, **params)
            return [record.data() for record in result]

    def get_nodes(
        self,
        *,
        workspace_id: str,
        project_id: str,
        node_type: str | None = None,
        node_id: str | None = None,
    ) -> list[dict[str, Any]]:
        label_clause = ""
        if node_type:
            label_clause = ":" + self._validate_identifier(node_type)

        id_clause = ""
        params: dict[str, Any] = {"workspace_id": workspace_id, "project_id": project_id}
        if node_id:
            id_clause = "{id: $node_id}"
            params["node_id"] = node_id

        query = f"""
        MATCH (n:Entity{label_clause} {id_clause})
        WHERE n.workspace_id = $workspace_id AND n.project_id = $project_id
        RETURN n, labels(n) as labels
        """
        with self.driver.session() as session:
            result = session.run(query, **params)
            return [self._shape(record, "n", "labels") for record in result]

    def delete_node(self, node_id: str, *, workspace_id: str, project_id: str) -> bool:
        query = (
            "MATCH (n:Entity {id: $id, workspace_id: $workspace_id, project_id: $project_id}) "
            "DETACH DELETE n"
        )
        with self.driver.session() as session:
            result = session.run(
                query, id=node_id, workspace_id=workspace_id, project_id=project_id
            )
            return result.consume().counters.nodes_deleted > 0

    def delete_workspace_nodes(self, workspace_id: str) -> int:
        with self.driver.session() as session:
            result = session.run(
                "MATCH (n:Entity {workspace_id: $workspace_id}) DETACH DELETE n",
                workspace_id=workspace_id,
            )
            return result.consume().counters.nodes_deleted

    def delete_project_nodes(self, workspace_id: str, project_id: str) -> int:
        with self.driver.session() as session:
            result = session.run(
                "MATCH (n:Entity {workspace_id: $workspace_id, project_id: $project_id}) "
                "DETACH DELETE n",
                workspace_id=workspace_id,
                project_id=project_id,
            )
            return result.consume().counters.nodes_deleted

    def get_top_10_nodes_by_name_and_type(
        self, node_type: str, name: str, *, workspace_id: str, project_id: str
    ) -> list[dict[str, Any]]:
        node_type = self._validate_identifier(node_type)
        query = f"""
        MATCH (n:Entity:{node_type})
        WHERE n.workspace_id = $workspace_id AND n.project_id = $project_id
          AND toLower(n.name) CONTAINS toLower($name)
        RETURN n, labels(n) as labels
        LIMIT 10
        """
        with self.driver.session() as session:
            result = session.run(query, name=name, workspace_id=workspace_id, project_id=project_id)
            return [self._shape(record, "n", "labels") for record in result]

    def search_nodes_by_name(
        self,
        query_string: str,
        *,
        workspace_id: str,
        project_id: str,
        type: str | None = None,
    ) -> list[dict[str, Any]]:
        if type:
            type = self._validate_identifier(type)
            type = f":{type}"
        else:
            type = ""
        query = f"""
        MATCH (n:Entity{type})
        WHERE n.workspace_id = $workspace_id AND n.project_id = $project_id
          AND (toLower(n.name) CONTAINS toLower($query_str)
               OR toLower(n.id) CONTAINS toLower($query_str))
        RETURN n.id AS id, n.name AS name, labels(n) AS labels
        LIMIT 20
        """
        with self.driver.session() as session:
            result = session.run(
                query,
                query_str=query_string,
                workspace_id=workspace_id,
                project_id=project_id,
            )
            nodes = []
            for record in result:
                labels = [label for label in record["labels"] if label != "Entity"]
                node_type = labels[0] if labels else "Unknown"
                nodes.append(
                    {"id": record["id"], "name": record["name"] or record["id"], "type": node_type}
                )
            return nodes

    def search_semantic_nodes(
        self,
        embedding: list[float],
        *,
        workspace_id: str,
        project_id: str,
        top_k: int = 5,
        min_score: float = 0.5,
    ) -> list[str]:
        """Vector-index search restricted to the workspace and project.

        The global index is oversampled, then filtered on the entity returned by
        the index itself (never a re-match by id, which could collide across
        projects) and trimmed back to ``top_k``.
        """
        candidate_k = min(max(top_k * SEMANTIC_OVERSAMPLE, top_k), SEMANTIC_MAX_CANDIDATES)
        query = """
        CALL db.index.vector.queryNodes('entity_embeddings', $candidate_k, $embedding)
        YIELD node, score
        WHERE score >= $min_score
          AND node.workspace_id = $workspace_id
          AND node.project_id = $project_id
        RETURN node.id AS id, score
        ORDER BY score DESC
        LIMIT $top_k
        """
        anchor_ids = []
        try:
            with self.driver.session() as session:
                result = session.run(
                    query,
                    candidate_k=candidate_k,
                    top_k=top_k,
                    embedding=embedding,
                    min_score=min_score,
                    workspace_id=workspace_id,
                    project_id=project_id,
                )
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
        self._instance: GraphManager | None = None

    def _resolve(self) -> GraphManager:
        if self._instance is None:
            self._instance = get_graph_manager()
        return self._instance

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resolve(), name)


graph_manager = _LazyGraphManager()
