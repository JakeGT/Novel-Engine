from typing import List, Dict, Any, Tuple
from backend.graph.manager import graph_manager
from backend.graph.policies import TraversalPolicy
import logging

logger = logging.getLogger(__name__)

class ContextTraversal:
    def __init__(self, manager=graph_manager):
        self.manager = manager

    def traverse(self, anchor_ids: List[str], policy: TraversalPolicy) -> List[Dict[str, Any]]:
        """
        Executes a controlled Cypher traversal from the given anchor IDs using
        the rules defined in the provided TraversalPolicy.
        Scores and limits the returned subgraph based on relevance.
        """
        if not anchor_ids:
            return []
            
        # Build dynamic Cypher query based on Policy rules
        allowed_rels = ""
        if policy.allowed_edge_types:
            allowed_rels = ":" + "|".join(policy.allowed_edge_types)
            
        excluded_labels = ""
        if policy.excluded_node_types:
            # We filter out nodes that have ANY of the excluded labels
            excluded_labels = " AND ".join([f"NOT '{t}' IN labels(n)" for t in policy.excluded_node_types])
            if excluded_labels:
                excluded_labels = f"WHERE {excluded_labels}"

        if policy.use_pagerank:
            # Native Cypher Structural Connectivity Scoring (Mimics Personalized PageRank)
            # We find all paths from anchors, and score nodes by summing the inverse distances.
            # A node connected to multiple anchors or via multiple short paths gets a massive score boost.
            query = f"""
            MATCH p=(anchor:Entity)-[r{allowed_rels}*0..{policy.max_depth + 1}]-(n:Entity)
            WHERE anchor.id IN $anchor_ids
            {excluded_labels if excluded_labels and 'WHERE' not in 'WHERE' else ('AND ' + excluded_labels.replace('WHERE', '') if excluded_labels else '')}
            
            WITH n, length(p) as depth
            
            // Base score is dampened by depth, just like PageRank damping factor
            WITH n, depth,
                 CASE 
                    WHEN depth = 0 THEN 100.0 
                    ELSE 100.0 * ( $damping_factor ^ depth )
                 END as path_score
                 
            WITH n, min(depth) as shortest_depth, sum(path_score) as structural_score
            
            RETURN n.id as id, 
                   labels(n) as labels, 
                   n as properties, 
                   shortest_depth, 
                   structural_score as final_score
            ORDER BY final_score DESC
            LIMIT $max_nodes
            """
        else:
            # Variable length path traversal (Hop-based)
            query = f"""
            MATCH p=(anchor:Entity)-[r{allowed_rels}*0..{policy.max_depth}]-(n:Entity)
            WHERE anchor.id IN $anchor_ids
            {excluded_labels if excluded_labels and 'WHERE' not in 'WHERE' else ('AND ' + excluded_labels.replace('WHERE', '') if excluded_labels else '')}
            
            WITH n, length(p) as depth, anchor
            
            // Calculate Base Relevance Score based on depth
            WITH n, depth, anchor,
                 CASE 
                    WHEN depth = 0 THEN 100.0 
                    ELSE 100.0 / (depth * $weight_depth) 
                 END as temp_score
                 
            RETURN n.id as id, 
                   labels(n) as labels, 
                   n as properties, 
                   min(depth) as shortest_depth, 
                   max(temp_score) as final_score
            ORDER BY final_score DESC
            LIMIT $max_nodes
            """
        
        try:
            with self.manager.driver.session() as session:
                result = session.run(
                    query, 
                    anchor_ids=anchor_ids, 
                    weight_depth=policy.weight_depth,
                    max_nodes=policy.max_nodes,
                    damping_factor=policy.pagerank_damping
                )
                
                scanned_nodes = []
                for record in result:
                    props = dict(record["properties"])
                    if 'id' in props: del props['id']
                    
                    labels = [l for l in record["labels"] if l != "Entity"]
                    node_type = labels[0] if labels else "Unknown"
                    
                    scanned_nodes.append({
                        "id": record["id"],
                        "type": node_type,
                        "depth": record["shortest_depth"],
                        "relevance_score": round(record["final_score"], 2),
                        "properties": props
                    })
                    
                return scanned_nodes
                
        except Exception as e:
            logger.error(f"Traversal failed for anchors {anchor_ids}: {e}")
            return []

context_traversal = ContextTraversal()
