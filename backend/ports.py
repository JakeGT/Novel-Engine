"""Application/domain ports.

Services depend on these protocols rather than concrete SDKs, drivers, or
process-global singletons. Adapters in ``backend.adapters`` implement them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from backend.graph.schema import Edge, Node


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class IdGenerator(Protocol):
    def new_id(self, kind: str | None = None) -> str: ...


@runtime_checkable
class GraphRepository(Protocol):
    def add_node(self, node: Node) -> None: ...

    def get_node(self, node_id: str) -> Node | None: ...

    def update_node(self, node: Node) -> Node | None: ...

    def add_edge(self, edge: Edge) -> None: ...

    def get_edges(self, node_id: str) -> list[Edge]: ...

    def get_nodes(self, node_type: str | None = None, node_id: str | None = None) -> list[Node]: ...

    def search_nodes_by_name(self, query: str, type: str | None = None) -> list[dict[str, Any]]: ...

    def search_semantic_nodes(
        self, embedding: Sequence[float], top_k: int = 5, min_score: float = 0.5
    ) -> list[str]: ...

    def traverse(self, anchor_ids: Sequence[str], policy: Any) -> list[dict[str, Any]]: ...

    def ping(self) -> bool: ...

    def close(self) -> None: ...


@runtime_checkable
class VectorStorePort(Protocol):
    def add_documents(
        self, documents: list[str], metadatas: list[dict[str, Any]], ids: list[str]
    ) -> None: ...

    def query(self, query_text: str, n_results: int = 5) -> list[dict[str, Any]]: ...

    def delete(self, ids: Sequence[str]) -> None: ...

    def health(self) -> bool: ...


@runtime_checkable
class EmbeddingPort(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def version(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def available(self) -> bool: ...

    def loaded(self) -> bool: ...

    def embed(self, text: str) -> list[float] | None: ...

    def embed_node(self, node_type: str, properties: dict[str, Any]) -> list[float] | None: ...


@runtime_checkable
class SchemaRegistryPort(Protocol):
    def is_valid_node_type(self, node_type: str) -> bool: ...

    def is_valid_edge_type(self, edge_type: str) -> bool: ...

    def add_custom_node_type(self, node_type: str) -> None: ...

    def add_custom_edge_type(self, edge_type: str) -> None: ...

    def get_all_node_types(self) -> list[str]: ...

    def get_all_edge_types(self) -> list[str]: ...

    @property
    def custom_node_types(self) -> set[str]: ...

    @property
    def custom_edge_types(self) -> set[str]: ...


@dataclass
class AIUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class StructuredResponse:
    parsed: Any
    model: str = ""
    usage: AIUsage = field(default_factory=AIUsage)


class AIError(RuntimeError):
    """Base class for normalized AI gateway failures."""


class AIProviderError(AIError):
    """Provider refused, timed out, or returned a transport/server error."""


class AIInvalidOutputError(AIError):
    """Provider returned output that could not be parsed into the requested schema."""


@runtime_checkable
class AIGateway(Protocol):
    def is_available(self) -> bool: ...

    async def generate_structured(
        self,
        *,
        messages: list[dict[str, str]],
        response_format: type,
        model: str | None = None,
    ) -> StructuredResponse: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class ArtifactStore(Protocol):
    def save(self, key: str, payload: str) -> None: ...

    def load(self, key: str) -> str | None: ...


@dataclass
class MigrationStatus:
    current: bool
    detail: str = ""


@runtime_checkable
class MigrationVerifier(Protocol):
    def status(self) -> MigrationStatus: ...


@runtime_checkable
class DatabasePort(Protocol):
    async def ready(self) -> bool: ...

    async def migration_status(self) -> MigrationStatus: ...

    async def aclose(self) -> None: ...
