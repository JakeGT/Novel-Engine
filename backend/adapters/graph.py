from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from backend.core.logging import instrument, log_event
from backend.graph.manager import GraphManager
from backend.graph.migrations import Neo4jMigrationRunner
from backend.graph.schema import Edge, Node
from backend.ports import MigrationStatus

logger = logging.getLogger(__name__)


def _parse_created_at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _to_node(data: dict[str, Any]) -> Node:
    payload = dict(data)
    node_id = payload.pop("id", None)
    node_type = payload.pop("type", None)
    labels = payload.pop("labels", None)
    embedding = payload.pop("embedding", None)
    created_at = _parse_created_at(payload.pop("created_at", None))
    workspace_id = payload.pop("workspace_id", None)
    project_id = payload.pop("project_id", None)
    if node_type is None and labels:
        domain = [label for label in labels if label != "Entity"]
        node_type = domain[0] if domain else (labels[0] if labels else "Unknown")
    node = Node(
        id=node_id or "",
        type=node_type or "Unknown",
        properties=payload,
        embedding=embedding,
        workspace_id=workspace_id,
        project_id=project_id,
    )
    if created_at is not None:
        node = node.model_copy(update={"created_at": created_at})
    return node


class Neo4jGraphRepository:
    """Adapts the existing Neo4j manager to the scoped graph repository port."""

    def __init__(self, manager: GraphManager) -> None:
        self.manager = manager

    @instrument("graph.add_node", logger)
    def add_node(self, node: Node) -> None:
        self.manager.add_node(node)

    @instrument("graph.get_node", logger)
    def get_node(self, node_id: str, *, workspace_id: str, project_id: str) -> Node | None:
        data = self.manager.get_node(node_id, workspace_id=workspace_id, project_id=project_id)
        return _to_node(data) if data else None

    @instrument("graph.update_node", logger)
    def update_node(self, node: Node) -> Node | None:
        data = self.manager.update_node(node)
        return _to_node(data) if data else None

    @instrument("graph.add_edge", logger)
    def add_edge(self, edge: Edge) -> None:
        self.manager.add_edge(edge)

    @instrument("graph.get_edges", logger)
    def get_edges(self, node_id: str, *, workspace_id: str, project_id: str) -> list[Edge]:
        return [
            Edge(
                source=record["source"],
                target=record["target"],
                type=record["type"],
                properties=record.get("properties") or {},
                workspace_id=workspace_id,
                project_id=project_id,
            )
            for record in self.manager.get_edges(
                node_id, workspace_id=workspace_id, project_id=project_id
            )
        ]

    @instrument("graph.get_nodes", logger)
    def get_nodes(
        self,
        *,
        workspace_id: str,
        project_id: str,
        node_type: str | None = None,
        node_id: str | None = None,
    ) -> list[Node]:
        nodes: list[Node] = []
        records = self.manager.get_nodes(
            workspace_id=workspace_id,
            project_id=project_id,
            node_type=node_type,
            node_id=node_id,
        )
        for record in records or []:
            if "n" in record:
                data = dict(record["n"])
                data["labels"] = record.get("labels")
            else:
                data = record
            nodes.append(_to_node(data))
        return nodes

    @instrument("graph.search_nodes_by_name", logger)
    def search_nodes_by_name(
        self,
        query: str,
        *,
        workspace_id: str,
        project_id: str,
        type: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.manager.search_nodes_by_name(
            query, workspace_id=workspace_id, project_id=project_id, type=type
        )

    @instrument("graph.search_semantic_nodes", logger)
    def search_semantic_nodes(
        self,
        embedding: Sequence[float],
        *,
        workspace_id: str,
        project_id: str,
        top_k: int = 5,
        min_score: float = 0.5,
    ) -> list[str]:
        return self.manager.search_semantic_nodes(
            list(embedding),
            workspace_id=workspace_id,
            project_id=project_id,
            top_k=top_k,
            min_score=min_score,
        )

    @instrument("graph.traverse", logger)
    def traverse(
        self,
        anchor_ids: Sequence[str],
        policy: Any,
        *,
        workspace_id: str,
        project_id: str,
    ) -> list[dict[str, Any]]:
        from backend.graph.traversal import ContextTraversal

        return ContextTraversal(self.manager).traverse(
            list(anchor_ids), policy, workspace_id=workspace_id, project_id=project_id
        )

    @instrument("graph.delete_node", logger)
    def delete_node(self, node_id: str, *, workspace_id: str, project_id: str) -> bool:
        return self.manager.delete_node(node_id, workspace_id=workspace_id, project_id=project_id)

    @instrument("graph.delete_workspace_nodes", logger)
    def delete_workspace_nodes(self, workspace_id: str) -> int:
        return self.manager.delete_workspace_nodes(workspace_id)

    @instrument("graph.delete_project_nodes", logger)
    def delete_project_nodes(self, workspace_id: str, project_id: str) -> int:
        return self.manager.delete_project_nodes(workspace_id, project_id)

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
