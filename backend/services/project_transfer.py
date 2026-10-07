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
from backend.ports import GraphRepository, ObjectStorePort

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
    def __init__(self, graph: GraphRepository, objects: ObjectStorePort | None = None) -> None:
        self._graph = graph
        self._objects = objects

    def export_project(self, workspace_id: str, project_id: str) -> dict[str, Any]:
        nodes = self._graph.get_nodes(workspace_id=workspace_id, project_id=project_id)
        edges: list[Edge] = []
        for node in nodes:
            edges.extend(
                self._graph.get_edges(node.id, workspace_id=workspace_id, project_id=project_id)
            )

        objects_manifest: list[dict[str, Any]] = []
        if self._objects is not None:
            objects_manifest = [
                {
                    "key": stat.key,
                    "content_type": stat.content_type,
                    "byte_length": stat.byte_length,
                    "checksum": stat.checksum,
                    # Attribution/licence fields are reserved for source media.
                    "attribution": {},
                }
                for stat in self._objects.list_objects(
                    workspace_id=workspace_id, project_id=project_id
                )
            ]

        body: dict[str, Any] = {
            "archive_version": ARCHIVE_VERSION,
            "schema_version": SCHEMA_VERSION,
            "project": {
                "workspace_id": workspace_id,
                "project_id": project_id,
                "node_count": len(nodes),
                "object_count": len(objects_manifest),
            },
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
            "objects": objects_manifest,
            # Derived sections (e.g. vectors) are rebuildable and optional.
            "derived": {},
            # Forward-compatible bucket for unknown future sections.
            "sections": {},
        }
        body["checksum"] = _checksum(body)
        return body

    @staticmethod
    def _validate_archive(archive: dict[str, Any]) -> tuple[list[dict], list[dict], list[dict]]:
        """Validate the whole archive before writing anything (preflight)."""
        if archive.get("archive_version") != ARCHIVE_VERSION:
            raise TransferError("unsupported archive version")
        if archive.get("schema_version") != SCHEMA_VERSION:
            raise TransferError("unsupported schema version")
        if archive.get("checksum") != _checksum(archive):
            raise TransferError("archive checksum mismatch")

        nodes = archive.get("nodes")
        if not isinstance(nodes, list):
            raise TransferError("archive nodes must be a list")
        node_ids: set[str] = set()
        for raw in nodes:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                raise TransferError("node entry is missing an id")
            if not isinstance(raw.get("type"), str):
                raise TransferError("node entry is missing a type")
            node_ids.add(raw["id"])

        edges = archive.get("edges")
        if not isinstance(edges, list):
            raise TransferError("archive edges must be a list")
        for raw in edges:
            if not isinstance(raw, dict):
                raise TransferError("edge entry must be an object")
            if raw.get("source") not in node_ids or raw.get("target") not in node_ids:
                raise TransferError("edge references a node outside the archive")

        objects = archive.get("objects", [])
        if not isinstance(objects, list):
            raise TransferError("archive objects must be a list")
        for raw in objects:
            if not isinstance(raw, dict) or not isinstance(raw.get("key"), str):
                raise TransferError("object manifest entry is missing a key")
            if not isinstance(raw.get("checksum"), str):
                raise TransferError("object manifest entry is missing a checksum")

        return nodes, edges, objects

    def import_project(
        self,
        target_workspace_id: str,
        target_project_id: str,
        archive: dict[str, Any],
    ) -> dict[str, Any]:
        nodes, edges, objects = self._validate_archive(archive)

        node_count = 0
        for raw in nodes:
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
        for raw in edges:
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
            "objects": len(objects),
            "checksum": archive["checksum"],
        }
