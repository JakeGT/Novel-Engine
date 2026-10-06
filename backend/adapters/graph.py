from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from backend.core.logging import instrument, log_event
from backend.graph.manager import GraphManager
from backend.graph.migrations import Neo4jMigrationRunner
from backend.graph.schema import Edge, Node
from backend.ports import MigrationStatus

logger = logging.getLogger(__name__)


def _to_node(data: dict[str, Any]) -> Node:
    payload = dict(data)
    node_id = payload.pop("id", None)
    node_type = payload.pop("type", None)
    labels = payload.pop("labels", None)
    embedding = payload.pop("embedding", None)
    if node_type is None and labels:
        domain = [label for label in labels if label != "Entity"]
        node_type = domain[0] if domain else (labels[0] if labels else "Unknown")
    return Node(
        id=node_id or "",
        type=node_type or "Unknown",
        properties=payload,
        embedding=embedding,
    )


class Neo4jGraphRepository:
    """Adapts the existing Neo4j manager to the graph repository port."""

    def __init__(self, manager: GraphManager) -> None:
        self.manager = manager

    @instrument("graph.add_node", logger)
    def add_node(self, node: Node) -> None:
        self.manager.add_node(node)

    @instrument("graph.get_node", logger)
    def get_node(self, node_id: str) -> Node | None:
        data = self.manager.get_node(node_id)
        return _to_node(data) if data else None

    @instrument("graph.update_node", logger)
    def update_node(self, node: Node) -> Node | None:
        data = self.manager.update_node(node)
        return _to_node(data) if data else None

    @instrument("graph.add_edge", logger)
    def add_edge(self, edge: Edge) -> None:
        self.manager.add_edge(edge)

    @instrument("graph.get_edges", logger)
    def get_edges(self, node_id: str) -> list[Edge]:
        return [
            Edge(
                source=record["source"],
                target=record["target"],
                type=record["type"],
                properties=record.get("properties") or {},
            )
            for record in self.manager.get_edges(node_id)
        ]

    @instrument("graph.get_nodes", logger)
    def get_nodes(self, node_type: str | None = None, node_id: str | None = None) -> list[Node]:
        nodes: list[Node] = []
        for record in self.manager.get_nodes(node_type=node_type, node_id=node_id) or []:
            if "n" in record:
                data = dict(record["n"])
                data["labels"] = record.get("labels")
            else:
                data = record
            nodes.append(_to_node(data))
        return nodes

    @instrument("graph.search_nodes_by_name", logger)
    def search_nodes_by_name(self, query: str, type: str | None = None) -> list[dict[str, Any]]:
        return self.manager.search_nodes_by_name(query, type)

    @instrument("graph.search_semantic_nodes", logger)
    def search_semantic_nodes(
        self, embedding: Sequence[float], top_k: int = 5, min_score: float = 0.5
    ) -> list[str]:
        return self.manager.search_semantic_nodes(list(embedding), top_k=top_k, min_score=min_score)

    @instrument("graph.traverse", logger)
    def traverse(self, anchor_ids: Sequence[str], policy: Any) -> list[dict[str, Any]]:
        from backend.graph.traversal import ContextTraversal

        return ContextTraversal(self.manager).traverse(list(anchor_ids), policy)

    @instrument("graph.delete_node", logger)
    def delete_node(self, node_id: str) -> bool:
        return self.manager.delete_node(node_id)

    def ping(self) -> bool:
        try:
            self.manager.driver.verify_connectivity()
            return True
        except Exception as error:
            log_event(
                logger,
                "graph.ping.failed",
                level=logging.WARNING,
                error_type=type(error).__name__,
            )
            return False

    def close(self) -> None:
        self.manager.close()


class Neo4jMigrationVerifier:
    def __init__(self, runner: Neo4jMigrationRunner) -> None:
        self.runner = runner

    def status(self) -> MigrationStatus:
        plan = self.runner.verify()
        if plan.is_current:
            return MigrationStatus(current=True)
        return MigrationStatus(
            current=False,
            detail=f"pending={list(plan.pending)} drift={list(plan.drift)}",
        )
