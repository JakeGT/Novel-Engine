"""Portable project export and import.

Exports one project's canonical graph and its project-scoped objects (never
credentials, sessions, or provider usage) into a checksummed, forward-compatible
archive and re-imports it into a target workspace/project. The archive carries
project identity, a schema version, an object manifest with base64 payloads, a
derived-data section, and a free-form ``sections`` map so the format does not need
a redesign when derived indexes arrive.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import datetime
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
            for stat in self._objects.list_objects(
                workspace_id=workspace_id, project_id=project_id
            ):
                payload = self._objects.open(
                    stat.key, workspace_id=workspace_id, project_id=project_id
                )
                objects_manifest.append(
                    {
                        "key": stat.key,
                        "content_type": stat.content_type,
                        "byte_length": stat.byte_length,
                        "checksum": stat.checksum,
                        # Base64 payload so the archive is self-contained.
                        "data": base64.b64encode(payload).decode("ascii"),
                        # Attribution/licence fields are reserved for source media.
                        "attribution": {},
                    }
                )

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
            checksum = raw.get("checksum")
            if not isinstance(checksum, str) or not checksum:
                raise TransferError("object manifest entry is missing a checksum")
            data = raw.get("data")
            if not isinstance(data, str):
                raise TransferError("object manifest entry is missing its payload")
            try:
                decoded = base64.b64decode(data, validate=True)
            except (ValueError, binascii.Error) as error:
                raise TransferError("object manifest entry has invalid base64 data") from error
            if hashlib.sha256(decoded).hexdigest() != checksum:
                raise TransferError("object checksum does not match its payload")
            byte_length = raw.get("byte_length")
            if byte_length is not None and int(byte_length) != len(decoded):
                raise TransferError("object byte_length does not match its payload")

        return nodes, edges, objects

    def import_project(
        self,
        target_workspace_id: str,
        target_project_id: str,
        archive: dict[str, Any],
    ) -> dict[str, Any]:
        nodes, edges, objects = self._validate_archive(archive)
        if objects and self._objects is None:
            raise TransferError("object store is not configured for this deployment")

        # Graph import is sequential; a partial failure raises with progress so the
        # caller can retry. True multi-store atomicity is deferred (no journal).
        node_count = 0
        try:
            for raw in nodes:
                node = Node(
                    id=raw["id"],
                    type=raw["type"],
                    properties=dict(raw.get("properties", {})),
                    workspace_id=target_workspace_id,
                    project_id=target_project_id,
                )
                created_at = raw.get("created_at")
                if created_at:
                    node = node.model_copy(
                        update={"created_at": datetime.fromisoformat(created_at)}
                    )
                self._graph.add_node(node)
                node_count += 1
        except Exception as error:  # pragma: no cover - adapter dependent
            raise TransferError(f"graph import failed after {node_count} nodes") from error

        edge_count = 0
        try:
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
        except Exception as error:  # pragma: no cover - adapter dependent
            raise TransferError(
                f"graph import failed after {node_count} nodes and {edge_count} edges"
            ) from error

        object_count = 0
        if self._objects is not None:
            # Deduplicate by checksum so a retried import does not create duplicate
            # objects for payloads already present in the target project.
            existing_checksums = {
                stat.checksum
                for stat in self._objects.list_objects(
                    workspace_id=target_workspace_id, project_id=target_project_id
                )
            }
            for raw in objects:
                payload = base64.b64decode(raw["data"], validate=True)
                if raw["checksum"] in existing_checksums:
                    continue
                self._objects.put(
                    payload,
                    workspace_id=target_workspace_id,
                    project_id=target_project_id,
                    content_type=raw.get("content_type", "application/octet-stream"),
                )
                existing_checksums.add(raw["checksum"])
                object_count += 1

        return {
            "nodes": node_count,
            "edges": edge_count,
            "objects": object_count,
            "checksum": archive["checksum"],
        }
