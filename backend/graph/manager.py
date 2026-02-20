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
        RETURN n
        """
        
        with self.driver.session() as session:
            session.run(query, id=node.id, properties=node.properties)

    def create_constraints(self):
        """
        Creates a uniqueness constraint on the 'id' property for all 'Entity' nodes.
        Reference: CREATE CONSTRAINT IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE
        """
        query = "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE"
        try:
            with self.driver.session() as session:
                session.run(query)
                logger.info("Unique constraint on Entity(id) verified.")
        except Exception as e:
            logger.error(f"Failed to create constraint: {e}")

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

    def execute_cypher(self, query: str, params: Dict[str, Any] = None):
        if params is None:
            params = {}
        with self.driver.session() as session:
            result = session.run(query, **params)
            return [record.data() for record in result]

# Singleton instance
graph_manager = GraphManager()
