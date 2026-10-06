"""Portable workspace/project export and import.

Exports canonical graph entities and edges (never credentials, sessions, or
provider usage) into a checksummed JSON bundle and re-imports the bundle into a
target workspace. Vector indexes are derived and rebuilt separately.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from backend.graph.schema import Edge, Node
from backend.ports import GraphRepository

BUNDLE_VERSION = "1"


class TransferError(RuntimeError):
    """Raised when a bundle is malformed or fails checksum verification."""


def _checksum(payload: dict[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "checksum"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _dedupe_edges(edges: list[Edge]) -> list[Edge]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[Edge] = []
    for edge in edges:
        key = (edge.source, edge.target, edge.type)
        if key in seen:
            continue
        seen.add(key)
        unique.append(edge)
    return unique


class ProjectTransferService:
    def __init__(self, graph: GraphRepository) -> None:
        self._graph = graph

    def export_workspace(self, workspace_id: str) -> dict[str, Any]:
        nodes = self._graph.get_nodes(workspace_id=workspace_id)
        edges: list[Edge] = []
        for node in nodes:
            edges.extend(self._graph.get_edges(node.id, workspace_id=workspace_id))

        body: dict[str, Any] = {
            "bundle_version": BUNDLE_VERSION,
            "workspace_id": workspace_id,
            "nodes": [
                {
                    "id": node.id,
                    "type": node.type,
                    "properties": node.properties,
                    "created_at": node.created_at.isoformat(),
                }
                for node in nodes
            ],
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "type": edge.type,
                    "properties": edge.properties,
                }
                for edge in _dedupe_edges(edges)
            ],
        }
        body["checksum"] = _checksum(body)
        return body

    def import_workspace(self, workspace_id: str, bundle: dict[str, Any]) -> dict[str, Any]:
        if bundle.get("bundle_version") != BUNDLE_VERSION:
            raise TransferError("unsupported bundle version")
        if bundle.get("checksum") != _checksum(bundle):
            raise TransferError("bundle checksum mismatch")

        node_count = 0
        for raw in bundle.get("nodes", []):
            node = Node(
                id=raw["id"],
                type=raw["type"],
                properties=dict(raw.get("properties", {})),
                workspace_id=workspace_id,
            )
            self._graph.add_node(node)
            node_count += 1

        edge_count = 0
        for raw in bundle.get("edges", []):
            edge = Edge(
                source=raw["source"],
                target=raw["target"],
                type=raw["type"],
                properties=dict(raw.get("properties", {})),
                workspace_id=workspace_id,
            )
            self._graph.add_edge(edge)
            edge_count += 1

        return {"nodes": node_count, "edges": edge_count, "checksum": bundle["checksum"]}
