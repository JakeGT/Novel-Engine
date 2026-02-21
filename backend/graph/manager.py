from neo4j import GraphDatabase
from typing import List, Optional, Dict, Any
from backend.graph.schema import Node, Edge, NodeType, EdgeType
from backend.core.config import settings
import logging
import re

logger = logging.getLogger(__name__)

class GraphManager:
    def __init__(self):
        self.driver = GraphDatabase.driver(
            settings.NEO4J_URI, 
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
        )
        # Ensure constraints and indexes are created on startup
        self.create_constraints()
        self.create_vector_index()

    def close(self):
        self.driver.close()

    def _validate_identifier(self, identifier: str) -> str:
        """
        Validates that the identifier (label or relationship type) contains only 
        alphanumeric characters and underscores to prevent Cypher injection.
        """
        if not re.match(r'^[a-zA-Z0-9_]+$', identifier):
            raise ValueError(f"Invalid identifier: {identifier}. Only alphanumeric characters and underscores are allowed.")
        return identifier

    def add_node(self, node: Node):
        # Validate and sanitize label
        label = self._validate_identifier(node.type)
        
        # Merge on generic Entity label + ID to ensure global uniqueness check
        # Then set the specific logic label
        query = f"""
        MERGE (n:Entity {{id: $id}})
        SET n:{label}, n += $properties
        """
        
        if node.embedding is not None:
            query += "SET n.embedding = $embedding\n"
            
        query += "RETURN n"
        
        with self.driver.session() as session:
            session.run(query, id=node.id, properties=node.properties, embedding=node.embedding)

    def create_constraints(self):
        """
        Creates a uniqueness constraint on the 'id' property for all 'Entity' nodes.
        Reference: CREATE CONSTRAINT IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE
        """
        queries = [
            "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE",
            ]
        try:
            with self.driver.session() as session:
                for query in queries:
                    session.run(query)
                logger.info("Constraints created successfully.")
        except Exception as e:
            logger.error(f"Failed to create constraint: {e}")

    def create_vector_index(self):
        """
        Creates a native vector index for Entity embeddings.
        Tries Neo4j 5.15+ syntax first, falls back to older 5.x procedure.
        """
        query_v5_15 = """
        CREATE VECTOR INDEX entity_embeddings IF NOT EXISTS
        FOR (n:Entity)
        ON (n.embedding)
        OPTIONS {indexConfig: {
         `vector.dimensions`: 384,
         `vector.similarity_function`: 'cosine'
        }}
        """
        
        query_v5_0 = """
        CALL db.index.vector.createNodeIndex('entity_embeddings', 'Entity', 'embedding', 384, 'cosine')
        """
        
        try:
            with self.driver.session() as session:
                try:
                    session.run(query_v5_15)
                    logger.info("Vector Index created successfully (v5.15+ syntax).")
                except Exception as e:
                    if "SyntaxError" in str(e) or "Neo.ClientError.Statement.SyntaxError" in str(e):
                        logger.warning("5.15+ syntax failed, trying older db.index.vector procedure...")
                        session.run(query_v5_0)
                        logger.info("Vector Index created successfully (v5.0 syntax).")
                    else:
                        raise e
        except Exception as e:
            if "already exists" not in str(e).lower():
                logger.error(f"Failed to create vector index: {e}")

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
            session.run(query, 
                source=edge.source, 
                target=edge.target, 
                properties=edge.properties
            )

    def delete_edge(self, edge_id: str):
        query = """
        MATCH ()-[r]->()
        WHERE id(r) = $edge_id
        DELETE r
        """
        with self.driver.session() as session:
            session.run(query, edge_id=edge_id)

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
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
                labels = [l for l in record["labels"] if l != "Entity"]
                if labels:
                    node_data["type"] = labels[0]
                elif record["labels"]:
                    # Fallback if only 'Entity' label exists
                    node_data["type"] = record["labels"][0]
                return node_data
            return None

    def update_node(self, node: Node) -> Optional[Dict[str, Any]]:
        query = """
        MATCH (n:Entity {id: $id})
        SET n += $properties
        """
        
        if node.embedding is not None:
            query += "SET n.embedding = $embedding\n"
            
        query += "RETURN n, labels(n) as labels"
        
        with self.driver.session() as session:
            result = session.run(query, id=node.id, properties=node.properties, embedding=node.embedding)
            record = result.single()
            if record:
                node_data = dict(record["n"])
                labels = [l for l in record["labels"] if l != "Entity"]
                if labels:
                    node_data["type"] = labels[0]
                elif record["labels"]:
                    node_data["type"] = record["labels"][0]
                return node_data
            return None

    def get_neighbors(self, node_id: str, edge_type: Optional[str] = None) -> List[Dict[str, Any]]:
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
                labels = [l for l in record["labels"] if l != "Entity"]
                if labels:
                    neighbor_data["type"] = labels[0]
                elif record["labels"]:
                    neighbor_data["type"] = record["labels"][0]

                neighbor_data["relationship"] = {
                    "type": record["rel_type"],
                    "properties": record["rel_props"]
                }
                neighbors.append(neighbor_data)
        return neighbors

    def get_edges(self, node_id: str) -> List[Dict[str, Any]]:
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
                edges.append({
                    "source": record["source"],
                    "target": record["target"],
                    "type": record["type"],
                    "properties": record["properties"]
                })
        return edges

    def get_edge(self, source_id: str, target_id: str, edge_type: Optional[str] = None) -> List[Dict[str, Any]]:
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
                edges.append({
                    "source": record["source"],
                    "target": record["target"],
                    "type": record["type"],
                    "properties": record["properties"]
                })
        return edges

    def execute_cypher(self, query: str, params: Dict[str, Any] = None):
        if params is None:
            params = {}
        with self.driver.session() as session:
            result = session.run(query, **params)
            return [record.data() for record in result]

    def get_nodes(self, node_type: Optional[str] = None, node_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        if node_type:
            node_type = self._validate_identifier(node_type)
            node_type = f":{node_type}"
        else:
            node_type = ""

        if node_id:
            node_id = self._validate_identifier(node_id)
            node_id = f"{{id: $node_id}}"
        else:
            node_id = ""

        query = f"""
        MATCH (n:Entity{node_type} {node_id})
        RETURN n, labels(n) as labels
        """
        with self.driver.session() as session:
            result = session.run(query, node_id=node_id)
            return [record.data() for record in result]

    def get_top_10_nodes_by_name_and_type(self, node_type: str, name: str) -> List[Dict[str, Any]]:
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
                labels = [l for l in record["labels"] if l != "Entity"]
                if labels:
                    node_data["type"] = labels[0]
                elif record["labels"]:
                    node_data["type"] = record["labels"][0]
                nodes.append(node_data)
            return nodes

    def search_nodes_by_name(self, query_string: str, type: Optional[str] = None) -> List[Dict[str, Any]]:
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
                labels = [l for l in record["labels"] if l != "Entity"]
                node_type = labels[0] if labels else "Unknown"
                
                nodes.append({
                    "id": record["id"],
                    "name": record["name"] or record["id"],
                    "type": node_type
                })
            return nodes

    def search_semantic_nodes(self, embedding: List[float], top_k: int = 5, min_score: float = 0.5) -> List[str]:
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
            logger.error(f"Semantic search failed: {e}")
        return anchor_ids

# Singleton instance
graph_manager = GraphManager()
