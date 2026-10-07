"""Portable project export and import.

Exports one project's canonical graph (never credentials, sessions, or provider
usage) into a checksummed, forward-compatible archive and re-imports it into a
target workspace/project. The archive carries project identity, a schema version,
an object manifest (empty until media exists), a derived-data section, and a
free-form ``sections`` map so the format does not need a redesign when media or
derived indexes arrive.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from backend.graph.schema import Edge, Node
from backend.ports import GraphRepository

ARCHIVE_VERSION = "2"
SCHEMA_VERSION = "1"


class TransferError(RuntimeError):
    """Raised when an archive is malformed or fails checksum verification."""


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

    def export_project(self, workspace_id: str, project_id: str) -> dict[str, Any]:
        nodes = self._graph.get_nodes(workspace_id=workspace_id, project_id=project_id)
        edges: list[Edge] = []
        for node in nodes:
            edges.extend(
                self._graph.get_edges(node.id, workspace_id=workspace_id, project_id=project_id)
            )

        body: dict[str, Any] = {
            "archive_version": ARCHIVE_VERSION,
            "schema_version": SCHEMA_VERSION,
            "project": {"workspace_id": workspace_id, "project_id": project_id},
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
            # Object manifest entries gain content_type/byte_length/checksum/attribution
            # when media exists; empty is valid for text-only projects.
            "objects": [],
            # Derived sections (e.g. vectors) are rebuildable and optional.
            "derived": {},
            # Forward-compatible bucket for unknown future sections.
            "sections": {},
        }
        body["checksum"] = _checksum(body)
        return body

    def import_project(
        self,
        target_workspace_id: str,
        target_project_id: str,
        archive: dict[str, Any],
    ) -> dict[str, Any]:
        if archive.get("archive_version") != ARCHIVE_VERSION:
            raise TransferError("unsupported archive version")
        if archive.get("checksum") != _checksum(archive):
            raise TransferError("archive checksum mismatch")

        node_count = 0
        for raw in archive.get("nodes", []):
            node = Node(
                id=raw["id"],
                type=raw["type"],
                properties=dict(raw.get("properties", {})),
                workspace_id=target_workspace_id,
                project_id=target_project_id,
            )
            self._graph.add_node(node)
            node_count += 1

        edge_count = 0
        for raw in archive.get("edges", []):
            edge = Edge(
                source=raw["source"],
                target=raw["target"],
                type=raw["type"],
                properties=dict(raw.get("properties", {})),
                workspace_id=target_workspace_id,
                project_id=target_project_id,
            )
            self._graph.add_edge(edge)
            edge_count += 1

        return {
            "nodes": node_count,
            "edges": edge_count,
            "objects": len(archive.get("objects", [])),
            "checksum": archive["checksum"],
        }
