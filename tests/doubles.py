"""Deterministic in-memory test doubles for Novel Engine application tests.

These fakes model application/domain ports in plain terms. They intentionally do
not mock Neo4j driver calls, OpenAI SDK internals, or filesystem layouts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from backend.graph.schema import Edge, Node
from backend.ports import MigrationStatus, StructuredResponse


@dataclass
class FakeClock:
    """Injectable UTC clock for deterministic temporal assertions."""

    current: datetime = field(default_factory=lambda: datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC))

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> datetime:
        self.current = self.current + timedelta(seconds=seconds)
        return self.current


class OrderedIdGenerator:
    """Deterministic, ordered identifier source used in place of UUIDv7."""

    def __init__(self, prefix: str = "id") -> None:
        self.prefix = prefix
        self._counter = 0

    def new_id(self, kind: str | None = None) -> str:
        self._counter += 1
        label = kind or self.prefix
        return f"{label}_{self._counter:04d}"


class InMemoryGraphRepository:
    """Graph repository fake with explicit behavior, not driver-call mocks."""

    def __init__(self, semantic_anchors: list[str] | None = None) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.semantic_anchors = list(semantic_anchors or [])
        self.close_calls = 0

    def add_node(self, node: Node) -> None:
        existing = self.nodes.get(node.id)
        if existing is not None:
            merged = dict(existing.properties)
            merged.update(node.properties)
            node = node.model_copy(
                update={
                    "properties": merged,
                    "embedding": node.embedding or existing.embedding,
                }
            )
        self.nodes[node.id] = node

    def get_node(self, node_id: str) -> Node | None:
        return self.nodes.get(node_id)

    def update_node(self, node: Node) -> Node | None:
        if node.id not in self.nodes:
            return None
        self.add_node(node)
        return self.nodes[node.id]

    def add_edge(self, edge: Edge) -> None:
        for existing in self.edges:
            if (existing.source, existing.target, existing.type) == (
                edge.source,
                edge.target,
                edge.type,
            ):
                existing.properties.update(edge.properties)
                return
        self.edges.append(edge)

    def get_edges(self, node_id: str) -> list[Edge]:
        return [edge for edge in self.edges if edge.source == node_id or edge.target == node_id]

    def get_neighbors(self, node_id: str, edge_type: str | None = None) -> list[Node]:
        neighbors: list[Node] = []
        for edge in self.edges:
            if edge.source != node_id:
                continue
            if edge_type is not None and edge.type != edge_type:
                continue
            target = self.get_node(edge.target)
            if target is not None:
                neighbors.append(target)
        return neighbors

    def get_nodes(self, node_type: str | None = None, node_id: str | None = None) -> list[Node]:
        return [
            node
            for node in self.nodes.values()
            if (node_type is None or node.type == node_type)
            and (node_id is None or node.id == node_id)
        ]

    def search_nodes_by_name(
        self, query_string: str, type: str | None = None
    ) -> list[dict[str, Any]]:
        needle = query_string.lower()
        results: list[dict[str, Any]] = []
        for node in self.nodes.values():
            if type is not None and node.type != type:
                continue
            name = str(node.properties.get("name") or node.id)
            if needle in name.lower() or needle in node.id.lower():
                results.append({"id": node.id, "name": name, "type": node.type})
        return results

    def search_semantic_nodes(
        self, embedding: list[float], top_k: int = 5, min_score: float = 0.5
    ) -> list[str]:
        return list(self.semantic_anchors[:top_k])

    def ping(self) -> bool:
        return True

    def delete_edge(self, edge_id: str) -> None:
        self.edges = [edge for edge in self.edges if getattr(edge, "id", None) != edge_id]

    def close(self) -> None:
        self.close_calls += 1
        return None


@dataclass
class _StoredDocument:
    document: str
    metadata: dict[str, Any]


class InMemoryVectorStore:
    """Vector store fake with deterministic scoring supplied by the test."""

    def __init__(self, results: list[dict[str, Any]] | None = None) -> None:
        self.documents: dict[str, _StoredDocument] = {}
        self.results = list(results or [])
        self.queries: list[str] = []

    def add_documents(
        self, documents: list[str], metadatas: list[dict[str, Any]], ids: list[str]
    ) -> None:
        for doc_id, document, metadata in zip(ids, documents, metadatas, strict=True):
            self.documents[doc_id] = _StoredDocument(document=document, metadata=metadata)

    def query(self, query_text: str, n_results: int = 5) -> list[dict[str, Any]]:
        self.queries.append(query_text)
        return list(self.results[:n_results])

    def delete(self, ids: list[str]) -> None:
        for doc_id in ids:
            self.documents.pop(doc_id, None)

    def health(self) -> bool:
        return True


class FakeEmbeddingProvider:
    """Embedding provider fake returning fixed-dimension deterministic vectors."""

    def __init__(self, dimension: int = 384, model: str = "fake-embedder", version: str = "1"):
        self.dimension = dimension
        self.model = model
        self.version = version
        self.calls: list[str] = []

    def available(self) -> bool:
        return True

    def loaded(self) -> bool:
        return True

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        seed = sum(ord(char) for char in text) % 97
        return [((seed + index) % 100) / 100.0 for index in range(self.dimension)]

    def embed_node(self, node_type: str, properties: dict[str, Any]) -> list[float]:
        return self.embed(f"{node_type}:{sorted(properties.items())}")


class ScriptedAIGateway:
    """Application/AI gateway fake. Queue a BaseException to simulate failure."""

    def __init__(self, responses: list[Any] | None = None, available: bool = True) -> None:
        self._responses = list(responses or [])
        self._index = 0
        self._available = available
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def is_available(self) -> bool:
        return self._available

    async def aclose(self) -> None:
        self.closed = True

    def queue(self, item: Any) -> None:
        self._responses.append(item)

    async def generate_structured(
        self, *, messages: list[dict[str, str]], response_format: type, model: str | None = None
    ) -> StructuredResponse:
        self.calls.append(
            {"messages": messages, "response_format": response_format, "model": model}
        )
        if self._index >= len(self._responses):
            raise AssertionError("ScriptedAIGateway has no response queued")
        item = self._responses[self._index]
        self._index += 1
        if isinstance(item, BaseException):
            raise item
        return StructuredResponse(parsed=item, model=model or "fake")

    @property
    def remaining(self) -> int:
        return len(self._responses) - self._index


class _FakeMessage:
    def __init__(self, parsed: Any) -> None:
        self.parsed = parsed


class _FakeChoice:
    def __init__(self, parsed: Any) -> None:
        self.message = _FakeMessage(parsed)


class _FakeResponse:
    def __init__(self, parsed: Any) -> None:
        self.choices = [_FakeChoice(parsed)]


class _ScriptedCompletions:
    def __init__(self, owner: ScriptedAIClient) -> None:
        self._owner = owner

    async def parse(self, **kwargs: Any) -> _FakeResponse:
        self._owner.calls.append(kwargs)
        item = self._owner._next()
        if isinstance(item, BaseException):
            raise item
        return _FakeResponse(item)


class ScriptedAIClient:
    """Scripted SDK-shaped client for exercising adapter internals."""

    def __init__(self, responses: list[Any] | None = None) -> None:
        self._responses = list(responses or [])
        self._index = 0
        self.calls: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(chat=SimpleNamespace(completions=_ScriptedCompletions(self)))

    def _next(self) -> Any:
        if self._index >= len(self._responses):
            raise AssertionError("ScriptedAIClient has no response queued")
        item = self._responses[self._index]
        self._index += 1
        return item

    @property
    def remaining(self) -> int:
        return len(self._responses) - self._index


@dataclass
class FakeMigrationChecker:
    """Fake migration/version checker for migration logic tests."""

    expected: set[str] = field(default_factory=set)
    applied: set[str] = field(default_factory=set)

    def pending(self) -> set[str]:
        return self.expected - self.applied

    def is_current(self) -> bool:
        return not self.pending()


class FakeMigrationVerifier:
    def __init__(self, current: bool = True, detail: str = "") -> None:
        self._status = MigrationStatus(current=current, detail=detail)
        self.calls = 0

    def status(self) -> MigrationStatus:
        self.calls += 1
        return self._status


class FakeDatabase:
    def __init__(self, ready: bool = True, current: bool = True, detail: str = "") -> None:
        self._ready = ready
        self._status = MigrationStatus(current=current, detail=detail)
        self.closed = False

    async def ready(self) -> bool:
        return self._ready

    async def migration_status(self) -> MigrationStatus:
        return self._status

    async def aclose(self) -> None:
        self.closed = True
