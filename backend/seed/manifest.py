"""Versioned reference-world seed manifest.

The manifest is the source of truth for the deterministic reference world. Its
checksum is computed over the definition (not database state), so reseeding is
idempotent and verifiable. Identifiers are stable strings; the fixed timestamp
keeps generated fixtures reproducible.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

SEED_VERSION = "2026-10-05.1"
FIXED_TIMESTAMP = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)


@dataclass(frozen=True)
class SeedNode:
    id: str
    type: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SeedEdge:
    source: str
    target: str
    type: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SeedDocument:
    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SeedQuery:
    id: str
    description: str
    kind: str
    argument: str
    expected_ids: tuple[str, ...]


@dataclass(frozen=True)
class SeedManifest:
    version: str
    timestamp: datetime
    nodes: tuple[SeedNode, ...]
    edges: tuple[SeedEdge, ...]
    documents: tuple[SeedDocument, ...]
    queries: tuple[SeedQuery, ...]

    def _payload(self) -> dict[str, Any]:
        def node_payload(node: SeedNode) -> dict[str, Any]:
            return {"id": node.id, "type": node.type, "properties": node.properties}

        def edge_payload(edge: SeedEdge) -> dict[str, Any]:
            return {
                "source": edge.source,
                "target": edge.target,
                "type": edge.type,
                "properties": edge.properties,
            }

        def document_payload(document: SeedDocument) -> dict[str, Any]:
            return {
                "id": document.id,
                "text": document.text,
                "metadata": document.metadata,
            }

        def query_payload(query: SeedQuery) -> dict[str, Any]:
            return {
                "id": query.id,
                "kind": query.kind,
                "argument": query.argument,
                "expected_ids": list(query.expected_ids),
            }

        return {
            "version": self.version,
            "nodes": [node_payload(node) for node in self.nodes],
            "edges": [edge_payload(edge) for edge in self.edges],
            "documents": [document_payload(document) for document in self.documents],
            "queries": [query_payload(query) for query in self.queries],
        }

    def checksum(self) -> str:
        encoded = json.dumps(self._payload(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def counts(self) -> dict[str, int]:
        node_types: dict[str, int] = {}
        for node in self.nodes:
            node_types[node.type] = node_types.get(node.type, 0) + 1
        edge_types: dict[str, int] = {}
        for edge in self.edges:
            edge_types[edge.type] = edge_types.get(edge.type, 0) + 1
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "documents": len(self.documents),
            "queries": len(self.queries),
            "node_types": len(node_types),
            "edge_types": len(edge_types),
        }

    def node_ids(self) -> set[str]:
        return {node.id for node in self.nodes}

    def nodes_of_type(self, node_type: str) -> list[SeedNode]:
        return [node for node in self.nodes if node.type == node_type]

    def neighbors_of(self, node_id: str) -> set[str]:
        neighbors: set[str] = set()
        for edge in self.edges:
            if edge.source == node_id:
                neighbors.add(edge.target)
            elif edge.target == node_id:
                neighbors.add(edge.source)
        return neighbors
