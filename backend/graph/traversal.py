from __future__ import annotations

import logging
from typing import Any

from backend.graph.manager import get_graph_manager
from backend.graph.policies import TraversalPolicy

logger = logging.getLogger(__name__)


def build_traversal_query(policy: TraversalPolicy, *, workspace_id: str | None = None) -> str:
    """Build the policy-bounded Cypher traversal.

    Traversal is intentionally undirected (``-[r*..]-``) so incoming relationships
    are considered, and excluded node types are filtered by label. When
    ``workspace_id`` is provided, results are confined to that workspace.
    """
    allowed_rels = ""
    if policy.allowed_edge_types:
        allowed_rels = ":" + "|".join(sorted(policy.allowed_edge_types))

    where_conditions = ["anchor.id IN $anchor_ids"]
    if workspace_id is not None:
        where_conditions.append("n.workspace_id = $workspace_id")
    for node_type in sorted(policy.excluded_node_types):
        where_conditions.append(f"NOT '{node_type}' IN labels(n)")
    where_clause = " AND ".join(where_conditions)

    if policy.use_pagerank:
        max_depth = policy.max_depth + 1
        return f"""
        MATCH p=(anchor:Entity)-[r{allowed_rels}*0..{max_depth}]-(n:Entity)
        WHERE {where_clause}

        WITH n, length(p) as depth

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

    return f"""
    MATCH p=(anchor:Entity)-[r{allowed_rels}*0..{policy.max_depth}]-(n:Entity)
    WHERE {where_clause}

    WITH n, length(p) as depth, anchor

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


class ContextTraversal:
    def __init__(self, manager: Any = None) -> None:
        self.manager = manager

    def traverse(
        self, anchor_ids: list[str], policy: TraversalPolicy, *, workspace_id: str
    ) -> list[dict[str, Any]]:
        if not anchor_ids:
            return []

        query = build_traversal_query(policy, workspace_id=workspace_id)
        manager = self.manager or get_graph_manager()
        try:
            with manager.driver.session() as session:
                result = session.run(
                    query,
                    anchor_ids=anchor_ids,
                    weight_depth=policy.weight_depth,
                    max_nodes=policy.max_nodes,
                    damping_factor=policy.pagerank_damping,
                    workspace_id=workspace_id,
                )

                scanned_nodes = []
                for record in result:
                    props = dict(record["properties"])
                    if "id" in props:
                        del props["id"]

                    labels = [label for label in record["labels"] if label != "Entity"]
                    node_type = labels[0] if labels else "Unknown"

                    scanned_nodes.append(
                        {
                            "id": record["id"],
                            "type": node_type,
                            "depth": record["shortest_depth"],
                            "relevance_score": round(record["final_score"], 2),
                            "properties": props,
                        }
                    )

                return scanned_nodes

        except Exception as e:
            from backend.core.logging import log_event

            log_event(
                logger,
                "graph.traversal_failed",
                level=logging.ERROR,
                anchor_count=len(anchor_ids),
                error_type=type(e).__name__,
            )
            return []


context_traversal = ContextTraversal()
