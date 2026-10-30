"""Deterministic in-memory test doubles for Novel Engine application tests.

These fakes model application/domain ports in plain terms. They intentionally do
not mock Neo4j driver calls, OpenAI SDK internals, or filesystem layouts.
"""

from __future__ import annotations

import copy
import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Literal

from backend.domain.archive import ArchiveImport, ImportStatus
from backend.domain.branching import working_branch_id, working_in
from backend.domain.canon.assertions import Belief, Fact, FactStatus, FactVisibility
from backend.domain.canon.entities import CanonEntity, EntityLifecycle
from backend.domain.canon.events import Event
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.naming import normalize_name
from backend.domain.canon.plans import apply_plan_update, plan_item_from_create
from backend.domain.canon.registry import CORE_ENTITY_TYPES
from backend.domain.canon.revisions import CanonRevision, OriginKind, Provenance
from backend.domain.credentials import (
    Credential,
    CredentialNotFound,
    CredentialProvider,
    CredentialStatus,
    CredentialUnavailable,
)
from backend.domain.history import CanonTag, CanonTagHistoryEntry
from backend.domain.identity import (
    IdentityClaims,
    InvalidIdentityToken,
    LoginAttempt,
    Role,
    Session,
    User,
    utcnow,
)
from backend.domain.journal import (
    AttemptStatus,
    Branch,
    BranchReservation,
    CommitAttempt,
    CommitEnvelope,
    HeadConflict,
    JournalError,
    MutationNotFound,
    MutationOperation,
    MutationSet,
    MutationStatus,
    MutationTransition,
    OperationDecision,
    OperationRevision,
    OutboxMessage,
    ReservationStatus,
    ReviewDecision,
    RevisionConflict,
)
from backend.domain.media import (
    AssetLink,
    LinkRole,
    MediaAsset,
    MediaAssetVersion,
    MediaLifecycleAction,
    MediaLifecycleEvent,
    PictureUpload,
    UploadState,
)
from backend.domain.merge import expand_merges
from backend.domain.objects import ObjectStat
from backend.domain.operations import (
    AmendEventOp,
    AmendFactOp,
    AssertBeliefOp,
    AssertFactOp,
    CloseBeliefOp,
    CloseFactOp,
    CreateEntityOp,
    CreateEventOp,
    CreatePlanItemOp,
    LinkAssetOp,
    RestoreEntityOp,
    RetireAssetLinkOp,
    RetireEntityOp,
    RetireEventOp,
    RetirePlanItemOp,
    UpdateEntityOp,
    UpdatePlanItemOp,
    canonicalize_operations,
    operations_checksum,
    parse_operation,
    resolve_temporary_ids,
)
from backend.domain.projection import EvidenceRecord, ProjectionStatus, RecordVersion
from backend.domain.providers import request_model
from backend.domain.review import (
    MutationIntent,
    effective_operations,
    ensure_transition,
    is_editable,
    review_gate,
)
from backend.domain.schemas import SchemaVersion, SchemaVersionStatus
from backend.domain.tenancy import (
    AuditEvent,
    Invitation,
    InvitationConflict,
    InvitationNotAcceptable,
    InvitationNotFound,
    Membership,
    MembershipConflict,
    Permission,
    Project,
    ProjectSlugConflict,
    RoleTransitionError,
    Workspace,
    WorkspaceSlugConflict,
    ensure_member_removable,
    ensure_ownership_transfer,
    ensure_role_change_allowed,
    role_allows,
)
from backend.domain.usage import UsageRecord, UsageTotals
from backend.domain.validation import (
    AuthoritativeValidator,
    CanonValidationError,
    DomainValidationReport,
    ValidationContext,
    ValidationFinding,
    ValidationSeverity,
)
from backend.domain.worldtime import Unknown
from backend.graph.schema import Edge, Node
from backend.ports import MigrationStatus, ProviderCapabilities, StructuredResponse
from backend.services.schema_compiler import compile_definition


def _stored_time(value: Any) -> Any:
    """As the repository stores a time: an unknown one with no words is no time."""
    if isinstance(value, Unknown) and not value.label:
        return None
    return value


_MEMORY_DEFAULT_VALIDATOR = AuthoritativeValidator()


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
    """Graph repository fake with explicit (workspace, project, id) identity."""

    def __init__(self, semantic_anchors: list[str] | None = None) -> None:
        self.nodes: dict[tuple[str, str, str], Node] = {}
        self.edges: list[Edge] = []
        self.semantic_anchors = list(semantic_anchors or [])
        self.close_calls = 0

    @staticmethod
    def _key(
        workspace_id: str | None, project_id: str | None, node_id: str
    ) -> tuple[str, str, str]:
        return (workspace_id or "", project_id or "", node_id)

    def add_node(self, node: Node) -> None:
        key = self._key(node.workspace_id, node.project_id, node.id)
        existing = self.nodes.get(key)
        if existing is not None:
            merged = dict(existing.properties)
            merged.update(node.properties)
            node = node.model_copy(
                update={
                    "properties": merged,
                    "embedding": node.embedding or existing.embedding,
                }
            )
        self.nodes[key] = node

    def get_node(self, node_id: str, *, workspace_id: str, project_id: str) -> Node | None:
        return self.nodes.get(self._key(workspace_id, project_id, node_id))

    def update_node(self, node: Node) -> Node | None:
        key = self._key(node.workspace_id, node.project_id, node.id)
        if key not in self.nodes:
            return None
        self.add_node(node)
        return self.nodes[key]

    def add_edge(self, edge: Edge) -> None:
        source = self.get_node(
            edge.source, workspace_id=edge.workspace_id or "", project_id=edge.project_id or ""
        )
        target = self.get_node(
            edge.target, workspace_id=edge.workspace_id or "", project_id=edge.project_id or ""
        )
        if source is None or target is None:
            return
        for existing in self.edges:
            if (
                existing.workspace_id,
                existing.project_id,
                existing.source,
                existing.target,
                existing.type,
            ) == (edge.workspace_id, edge.project_id, edge.source, edge.target, edge.type):
                existing.properties.update(edge.properties)
                return
        self.edges.append(edge)

    def get_edges(self, node_id: str, *, workspace_id: str, project_id: str) -> list[Edge]:
        return [
            edge
            for edge in self.edges
            if (edge.source == node_id or edge.target == node_id)
            and edge.workspace_id == workspace_id
            and edge.project_id == project_id
        ]

    def get_neighbors(
        self,
        node_id: str,
        *,
        workspace_id: str,
        project_id: str,
        edge_type: str | None = None,
    ) -> list[Node]:
        neighbors: list[Node] = []
        for edge in self.edges:
            if (
                edge.source != node_id
                or edge.workspace_id != workspace_id
                or edge.project_id != project_id
            ):
                continue
            if edge_type is not None and edge.type != edge_type:
                continue
            target = self.get_node(edge.target, workspace_id=workspace_id, project_id=project_id)
            if target is not None:
                neighbors.append(target)
        return neighbors

    def get_nodes(
        self,
        *,
        workspace_id: str,
        project_id: str,
        node_type: str | None = None,
        node_id: str | None = None,
    ) -> list[Node]:
        return [
            node
            for (ws, prj, _), node in self.nodes.items()
            if ws == workspace_id
            and prj == project_id
            and (node_type is None or node.type == node_type)
            and (node_id is None or node.id == node_id)
        ]

    def search_nodes_by_name(
        self,
        query_string: str,
        *,
        workspace_id: str,
        project_id: str,
        type: str | None = None,
    ) -> list[dict[str, Any]]:
        needle = query_string.lower()
        results: list[dict[str, Any]] = []
        for (ws, prj, _), node in self.nodes.items():
            if ws != workspace_id or prj != project_id:
                continue
            if type is not None and node.type != type:
                continue
            name = str(node.properties.get("name") or node.id)
            if needle in name.lower() or needle in node.id.lower():
                results.append({"id": node.id, "name": name, "type": node.type})
        return results

    def search_semantic_nodes(
        self,
        embedding: list[float],
        *,
        workspace_id: str,
        project_id: str,
        top_k: int = 5,
        min_score: float = 0.5,
    ) -> list[str]:
        any_id = {key[2] for key in self.nodes}
        scoped = [
            node_id
            for node_id in self.semantic_anchors
            if self._key(workspace_id, project_id, node_id) in self.nodes or node_id not in any_id
        ]
        return scoped[:top_k]

    def traverse(
        self,
        anchor_ids: list[str],
        policy: Any,
        *,
        workspace_id: str,
        project_id: str,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for node_id in anchor_ids:
            node = self.get_node(node_id, workspace_id=workspace_id, project_id=project_id)
            if node is not None:
                results.append(
                    {
                        "id": node.id,
                        "type": node.type,
                        "depth": 0,
                        "relevance_score": 100.0,
                        "properties": dict(node.properties),
                    }
                )
        return results

    def ping(self) -> bool:
        return True

    def delete_node(self, node_id: str, *, workspace_id: str, project_id: str) -> bool:
        if self._key(workspace_id, project_id, node_id) not in self.nodes:
            return False
        del self.nodes[self._key(workspace_id, project_id, node_id)]
        self.edges = [
            edge
            for edge in self.edges
            if not (
                (edge.source == node_id or edge.target == node_id)
                and edge.workspace_id == workspace_id
                and edge.project_id == project_id
            )
        ]
        return True

    def delete_edge(self, edge_id: str) -> None:
        self.edges = [edge for edge in self.edges if getattr(edge, "id", None) != edge_id]

    def close(self) -> None:
        self.close_calls += 1
        return None

    def delete_workspace_nodes(self, workspace_id: str) -> int:
        keys = [key for key in self.nodes if key[0] == workspace_id]
        for key in keys:
            del self.nodes[key]
        self.edges = [e for e in self.edges if e.workspace_id != workspace_id]
        return len(keys)

    def delete_project_nodes(self, workspace_id: str, project_id: str) -> int:
        keys = [key for key in self.nodes if key[0] == workspace_id and key[1] == project_id]
        for key in keys:
            del self.nodes[key]
        self.edges = [
            e
            for e in self.edges
            if not (e.workspace_id == workspace_id and e.project_id == project_id)
        ]
        return len(keys)


@dataclass
class _StoredDocument:
    document: str
    metadata: dict[str, Any]
    workspace_id: str = ""
    project_id: str = ""


class InMemoryVectorStore:
    """Vector store fake with deterministic scoring supplied by the test."""

    def __init__(self, results: list[dict[str, Any]] | None = None) -> None:
        self.documents: dict[str, _StoredDocument] = {}
        self.results = list(results or [])
        self.queries: list[str] = []
        self.filters: list[dict[str, Any]] = []

    def add_documents(
        self,
        documents: list[str],
        metadatas: list[dict[str, Any]],
        ids: list[str],
        *,
        workspace_id: str,
        project_id: str,
    ) -> None:
        for doc_id, document, metadata in zip(ids, documents, metadatas, strict=True):
            self.documents[doc_id] = _StoredDocument(
                document=document,
                metadata=metadata,
                workspace_id=workspace_id,
                project_id=project_id,
            )

    def query(
        self,
        query_text: str,
        *,
        workspace_id: str,
        project_id: str,
        n_results: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.queries.append(query_text)
        self.filters.append(dict(where or {}))
        scoped = [
            result
            for result in self.results
            if result.get("id") not in self.documents
            or (
                self.documents[result["id"]].workspace_id == workspace_id
                and self.documents[result["id"]].project_id == project_id
            )
        ]
        if where:
            # Metadata filters, as the real store applies them before ranking (CTX-004).
            scoped = [
                result
                for result in scoped
                if result.get("id") in self.documents
                and all(
                    str(self.documents[result["id"]].metadata.get(key)) in {str(v) for v in values}
                    for key, values in where.items()
                )
            ]
        return scoped[:n_results]

    def delete(self, ids: list[str], *, workspace_id: str, project_id: str) -> None:
        for doc_id in ids:
            stored = self.documents.get(doc_id)
            if (
                stored is not None
                and stored.workspace_id == workspace_id
                and stored.project_id == project_id
            ):
                self.documents.pop(doc_id, None)

    def health(self) -> bool:
        return True

    def delete_workspace(self, *, workspace_id: str) -> int:
        ids = [doc_id for doc_id, doc in self.documents.items() if doc.workspace_id == workspace_id]
        for doc_id in ids:
            del self.documents[doc_id]
        return len(ids)

    def delete_project(self, *, workspace_id: str, project_id: str) -> int:
        ids = [
            doc_id
            for doc_id, doc in self.documents.items()
            if doc.workspace_id == workspace_id and doc.project_id == project_id
        ]
        for doc_id in ids:
            del self.documents[doc_id]
        return len(ids)


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


class DisabledEmbeddingProvider:
    """Embedding provider fake representing a disabled/unavailable capability."""

    def __init__(self, dimension: int = 384, model: str = "disabled", version: str = "0"):
        self.dimension = dimension
        self.model = model
        self.version = version

    def available(self) -> bool:
        return False

    def loaded(self) -> bool:
        return False

    def embed(self, text: str) -> None:
        return None

    def embed_node(self, node_type: str, properties: dict[str, Any]) -> None:
        return None


class ScriptedAIGateway:
    """Application/AI gateway fake. Queue a BaseException to simulate failure."""

    def __init__(
        self,
        responses: list[Any] | None = None,
        available: bool = True,
        *,
        sees_images: bool = False,
        max_images: int = 4,
        max_image_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        self._responses = list(responses or [])
        self._index = 0
        self._available = available
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self.models: Any = ["model-a", "model-b"]
        self.model_list_calls: list[dict[str, Any]] = []
        # The key check's answer, and the calls it has seen (key and address only).
        self.key_check_result: Literal["ok", "refused", "unknown"] = "ok"
        self.key_check_calls: list[dict[str, Any]] = []
        # CTX-013: a scripted route is text only unless a test says it can see pictures.
        self.sees_images = sees_images
        self.max_images = max_images
        self.max_image_bytes = max_image_bytes
        # Pictures the route refused before anything was "sent" (nothing was spent).
        self.refused: list[dict[str, Any]] = []
        # SCN-002: prose is handed on in pieces of this many words when asked to stream;
        # False answers whole, as a computer through the connector does.
        self.streams = True
        self.stream_words = 3
        # Whether a streamed answer says what it used (a provider may not).
        self.stream_usage = True
        # Raised half way through the next streamed answer (a dropped connection, an
        # error the provider sends part way), then forgotten.
        self.stream_error: BaseException | None = None

    def is_available(self) -> bool:
        return self._available

    def capabilities(
        self, *, base_url: str | None = None, model: str | None = None
    ) -> ProviderCapabilities:
        if not self.sees_images:
            return ProviderCapabilities(
                provider="scripted", model="scripted", supports_streaming=self.streams
            )
        return ProviderCapabilities(
            provider="scripted",
            model="scripted",
            input_modalities=frozenset({"text", "image"}),
            max_images=self.max_images,
            max_image_bytes=self.max_image_bytes,
            image_types=frozenset({"image/png", "image/jpeg", "image/webp"}),
            supports_streaming=self.streams,
        )

    async def aclose(self) -> None:
        self.closed = True

    def queue(self, item: Any) -> None:
        self._responses.append(item)

    async def generate_structured(
        self,
        *,
        messages: list[dict[str, str]],
        response_format: type,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        images: Any = (),
    ) -> StructuredResponse:
        from backend.adapters.ai import check_images

        try:
            # The real gateway's own check: a picture the route cannot take is refused
            # before anything is sent.
            check_images(list(images), self.capabilities(base_url=base_url))
        except Exception:
            self.refused.append({"images": list(images), "response_format": response_format})
            raise
        self.calls.append(
            {
                "messages": messages,
                "response_format": response_format,
                "model": model,
                "api_key": api_key,
                "base_url": base_url,
                # What the real gateway would use: the workspace's choice, when there is one.
                "route_model": request_model(),
                "images": list(images),
            }
        )
        if self._index >= len(self._responses):
            raise AssertionError("ScriptedAIGateway has no response queued")
        item = self._responses[self._index]
        self._index += 1
        if isinstance(item, BaseException):
            raise item
        # A queued response is returned as it is, so a test can say what usage it reports.
        if isinstance(item, StructuredResponse):
            return item
        return StructuredResponse(parsed=item, model=model or "fake")

    async def generate_text(
        self,
        *,
        messages: list[dict[str, str]],
        max_tokens: int,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        images: Any = (),
        on_text: Any = None,
    ) -> Any:
        from backend.adapters.ai import check_images
        from backend.ports import AIUsage, TextResponse

        try:
            check_images(list(images), self.capabilities(base_url=base_url))
        except Exception:
            self.refused.append({"images": list(images), "text": True})
            raise
        self.calls.append(
            {
                "messages": messages,
                "max_tokens": max_tokens,
                "model": model,
                "api_key": api_key,
                "base_url": base_url,
                "route_model": request_model(),
                "text": True,
                "images": list(images),
                "streamed": on_text is not None and self.streams,
            }
        )
        if self._index >= len(self._responses):
            raise AssertionError("ScriptedAIGateway has no response queued")
        item = self._responses[self._index]
        self._index += 1
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, TextResponse):
            response = item
        else:
            words = len(str(item).split())
            response = TextResponse(
                text=str(item),
                model=model or "fake",
                usage=AIUsage(prompt_tokens=100, completion_tokens=words),
            )
        if on_text is None or not self.streams:
            return response
        # The answer in pieces, spaces kept, as a provider streams it.
        pieces = response.text.split(" ")
        size = max(1, self.stream_words)
        for start in range(0, len(pieces), size):
            if self.stream_error is not None and start and start >= len(pieces) // 2:
                failure, self.stream_error = self.stream_error, None
                raise failure
            chunk = " ".join(pieces[start : start + size])
            await on_text(chunk if start + size >= len(pieces) else chunk + " ")
        return TextResponse(
            text=response.text,
            model=response.model,
            usage=response.usage if self.stream_usage else AIUsage(),
            truncated=response.truncated,
            usage_reported=self.stream_usage,
            streamed=True,
        )

    async def list_models(self, *, api_key: str, base_url: str) -> list[str]:
        self.model_list_calls.append({"api_key": api_key, "base_url": base_url})
        if isinstance(self.models, BaseException):
            raise self.models
        return list(self.models)

    async def check_key(
        self, *, api_key: str, base_url: str
    ) -> Literal["ok", "refused", "unknown"]:
        self.key_check_calls.append({"api_key": api_key, "base_url": base_url})
        return self.key_check_result

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


DEFAULT_TEST_ISSUER = "https://idp.test/application/o/novel-engine/"


class FakeIdentityProvider:
    """Deterministic OIDC provider fake: no network, no vendor SDK."""

    def __init__(
        self,
        *,
        issuer: str = DEFAULT_TEST_ISSUER,
        authorized_code: str = "code-123",
        claims: IdentityClaims | None = None,
        available: bool = True,
    ) -> None:
        self.issuer = issuer
        self._authorized_code = authorized_code
        self._claims = claims or IdentityClaims(
            issuer=issuer, subject="subject-1", email="user@example.com", display_name="Test User"
        )
        self._available = available
        self.authorization_calls: list[dict[str, str]] = []
        self.logout_calls: list[dict[str, Any]] = []
        self.exchange_calls: list[dict[str, str]] = []
        self.closed = False

    def is_available(self) -> bool:
        return self._available

    async def authorization_url(
        self, *, state: str, nonce: str, code_challenge: str, redirect_uri: str
    ) -> str:
        self.authorization_calls.append(
            {
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "redirect_uri": redirect_uri,
            }
        )
        return (
            f"https://idp.test/authorize?state={state}&nonce={nonce}"
            f"&code_challenge={code_challenge}&code_challenge_method=S256"
            f"&redirect_uri={redirect_uri}"
        )

    async def exchange_code(
        self, *, code: str, code_verifier: str, redirect_uri: str, expected_nonce: str
    ) -> IdentityClaims:
        self.exchange_calls.append(
            {
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
                "expected_nonce": expected_nonce,
            }
        )
        if code != self._authorized_code:
            raise InvalidIdentityToken("authorization code rejected")
        # A real provider returns the signed token alongside the validated claims.
        return self._claims.model_copy(update={"id_token": f"id-token-for-{code}"})

    async def logout_url(
        self,
        *,
        post_logout_redirect_uri: str,
        id_token_hint: str | None = None,
        state: str | None = None,
    ) -> str:
        self.logout_calls.append({"id_token_hint": id_token_hint})
        if not id_token_hint:
            return "https://idp.test/end-session"
        return (
            f"https://idp.test/end-session?post_logout_redirect_uri={post_logout_redirect_uri}"
            f"&id_token_hint={id_token_hint}"
        )

    async def aclose(self) -> None:
        self.closed = True


class InMemoryIdentityStore:
    def __init__(self) -> None:
        self.users: dict[str, User] = {}
        self.identities: dict[tuple[str, str], str] = {}

    async def get_or_create_user(self, claims: IdentityClaims) -> User:
        key = (claims.issuer, claims.subject)
        if key in self.identities:
            user = self.users[self.identities[key]]
            user.email = claims.email
            user.display_name = claims.display_name
            return user
        user = User(
            id=f"user_{len(self.users) + 1:04d}",
            email=claims.email,
            display_name=claims.display_name,
        )
        self.users[user.id] = user
        self.identities[key] = user.id
        return user

    async def get_user(self, user_id: str) -> User | None:
        return self.users.get(user_id)

    async def list_users(self) -> list[User]:
        return list(self.users.values())

    async def get_user_for_identity(self, issuer: str, subject: str) -> User | None:
        user_id = self.identities.get((issuer, subject))
        return self.users.get(user_id) if user_id else None

    async def unlink_identity(self, issuer: str, subject: str) -> bool:
        return self.identities.pop((issuer, subject), None) is not None


class InMemorySessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    async def create(
        self,
        *,
        user_id: str,
        ttl_seconds: int,
        csrf_token: str,
        rotated_from: str | None = None,
        id_token: str | None = None,
    ) -> Session:
        from datetime import timedelta

        raw = f"sess_{len(self.sessions) + 1:04d}"
        now = utcnow()
        session = Session(
            id=raw,
            user_id=user_id,
            csrf_token=csrf_token,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            rotated_from=rotated_from,
            id_token=id_token,
        )
        self.sessions[raw] = session
        return session

    async def get(self, session_id: str) -> Session | None:
        session = self.sessions.get(session_id)
        if session is None:
            return None
        if session.expires_at <= utcnow():
            del self.sessions[session_id]
            return None
        return session

    async def has_successor(self, session_id: str) -> bool:
        from backend.core.security import hash_token

        wanted = hash_token(session_id)
        return any(session.rotated_from == wanted for session in self.sessions.values())

    async def expire_within(self, session_id: str, seconds: int) -> None:
        from datetime import timedelta

        session = self.sessions.get(session_id)
        deadline = utcnow() + timedelta(seconds=seconds)
        if session is not None and session.expires_at > deadline:
            self.sessions[session_id] = session.model_copy(update={"expires_at": deadline})

    async def revoke(self, session_id: str) -> bool:
        return self.sessions.pop(session_id, None) is not None

    async def revoke_all_for_user(self, user_id: str) -> int:
        ids = [key for key, value in self.sessions.items() if value.user_id == user_id]
        for key in ids:
            del self.sessions[key]
        return len(ids)


class InMemoryLoginAttemptStore:
    def __init__(self) -> None:
        self.attempts: dict[str, LoginAttempt] = {}

    async def create(
        self,
        *,
        state: str,
        nonce: str,
        code_verifier: str,
        redirect_uri: str,
        ttl_seconds: int,
    ) -> LoginAttempt:
        from datetime import timedelta

        now = utcnow()
        attempt = LoginAttempt(
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self.attempts[state] = attempt
        return attempt

    async def consume(self, state: str) -> LoginAttempt | None:
        attempt = self.attempts.pop(state, None)
        if attempt is None:
            return None
        if attempt.expires_at <= utcnow():
            return None
        return attempt


class InMemoryTenancyStore:
    """In-memory tenancy store implementing ``TenancyStorePort`` with the same invariants."""

    def __init__(self) -> None:
        self.workspaces: dict[str, Workspace] = {}
        self.memberships: dict[tuple[str, str], Membership] = {}
        self.projects: dict[str, Project] = {}
        self.invitations: dict[str, Invitation] = {}
        self.audit: list[AuditEvent] = []
        self._counter = 0

    def _next(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self._counter:04d}"

    async def create_workspace(self, *, slug: str, name: str) -> Workspace:
        if any(w.slug == slug for w in self.workspaces.values()):
            raise WorkspaceSlugConflict(f"workspace slug {slug!r} already exists")
        workspace = Workspace(id=self._next("ws"), slug=slug, name=name)
        self.workspaces[workspace.id] = workspace
        return workspace

    async def get_workspace(self, workspace_id: str) -> Workspace | None:
        return self.workspaces.get(workspace_id)

    async def get_workspace_by_slug(self, slug: str) -> Workspace | None:
        return next((w for w in self.workspaces.values() if w.slug == slug), None)

    async def list_workspaces_for_user(self, user_id: str) -> list[Workspace]:
        ids = [ws_id for (ws_id, uid) in self.memberships if uid == user_id]
        return [self.workspaces[ws_id] for ws_id in sorted(ids) if ws_id in self.workspaces]

    async def delete_workspace(self, workspace_id: str) -> bool:
        if workspace_id not in self.workspaces:
            return False
        del self.workspaces[workspace_id]
        self.memberships = {
            key: value for key, value in self.memberships.items() if key[0] != workspace_id
        }
        self.projects = {
            pid: project
            for pid, project in self.projects.items()
            if project.workspace_id != workspace_id
        }
        self.invitations = {
            iid: invitation
            for iid, invitation in self.invitations.items()
            if invitation.workspace_id != workspace_id
        }
        return True

    async def delete_project(self, workspace_id: str, project_id: str) -> bool:
        project = self.projects.get(project_id)
        if project is None or project.workspace_id != workspace_id:
            return False
        del self.projects[project_id]
        return True

    async def add_membership(self, *, workspace_id: str, user_id: str, role: Role) -> Membership:
        if (workspace_id, user_id) in self.memberships:
            raise MembershipConflict("user is already a member of the workspace")
        membership = Membership(workspace_id=workspace_id, user_id=user_id, role=role)
        self.memberships[(workspace_id, user_id)] = membership
        return membership

    async def get_membership(self, workspace_id: str, user_id: str) -> Membership | None:
        return self.memberships.get((workspace_id, user_id))

    async def list_memberships(self, workspace_id: str) -> list[Membership]:
        return sorted(
            (m for (ws_id, _uid), m in self.memberships.items() if ws_id == workspace_id),
            key=lambda m: m.user_id,
        )

    async def set_role(self, *, workspace_id: str, user_id: str, role: Role) -> Membership:
        target = self.memberships.get((workspace_id, user_id))
        if target is None:
            raise MembershipConflict("user is not a member of the workspace")
        ensure_role_change_allowed(await self.list_memberships(workspace_id), user_id, role)
        updated = target.model_copy(update={"role": role, "updated_at": utcnow()})
        self.memberships[(workspace_id, user_id)] = updated
        return updated

    async def remove_membership(self, *, workspace_id: str, user_id: str) -> bool:
        if (workspace_id, user_id) not in self.memberships:
            return False
        ensure_member_removable(await self.list_memberships(workspace_id), user_id)
        del self.memberships[(workspace_id, user_id)]
        return True

    async def transfer_ownership(
        self, *, workspace_id: str, from_user_id: str, to_user_id: str
    ) -> None:
        ensure_ownership_transfer(
            await self.list_memberships(workspace_id), from_user_id, to_user_id
        )
        for user_id, role in ((from_user_id, Role.EDITOR), (to_user_id, Role.OWNER)):
            current = self.memberships[(workspace_id, user_id)]
            self.memberships[(workspace_id, user_id)] = current.model_copy(
                update={"role": role, "updated_at": utcnow()}
            )

    async def create_project(self, *, workspace_id: str, slug: str, name: str) -> Project:
        if any(p.workspace_id == workspace_id and p.slug == slug for p in self.projects.values()):
            raise ProjectSlugConflict("project slug already exists in the workspace")
        project = Project(id=self._next("prj"), workspace_id=workspace_id, slug=slug, name=name)
        self.projects[project.id] = project
        return project

    async def get_project(self, project_id: str) -> Project | None:
        return self.projects.get(project_id)

    async def list_projects(self, workspace_id: str) -> list[Project]:
        return sorted(
            (p for p in self.projects.values() if p.workspace_id == workspace_id),
            key=lambda p: p.id,
        )

    async def create_invitation(
        self,
        *,
        workspace_id: str,
        role: Role,
        token_hash: str,
        created_by_user_id: str,
        ttl_seconds: int,
        email: str | None = None,
    ) -> Invitation:
        if any(i.token_hash == token_hash for i in self.invitations.values()):
            raise InvitationConflict("invitation token already exists")
        from datetime import timedelta

        invitation = Invitation(
            id=self._next("inv"),
            workspace_id=workspace_id,
            role=role,
            token_hash=token_hash,
            created_by_user_id=created_by_user_id,
            email=email,
            expires_at=utcnow() + timedelta(seconds=ttl_seconds),
        )
        self.invitations[invitation.id] = invitation
        return invitation

    async def get_invitation_by_token_hash(self, token_hash: str) -> Invitation | None:
        return next((i for i in self.invitations.values() if i.token_hash == token_hash), None)

    async def list_invitations(self, workspace_id: str) -> list[Invitation]:
        return sorted(
            (i for i in self.invitations.values() if i.workspace_id == workspace_id),
            key=lambda i: i.id,
        )

    async def revoke_invitation(self, *, workspace_id: str, invitation_id: str) -> bool:
        invitation = self.invitations.get(invitation_id)
        if invitation is None or invitation.workspace_id != workspace_id:
            return False
        if invitation.accepted_at is not None or invitation.revoked_at is not None:
            return False
        self.invitations[invitation_id] = invitation.model_copy(update={"revoked_at": utcnow()})
        return True

    async def accept_invitation(self, *, token_hash: str, user_id: str) -> Membership:
        invitation = await self.get_invitation_by_token_hash(token_hash)
        if invitation is None:
            raise InvitationNotFound("no invitation matches the supplied token")
        if not invitation.is_acceptable():
            raise InvitationNotAcceptable("invitation is not acceptable")
        inviter = self.memberships.get((invitation.workspace_id, invitation.created_by_user_id))
        if inviter is None or not role_allows(inviter.role, Permission.MEMBER_INVITE):
            raise RoleTransitionError("inviter is no longer authorized to invite")
        if (invitation.workspace_id, user_id) in self.memberships:
            raise MembershipConflict("user is already a member of the workspace")
        membership = Membership(
            workspace_id=invitation.workspace_id, user_id=user_id, role=invitation.role
        )
        self.memberships[(invitation.workspace_id, user_id)] = membership
        self.invitations[invitation.id] = invitation.model_copy(
            update={"accepted_at": utcnow(), "accepted_by_user_id": user_id}
        )
        return membership

    async def record_audit(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        action: str,
        target_type: str,
        target_id: str,
        detail: dict | None = None,
        correlation_id: str = "",
    ) -> AuditEvent:
        event = AuditEvent(
            id=self._next("audit"),
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=dict(detail or {}),
            correlation_id=correlation_id,
        )
        self.audit.append(event)
        return event


class InMemoryRateLimiter:
    """Deterministic sliding-window limiter for tests."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}

    async def exhausted(self, key: str, *, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        return len([hit for hit in self._hits.get(key, []) if now - hit < window_seconds]) >= limit

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        hits = [hit for hit in self._hits.get(key, []) if now - hit < window_seconds]
        if len(hits) >= limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True


class InMemoryCredentialVault:
    """Deterministic credential vault fake; keeps plaintext in memory only."""

    def __init__(self, *, configured: bool = True) -> None:
        from backend.core.crypto import key_fingerprint, key_last4

        self._configured = configured
        self._key_fingerprint = key_fingerprint
        self._key_last4 = key_last4
        self._credentials: dict[str, Credential] = {}
        self._secrets: dict[str, str] = {}
        self._counter = 0

    @property
    def is_configured(self) -> bool:
        return self._configured

    def _next(self) -> str:
        self._counter += 1
        return f"cred_{self._counter:04d}"

    def _require(self) -> None:
        if not self._configured:
            raise CredentialUnavailable("vault not configured")

    async def store(
        self,
        *,
        workspace_id: str,
        owner_user_id: str,
        provider: CredentialProvider,
        label: str,
        plaintext_key: str,
    ) -> Credential:
        self._require()
        now = datetime.now(UTC)
        for credential_id, existing in list(self._credentials.items()):
            if (
                existing.workspace_id == workspace_id
                and existing.provider == provider
                and existing.status == CredentialStatus.ACTIVE
            ):
                self._credentials[credential_id] = existing.model_copy(
                    update={"status": CredentialStatus.REVOKED, "revoked_at": now}
                )
        credential = Credential(
            id=self._next(),
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            provider=provider,
            label=label,
            key_fingerprint=self._key_fingerprint(plaintext_key),
            key_last4=self._key_last4(plaintext_key),
            key_version=1,
        )
        self._credentials[credential.id] = credential
        self._secrets[credential.id] = plaintext_key
        return credential

    async def list_for_workspace(self, workspace_id: str) -> list[Credential]:
        return [c for c in self._credentials.values() if c.workspace_id == workspace_id]

    async def get(self, *, workspace_id: str, credential_id: str) -> Credential | None:
        credential = self._credentials.get(credential_id)
        if credential is None or credential.workspace_id != workspace_id:
            return None
        return credential

    async def rotate(
        self, *, workspace_id: str, credential_id: str, plaintext_key: str
    ) -> Credential:
        credential = await self.get(workspace_id=workspace_id, credential_id=credential_id)
        if credential is None:
            raise CredentialNotFound("Credential not found.")
        now = datetime.now(UTC)
        for other_id, existing in list(self._credentials.items()):
            if (
                other_id != credential_id
                and existing.workspace_id == workspace_id
                and existing.provider == credential.provider
                and existing.status == CredentialStatus.ACTIVE
            ):
                self._credentials[other_id] = existing.model_copy(
                    update={"status": CredentialStatus.REVOKED, "revoked_at": now}
                )
        updated = credential.model_copy(
            update={
                "key_fingerprint": self._key_fingerprint(plaintext_key),
                "key_last4": self._key_last4(plaintext_key),
                "rotated_at": now,
                "status": CredentialStatus.ACTIVE,
                "revoked_at": None,
            }
        )
        self._credentials[credential_id] = updated
        self._secrets[credential_id] = plaintext_key
        return updated

    async def revoke(self, *, workspace_id: str, credential_id: str) -> Credential:
        credential = await self.get(workspace_id=workspace_id, credential_id=credential_id)
        if credential is None:
            raise CredentialNotFound("Credential not found.")
        updated = credential.model_copy(
            update={"status": CredentialStatus.REVOKED, "revoked_at": datetime.now(UTC)}
        )
        self._credentials[credential_id] = updated
        return updated

    async def delete(self, *, workspace_id: str, credential_id: str) -> bool:
        credential = self._credentials.get(credential_id)
        if credential is None or credential.workspace_id != workspace_id:
            return False
        del self._credentials[credential_id]
        self._secrets.pop(credential_id, None)
        return True

    async def resolve_plaintext(
        self, *, workspace_id: str, credential_id: str, actor_user_id: str
    ) -> str:
        credential = await self.get(workspace_id=workspace_id, credential_id=credential_id)
        if credential is None or credential.status != CredentialStatus.ACTIVE:
            raise CredentialNotFound("Active credential not found.")
        return self._secrets[credential_id]

    async def mark_used(self, *, workspace_id: str, credential_id: str) -> None:
        credential = await self.get(workspace_id=workspace_id, credential_id=credential_id)
        if credential is not None:
            self._credentials[credential_id] = credential.model_copy(
                update={"last_used_at": datetime.now(UTC)}
            )

    async def delete_workspace(self, workspace_id: str) -> int:
        ids = [cid for cid, cred in self._credentials.items() if cred.workspace_id == workspace_id]
        for cid in ids:
            self._credentials.pop(cid, None)
            self._secrets.pop(cid, None)
        return len(ids)


class InMemoryProviderUsageStore:
    def __init__(self) -> None:
        self.records: list[UsageRecord] = []

    async def record(self, record: UsageRecord) -> UsageRecord:
        self.records.append(record)
        return record

    async def totals(
        self, *, workspace_id: str, actor_user_id: str, since: datetime
    ) -> UsageTotals:
        matches = [
            r
            for r in self.records
            if r.workspace_id == workspace_id
            and r.actor_user_id == actor_user_id
            and r.created_at >= since
        ]
        return self._sum(matches)

    async def totals_for_job(self, *, idempotency_key: str) -> UsageTotals:
        return self._sum([r for r in self.records if r.idempotency_key == idempotency_key])

    async def has_idempotency_key(self, *, idempotency_key: str) -> bool:
        return any(r.idempotency_key == idempotency_key for r in self.records)

    @staticmethod
    def _sum(records: list[UsageRecord]) -> UsageTotals:
        return UsageTotals(
            request_count=len(records),
            prompt_tokens=sum(r.prompt_tokens for r in records),
            completion_tokens=sum(r.completion_tokens for r in records),
            provider_cost_usd=sum(r.provider_cost_usd or 0.0 for r in records),
            cached_prompt_tokens=sum(r.cached_prompt_tokens for r in records),
        )

    async def delete_workspace(self, workspace_id: str) -> int:
        before = len(self.records)
        self.records = [r for r in self.records if r.workspace_id != workspace_id]
        return before - len(self.records)


class InMemorySchemaVersionStore:
    """Deterministic project-scoped schema-version store for tests."""

    def __init__(self) -> None:
        self.versions: list[SchemaVersion] = []
        self._counter = 0

    def _for_project(self, workspace_id: str, project_id: str) -> list[SchemaVersion]:
        return [
            v
            for v in self.versions
            if v.workspace_id == workspace_id and v.project_id == project_id
        ]

    async def create_draft(
        self,
        *,
        workspace_id: str,
        project_id: str,
        definition: dict[str, Any],
        content_hash: str,
        compatibility: str,
        created_by_user_id: str | None,
    ) -> SchemaVersion:
        self._counter += 1
        existing = self._for_project(workspace_id, project_id)
        version = max((v.version for v in existing), default=0) + 1
        item = SchemaVersion(
            id=f"scv_{self._counter:04d}",
            workspace_id=workspace_id,
            project_id=project_id,
            version=version,
            status=SchemaVersionStatus.DRAFT,
            definition=definition,
            content_hash=content_hash,
            compatibility=compatibility,
            created_by_user_id=created_by_user_id,
        )
        self.versions.append(item)
        return item

    async def get(
        self, *, workspace_id: str, project_id: str, version_id: str
    ) -> SchemaVersion | None:
        for version in self._for_project(workspace_id, project_id):
            if version.id == version_id:
                return version
        return None

    async def get_published(self, *, workspace_id: str, project_id: str) -> SchemaVersion | None:
        published = [
            v
            for v in self._for_project(workspace_id, project_id)
            if v.status is SchemaVersionStatus.PUBLISHED
        ]
        return max(published, key=lambda v: v.version) if published else None

    async def list(self, *, workspace_id: str, project_id: str) -> list[SchemaVersion]:
        return sorted(
            self._for_project(workspace_id, project_id), key=lambda v: v.version, reverse=True
        )

    async def publish(
        self, *, workspace_id: str, project_id: str, version_id: str
    ) -> SchemaVersion | None:
        for index, version in enumerate(self.versions):
            if (
                version.workspace_id == workspace_id
                and version.project_id == project_id
                and version.id == version_id
                and version.status is SchemaVersionStatus.DRAFT
            ):
                updated = version.model_copy(
                    update={
                        "status": SchemaVersionStatus.PUBLISHED,
                        "published_at": utcnow(),
                    }
                )
                self.versions[index] = updated
                return updated
        return None


class InMemoryCanonJournal:
    """In-memory canon journal implementing the same concurrency/idempotency rules."""

    def __init__(self) -> None:
        self._counter = 0
        self.branches: dict[str, dict] = {}
        self.branch_ids: dict[str, str] = {}
        self.mutations: dict[str, MutationSet] = {}
        self.mutation_keys: dict[tuple[str, str, str], str] = {}
        self.operations: dict[str, list[MutationOperation]] = {}
        self.operation_revisions: list[OperationRevision] = []
        self.operation_decisions: list[OperationDecision] = []
        self.transitions: list[MutationTransition] = []
        self.decisions: list[ReviewDecision] = []
        self.reservations: dict[str, BranchReservation] = {}
        self.reservations_by_branch: dict[str, str] = {}
        self.envelopes: dict[str, CommitEnvelope] = {}
        self.attempts: list[CommitAttempt] = []
        self.revisions: dict[str, CanonRevision] = {}
        self.outbox: dict[str, OutboxMessage] = {}
        self.outbox_attempts: dict[str, int] = {}
        self.outbox_errors: dict[str, str] = {}

    def _id(self, kind: str) -> str:
        self._counter += 1
        return f"{kind}_{self._counter:04d}"

    def _now(self) -> datetime:
        return datetime.now(UTC)

    async def ensure_root_branch(
        self, *, workspace_id: str, project_id: str, name: str = "main"
    ) -> Branch:
        key = f"{workspace_id}\x1f{project_id}\x1f{name}"
        existing = self.branch_ids.get(key)
        if existing is not None:
            return Branch(**self.branches[existing])
        branch_id = self._id("branch")
        self.branch_ids[key] = branch_id
        self.branches[branch_id] = {
            "id": branch_id,
            "workspace_id": workspace_id,
            "project_id": project_id,
            "name": name,
            "is_root": True,
            "head_revision": 0,
            "created_at": self._now(),
        }
        return Branch(**self.branches[branch_id])

    async def get_branch(
        self, *, workspace_id: str, project_id: str, branch_id: str
    ) -> Branch | None:
        data = self.branches.get(branch_id)
        if data is None or data["workspace_id"] != workspace_id or data["project_id"] != project_id:
            return None
        return Branch(**data)

    async def list_branches(self, *, workspace_id: str, project_id: str) -> list[Branch]:
        return [
            Branch(**data)
            for data in self.branches.values()
            if data["workspace_id"] == workspace_id and data["project_id"] == project_id
        ]

    async def working_branch(self, *, workspace_id: str, project_id: str) -> Branch:
        branch_id = working_branch_id()
        if branch_id is None:
            return await self.ensure_root_branch(workspace_id=workspace_id, project_id=project_id)
        branch = await self.get_branch(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
        )
        if branch is None:
            from backend.domain.errors import NotFoundError

            raise NotFoundError("That what-if was not found.")
        return branch

    def is_root(self, branch_id: str) -> bool:
        return bool(self.branches.get(branch_id, {}).get("is_root", True))

    async def create_mutation(
        self,
        *,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        expected_head: int,
        actor_user_id: str | None = None,
        origin: str = "author",
        schema_version_id: str | None = None,
        correlation_id: str = "",
        idempotency_key: str = "",
        intent: str = "edit",
    ) -> MutationSet:
        if idempotency_key:
            key = (workspace_id, project_id, idempotency_key)
            existing = self.mutation_keys.get(key)
            if existing is not None:
                return self.mutations[existing]
        mutation_id = self._id("mutation")
        now = self._now()
        mutation = MutationSet(
            id=mutation_id,
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch_id,
            schema_version_id=schema_version_id,
            status=MutationStatus.DRAFT,
            origin=origin,
            actor_user_id=actor_user_id,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            expected_head=expected_head,
            intent=intent,
            created_at=now,
            updated_at=now,
        )
        self.mutations[mutation_id] = mutation
        if idempotency_key:
            self.mutation_keys[(workspace_id, project_id, idempotency_key)] = mutation_id
        return mutation

    async def get_mutation(
        self, *, workspace_id: str, project_id: str, mutation_id: str
    ) -> MutationSet | None:
        mutation = self.mutations.get(mutation_id)
        if mutation is None or mutation.workspace_id != workspace_id:
            return None
        if mutation.project_id != project_id:
            return None
        return mutation

    def purge(self, workspace_id: str, project_id: str | None) -> int:
        def owned(ws: str, pr: str) -> bool:
            return ws == workspace_id and (project_id is None or pr == project_id)

        mutation_ids = {
            mid for mid, m in self.mutations.items() if owned(m.workspace_id, m.project_id)
        }
        for mutation_id in mutation_ids:
            del self.mutations[mutation_id]
            self.operations.pop(mutation_id, None)
        self.mutation_keys = {
            key: mid for key, mid in self.mutation_keys.items() if mid not in mutation_ids
        }
        self.operation_revisions = [
            r for r in self.operation_revisions if r.mutation_id not in mutation_ids
        ]
        self.operation_decisions = [
            d for d in self.operation_decisions if d.mutation_id not in mutation_ids
        ]
        self.transitions = [t for t in self.transitions if t.mutation_id not in mutation_ids]
        self.decisions = [d for d in self.decisions if d.mutation_id not in mutation_ids]
        branch_ids = {
            bid
            for bid, data in self.branches.items()
            if owned(data["workspace_id"], data["project_id"])
        }
        for branch_id in branch_ids:
            del self.branches[branch_id]
        self.branch_ids = {k: v for k, v in self.branch_ids.items() if v not in branch_ids}
        self.outbox = {
            key: message
            for key, message in self.outbox.items()
            if not owned(message.workspace_id, message.project_id)
        }
        return len(mutation_ids) + len(branch_ids)

    def _require(self, mutation_id: str) -> MutationSet:
        mutation = self.mutations.get(mutation_id)
        if mutation is None:
            raise MutationNotFound(f"mutation {mutation_id!r} not found")
        return mutation

    def apply_transition(
        self,
        mutation_id: str,
        to_status: MutationStatus,
        *,
        actor_user_id: str | None,
        reason: str,
        correlation_id: str = "",
    ) -> MutationSet:
        mutation = self._require(mutation_id)
        ensure_transition(mutation.status, to_status)
        if not reason:
            raise ValueError("a transition reason is required")
        self.transitions.append(
            MutationTransition(
                id=self._id("operation"),
                mutation_id=mutation_id,
                from_status=mutation.status,
                to_status=to_status,
                actor_user_id=actor_user_id,
                reason=reason,
                correlation_id=correlation_id or mutation.correlation_id,
                idempotency_key=mutation.idempotency_key,
                created_at=self._now(),
            )
        )
        updated = mutation.model_copy(update={"status": to_status, "updated_at": self._now()})
        self.mutations[mutation_id] = updated
        return updated

    async def list_mutations(
        self,
        *,
        workspace_id: str,
        project_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MutationSet]:
        working = working_branch_id()
        matches = [
            mutation
            for mutation in reversed(list(self.mutations.values()))
            if mutation.workspace_id == workspace_id
            and mutation.project_id == project_id
            and (status is None or mutation.status.value == status)
            and (
                mutation.branch_id == working
                if working is not None
                else self.is_root(mutation.branch_id)
            )
        ]
        return matches[offset : offset + limit]

    async def transition_mutation(
        self,
        *,
        mutation_id: str,
        to_status: MutationStatus,
        actor_user_id: str | None,
        reason: str,
        correlation_id: str = "",
    ) -> MutationSet:
        return self.apply_transition(
            mutation_id,
            to_status,
            actor_user_id=actor_user_id,
            reason=reason,
            correlation_id=correlation_id,
        )

    async def list_transitions(self, *, mutation_id: str) -> list[MutationTransition]:
        return [item for item in self.transitions if item.mutation_id == mutation_id]

    async def retarget_mutation(
        self,
        *,
        mutation_id: str,
        expected_head: int,
        actor_user_id: str | None,
        reason: str,
        keep_approval: bool = False,
    ) -> MutationSet:
        mutation = self._require(mutation_id)
        if not is_editable(mutation.status):
            raise RevisionConflict(
                f"mutation in state {mutation.status.value!r} cannot be retargeted"
            )
        self.mutations[mutation_id] = mutation.model_copy(
            update={"expected_head": expected_head, "updated_at": self._now()}
        )
        if mutation.status is MutationStatus.APPROVED and not keep_approval:
            self.apply_transition(
                mutation_id,
                MutationStatus.NEEDS_REVIEW,
                actor_user_id=actor_user_id,
                reason=reason,
            )
        return self.mutations[mutation_id]

    async def record_operations(
        self, *, mutation_id: str, operations: list[MutationOperation]
    ) -> None:
        bucket = self.operations.setdefault(mutation_id, [])
        existing = {operation.ordinal: operation for operation in bucket}
        for operation in operations:
            previous = existing.get(operation.ordinal)
            if previous is not None and (
                previous.operation_type != operation.operation_type
                or previous.payload != operation.payload
            ):
                raise RevisionConflict(
                    "mutation operation ordinal was reused with different content"
                )
        for operation in operations:
            if operation.ordinal in existing:
                continue
            bucket.append(operation)
            self.operation_revisions.append(
                OperationRevision(
                    mutation_id=mutation_id,
                    ordinal=operation.ordinal,
                    revision=operation.revision,
                    operation_type=operation.operation_type,
                    payload=operation.payload,
                    reason="proposed",
                    created_at=self._now(),
                )
            )

    async def revise_operation(
        self,
        *,
        mutation_id: str,
        ordinal: int,
        operation_type: str,
        payload: dict[str, Any],
        actor_user_id: str | None,
        reason: str,
    ) -> MutationOperation:
        mutation = self._require(mutation_id)
        if not is_editable(mutation.status):
            raise RevisionConflict(f"mutation in state {mutation.status.value!r} cannot be edited")
        bucket = self.operations.get(mutation_id, [])
        for index, current in enumerate(bucket):
            if current.ordinal == ordinal:
                revised = MutationOperation(
                    ordinal=ordinal,
                    operation_type=operation_type,
                    payload=payload,
                    revision=current.revision + 1,
                )
                bucket[index] = revised
                self.operation_revisions.append(
                    OperationRevision(
                        mutation_id=mutation_id,
                        ordinal=ordinal,
                        revision=revised.revision,
                        operation_type=operation_type,
                        payload=payload,
                        actor_user_id=actor_user_id,
                        reason=reason,
                        created_at=self._now(),
                    )
                )
                if mutation.status is MutationStatus.APPROVED:
                    self.apply_transition(
                        mutation_id,
                        MutationStatus.NEEDS_REVIEW,
                        actor_user_id=actor_user_id,
                        reason="operation edited after approval",
                    )
                return revised
        raise MutationNotFound(f"operation {ordinal} not found in {mutation_id!r}")

    async def list_operation_revisions(self, *, mutation_id: str) -> list[OperationRevision]:
        return sorted(
            (item for item in self.operation_revisions if item.mutation_id == mutation_id),
            key=lambda item: (item.ordinal, item.revision),
        )

    async def record_operation_decision(
        self,
        *,
        mutation_id: str,
        ordinal: int,
        decision: str,
        actor_user_id: str | None = None,
        note: str | None = None,
    ) -> OperationDecision:
        mutation = self._require(mutation_id)
        if not is_editable(mutation.status):
            raise RevisionConflict(
                f"mutation in state {mutation.status.value!r} cannot be reviewed"
            )
        current = next(
            (op for op in self.operations.get(mutation_id, []) if op.ordinal == ordinal), None
        )
        if current is None:
            raise MutationNotFound(f"operation {ordinal} not found in {mutation_id!r}")
        if decision not in {"accepted", "rejected"}:
            raise ValueError(f"unknown operation decision {decision!r}")
        record = OperationDecision(
            id=self._id("operation"),
            mutation_id=mutation_id,
            ordinal=ordinal,
            operation_revision=current.revision,
            decision=decision,
            actor_user_id=actor_user_id,
            note=note,
            created_at=self._now(),
        )
        self.operation_decisions.append(record)
        if mutation.status is MutationStatus.APPROVED:
            self.apply_transition(
                mutation_id,
                MutationStatus.NEEDS_REVIEW,
                actor_user_id=actor_user_id,
                reason="operation decision changed after approval",
            )
        return record

    async def record_operation_decisions(
        self, *, mutation_id: str, decisions, actor_user_id: str | None = None
    ) -> list[OperationDecision]:
        known = {op.ordinal for op in self.operations.get(mutation_id, [])}
        for ordinal, _decision, _note in decisions:
            if ordinal not in known:
                raise MutationNotFound(f"operation {ordinal} not found in {mutation_id!r}")
        return [
            await self.record_operation_decision(
                mutation_id=mutation_id,
                ordinal=ordinal,
                decision=decision,
                actor_user_id=actor_user_id,
                note=note,
            )
            for ordinal, decision, note in decisions
        ]

    async def list_operation_decisions(self, *, mutation_id: str) -> list[OperationDecision]:
        return [item for item in self.operation_decisions if item.mutation_id == mutation_id]

    async def list_operations(self, *, mutation_id: str) -> list[MutationOperation]:
        return sorted(self.operations.get(mutation_id, []), key=lambda op: op.ordinal)

    async def record_decision(
        self,
        *,
        mutation_id: str,
        decision: str,
        actor_user_id: str | None = None,
        note: str | None = None,
        transition_to: MutationStatus | None = None,
        correlation_id: str = "",
    ) -> ReviewDecision:
        if transition_to is not None and self._require(mutation_id).status is not transition_to:
            self.apply_transition(
                mutation_id,
                transition_to,
                actor_user_id=actor_user_id,
                reason=note or f"review decision: {decision}",
                correlation_id=correlation_id,
            )
        record = ReviewDecision(
            id=self._id("operation"),
            mutation_id=mutation_id,
            decision=decision,
            actor_user_id=actor_user_id,
            note=note,
            created_at=self._now(),
        )
        self.decisions.append(record)
        return record

    async def list_decisions(self, *, mutation_id: str) -> list[ReviewDecision]:
        return [decision for decision in self.decisions if decision.mutation_id == mutation_id]

    async def reserve_head(
        self,
        *,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        mutation_id: str,
        expected_revision: int,
    ) -> BranchReservation:
        data = self.branches.get(branch_id)
        if data is None or data["workspace_id"] != workspace_id or data["project_id"] != project_id:
            raise HeadConflict("branch not found in this project")
        if int(data["head_revision"]) != expected_revision:
            raise HeadConflict(
                f"expected head {expected_revision} but branch is at {data['head_revision']}"
            )
        if self.reservations_by_branch.get(branch_id):
            active = self.reservations[self.reservations_by_branch[branch_id]]
            if active.status == ReservationStatus.ACTIVE:
                raise HeadConflict("another commit reservation is active for this branch")
        reservation = BranchReservation(
            id=self._id("revision"),
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch_id,
            mutation_id=mutation_id,
            expected_revision=expected_revision,
            reserved_revision=expected_revision + 1,
            status=ReservationStatus.ACTIVE,
            created_at=self._now(),
        )
        self.reservations[reservation.id] = reservation
        self.reservations_by_branch[branch_id] = reservation.id
        return reservation

    async def append_envelope(self, envelope: CommitEnvelope) -> None:
        existing = self.envelopes.get(envelope.commit_id)
        if existing is not None:
            if existing.checksum != envelope.checksum:
                raise RevisionConflict("commit id reused with a different checksum")
            return
        self.envelopes[envelope.commit_id] = envelope

    async def get_envelope(self, *, commit_id: str) -> CommitEnvelope | None:
        return self.envelopes.get(commit_id)

    async def record_attempt(
        self, *, commit_id: str, status: str, detail: str | None = None
    ) -> CommitAttempt:
        attempt = CommitAttempt(
            id=self._id("operation"),
            commit_id=commit_id,
            status=AttemptStatus(status),
            detail=detail,
            created_at=self._now(),
        )
        self.attempts.append(attempt)
        return attempt

    async def finalize(self, *, commit_id: str) -> CanonRevision:
        envelope = self.envelopes.get(commit_id)
        if envelope is None:
            raise JournalError("commit envelope not found")
        existing = self.revisions.get(commit_id)
        if existing is not None:
            return existing
        data = self.branches.get(envelope.branch_id)
        if data is None or int(data["head_revision"]) != envelope.expected_head:
            raise HeadConflict("branch head advanced past the expected revision")
        data["head_revision"] = envelope.revision
        reservation_id = self.reservations_by_branch.get(envelope.branch_id)
        if reservation_id:
            reservation = self.reservations[reservation_id]
            self.reservations[reservation_id] = reservation.model_copy(
                update={"status": ReservationStatus.FINALIZED}
            )
        revision = CanonRevision(
            revision=envelope.revision,
            workspace_id=envelope.workspace_id,
            project_id=envelope.project_id,
            branch_id=envelope.branch_id,
            commit_id=envelope.commit_id,
            expected_head=envelope.expected_head,
            result_head=envelope.revision,
            schema_version_id=envelope.schema_version_id,
            envelope_checksum=envelope.checksum,
            created_at=self._now(),
        )
        self.revisions[commit_id] = revision
        message = OutboxMessage(
            id=self._id("operation"),
            topic="canon.commit",
            workspace_id=envelope.workspace_id,
            project_id=envelope.project_id,
            branch_id=envelope.branch_id,
            revision=envelope.revision,
            payload={"commit_id": envelope.commit_id},
            idempotency_key=f"commit:{envelope.commit_id}",
            created_at=self._now(),
        )
        self.outbox.setdefault(message.idempotency_key, message)
        return revision

    async def append_outbox(self, message: OutboxMessage) -> None:
        self.outbox.setdefault(message.idempotency_key, message)

    async def list_outbox(
        self, *, workspace_id: str, project_id: str, limit: int = 100, pending_only: bool = False
    ) -> list[OutboxMessage]:
        return [
            message
            for message in self.outbox.values()
            if message.workspace_id == workspace_id
            and message.project_id == project_id
            and (not pending_only or message.published_at is None)
        ][:limit]

    async def projects_with_pending_outbox(self, *, limit: int = 50) -> list[tuple[str, str]]:
        seen: list[tuple[str, str]] = []
        for message in self.outbox.values():
            pair = (message.workspace_id, message.project_id)
            if message.published_at is None and pair not in seen:
                seen.append(pair)
        return seen[:limit]

    async def mark_outbox_published(self, *, message_id: str) -> None:
        for key, message in self.outbox.items():
            if message.id == message_id:
                self.outbox[key] = message.model_copy(update={"published_at": self._now()})
                return

    async def mark_outbox_failed(self, *, message_id: str, error: str) -> None:
        self.outbox_attempts[message_id] = self.outbox_attempts.get(message_id, 0) + 1
        self.outbox_errors[message_id] = error

    async def count_failed_outbox(self, *, workspace_id: str, project_id: str) -> int:
        return sum(
            1
            for message in self.outbox.values()
            if message.workspace_id == workspace_id
            and message.project_id == project_id
            and message.published_at is None
            and self.outbox_attempts.get(message.id, 0) > 0
        )


class InMemoryCanonWriter:
    """In-memory canon writer with deterministic, fault-injectable apply."""

    def __init__(self) -> None:
        from backend.domain.commit import ApplyOutcome, CommitMarker
        from backend.domain.journal import RevisionConflict

        self._ApplyOutcome = ApplyOutcome
        self._CommitMarker = CommitMarker
        self._RevisionConflict = RevisionConflict
        self.markers: dict[str, CommitMarker] = {}
        self.objects: dict[tuple[str, str, str], dict] = {}
        self.entities: dict[tuple[str, str, str], dict] = {}
        self.facts: dict[tuple[str, str, str], dict] = {}
        self.beliefs: dict[tuple[str, str, str], dict] = {}
        self.events: dict[tuple[str, str, str], dict] = {}
        self.applied_operations: list = []
        # When set, apply fails after this many operations (crash simulation),
        # leaving a partially applied graph and no marker.
        self.fail_after_ops: int | None = None

    async def apply_commit(
        self,
        *,
        commit_id,
        revision,
        checksum,
        workspace_id,
        project_id,
        branch_id,
        operations,
        schema_version_id=None,
    ):
        existing = self.markers.get(commit_id)
        if existing is not None:
            if existing.checksum != checksum:
                raise self._RevisionConflict("commit id reused with a different checksum")
            return self._ApplyOutcome.ALREADY_APPLIED
        applied = 0
        for operation in operations:
            if self.fail_after_ops is not None and applied >= self.fail_after_ops:
                raise RuntimeError("injected graph apply failure")
            self.objects[(workspace_id, project_id, operation.id)] = {
                **operation.model_dump(mode="json"),
                "revision": revision,
            }
            self._apply_entity(
                workspace_id, project_id, branch_id, schema_version_id, revision, operation
            )
            self._apply_assertion(
                workspace_id, project_id, branch_id, revision, operation, commit_id
            )
            self.applied_operations.append(operation)
            applied += 1
        self.markers[commit_id] = self._CommitMarker(
            commit_id=commit_id,
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch_id,
            revision=revision,
            checksum=checksum,
        )
        return self._ApplyOutcome.APPLIED

    async def get_marker(self, commit_id):
        return self.markers.get(commit_id)

    async def purge(self, *, workspace_id: str, project_id: str | None = None) -> int:
        def owned(key: tuple) -> bool:
            return key[0] == workspace_id and (project_id is None or key[1] == project_id)

        removed = 0
        for store in (self.objects, self.entities, self.facts, self.beliefs, self.events):
            for key in [k for k in store if owned(k)]:
                del store[key]
                removed += 1
        for commit_id in [
            commit_id
            for commit_id, marker in self.markers.items()
            if owned((marker.workspace_id, marker.project_id))
        ]:
            del self.markers[commit_id]
            removed += 1
        return removed

    async def discard(self, *, workspace_id: str, project_id: str, branch_id: str) -> int:
        removed = 0
        for store in (self.objects, self.entities, self.facts, self.beliefs, self.events):
            for key in [k for k in store if k[0] == workspace_id and k[1] == project_id]:
                del store[key]
                removed += 1
        for commit_id in [
            commit_id
            for commit_id, marker in self.markers.items()
            if marker.workspace_id == workspace_id
            and marker.project_id == project_id
            and marker.branch_id == branch_id
        ]:
            del self.markers[commit_id]
        return removed

    def _apply_entity(self, ws, pr, branch_id, schema_version_id, revision, operation) -> None:
        from backend.domain.operations import CreateEntityOp, RetireEntityOp, UpdateEntityOp

        key = (ws, pr, operation.id)
        if isinstance(operation, CreateEntityOp):
            self.entities[key] = {
                "branch_id": branch_id,
                "schema_version_id": schema_version_id or "",
                "type": operation.type,
                "name": operation.name,
                "slug": operation.slug,
                "lifecycle": "active",
                "created_revision": revision,
                "retired_revision": None,
                "attributes": dict(operation.attributes),
                "aliases": list(operation.aliases),
                "external_ids": dict(operation.external_ids),
            }
        elif isinstance(operation, UpdateEntityOp):
            entity = self.entities.get(key)
            if entity is not None:
                if operation.name is not None:
                    entity["name"] = operation.name
                if operation.slug is not None:
                    entity["slug"] = operation.slug
                if operation.attributes is not None:
                    entity["attributes"] = dict(operation.attributes)
                if operation.aliases is not None:
                    entity["aliases"] = list(operation.aliases)
                if operation.external_ids is not None:
                    entity["external_ids"] = dict(operation.external_ids)
                if operation.type is not None:
                    entity["type"] = operation.type
        elif isinstance(operation, RetireEntityOp):
            entity = self.entities.get(key)
            if entity is not None:
                entity["lifecycle"] = "retired"
                entity["retired_revision"] = revision
        elif isinstance(operation, RestoreEntityOp):
            entity = self.entities.get(key)
            if entity is not None:
                entity["lifecycle"] = "active"
                entity["retired_revision"] = None

    def _apply_assertion(self, ws, pr, branch_id, revision, operation, commit_id) -> None:
        from backend.domain.operations import (
            AmendEventOp,
            AmendFactOp,
            AssertBeliefOp,
            AssertFactOp,
            CloseBeliefOp,
            CloseFactOp,
            CreateEventOp,
            RetireEventOp,
        )

        key = (ws, pr, operation.id)
        if isinstance(operation, AssertFactOp):
            self.facts[key] = {
                "branch_id": branch_id,
                "proposition": operation.proposition.model_copy(deep=True),
                "status": "asserted",
                "confidence": operation.confidence,
                "provenance_id": f"prov_{commit_id}",
                "created_revision": revision,
                "closed_revision": None,
                "valid_from": operation.valid_from,
                "valid_until": operation.valid_until,
                "starts": _stored_time(operation.starts),
                "ends": _stored_time(operation.ends),
                "visibility": (operation.visibility or "ordinary"),
            }
        elif isinstance(operation, AmendFactOp):
            fact = self.facts.get(key)
            if fact is not None:
                if operation.starts is not None:
                    fact["starts"] = _stored_time(operation.starts)
                if operation.ends is not None:
                    fact["ends"] = _stored_time(operation.ends)
                if operation.visibility is not None:
                    fact["visibility"] = operation.visibility
        elif isinstance(operation, CloseFactOp):
            fact = self.facts.get(key)
            if fact is not None:
                fact["status"] = "closed"
                fact["closed_revision"] = revision
        elif isinstance(operation, AssertBeliefOp):
            self.beliefs[key] = {
                "branch_id": branch_id,
                "holder_entity_id": operation.holder_entity_id,
                "proposition": operation.proposition.model_copy(deep=True),
                "confidence": operation.confidence,
                "acquisition_provenance_id": f"prov_{commit_id}",
                "source_ref": operation.source_ref,
                "status": "asserted",
                "created_revision": revision,
                "closed_revision": None,
            }
        elif isinstance(operation, CloseBeliefOp):
            belief = self.beliefs.get(key)
            if belief is not None:
                belief["status"] = "closed"
                belief["closed_revision"] = revision
        elif isinstance(operation, CreateEventOp):
            self.events[key] = {
                "branch_id": branch_id,
                "kind": operation.kind,
                "name": operation.name,
                "participants": [p.model_copy(deep=True) for p in operation.participants],
                "location_ids": list(operation.location_ids),
                "cause_ids": list(operation.cause_ids),
                "outcome_ids": list(operation.outcome_ids),
                "world_position": operation.world_position,
                "when": _stored_time(operation.when),
                "claim": operation.claim,
                "scene_id": operation.scene_id,
                "premises": list(operation.premises),
                "provenance_id": f"prov_{commit_id}",
                "created_revision": revision,
                "lifecycle": "active",
            }
        elif isinstance(operation, AmendEventOp):
            event = self.events.get(key)
            if event is not None:
                if operation.name is not None:
                    event["name"] = operation.name
                if operation.world_position is not None:
                    event["world_position"] = operation.world_position
                if operation.when is not None:
                    event["when"] = _stored_time(operation.when)
                if operation.participants is not None:
                    event["participants"] = [
                        p.model_copy(deep=True) for p in operation.participants
                    ]
                if operation.location_ids is not None:
                    event["location_ids"] = list(operation.location_ids)
                if operation.cause_ids is not None:
                    event["cause_ids"] = list(operation.cause_ids)
                if operation.outcome_ids is not None:
                    event["outcome_ids"] = list(operation.outcome_ids)
                if operation.claim is not None:
                    event["claim"] = operation.claim
        elif isinstance(operation, RetireEventOp):
            event = self.events.get(key)
            if event is not None:
                event["lifecycle"] = "retired"
                event["retired_revision"] = revision

    def _entity(self, key):
        from backend.domain.canon.entities import CanonEntity

        data = self.entities.get(key)
        if data is None:
            return None
        return CanonEntity(
            id=key[2],
            workspace_id=key[0],
            project_id=key[1],
            branch_id=data["branch_id"],
            type=data["type"],
            name=data["name"],
            slug=data["slug"],
            lifecycle=data["lifecycle"],
            schema_version_id=data["schema_version_id"],
            created_revision=data["created_revision"],
            retired_revision=data["retired_revision"],
            attributes=data["attributes"],
            aliases=data.get("aliases", []),
            external_ids=data.get("external_ids", {}),
        )

    async def list_fact_edges(self, *, workspace_id, project_id):
        edges = []
        for key, data in self.facts.items():
            obj = data["proposition"].object
            if (
                key[0] == workspace_id
                and key[1] == project_id
                and data["status"] == "asserted"
                and getattr(obj, "entity_id", None) is not None
            ):
                edges.append(
                    (
                        key[2],
                        data["proposition"].subject_id,
                        data["proposition"].predicate,
                        obj.entity_id,
                    )
                )
        return sorted(edges)

    async def get_entity(self, *, workspace_id, project_id, entity_id):
        return self._entity((workspace_id, project_id, entity_id))

    async def list_entities(
        self,
        *,
        workspace_id,
        project_id,
        entity_type=None,
        include_retired=False,
        limit=50,
        offset=0,
    ):
        items = []
        for key, data in sorted(self.entities.items(), key=lambda kv: (kv[1]["name"], kv[0][2])):
            if key[0] != workspace_id or key[1] != project_id:
                continue
            if entity_type is not None and data["type"] != entity_type:
                continue
            if not include_retired and data["lifecycle"] != "active":
                continue
            items.append(self._entity(key))
        return items[offset : offset + limit]

    async def search_entities(
        self,
        *,
        workspace_id,
        project_id,
        query,
        entity_type=None,
        include_retired=False,
        limit=50,
    ):
        needle = query.lower()
        return [
            entity
            for entity in await self.list_entities(
                workspace_id=workspace_id,
                project_id=project_id,
                entity_type=entity_type,
                include_retired=include_retired,
                limit=limit,
                offset=0,
            )
            if needle in entity.name.lower()
        ]

    def _fact(self, key):
        from backend.domain.canon.assertions import Fact, FactStatus

        data = self.facts.get(key)
        if data is None:
            return None
        return Fact(
            id=key[2],
            workspace_id=key[0],
            project_id=key[1],
            branch_id=data["branch_id"],
            proposition=data["proposition"],
            status=FactStatus(data["status"]),
            confidence=data["confidence"],
            provenance_id=data["provenance_id"],
            created_revision=data["created_revision"],
            closed_revision=data["closed_revision"],
            valid_from=data["valid_from"],
            valid_until=data["valid_until"],
            starts=data.get("starts"),
            ends=data.get("ends"),
            visibility=data.get("visibility") or "ordinary",
        )

    async def get_fact(self, *, workspace_id, project_id, fact_id):
        return self._fact((workspace_id, project_id, fact_id))

    async def list_facts(
        self,
        *,
        workspace_id,
        project_id,
        include_closed=False,
        subject_id=None,
        predicate=None,
        limit=50,
        offset=0,
    ):
        items = []
        for key, data in sorted(
            self.facts.items(), key=lambda kv: (kv[1]["created_revision"], kv[0][2])
        ):
            if key[0] != workspace_id or key[1] != project_id:
                continue
            if not include_closed and data["status"] != "asserted":
                continue
            proposition = data["proposition"]
            if subject_id is not None and proposition.subject_id != subject_id:
                continue
            if predicate is not None and proposition.predicate != predicate:
                continue
            items.append(self._fact(key))
        return items[offset : offset + limit]

    def _belief(self, key):
        from backend.domain.canon.assertions import Belief

        data = self.beliefs.get(key)
        if data is None:
            return None
        return Belief(
            id=key[2],
            workspace_id=key[0],
            project_id=key[1],
            branch_id=data["branch_id"],
            holder_entity_id=data["holder_entity_id"],
            proposition=data["proposition"],
            confidence=data["confidence"],
            acquisition_provenance_id=data["acquisition_provenance_id"],
            source_ref=data.get("source_ref"),
            created_revision=data["created_revision"],
            closed_revision=data["closed_revision"],
        )

    async def get_belief(self, *, workspace_id, project_id, belief_id):
        return self._belief((workspace_id, project_id, belief_id))

    async def list_beliefs(
        self,
        *,
        workspace_id,
        project_id,
        holder_entity_id=None,
        include_closed=False,
        limit=50,
        offset=0,
    ):
        items = []
        for key, data in sorted(
            self.beliefs.items(), key=lambda kv: (kv[1]["created_revision"], kv[0][2])
        ):
            if key[0] != workspace_id or key[1] != project_id:
                continue
            if not include_closed and data["status"] != "asserted":
                continue
            if holder_entity_id is not None and data["holder_entity_id"] != holder_entity_id:
                continue
            items.append(self._belief(key))
        return items[offset : offset + limit]

    def _event(self, key):
        from backend.domain.canon.events import Event

        data = self.events.get(key)
        if data is None:
            return None
        return Event(
            id=key[2],
            workspace_id=key[0],
            project_id=key[1],
            branch_id=data["branch_id"],
            kind=data["kind"],
            name=data["name"],
            participants=list(data["participants"]),
            location_ids=list(data["location_ids"]),
            cause_ids=list(data["cause_ids"]),
            outcome_ids=list(data["outcome_ids"]),
            world_position=data["world_position"],
            when=data.get("when"),
            claim=data.get("claim"),
            scene_id=data.get("scene_id"),
            premises=list(data.get("premises") or []),
            provenance_id=data["provenance_id"],
            created_revision=data["created_revision"],
            retired_revision=data.get("retired_revision"),
        )

    async def get_event(self, *, workspace_id, project_id, event_id):
        return self._event((workspace_id, project_id, event_id))

    async def list_events(
        self, *, workspace_id, project_id, include_retired=False, limit=50, offset=0
    ):
        items = []
        for key, data in sorted(
            self.events.items(), key=lambda kv: (kv[1]["created_revision"], kv[0][2])
        ):
            if key[0] != workspace_id or key[1] != project_id:
                continue
            if not include_retired and data["lifecycle"] != "active":
                continue
            items.append(self._event(key))
        return items[offset : offset + limit]

    async def health(self) -> bool:
        return True


_CANON_RECORD_TYPE = {
    "create_entity": "entity",
    "update_entity": "entity",
    "retire_entity": "entity",
    "restore_entity": "entity",
    "assert_fact": "fact",
    "close_fact": "fact",
    "amend_fact": "fact",
    "assert_belief": "belief",
    "close_belief": "belief",
    "create_event": "event",
    "amend_event": "event",
    "retire_event": "event",
    "link_asset": "asset_link",
    "retire_asset_link": "asset_link",
    "create_plan_item": "plan_item",
    "update_plan_item": "plan_item",
    "retire_plan_item": "plan_item",
}


class _MemoryCanonReader:
    """In-memory canonical reads bound to a commit scope (for in-tx validation)."""

    def __init__(self, repository: Any, workspace_id: str, project_id: str, branch_id: str) -> None:
        self._repository = repository
        self._workspace_id = workspace_id
        self._project_id = project_id
        self._branch_id = branch_id

    async def get_entity(self, entity_id: str) -> Any:
        return self._repository.entities.get((self._workspace_id, self._project_id, entity_id))

    async def open_facts(self, subject_id: str, predicate: str) -> list[Any]:
        return [
            fact
            for fact in self._repository.facts.values()
            if fact.workspace_id == self._workspace_id
            and fact.project_id == self._project_id
            and fact.branch_id == self._branch_id
            and getattr(fact.status, "value", fact.status) == "asserted"
            and fact.proposition.subject_id == subject_id
            and fact.proposition.predicate == predicate
        ]

    async def get_fact(self, fact_id: str) -> Any:
        return self._repository.facts.get((self._workspace_id, self._project_id, fact_id))

    async def get_belief(self, belief_id: str) -> Any:
        return self._repository.beliefs.get((self._workspace_id, self._project_id, belief_id))

    async def get_event(self, event_id: str) -> Any:
        return self._repository.events.get((self._workspace_id, self._project_id, event_id))

    async def get_plan_item(self, plan_id: str) -> Any:
        return self._repository.plan_items.get((self._workspace_id, self._project_id, plan_id))

    async def plan_child_ids(self, plan_id: str) -> list[str]:
        return [
            item.id
            for (w, p, _), item in self._repository.plan_items.items()
            if w == self._workspace_id
            and p == self._project_id
            and item.parent_id == plan_id
            and item.retired_revision is None
        ]

    async def get_link(self, link_id: str) -> Any:
        return self._repository.asset_links.get((self._workspace_id, self._project_id, link_id))

    async def get_asset(self, asset_id: str) -> Any:
        if self._repository._media is None:
            return None
        return await self._repository._media.get_asset(
            workspace_id=self._workspace_id, project_id=self._project_id, asset_id=asset_id
        )

    async def get_asset_version(self, version_id: str) -> Any:
        if self._repository._media is None:
            return None
        return await self._repository._media.get_version(
            workspace_id=self._workspace_id, project_id=self._project_id, version_id=version_id
        )

    async def find_entity_by_slug(self, slug: str) -> Any:
        return await self._repository.get_entity_by_slug(
            workspace_id=self._workspace_id, project_id=self._project_id, slug=slug
        )

    async def find_entity_by_external_id(self, namespace: str, value: str) -> Any:
        return await self._repository.get_entity_by_external_id(
            workspace_id=self._workspace_id,
            project_id=self._project_id,
            namespace=namespace,
            value=value,
        )

    async def find_entities_by_name(self, entity_type: str, name: str) -> list[Any]:
        return await self._repository.find_entities_by_name(
            workspace_id=self._workspace_id,
            project_id=self._project_id,
            entity_type=entity_type,
            name=name,
        )

    async def count_active_references(self, entity_id: str) -> int:
        return await self._repository.count_active_references(
            workspace_id=self._workspace_id, project_id=self._project_id, entity_id=entity_id
        )

    async def active_entities(self, limit: int) -> list[Any]:
        found = [
            entity
            for (w, p, _), entity in sorted(self._repository.entities.items())
            if (w, p) == (self._workspace_id, self._project_id) and entity.is_active
        ]
        return found[:limit]

    async def asserted_facts(self, limit: int) -> list[Any]:
        found = [
            fact
            for (w, p, _), fact in sorted(self._repository.facts.items())
            if (w, p) == (self._workspace_id, self._project_id)
            and getattr(fact.status, "value", fact.status) == "asserted"
        ]
        return found[:limit]

    async def current_beliefs(self, limit: int) -> list[Any]:
        return list(
            await self._repository.list_beliefs(
                workspace_id=self._workspace_id, project_id=self._project_id, limit=limit
            )
        )

    async def live_events(self, limit: int) -> list[Any]:
        return list(
            await self._repository.list_events(
                workspace_id=self._workspace_id, project_id=self._project_id, limit=limit
            )
        )

    async def live_plan_items(self, limit: int) -> list[Any]:
        found = [
            item
            for (w, p, _), item in sorted(self._repository.plan_items.items())
            if (w, p) == (self._workspace_id, self._project_id) and item.retired_revision is None
        ]
        return found[:limit]

    async def live_asset_links(self, limit: int) -> list[Any]:
        found = [
            link
            for (w, p, _), link in sorted(self._repository.asset_links.items())
            if (w, p) == (self._workspace_id, self._project_id) and link.retired_revision is None
        ]
        return found[:limit]


def _per_branch(name: str, factory: Any) -> property:
    """A store kept separately for each line of the story (ADR-020).

    Reads see the what-if the request works in, or the main story; a commit sees
    the branch it is committed to. ``None`` is the main story.
    """

    def get(self: Any) -> Any:
        store = self._stores.setdefault(self._namespace(), {})
        if name not in store:
            store[name] = factory()
        return store[name]

    def put(self: Any, value: Any) -> None:
        self._stores.setdefault(self._namespace(), {})[name] = value

    return property(get, put)


_BRANCHED_STORES = (
    ("entities", dict),
    ("facts", dict),
    ("beliefs", dict),
    ("events", dict),
    ("plan_items", dict),
    ("provenance", dict),
    ("record_versions", list),
    ("asset_links", dict),
    ("evidence", list),
)


class InMemoryCanonRepository:
    """In-memory canonical PostgreSQL canon (ADR-012) with matching invariants."""

    entities = _per_branch("entities", dict)
    facts = _per_branch("facts", dict)
    beliefs = _per_branch("beliefs", dict)
    events = _per_branch("events", dict)
    plan_items = _per_branch("plan_items", dict)
    provenance = _per_branch("provenance", dict)
    record_versions = _per_branch("record_versions", list)
    asset_links = _per_branch("asset_links", dict)
    evidence = _per_branch("evidence", list)

    def _namespace(self) -> str | None:
        return getattr(self, "_committing", None) or working_branch_id()

    def _branch_namespace(self, branch_id: str) -> str | None:
        if self._journal is None or self._journal.is_root(branch_id):
            return None
        return branch_id

    async def fork_branch(
        self,
        *,
        workspace_id: str,
        project_id: str,
        parent_branch_id: str,
        branch_id: str,
        name: str,
        description: str | None,
        actor_user_id: str | None,
    ) -> int:
        import copy

        assert self._journal is not None
        parent = self._journal.branches[parent_branch_id]
        head = self.heads.get((workspace_id, project_id, parent_branch_id), 0)
        self._journal.branches[branch_id] = {
            **parent,
            "id": branch_id,
            "name": name,
            "is_root": False,
            "head_revision": head,
            "created_at": utcnow(),
            "parent_branch_id": parent_branch_id,
            "fork_revision": head,
            "description": description,
            "status": "active",
            "created_by": actor_user_id,
        }
        source = self._stores.get(self._branch_namespace(parent_branch_id), {})
        self._stores[branch_id] = {key: copy.copy(value) for key, value in source.items()}
        self.heads[(workspace_id, project_id, branch_id)] = head
        # Like _FORKED_TABLES: the scene-to-text links are copied too.
        studio = getattr(self, "studio", None)
        if studio is not None:
            for (ws, pj, branch, plan), text_id in list(studio.manuscripts.items()):
                if (ws, pj, branch) == (workspace_id, project_id, parent_branch_id):
                    studio.manuscripts[(ws, pj, branch_id, plan)] = text_id
            from dataclasses import replace as _replace

            for reading in list(studio.saved_readings):
                if (reading.workspace_id, reading.project_id, reading.branch_id) == (
                    workspace_id,
                    project_id,
                    parent_branch_id,
                ):
                    studio.saved_readings.append(_replace(reading, branch_id=branch_id))
            # The author's dismissed findings come too (finding_dismissals is forked).
            for dismissal in list(studio.saved_dismissals):
                if (dismissal.workspace_id, dismissal.project_id, dismissal.branch_id) == (
                    workspace_id,
                    project_id,
                    parent_branch_id,
                ):
                    studio.saved_dismissals.append(_replace(dismissal, branch_id=branch_id))
        # And the dismissals for a character or a stretch of the story (CON-016).
        scoped = getattr(self, "scoped_dismissals", None)
        if scoped is not None:
            from dataclasses import replace as _scoped_copy

            for item in list(scoped.items):
                if (item.workspace_id, item.project_id, item.branch_id) == (
                    workspace_id,
                    project_id,
                    parent_branch_id,
                ):
                    scoped.items.append(_scoped_copy(item, branch_id=branch_id))
        # The checks kept as runs come too (consistency_runs is forked).
        runs = getattr(self, "consistency_runs", None)
        if runs is not None:
            from dataclasses import replace as _copy

            for run in list(runs.runs):
                if (run.workspace_id, run.project_id, run.branch_id) == (
                    workspace_id,
                    project_id,
                    parent_branch_id,
                ):
                    runs.runs.append(_copy(run, branch_id=branch_id))
        return head

    async def set_branch_status(
        self, *, workspace_id: str, project_id: str, branch_id: str, status: str
    ) -> None:
        assert self._journal is not None
        branch = self._journal.branches.get(branch_id)
        if branch is not None and not branch["is_root"]:
            branch["status"] = status

    def __init__(
        self,
        journal: InMemoryCanonJournal | None = None,
        media: Any = None,
        schemas: InMemorySchemaVersionStore | None = None,
    ) -> None:
        self._journal = journal
        self._media = media
        self._schemas = schemas
        self._stores: dict[str | None, dict[str, Any]] = {}
        self._committing: str | None = None
        self.heads: dict[tuple[str, str, str], int] = {}
        self.envelopes: dict[str, Any] = {}
        self.envelope_by_mutation: dict[str, str] = {}
        self.revisions: list[Any] = []
        self.watermarks: dict[tuple[str, str, str, str], Any] = {}
        self.tags: dict[tuple[str, str, str, str], Any] = {}
        self.tag_history: dict[tuple[str, str, str, str], list[Any]] = {}
        self.imports: dict[tuple[str, str, str], ArchiveImport] = {}

    def set_head(self, workspace_id: str, project_id: str, branch_id: str, head: int) -> None:
        self.heads[(workspace_id, project_id, branch_id)] = head

    async def begin_import(
        self,
        *,
        import_id: str,
        workspace_id: str,
        project_id: str,
        archive_checksum: str,
        format_version: str,
        commits_total: int,
        actor_user_id: str | None = None,
    ) -> ArchiveImport:
        key = (workspace_id, project_id, archive_checksum)
        existing = self.imports.get(key)
        now = utcnow()
        if existing is None:
            record = ArchiveImport(
                id=import_id,
                workspace_id=workspace_id,
                project_id=project_id,
                archive_checksum=archive_checksum,
                format_version=format_version,
                status=ImportStatus.IMPORTING,
                commits_total=commits_total,
                actor_user_id=actor_user_id,
                started_at=now,
                updated_at=now,
            )
        elif existing.status is ImportStatus.COMPLETED:
            record = existing
        else:
            record = existing.model_copy(
                update={"status": ImportStatus.IMPORTING, "error": None, "updated_at": now}
            )
        self.imports[key] = record
        return record

    def _import_by_id(self, import_id: str) -> tuple[tuple[str, str, str], ArchiveImport]:
        return next((key, item) for key, item in self.imports.items() if item.id == import_id)

    async def update_import(
        self,
        *,
        import_id: str,
        stage: str,
        commits_applied: int | None = None,
        object_keys: Any = None,
        created_records: dict[str, list[str]] | None = None,
    ) -> None:
        key, record = self._import_by_id(import_id)
        if record.status is not ImportStatus.IMPORTING:
            return
        update: dict[str, Any] = {"stage": stage, "updated_at": utcnow()}
        if created_records is not None:
            update["created_records"] = created_records
        if commits_applied is not None:
            update["commits_applied"] = commits_applied
        if object_keys is not None:
            update["object_keys"] = list(object_keys)
            update["objects_imported"] = len(object_keys)
        self.imports[key] = record.model_copy(update=update)

    async def finish_import(
        self, *, import_id: str, status: ImportStatus, error: str | None = None
    ) -> ArchiveImport:
        key, record = self._import_by_id(import_id)
        now = utcnow()
        completed = status is ImportStatus.COMPLETED
        self.imports[key] = record.model_copy(
            update={
                "status": status,
                "error": error,
                "stage": "verified" if completed else record.stage,
                "updated_at": now,
                "completed_at": now if completed else record.completed_at,
            }
        )
        return self.imports[key]

    async def record_removed_import(self, record: ArchiveImport) -> ArchiveImport:
        tombstone = record.model_copy(
            update={
                "status": ImportStatus.REMOVED,
                "stage": "removed",
                "commits_applied": 0,
                "objects_imported": 0,
                "object_keys": [],
                "updated_at": utcnow(),
            }
        )
        self.imports[(record.workspace_id, record.project_id, record.archive_checksum)] = tombstone
        return tombstone

    async def list_imports(self, *, workspace_id: str, project_id: str) -> list[ArchiveImport]:
        items = [
            item
            for (w, p, _), item in self.imports.items()
            if w == workspace_id and p == project_id
        ]
        return sorted(items, key=lambda item: item.updated_at, reverse=True)

    async def purge_project(self, *, workspace_id: str, project_id: str) -> int:
        return self._purge(workspace_id, project_id)

    async def purge_workspace(self, *, workspace_id: str) -> int:
        return self._purge(workspace_id, None)

    async def purge_import(
        self, *, workspace_id: str, project_id: str, created_records: dict[str, list[str]]
    ) -> int:
        """Remove imported canon and only the schema/media records the import created."""
        schemas, media, imports = self._schemas, self._media, dict(self.imports)
        self._schemas, self._media = None, None
        try:
            removed = self._purge(workspace_id, project_id)
        finally:
            self._schemas, self._media = schemas, media
        self.imports = imports
        if schemas is not None:
            doomed = set(created_records.get("schema_versions", []))
            kept = [
                v
                for v in schemas.versions
                if not (
                    v.workspace_id == workspace_id and v.project_id == project_id and v.id in doomed
                )
            ]
            removed += len(schemas.versions) - len(kept)
            schemas.versions[:] = kept
        if media is not None:
            for store, key in ((media.versions, "asset_versions"), (media.assets, "assets")):
                for record_id in created_records.get(key, []):
                    if store.pop((workspace_id, project_id, record_id), None) is not None:
                        removed += 1
            gone_assets = set(created_records.get("assets", []))
            gone_versions = set(created_records.get("asset_versions", []))
            media.events = [
                e
                for e in media.events
                if not (
                    e.workspace_id == workspace_id
                    and e.project_id == project_id
                    and (e.asset_id in gone_assets or e.asset_version_id in gone_versions)
                )
            ]
        return removed

    def _purge(self, workspace_id: str, project_id: str | None) -> int:
        def owned(ws: str, pr: str) -> bool:
            return ws == workspace_id and (project_id is None or pr == project_id)

        removed = 0
        for store in (
            self.heads,
            self.entities,
            self.facts,
            self.beliefs,
            self.events,
            self.plan_items,
            self.asset_links,
            self.watermarks,
            self.tags,
            self.tag_history,
            self.imports,
        ):
            # Watermarks are keyed (projection, ws, pr, branch); the rest lead with (ws, pr).
            offset = 1 if store is self.watermarks else 0
            for key in [k for k in store if owned(k[offset], k[offset + 1])]:
                del store[key]
                removed += 1
        commit_ids = {
            cid for cid, env in self.envelopes.items() if owned(env.workspace_id, env.project_id)
        }
        for commit_id in commit_ids:
            mutation_id = self.envelopes.pop(commit_id).mutation_id
            self.envelope_by_mutation.pop(mutation_id, None)
            self.provenance.pop(f"prov_{commit_id}", None)
            removed += 1
        self.evidence = [item for item in self.evidence if item.commit_id not in commit_ids]
        before = len(self.revisions) + len(self.record_versions)
        self.revisions = [r for r in self.revisions if not owned(r.workspace_id, r.project_id)]
        self.record_versions = [
            r for r in self.record_versions if not owned(r.workspace_id, r.project_id)
        ]
        removed += before - len(self.revisions) - len(self.record_versions)
        if self._journal is not None:
            removed += self._journal.purge(workspace_id, project_id)
        if self._schemas is not None:
            kept = [v for v in self._schemas.versions if not owned(v.workspace_id, v.project_id)]
            removed += len(self._schemas.versions) - len(kept)
            self._schemas.versions[:] = kept
        if self._media is not None:
            removed += self._media.purge(workspace_id, project_id)
        return removed

    async def get_head(self, *, workspace_id: str, project_id: str, branch_id: str) -> int:
        return self.heads.get((workspace_id, project_id, branch_id), 0)

    async def apply_commit(
        self,
        *,
        envelope: Any,
        operations: list[Any],
        validator: Any = None,
        origin: str = "author",
    ) -> int:
        previous = self._committing
        self._committing = self._branch_namespace(envelope.branch_id)
        try:
            with working_in(self._committing):
                return await self._apply_commit(
                    envelope=envelope, operations=operations, validator=validator, origin=origin
                )
        finally:
            self._committing = previous

    async def _apply_commit(
        self,
        *,
        envelope: Any,
        operations: list[Any],
        validator: Any = None,
        origin: str = "author",
    ) -> int:
        existing = self.envelopes.get(envelope.commit_id)
        if existing is not None:
            if existing.checksum != envelope.checksum:
                raise RevisionConflict("commit id reused with a different checksum")
            return existing.revision
        previous_commit_id = self.envelope_by_mutation.get(envelope.mutation_id)
        if previous_commit_id is not None:
            raise RevisionConflict(
                f"mutation {envelope.mutation_id!r} already committed as {previous_commit_id!r}"
            )
        if (
            canonicalize_operations(operations) != envelope.canonical
            or operations_checksum(operations) != envelope.checksum
        ):
            raise RevisionConflict("commit envelope does not match supplied operations")
        mutation = self._journal.mutations.get(envelope.mutation_id) if self._journal else None
        effective_origin = origin
        intent = MutationIntent.EDIT.value
        status = MutationStatus.DRAFT
        if mutation is not None:
            if (
                mutation.workspace_id != envelope.workspace_id
                or mutation.project_id != envelope.project_id
                or mutation.branch_id != envelope.branch_id
                or mutation.expected_head != envelope.expected_head
                or mutation.schema_version_id != envelope.schema_version_id
            ):
                raise RevisionConflict("commit envelope does not match its mutation set")
            assert self._journal is not None
            accepted = effective_operations(
                await self._journal.list_operations(mutation_id=envelope.mutation_id),
                await self._journal.list_operation_decisions(mutation_id=envelope.mutation_id),
            )
            stored_operations, _origin = await expand_merges(
                [parse_operation(item.payload) for item in accepted],
                _MemoryCanonReader(
                    self, envelope.workspace_id, envelope.project_id, envelope.branch_id
                ),
            )
            resolved_stored, _ = resolve_temporary_ids(
                stored_operations,
                new_id=lambda kind, temporary_id: format_canon_id(
                    kind,
                    hashlib.sha256(f"{envelope.mutation_id}:{temporary_id}".encode()).hexdigest()[
                        :16
                    ],
                ),
            )
            if canonicalize_operations(resolved_stored) != envelope.canonical:
                raise RevisionConflict("commit operations do not match the reviewed mutation")
            effective_origin = mutation.origin
            intent = mutation.intent
            status = mutation.status
            delegation = getattr(self, "delegation", None)
            if delegation is not None:
                refused = await delegation.rule_off_message(
                    mutation.workspace_id, mutation.project_id, mutation.id
                )
                if refused:
                    raise CanonValidationError(
                        DomainValidationReport(
                            findings=(
                                ValidationFinding(
                                    ValidationSeverity.INTEGRITY, "kept_rule_off", refused
                                ),
                            )
                        )
                    )
        gate = review_gate(
            origin=effective_origin,
            intent=intent,
            status=status,
            operation_types=[operation.op for operation in operations],
        )
        if not gate.ok:
            raise CanonValidationError(
                DomainValidationReport(
                    findings=(
                        ValidationFinding(
                            ValidationSeverity.INTEGRITY, gate.code or "", gate.message
                        ),
                    )
                )
            )
        key = (envelope.workspace_id, envelope.project_id, envelope.branch_id)
        head = self.heads.get(key, 0)
        if head != envelope.expected_head:
            raise HeadConflict(f"expected head {envelope.expected_head}, found {head}")
        validator = validator or _MEMORY_DEFAULT_VALIDATOR
        reader = _MemoryCanonReader(
            self, envelope.workspace_id, envelope.project_id, envelope.branch_id
        )
        compiled = None
        if envelope.schema_version_id is not None and self._schemas is not None:
            version = await self._schemas.get(
                workspace_id=envelope.workspace_id,
                project_id=envelope.project_id,
                version_id=envelope.schema_version_id,
            )
            if version is None or version.status is not SchemaVersionStatus.PUBLISHED:
                raise CanonValidationError(
                    DomainValidationReport(
                        findings=(
                            ValidationFinding(
                                ValidationSeverity.INTEGRITY,
                                "invalid_schema_version",
                                "commit schema version is missing, outside this project, or unpublished",
                            ),
                        )
                    )
                )
            compiled = compile_definition(
                version.definition,
                workspace_id=envelope.workspace_id,
                project_id=envelope.project_id,
            )
        context = ValidationContext(
            workspace_id=envelope.workspace_id,
            project_id=envelope.project_id,
            branch_id=envelope.branch_id,
            schema_version_id=envelope.schema_version_id,
            head_revision=envelope.expected_head,
            reader=reader,
            allowed_entity_types=frozenset(
                set(CORE_ENTITY_TYPES) | (set(compiled.entity_types) if compiled else set())
            ),
            entity_attribute_models=compiled.entity_models if compiled else {},
            predicate_specs=compiled.predicate_specs if compiled else {},
        )
        report = await validator.validate(operations, context=context)
        if not report.ok:
            raise CanonValidationError(report)
        reserved = head + 1
        self.envelopes[envelope.commit_id] = envelope
        self.envelope_by_mutation[envelope.mutation_id] = envelope.commit_id
        provenance_id = f"prov_{envelope.commit_id}"
        self.provenance[provenance_id] = Provenance(
            id=provenance_id,
            workspace_id=envelope.workspace_id,
            project_id=envelope.project_id,
            branch_id=envelope.branch_id,
            origin=OriginKind(effective_origin),
            actor_user_id=envelope.actor_user_id,
            created_at=utcnow(),
        )
        for ordinal, operation in enumerate(operations):
            self._apply(envelope, operation, provenance_id, reserved)
            for evidence in operation.evidence:
                self.evidence.append(
                    EvidenceRecord(
                        record_type=_CANON_RECORD_TYPE.get(operation.op, operation.op),
                        record_id=operation.id,
                        revision=reserved,
                        commit_id=envelope.commit_id,
                        ordinal=ordinal,
                        source_artefact_id=evidence.source_artefact_id,
                        location=evidence.location,
                    )
                )
            self.record_versions.append(
                RecordVersion(
                    workspace_id=envelope.workspace_id,
                    project_id=envelope.project_id,
                    branch_id=envelope.branch_id,
                    record_type=_CANON_RECORD_TYPE.get(operation.op, operation.op),
                    record_id=operation.id,
                    revision=reserved,
                    operation_type=operation.op,
                    commit_id=envelope.commit_id,
                    change=operation.model_dump(mode="json"),
                    created_at=utcnow(),
                    ordinal=ordinal,
                )
            )
        self.heads[key] = reserved
        self.revisions.append(
            CanonRevision(
                revision=reserved,
                workspace_id=envelope.workspace_id,
                project_id=envelope.project_id,
                branch_id=envelope.branch_id,
                commit_id=envelope.commit_id,
                expected_head=envelope.expected_head,
                result_head=reserved,
                schema_version_id=envelope.schema_version_id,
                envelope_checksum=envelope.checksum,
                created_at=utcnow(),
            )
        )
        if self._journal is not None:
            if mutation is not None:
                if status is MutationStatus.DRAFT:
                    await self._journal.record_decision(
                        mutation_id=mutation.id,
                        decision="approved",
                        actor_user_id=envelope.actor_user_id,
                        note=f"{effective_origin}-direct",
                        transition_to=MutationStatus.APPROVED,
                    )
                self._journal.apply_transition(
                    mutation.id,
                    MutationStatus.COMMITTED,
                    actor_user_id=envelope.actor_user_id,
                    reason=f"committed as revision {reserved}",
                )
            self._journal.outbox.setdefault(
                f"commit:{envelope.commit_id}",
                OutboxMessage(
                    id=f"ob_{envelope.commit_id}",
                    topic="canon.commit",
                    workspace_id=envelope.workspace_id,
                    project_id=envelope.project_id,
                    branch_id=envelope.branch_id,
                    revision=reserved,
                    payload={"commit_id": envelope.commit_id},
                    idempotency_key=f"commit:{envelope.commit_id}",
                    created_at=utcnow(),
                ),
            )
        return reserved

    async def get_envelope(self, commit_id: str) -> Any:
        return self.envelopes.get(commit_id)

    async def list_commits(
        self,
        *,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        after_revision: int = 0,
        limit: int = 100,
    ) -> list[Any]:
        matches = [
            revision
            for revision in self.revisions
            if revision.workspace_id == workspace_id
            and revision.project_id == project_id
            and revision.branch_id == branch_id
            and revision.revision > after_revision
        ]
        matches.sort(key=lambda revision: revision.revision)
        return matches[:limit]

    def _apply(self, envelope: Any, operation: Any, provenance_id: str, revision: int) -> None:
        ws, pr, br = envelope.workspace_id, envelope.project_id, envelope.branch_id
        key = (ws, pr, operation.id)
        if isinstance(operation, CreateEntityOp):
            self.entities[key] = CanonEntity(
                id=operation.id,
                workspace_id=ws,
                project_id=pr,
                branch_id=br,
                type=operation.type,
                name=operation.name,
                slug=operation.slug,
                schema_version_id=envelope.schema_version_id or "",
                created_revision=revision,
                attributes=dict(operation.attributes),
                aliases=list(operation.aliases),
                external_ids=dict(operation.external_ids),
            )
        elif isinstance(operation, UpdateEntityOp) and key in self.entities:
            current = self.entities[key]
            self.entities[key] = current.model_copy(
                update={
                    "name": current.name if operation.name is None else operation.name,
                    "slug": operation.slug if operation.slug is not None else current.slug,
                    "attributes": operation.attributes
                    if operation.attributes is not None
                    else current.attributes,
                    "aliases": operation.aliases
                    if operation.aliases is not None
                    else current.aliases,
                    "external_ids": operation.external_ids
                    if operation.external_ids is not None
                    else current.external_ids,
                    "type": current.type if operation.type is None else operation.type,
                }
            )
        elif isinstance(operation, RetireEntityOp) and key in self.entities:
            current = self.entities[key]
            self.entities[key] = current.model_copy(
                update={"lifecycle": EntityLifecycle.RETIRED, "retired_revision": revision}
            )
        elif isinstance(operation, RestoreEntityOp) and key in self.entities:
            self.entities[key] = self.entities[key].model_copy(
                update={"lifecycle": EntityLifecycle.ACTIVE, "retired_revision": None}
            )
        elif isinstance(operation, AssertFactOp):
            self.facts[key] = Fact(
                id=operation.id,
                workspace_id=ws,
                project_id=pr,
                branch_id=br,
                proposition=operation.proposition,
                confidence=operation.confidence,
                provenance_id=provenance_id,
                created_revision=revision,
                valid_from=operation.valid_from,
                valid_until=operation.valid_until,
                starts=_stored_time(operation.starts),
                ends=_stored_time(operation.ends),
                visibility=operation.visibility or FactVisibility.ORDINARY,
            )
        elif isinstance(operation, AmendFactOp) and key in self.facts:
            current_fact = self.facts[key]
            self.facts[key] = current_fact.model_copy(
                update={
                    "starts": current_fact.starts
                    if operation.starts is None
                    else _stored_time(operation.starts),
                    "ends": current_fact.ends
                    if operation.ends is None
                    else _stored_time(operation.ends),
                    "visibility": operation.visibility or current_fact.visibility,
                }
            )
        elif isinstance(operation, CloseFactOp) and key in self.facts:
            self.facts[key] = self.facts[key].model_copy(
                update={"status": FactStatus.CLOSED, "closed_revision": revision}
            )
        elif isinstance(operation, AssertBeliefOp):
            self.beliefs[key] = Belief(
                id=operation.id,
                workspace_id=ws,
                project_id=pr,
                branch_id=br,
                holder_entity_id=operation.holder_entity_id,
                proposition=operation.proposition,
                confidence=operation.confidence,
                acquisition_provenance_id=provenance_id,
                source_ref=operation.source_ref,
                created_revision=revision,
            )
        elif isinstance(operation, CloseBeliefOp) and key in self.beliefs:
            self.beliefs[key] = self.beliefs[key].model_copy(update={"closed_revision": revision})
        elif isinstance(operation, CreateEventOp):
            self.events[key] = Event(
                id=operation.id,
                workspace_id=ws,
                project_id=pr,
                branch_id=br,
                kind=operation.kind,
                name=operation.name,
                participants=list(operation.participants),
                location_ids=list(operation.location_ids),
                cause_ids=list(operation.cause_ids),
                outcome_ids=list(operation.outcome_ids),
                world_position=operation.world_position,
                when=_stored_time(operation.when),
                claim=operation.claim,
                scene_id=operation.scene_id,
                premises=list(operation.premises),
                provenance_id=provenance_id,
                created_revision=revision,
            )
        elif isinstance(operation, AmendEventOp) and key in self.events:
            current = self.events[key]
            self.events[key] = current.model_copy(
                update={
                    "participants": current.participants
                    if operation.participants is None
                    else list(operation.participants),
                    "location_ids": current.location_ids
                    if operation.location_ids is None
                    else list(operation.location_ids),
                    "cause_ids": current.cause_ids
                    if operation.cause_ids is None
                    else list(operation.cause_ids),
                    "outcome_ids": current.outcome_ids
                    if operation.outcome_ids is None
                    else list(operation.outcome_ids),
                    "claim": current.claim if operation.claim is None else operation.claim,
                    "name": current.name if operation.name is None else operation.name,
                    "world_position": operation.world_position
                    if operation.world_position is not None
                    else current.world_position,
                    "when": current.when
                    if operation.when is None
                    else _stored_time(operation.when),
                }
            )
        elif isinstance(operation, RetireEventOp) and key in self.events:
            self.events[key] = self.events[key].model_copy(update={"retired_revision": revision})
        elif isinstance(operation, CreatePlanItemOp):
            self.plan_items[key] = plan_item_from_create(
                operation,
                workspace_id=ws,
                project_id=pr,
                branch_id=br,
                provenance_id=provenance_id,
                revision=revision,
            )
        elif isinstance(operation, UpdatePlanItemOp) and key in self.plan_items:
            self.plan_items[key] = apply_plan_update(self.plan_items[key], operation)
        elif isinstance(operation, RetirePlanItemOp) and key in self.plan_items:
            self.plan_items[key] = self.plan_items[key].model_copy(
                update={"retired_revision": revision}
            )
        elif isinstance(operation, LinkAssetOp):
            self.asset_links[key] = AssetLink(
                id=operation.id,
                workspace_id=ws,
                project_id=pr,
                branch_id=br,
                asset_id=operation.asset_id,
                asset_version_id=operation.asset_version_id,
                role=LinkRole(operation.role),
                target_record_type=operation.target_record_type,
                target_record_id=operation.target_id,
                provenance_id=provenance_id,
                created_revision=revision,
                visibility=operation.visibility or "open",
                known_by=list(operation.known_by),
                applies_from=operation.applies_from,
                applies_until=operation.applies_until,
                shows_version=bool(operation.shows_version),
            )
        elif isinstance(operation, RetireAssetLinkOp) and key in self.asset_links:
            self.asset_links[key] = self.asset_links[key].model_copy(
                update={"retired_revision": revision}
            )

    async def get_link(self, *, workspace_id: str, project_id: str, link_id: str):
        return self.asset_links.get((workspace_id, project_id, link_id))

    async def list_links(
        self,
        *,
        workspace_id: str,
        project_id: str,
        asset_id: str | None = None,
        branch_id: str | None = None,
        include_retired: bool = False,
        limit: int = 100,
        offset: int = 0,
        target_record_id: str | None = None,
    ):
        items = [
            link
            for (w, p, _), link in self.asset_links.items()
            if w == workspace_id
            and p == project_id
            and (target_record_id is None or link.target_record_id == target_record_id)
            and (asset_id is None or link.asset_id == asset_id)
            and (branch_id is None or link.branch_id == branch_id)
            and (include_retired or link.retired_revision is None)
        ]
        items.sort(key=lambda link: (link.created_revision, link.id), reverse=True)
        return items[offset : offset + limit]

    async def get_entity(self, *, workspace_id: str, project_id: str, entity_id: str):
        return self.entities.get((workspace_id, project_id, entity_id))

    async def list_entities(
        self,
        *,
        workspace_id: str,
        project_id: str,
        entity_type: str | None = None,
        include_retired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ):
        items = [
            e
            for (w, p, _), e in self.entities.items()
            if w == workspace_id
            and p == project_id
            and (entity_type is None or e.type == entity_type)
            and (include_retired or e.lifecycle is EntityLifecycle.ACTIVE)
        ]
        items.sort(key=lambda e: e.name)
        return items[offset : offset + limit]

    async def search_entities(self, *, workspace_id: str, project_id: str, query: str, **kwargs):
        needle = query.lower()
        entity_type = kwargs.get("entity_type")
        items = [
            e
            for (w, p, _), e in self.entities.items()
            if w == workspace_id
            and p == project_id
            and (
                needle in e.name.lower()
                or needle in (e.slug or "")
                or any(needle in alias.lower() for alias in e.aliases)
            )
            and (entity_type is None or e.type == entity_type)
            and (kwargs.get("include_retired") or e.lifecycle is EntityLifecycle.ACTIVE)
        ]
        return sorted(items, key=lambda e: e.name)[: kwargs.get("limit", 50)]

    def _scoped_entities(self, workspace_id: str, project_id: str) -> list[CanonEntity]:
        return [
            e for (w, p, _), e in self.entities.items() if w == workspace_id and p == project_id
        ]

    async def get_entity_by_slug(self, *, workspace_id: str, project_id: str, slug: str):
        return next(
            (
                e
                for e in self._scoped_entities(workspace_id, project_id)
                if e.slug == slug and e.lifecycle is EntityLifecycle.ACTIVE
            ),
            None,
        )

    async def get_entity_by_external_id(
        self, *, workspace_id: str, project_id: str, namespace: str, value: str
    ):
        return next(
            (
                e
                for e in self._scoped_entities(workspace_id, project_id)
                if e.external_ids.get(namespace) == value
            ),
            None,
        )

    async def find_entities_by_name(
        self, *, workspace_id: str, project_id: str, entity_type: str, name: str
    ):
        key = normalize_name(name)
        return [
            e
            for e in self._scoped_entities(workspace_id, project_id)
            if e.type == entity_type
            and e.lifecycle is EntityLifecycle.ACTIVE
            and normalize_name(e.name) == key
        ]

    async def list_active_references(
        self, *, workspace_id: str, project_id: str, entity_id: str
    ) -> list[tuple[str, str]]:
        def scoped(records: dict) -> list[Any]:
            return [r for (w, p, _), r in records.items() if w == workspace_id and p == project_id]

        def refers(proposition: Any) -> bool:
            obj = proposition.object
            return (
                proposition.subject_id == entity_id or getattr(obj, "entity_id", None) == entity_id
            )

        found: list[tuple[str, str]] = []
        found += [
            ("fact", fact.id)
            for fact in scoped(self.facts)
            if getattr(fact.status, "value", fact.status) == "asserted" and refers(fact.proposition)
        ]
        found += [
            ("belief", belief.id)
            for belief in scoped(self.beliefs)
            if belief.closed_revision is None
            and (belief.holder_entity_id == entity_id or refers(belief.proposition))
        ]
        found += [
            ("event", event.id)
            for event in scoped(self.events)
            if event.retired_revision is None
            and (
                any(p.entity_id == entity_id for p in event.participants)
                or entity_id in event.location_ids
                or entity_id in event.cause_ids
                or entity_id in event.outcome_ids
            )
        ]
        found += [
            ("asset_link", link.id)
            for link in scoped(self.asset_links)
            if link.retired_revision is None and link.target_record_id == entity_id
        ]
        return sorted(found)

    async def count_active_references(
        self, *, workspace_id: str, project_id: str, entity_id: str
    ) -> int:
        return len(
            await self.list_active_references(
                workspace_id=workspace_id, project_id=project_id, entity_id=entity_id
            )
        )

    async def list_evidence(
        self,
        *,
        workspace_id: str,
        project_id: str,
        record_type: str | None = None,
        record_id: str | None = None,
        commit_id: str | None = None,
    ) -> list[EvidenceRecord]:
        scoped_commits = {
            cid
            for cid, envelope in self.envelopes.items()
            if envelope.workspace_id == workspace_id and envelope.project_id == project_id
        }
        return [
            item
            for item in self.evidence
            if item.commit_id in scoped_commits
            and (record_type is None or item.record_type == record_type)
            and (record_id is None or item.record_id == record_id)
            and (commit_id is None or item.commit_id == commit_id)
        ]

    async def get_fact(self, *, workspace_id: str, project_id: str, fact_id: str):
        return self.facts.get((workspace_id, project_id, fact_id))

    async def list_facts(
        self,
        *,
        workspace_id: str,
        project_id: str,
        include_closed: bool = False,
        subject_id: str | None = None,
        predicate: str | None = None,
        limit: int = 50,
        offset: int = 0,
        object_entity_id: str | None = None,
    ):
        items = [
            f
            for (w, p, _), f in self.facts.items()
            if w == workspace_id
            and p == project_id
            and (
                object_entity_id is None
                or getattr(f.proposition.object, "entity_id", None) == object_entity_id
            )
            and (include_closed or f.closed_revision is None)
            and (subject_id is None or f.proposition.subject_id == subject_id)
            and (predicate is None or f.proposition.predicate == predicate)
        ]
        return items[offset : offset + limit]

    async def get_belief(self, *, workspace_id: str, project_id: str, belief_id: str):
        return self.beliefs.get((workspace_id, project_id, belief_id))

    async def list_beliefs(
        self,
        *,
        workspace_id: str,
        project_id: str,
        holder_entity_id: str | None = None,
        include_closed: bool = False,
        limit: int = 50,
        offset: int = 0,
    ):
        items = [
            b
            for (w, p, _), b in self.beliefs.items()
            if w == workspace_id
            and p == project_id
            and (include_closed or b.closed_revision is None)
            and (holder_entity_id is None or b.holder_entity_id == holder_entity_id)
        ]
        return items[offset : offset + limit]

    async def get_event(self, *, workspace_id: str, project_id: str, event_id: str):
        return self.events.get((workspace_id, project_id, event_id))

    async def get_plan_item(self, *, workspace_id: str, project_id: str, plan_id: str):
        return self.plan_items.get((workspace_id, project_id, plan_id))

    async def list_plan_items(
        self,
        *,
        workspace_id: str,
        project_id: str,
        include_retired: bool = False,
        limit: int = 2000,
    ):
        items = [
            item
            for (w, p, _), item in self.plan_items.items()
            if w == workspace_id
            and p == project_id
            and (include_retired or item.retired_revision is None)
        ]
        items.sort(key=lambda item: (item.position, item.created_revision, item.id))
        return items[:limit]

    async def list_events(
        self,
        *,
        workspace_id: str,
        project_id: str,
        include_retired: bool = False,
        limit: int = 50,
        offset: int = 0,
        entity_id: str | None = None,
    ):
        items = [
            e
            for (w, p, _), e in self.events.items()
            if w == workspace_id
            and p == project_id
            and (include_retired or e.retired_revision is None)
            and (
                entity_id is None
                or any(participant.entity_id == entity_id for participant in e.participants)
                or entity_id in e.location_ids
                or entity_id in e.cause_ids
                or entity_id in e.outcome_ids
            )
        ]
        return items[offset : offset + limit]

    async def get_provenance(
        self, *, workspace_id: str, project_id: str, branch_id: str, provenance_id: str
    ):
        return self.provenance.get(provenance_id)

    async def list_record_versions(
        self,
        *,
        workspace_id: str,
        project_id: str,
        record_type: str | None = None,
        record_id: str | None = None,
        min_revision: int | None = None,
        max_revision: int | None = None,
        limit: int = 100,
    ):
        matches = [
            rv
            for rv in self.record_versions
            if rv.workspace_id == workspace_id
            and rv.project_id == project_id
            and (record_type is None or rv.record_type == record_type)
            and (record_id is None or rv.record_id == record_id)
            and (min_revision is None or rv.revision > min_revision)
            and (max_revision is None or rv.revision <= max_revision)
        ]
        # Newest first, like the PostgreSQL adapter.
        matches.sort(key=lambda rv: (rv.revision, rv.ordinal), reverse=True)
        return matches[:limit]

    async def get_watermark(
        self, *, projection: str, workspace_id: str, project_id: str, branch_id: str
    ):
        return self.watermarks.get((projection, workspace_id, project_id, branch_id))

    async def set_watermark(self, watermark: Any) -> None:
        key = (
            watermark.projection,
            watermark.workspace_id,
            watermark.project_id,
            watermark.branch_id,
        )
        # Like the PostgreSQL adapter: only forward, unless a rebuild or a new layout.
        previous = self.watermarks.get(key)
        if (
            previous is not None
            and watermark.revision < previous.revision
            and watermark.status is not ProjectionStatus.REBUILDING
            and watermark.projection_schema_version == previous.projection_schema_version
        ):
            return
        self.watermarks[key] = watermark

    async def create_tag(self, tag: Any) -> Any:
        key = (tag.workspace_id, tag.project_id, tag.branch_id, tag.name)
        previous = self.tags.get(key)
        if previous is not None:
            entry = CanonTagHistoryEntry(
                workspace_id=previous.workspace_id,
                project_id=previous.project_id,
                branch_id=previous.branch_id,
                name=previous.name,
                revision=previous.revision,
                commit_id=previous.commit_id,
                replaced_at=datetime.now(UTC),
            )
            self.tag_history.setdefault(key, []).append(entry)
        stored = CanonTag(
            workspace_id=tag.workspace_id,
            project_id=tag.project_id,
            branch_id=tag.branch_id,
            name=tag.name,
            revision=tag.revision,
            commit_id=tag.commit_id,
            created_by_user_id=tag.created_by_user_id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.tags[key] = stored
        return stored

    async def get_tag(
        self, *, workspace_id: str, project_id: str, branch_id: str, name: str
    ) -> Any:
        return self.tags.get((workspace_id, project_id, branch_id, name))

    async def list_tags(self, *, workspace_id: str, project_id: str, branch_id: str) -> list[Any]:
        return sorted(
            (
                tag
                for (w, p, b, _name), tag in self.tags.items()
                if (w, p, b) == (workspace_id, project_id, branch_id)
            ),
            key=lambda tag: tag.name,
        )

    async def list_tag_history(
        self, *, workspace_id: str, project_id: str, branch_id: str, name: str
    ) -> list[Any]:
        return list(reversed(self.tag_history.get((workspace_id, project_id, branch_id, name), [])))

    async def delete_tag(
        self, *, workspace_id: str, project_id: str, branch_id: str, name: str
    ) -> bool:
        return self.tags.pop((workspace_id, project_id, branch_id, name), None) is not None

    async def health(self) -> bool:
        return True


class InMemoryMediaStore:
    """In-memory media identity store for tests."""

    def __init__(self) -> None:
        self.assets: dict[tuple[str, str, str], MediaAsset] = {}
        self.versions: dict[tuple[str, str, str], MediaAssetVersion] = {}
        self.events: list[MediaLifecycleEvent] = []
        self._counter = 0
        # Two-step uploads (AST-002).
        self.uploads: dict[tuple[str, str, str], PictureUpload] = {}

    async def create_upload(
        self,
        upload: PictureUpload,
        *,
        max_per_person: int,
        max_per_workspace: int,
    ) -> PictureUpload | None:
        mine = await self.open_uploads(
            workspace_id=upload.workspace_id, actor_user_id=upload.actor_user_id or ""
        )
        everyone = await self.open_uploads(workspace_id=upload.workspace_id)
        if mine >= max_per_person or everyone >= max_per_workspace:
            return None
        self.uploads[(upload.workspace_id, upload.project_id, upload.id)] = upload
        return upload

    async def get_upload(
        self, *, workspace_id: str, project_id: str, upload_id: str
    ) -> PictureUpload | None:
        return self.uploads.get((workspace_id, project_id, upload_id))

    async def open_uploads(self, *, workspace_id: str, actor_user_id: str | None = None) -> int:
        now = utcnow()
        return sum(
            1
            for upload in self.uploads.values()
            if upload.workspace_id == workspace_id
            and (actor_user_id is None or (upload.actor_user_id or "") == actor_user_id)
            and upload.state in (UploadState.WAITING, UploadState.RECEIVED, UploadState.FINISHING)
            and upload.expires_at > now
        )

    async def move_upload(
        self,
        *,
        workspace_id: str,
        project_id: str,
        upload_id: str,
        from_state: UploadState,
        to_state: UploadState,
        staged_key: str | None = None,
        content_type: str | None = None,
        asset_id: str | None = None,
        version_id: str | None = None,
        link_id: str | None = None,
        unexpired: bool = True,
    ) -> PictureUpload | None:
        key = (workspace_id, project_id, upload_id)
        upload = self.uploads.get(key)
        if upload is None or upload.state is not from_state:
            return None
        if unexpired and upload.expires_at <= utcnow():
            return None
        changes: dict[str, Any] = {"state": to_state}
        for name, value in (
            ("staged_key", staged_key),
            ("content_type", content_type),
            ("asset_id", asset_id),
            ("version_id", version_id),
            ("link_id", link_id),
        ):
            if value is not None:
                changes[name] = value
        if to_state is UploadState.FINISHED:
            changes["finished_at"] = utcnow()
        self.uploads[key] = moved = upload.model_copy(update=changes)
        return moved

    async def expire_uploads(
        self, *, limit: int = 100, finishing_grace_seconds: int = 900
    ) -> list[PictureUpload]:
        now = utcnow()
        grace = timedelta(seconds=finishing_grace_seconds)
        due = [
            key
            for key, upload in self.uploads.items()
            if (
                upload.state in (UploadState.WAITING, UploadState.RECEIVED)
                and upload.expires_at <= now
            )
            or (upload.state is UploadState.FINISHING and upload.expires_at <= now - grace)
        ][:limit]
        out = []
        for key in due:
            self.uploads[key] = expired = self.uploads[key].model_copy(
                update={"state": UploadState.EXPIRED}
            )
            out.append(expired)
        return out

    def _record(
        self,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        action: MediaLifecycleAction,
        *,
        asset_version_id: str | None = None,
        actor_user_id: str | None = None,
        canon_revision: int | None = None,
        item_count: int | None = None,
    ) -> None:
        self._counter += 1
        self.events.append(
            MediaLifecycleEvent(
                id=f"opn_media_{self._counter:04d}",
                workspace_id=workspace_id,
                project_id=project_id,
                asset_id=asset_id,
                asset_version_id=asset_version_id,
                action=action,
                actor_user_id=actor_user_id,
                canon_revision=canon_revision,
                item_count=item_count,
            )
        )

    async def record_notes_removed(
        self,
        *,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        count: int,
        actor_user_id: str | None = None,
        canon_revision: int | None = None,
    ) -> None:
        self._record(
            workspace_id,
            project_id,
            asset_id,
            MediaLifecycleAction.NOTES_REMOVED,
            actor_user_id=actor_user_id,
            canon_revision=canon_revision,
            item_count=count,
        )

    async def list_lifecycle_events(
        self, *, workspace_id: str, project_id: str, asset_id: str
    ) -> list[MediaLifecycleEvent]:
        return [
            event
            for event in self.events
            if event.workspace_id == workspace_id
            and event.project_id == project_id
            and event.asset_id == asset_id
        ]

    def purge(self, workspace_id: str, project_id: str | None) -> int:
        def owned(key: tuple) -> bool:
            return key[0] == workspace_id and (project_id is None or key[1] == project_id)

        removed = 0
        for store in (self.assets, self.versions):
            for key in [k for k in store if owned(k)]:
                del store[key]
                removed += 1
        self.events = [e for e in self.events if not owned((e.workspace_id, e.project_id))]
        for key in [k for k in self.uploads if owned(k)]:
            del self.uploads[key]
        return removed

    async def create_asset(
        self,
        *,
        workspace_id: str,
        project_id: str,
        name: str,
        actor_user_id: str | None = None,
    ) -> MediaAsset:
        self._counter += 1
        asset_id = f"ast_{self._counter:04d}"
        asset = MediaAsset(
            id=asset_id,
            workspace_id=workspace_id,
            project_id=project_id,
            name=name,
            created_revision=0,
        )
        self.assets[(workspace_id, project_id, asset_id)] = asset
        self._record(
            workspace_id,
            project_id,
            asset_id,
            MediaLifecycleAction.ASSET_REGISTERED,
            actor_user_id=actor_user_id,
        )
        return asset

    async def get_asset(
        self, *, workspace_id: str, project_id: str, asset_id: str
    ) -> MediaAsset | None:
        return self.assets.get((workspace_id, project_id, asset_id))

    async def list_assets(
        self, *, workspace_id: str, project_id: str, limit: int = 100, offset: int = 0
    ) -> list[MediaAsset]:
        items = [a for (w, p, _), a in self.assets.items() if w == workspace_id and p == project_id]
        items.sort(key=lambda a: a.id)
        return items[offset : offset + limit]

    async def import_asset(self, asset: MediaAsset) -> MediaAsset:
        key = (asset.workspace_id, asset.project_id, asset.id)
        if key not in self.assets:
            self.assets[key] = asset
            self._record(
                asset.workspace_id,
                asset.project_id,
                asset.id,
                MediaLifecycleAction.ASSET_IMPORTED,
                canon_revision=asset.created_revision,
            )
        return self.assets[key]

    async def import_version(self, version: MediaAssetVersion) -> MediaAssetVersion:
        key = (version.workspace_id, version.project_id, version.id)
        if key not in self.versions:
            self.versions[key] = version
            self._record(
                version.workspace_id,
                version.project_id,
                version.asset_id,
                MediaLifecycleAction.VERSION_IMPORTED,
                asset_version_id=version.id,
                canon_revision=version.created_revision,
            )
        return self.versions[key]

    async def retire_asset(
        self,
        *,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        retired_revision: int | None = None,
        actor_user_id: str | None = None,
    ) -> MediaAsset | None:
        from backend.domain.media import AssetLifecycle

        key = (workspace_id, project_id, asset_id)
        asset = self.assets.get(key)
        if asset is None:
            return None
        if asset.lifecycle is not AssetLifecycle.ACTIVE:
            return asset
        self._record(
            workspace_id,
            project_id,
            asset_id,
            MediaLifecycleAction.ASSET_RETIRED,
            actor_user_id=actor_user_id,
            canon_revision=retired_revision,
        )
        retired = asset.model_copy(
            update={
                "lifecycle": AssetLifecycle.RETIRED,
                "retired_revision": retired_revision,
            }
        )
        self.assets[key] = retired
        return retired

    async def retire_version(
        self,
        *,
        workspace_id: str,
        project_id: str,
        version_id: str,
        retired_revision: int | None = None,
        actor_user_id: str | None = None,
    ) -> MediaAssetVersion | None:
        key = (workspace_id, project_id, version_id)
        version = self.versions.get(key)
        if version is None:
            return None
        if version.retired_revision is not None:
            return version
        self._record(
            workspace_id,
            project_id,
            version.asset_id,
            MediaLifecycleAction.VERSION_RETIRED,
            asset_version_id=version_id,
            actor_user_id=actor_user_id,
            canon_revision=retired_revision,
        )
        retired = version.model_copy(
            update={"retired_revision": retired_revision if retired_revision is not None else 0}
        )
        self.versions[key] = retired
        return retired

    async def delete_original(
        self,
        *,
        workspace_id: str,
        project_id: str,
        version_id: str,
        retired_revision: int | None = None,
        actor_user_id: str | None = None,
    ) -> MediaAssetVersion | None:
        key = (workspace_id, project_id, version_id)
        version = self.versions.get(key)
        if version is None or version.deleted_at is not None:
            return version
        self._record(
            workspace_id,
            project_id,
            version.asset_id,
            MediaLifecycleAction.ORIGINAL_DELETED,
            asset_version_id=version_id,
            actor_user_id=actor_user_id,
            canon_revision=retired_revision,
        )
        gone = version.model_copy(
            update={
                "deleted_at": datetime.now(UTC),
                "retired_revision": version.retired_revision
                if version.retired_revision is not None
                else (retired_revision if retired_revision is not None else 0),
            }
        )
        self.versions[key] = gone
        return gone

    async def delete_asset(
        self,
        *,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        retired_revision: int | None = None,
        actor_user_id: str | None = None,
    ) -> MediaAsset | None:
        from backend.domain.media import AssetLifecycle

        key = (workspace_id, project_id, asset_id)
        asset = self.assets.get(key)
        if asset is None or self._deletion_recorded(workspace_id, project_id, asset_id):
            return asset
        self._record(
            workspace_id,
            project_id,
            asset_id,
            MediaLifecycleAction.ASSET_DELETED,
            actor_user_id=actor_user_id,
            canon_revision=retired_revision,
        )
        gone = asset.model_copy(
            update={
                "lifecycle": AssetLifecycle.DELETED,
                "retired_revision": asset.retired_revision
                if asset.retired_revision is not None
                else (retired_revision if retired_revision is not None else 0),
            }
        )
        self.assets[key] = gone
        return gone

    def _deletion_recorded(self, workspace_id: str, project_id: str, asset_id: str) -> bool:
        return any(
            (event.workspace_id, event.project_id, event.asset_id)
            == (workspace_id, project_id, asset_id)
            and event.action is MediaLifecycleAction.ASSET_DELETED
            for event in self.events
        )

    async def hold_for_deletion(self, *, workspace_id, project_id, asset_id, lifecycle) -> bool:
        from backend.domain.media import AssetLifecycle

        key = (workspace_id, project_id, asset_id)
        asset = self.assets.get(key)
        if asset is None or asset.lifecycle is not lifecycle:
            return False
        self.assets[key] = asset.model_copy(update={"lifecycle": AssetLifecycle.DELETED})
        return True

    async def release_hold(self, *, workspace_id, project_id, asset_id, lifecycle) -> None:
        from backend.domain.media import AssetLifecycle

        key = (workspace_id, project_id, asset_id)
        asset = self.assets.get(key)
        if (
            asset is not None
            and asset.lifecycle is AssetLifecycle.DELETED
            and not self._deletion_recorded(workspace_id, project_id, asset_id)
        ):
            self.assets[key] = asset.model_copy(update={"lifecycle": lifecycle})

    async def object_keys_in_use(
        self, *, workspace_id, project_id, object_keys, except_asset_id
    ) -> set[str]:
        wanted = set(object_keys)
        return {
            version.object_key
            for (w, p, _), version in self.versions.items()
            if (w, p) == (workspace_id, project_id)
            and version.object_key in wanted
            and version.asset_id != except_asset_id
            and version.deleted_at is None
        }

    async def create_version(
        self,
        *,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        object_key: str,
        content_type: str,
        byte_length: int,
        checksum: str,
        source: str | None = None,
        licence: str | None = None,
        attribution: str | None = None,
        authority: str | None = None,
        visibility: str | None = None,
        created_revision: int = 0,
        actor_user_id: str | None = None,
    ) -> MediaAssetVersion:
        self._counter += 1
        version_id = f"asv_{self._counter:04d}"
        existing = [
            v
            for (w, p, _), v in self.versions.items()
            if w == workspace_id and p == project_id and v.asset_id == asset_id
        ]
        version = MediaAssetVersion(
            id=version_id,
            asset_id=asset_id,
            workspace_id=workspace_id,
            project_id=project_id,
            version=len(existing) + 1,
            object_key=object_key,
            content_type=content_type,
            byte_length=byte_length,
            checksum=checksum,
            source=source,
            licence=licence,
            attribution=attribution,
            authority=authority,
            visibility=visibility,
            created_revision=created_revision,
        )
        self.versions[(workspace_id, project_id, version_id)] = version
        self._record(
            workspace_id,
            project_id,
            asset_id,
            MediaLifecycleAction.VERSION_REGISTERED,
            asset_version_id=version_id,
            actor_user_id=actor_user_id,
            canon_revision=created_revision,
        )
        return version

    async def get_version(
        self, *, workspace_id: str, project_id: str, version_id: str
    ) -> MediaAssetVersion | None:
        return self.versions.get((workspace_id, project_id, version_id))

    async def list_versions(
        self, *, workspace_id: str, project_id: str, asset_id: str
    ) -> list[MediaAssetVersion]:
        items = [
            v
            for (w, p, _), v in self.versions.items()
            if w == workspace_id and p == project_id and v.asset_id == asset_id
        ]
        items.sort(key=lambda v: v.version)
        return items

    async def list_versions_for_assets(
        self, *, workspace_id: str, project_id: str, asset_ids: list[str]
    ) -> dict[str, list[MediaAssetVersion]]:
        wanted = set(asset_ids)
        found: dict[str, list[MediaAssetVersion]] = {}
        for (w, p, _), v in sorted(self.versions.items(), key=lambda item: item[1].version):
            if w == workspace_id and p == project_id and v.asset_id in wanted:
                found.setdefault(v.asset_id, []).append(v)
        return found

    async def discard_asset(self, *, workspace_id: str, project_id: str, asset_id: str) -> None:
        self.assets.pop((workspace_id, project_id, asset_id), None)
        for key in [
            k
            for k, v in self.versions.items()
            if k[0] == workspace_id and k[1] == project_id and v.asset_id == asset_id
        ]:
            del self.versions[key]
        self.events = [
            e
            for e in self.events
            if not (
                e.workspace_id == workspace_id
                and e.project_id == project_id
                and e.asset_id == asset_id
            )
        ]

    async def health(self) -> bool:
        return True


class InMemoryObjectStore:
    """In-memory immutable object store with opaque server-generated keys."""

    def __init__(self) -> None:
        self._objects: dict[tuple[str, str, str], tuple[bytes, ObjectStat]] = {}
        self._counter = 0

    def put(
        self,
        data: bytes,
        *,
        workspace_id: str,
        project_id: str,
        content_type: str = "application/octet-stream",
    ) -> ObjectStat:
        self._counter += 1
        key = f"obj_{self._counter:04d}"
        stat = ObjectStat(
            key=key,
            workspace_id=workspace_id,
            project_id=project_id,
            content_type=content_type,
            byte_length=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
        )
        self._objects[(workspace_id, project_id, key)] = (data, stat)
        return stat

    def open(self, key: str, *, workspace_id: str, project_id: str) -> bytes:
        return self._objects[(workspace_id, project_id, key)][0]

    def stat(self, key: str, *, workspace_id: str, project_id: str) -> ObjectStat | None:
        entry = self._objects.get((workspace_id, project_id, key))
        return entry[1] if entry else None

    def delete(self, key: str, *, workspace_id: str, project_id: str) -> bool:
        return self._objects.pop((workspace_id, project_id, key), None) is not None

    def verify_checksum(self, key: str, *, workspace_id: str, project_id: str) -> bool:
        entry = self._objects.get((workspace_id, project_id, key))
        if entry is None:
            return False
        return hashlib.sha256(entry[0]).hexdigest() == entry[1].checksum

    def list_objects(self, *, workspace_id: str, project_id: str) -> list[ObjectStat]:
        return [
            stat
            for (w, p, _), (_data, stat) in self._objects.items()
            if w == workspace_id and p == project_id
        ]

    def delete_workspace(self, *, workspace_id: str) -> int:
        keys = [k for k in self._objects if k[0] == workspace_id]
        for k in keys:
            del self._objects[k]
        return len(keys)

    def delete_project(self, *, workspace_id: str, project_id: str) -> int:
        keys = [k for k in self._objects if k[0] == workspace_id and k[1] == project_id]
        for k in keys:
            del self._objects[k]
        return len(keys)


class InMemoryAssistSessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, Any] = {}

    async def save(self, session: Any) -> Any:
        current = self.sessions.get(session.id)
        if current is not None and (
            current.workspace_id != session.workspace_id or current.project_id != session.project_id
        ):
            return session
        self.sessions[session.id] = session
        return session

    async def get(self, *, workspace_id: str, project_id: str, session_id: str) -> Any:
        session = self.sessions.get(session_id)
        if (
            session is None
            or session.workspace_id != workspace_id
            or session.project_id != project_id
        ):
            return None
        return session

    def _scoped(self, workspace_id: str, project_id: str) -> list[Any]:
        return [
            session
            for session in self.sessions.values()
            if session.workspace_id == workspace_id and session.project_id == project_id
        ]

    async def list_open(
        self, *, workspace_id: str, project_id: str, actor_user_id: str, limit: int
    ) -> list[Any]:
        found = [
            session
            for session in self._scoped(workspace_id, project_id)
            if session.actor_user_id == actor_user_id and session.status.value == "open"
        ]
        found.sort(key=lambda session: (session.updated_at, session.id), reverse=True)
        return found[:limit]

    async def list_for_project(
        self, *, workspace_id: str, project_id: str, limit: int
    ) -> list[Any]:
        found = sorted(
            self._scoped(workspace_id, project_id),
            key=lambda session: (session.created_at, session.id),
        )
        return found[:limit]

    async def delete_stale_open(self, *, workspace_id: str, project_id: str, before: Any) -> int:
        stale = [
            session.id
            for session in self._scoped(workspace_id, project_id)
            if session.status.value == "open" and session.updated_at < before
        ]
        for session_id in stale:
            del self.sessions[session_id]
        return len(stale)

    async def for_mutation(self, *, workspace_id: str, project_id: str, mutation_id: str) -> Any:
        found = [
            session
            for session in self._scoped(workspace_id, project_id)
            if session.mutation_id == mutation_id
        ]
        found.sort(key=lambda session: session.updated_at, reverse=True)
        return found[0] if found else None

    async def tokens_used(self, *, workspace_id: str, project_id: str, session_id: str) -> int:
        if await self.get(workspace_id=workspace_id, project_id=project_id, session_id=session_id):
            return getattr(self, "tokens", {}).get(session_id, 0)  # type: ignore[no-any-return]
        return 0

    async def add_tokens(
        self, *, workspace_id: str, project_id: str, session_id: str, tokens: int
    ) -> None:
        if await self.get(workspace_id=workspace_id, project_id=project_id, session_id=session_id):
            counts = self.__dict__.setdefault("tokens", {})
            counts[session_id] = counts.get(session_id, 0) + max(0, tokens)

    async def delete(self, *, workspace_id: str, project_id: str, session_id: str) -> int:
        found = await self.get(
            workspace_id=workspace_id, project_id=project_id, session_id=session_id
        )
        if found is None:
            return 0
        del self.sessions[session_id]
        return 1

    async def delete_workspace(self, workspace_id: str) -> int:
        doomed = [key for key, s in self.sessions.items() if s.workspace_id == workspace_id]
        for key in doomed:
            del self.sessions[key]
        return len(doomed)

    async def delete_project(self, workspace_id: str, project_id: str) -> int:
        doomed = [session.id for session in self._scoped(workspace_id, project_id)]
        for key in doomed:
            del self.sessions[key]
        return len(doomed)


class InMemoryAiSettingsStore:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, str]] = {}

    async def get(self, workspace_id: str) -> tuple[str, str] | None:
        return self.rows.get(workspace_id)

    async def set(
        self, *, workspace_id: str, provider: str, model: str, actor_user_id: str | None
    ) -> None:
        self.rows[workspace_id] = (provider, model)

    async def delete_workspace(self, workspace_id: str) -> int:
        return 1 if self.rows.pop(workspace_id, None) is not None else 0


class InMemoryAllowanceStore:
    def __init__(self) -> None:
        self.rows: dict[str, int] = {}

    async def get(self, workspace_id: str) -> int | None:
        return self.rows.get(workspace_id)

    async def set(
        self, *, workspace_id: str, daily_tokens: int | None, actor_user_id: str | None
    ) -> None:
        if daily_tokens is None:
            self.rows.pop(workspace_id, None)
        else:
            self.rows[workspace_id] = daily_tokens

    async def delete_workspace(self, workspace_id: str) -> int:
        return 1 if self.rows.pop(workspace_id, None) is not None else 0


class InMemoryAssistantSettingsStore:
    """A story's (or, with no story, a workspace's) assistant settings (CON-009, OPS-002)."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], Any] = {}

    async def get(self, *, workspace_id: str, project_id: str = "") -> Any:
        from backend.domain.usage_controls import AssistantSettings

        return self.rows.get((workspace_id, project_id), AssistantSettings())

    async def set(
        self, *, workspace_id: str, project_id: str = "", settings: Any, actor_user_id: Any
    ) -> None:
        self.rows[(workspace_id, project_id)] = settings


class InMemoryDelegationStore:
    """Keep the safe ones for me (AST-EXP-015), with the same rules as PostgreSQL."""

    def __init__(self) -> None:
        self.settings: dict[tuple[str, str], Any] = {}
        self.records: dict[tuple[str, str, str], Any] = {}
        self.seen: dict[tuple[str, str, str], Any] = {}
        self._last: Any = None

    def _now(self) -> Any:
        from datetime import UTC, datetime, timedelta

        # The real time, but strictly increasing, so "since" comparisons are exact in tests.
        now = datetime.now(UTC)
        if self._last is not None and now <= self._last:
            now = self._last + timedelta(microseconds=1)
        self._last = now
        return now

    async def get(self, *, workspace_id: str, project_id: str = "") -> Any:
        from backend.domain.delegation import DelegationSettings

        return self.settings.get((workspace_id, project_id), DelegationSettings())

    async def set(
        self, *, workspace_id: str, project_id: str = "", settings: Any, actor_user_id: Any
    ) -> None:
        self.settings[(workspace_id, project_id)] = settings

    async def hand_counts(self, *, workspace_id: str, project_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.records.values():
            if (record.workspace_id, record.project_id, record.how) == (
                workspace_id,
                project_id,
                "by_hand",
            ):
                for kind in record.kinds:
                    counts[kind] = counts.get(kind, 0) + 1
        return counts

    async def get_record(self, *, workspace_id: str, project_id: str, mutation_id: str) -> Any:
        return self.records.get((workspace_id, project_id, mutation_id))

    async def put_record(
        self,
        *,
        workspace_id: str,
        project_id: str,
        mutation_id: str,
        how: str,
        state: str,
        kinds: Any,
        branch_id: str,
        actor_user_id: Any,
        via: str = "session",
        reason_code: Any = None,
        reason: Any = None,
    ) -> None:
        from dataclasses import replace

        from backend.domain.delegation import DelegationRecord

        key = (workspace_id, project_id, mutation_id)
        existing = self.records.get(key)
        if existing is not None:
            self.records[key] = replace(
                existing,
                how=how,
                state=state,
                kinds=tuple(kinds),
                branch_id=branch_id,
                actor_user_id=actor_user_id,
                via=via,
                reason_code=reason_code,
                reason=reason,
                created_at=self._now() if how == "kept" else existing.created_at,
            )
            return
        self.records[key] = DelegationRecord(
            workspace_id=workspace_id,
            project_id=project_id,
            mutation_id=mutation_id,
            how=how,
            state=state,
            kinds=tuple(kinds),
            branch_id=branch_id,
            actor_user_id=actor_user_id,
            revision=None,
            undo_mutation_id=None,
            created_at=self._now(),
            via=via,
            reason_code=reason_code,
            reason=reason,
        )

    async def update_record(
        self,
        *,
        workspace_id: str,
        project_id: str,
        mutation_id: str,
        state: Any = None,
        revision: Any = None,
        undo_mutation_id: Any = None,
    ) -> None:
        from dataclasses import replace

        key = (workspace_id, project_id, mutation_id)
        record = self.records.get(key)
        if record is None:
            return
        self.records[key] = replace(
            record,
            state=state if state is not None else record.state,
            revision=revision if revision is not None else record.revision,
            undo_mutation_id=undo_mutation_id
            if undo_mutation_id is not None
            else record.undo_mutation_id,
        )

    async def list_records(
        self,
        *,
        workspace_id: str,
        project_id: Any,
        how: str,
        states: Any,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Any]:
        found = [
            record
            for record in self.records.values()
            if record.workspace_id == workspace_id
            and (project_id is None or record.project_id == project_id)
            and record.how == how
            and record.state in set(states)
        ]
        found.sort(key=lambda item: (item.created_at, item.mutation_id), reverse=True)
        return found[offset : offset + limit]

    async def count_kept_since(self, *, workspace_id: str, project_id: str, since: Any) -> int:
        return sum(
            1
            for record in self.records.values()
            if (record.workspace_id, record.project_id, record.how, record.state)
            == (workspace_id, project_id, "kept", "kept")
            and (since is None or record.created_at > since)
        )

    async def get_seen(self, *, workspace_id: str, project_id: str, user_id: str) -> Any:
        return self.seen.get((workspace_id, project_id, user_id))

    async def set_seen(self, *, workspace_id: str, project_id: str, user_id: str) -> None:
        self.seen[(workspace_id, project_id, user_id)] = self._now()

    async def rule_off_message(self, workspace_id: str, project_id: str, mutation_id: str) -> Any:
        """What the commit transaction checks for a suggestion being kept (see PostgreSQL)."""
        from backend.domain.delegation import RULE_OFF_MESSAGE, rule_still_on

        record = self.records.get((workspace_id, project_id, mutation_id))
        if record is None or (record.how, record.state) != ("kept", "pending"):
            return None
        story = self.settings.get((workspace_id, project_id))
        workspace = self.settings.get((workspace_id, ""))
        forbidden = bool(workspace is not None and workspace.forbidden)
        if story is None or not rule_still_on(record.kinds, story, forbidden):
            return RULE_OFF_MESSAGE
        return None


class InMemorySourceStore:
    """Source texts, versions and name memory, with the same rules as PostgreSQL."""

    def __init__(self) -> None:
        # Keyed by (workspace, project, id), as the tables are.
        self.sources: dict[tuple[str, str, str], Any] = {}
        self.versions: dict[tuple[str, str, str], Any] = {}
        self.bindings: dict[tuple[str, str, str], Any] = {}

    def _summary(self, source: Any) -> Any:
        from dataclasses import replace

        versions = sorted(
            (
                item
                for item in self.versions.values()
                if (item.workspace_id, item.project_id, item.source_id)
                == (source.workspace_id, source.project_id, source.id)
            ),
            key=lambda item: item.number,
        )
        return replace(
            source,
            versions=len(versions),
            latest_version_id=versions[-1].id if versions else None,
            length=len(versions[-1].content) if versions else 0,
        )

    async def add_source(
        self,
        *,
        workspace_id: str,
        project_id: str,
        source_id: str,
        title: str,
        kind: Any,
        actor_user_id: str | None,
    ) -> None:
        from backend.domain.sources import SourceText

        self.sources[(workspace_id, project_id, source_id)] = SourceText(
            workspace_id=workspace_id,
            project_id=project_id,
            id=source_id,
            title=title,
            kind=kind,
            created_by=actor_user_id,
            created_at=datetime.now(UTC),
        )

    async def add_version(
        self,
        *,
        workspace_id: str,
        project_id: str,
        version_id: str,
        source_id: str,
        content: str,
        content_hash: str,
        actor_user_id: str | None,
        origin: Any = None,
        parent_version_id: str | None = None,
        draft_id: str | None = None,
        manifest_id: str | None = None,
    ) -> Any:
        from backend.domain.sources import SourceVersion, VersionOrigin

        number = 1 + max(
            (
                item.number
                for item in self.versions.values()
                if (item.workspace_id, item.project_id, item.source_id)
                == (workspace_id, project_id, source_id)
            ),
            default=0,
        )
        version = SourceVersion(
            workspace_id=workspace_id,
            project_id=project_id,
            id=version_id,
            source_id=source_id,
            number=number,
            content=content,
            content_hash=content_hash,
            created_by=actor_user_id,
            created_at=datetime.now(UTC),
            origin=origin or VersionOrigin.AUTHOR,
            parent_version_id=parent_version_id,
            draft_id=draft_id,
            manifest_id=manifest_id,
        )
        self.versions[(workspace_id, project_id, version_id)] = version
        return version

    async def rename(
        self, *, workspace_id: str, project_id: str, source_id: str, title: str
    ) -> None:
        from dataclasses import replace

        key = (workspace_id, project_id, source_id)
        if key in self.sources:
            self.sources[key] = replace(self.sources[key], title=title)

    async def list_sources(self, *, workspace_id: str, project_id: str) -> list[Any]:
        found = [
            self._summary(item)
            for item in self.sources.values()
            if (item.workspace_id, item.project_id) == (workspace_id, project_id)
            and item.removed_at is None
        ]
        return sorted(found, key=lambda item: (item.created_at, item.id), reverse=True)

    async def get_source(self, *, workspace_id: str, project_id: str, source_id: str) -> Any:
        source = self.sources.get((workspace_id, project_id, source_id))
        return self._summary(source) if source is not None else None

    async def latest_hashes(
        self, *, workspace_id: str, project_id: str, source_ids: list[str]
    ) -> dict[str, str]:
        self.hash_reads = getattr(self, "hash_reads", 0) + 1
        found: dict[str, str] = {}
        for source_id in source_ids:
            source = self.sources.get((workspace_id, project_id, source_id))
            if source is None or source.removed_at is not None:
                continue
            versions = [
                item
                for item in self.versions.values()
                if (item.workspace_id, item.project_id, item.source_id)
                == (workspace_id, project_id, source_id)
            ]
            if versions:
                found[source_id] = max(versions, key=lambda item: item.number).content_hash
        return found

    async def list_versions(
        self, *, workspace_id: str, project_id: str, source_id: str
    ) -> list[Any]:
        return sorted(
            (
                item
                for item in self.versions.values()
                if (item.workspace_id, item.project_id, item.source_id)
                == (workspace_id, project_id, source_id)
            ),
            key=lambda item: item.number,
        )

    async def get_version(self, *, workspace_id: str, project_id: str, version_id: str) -> Any:
        return self.versions.get((workspace_id, project_id, version_id))

    async def remove_source(self, *, workspace_id: str, project_id: str, source_id: str) -> None:
        from dataclasses import replace

        key = (workspace_id, project_id, source_id)
        source = self.sources.get(key)
        if source is None:
            return

        def own(item: Any) -> bool:
            return (item.workspace_id, item.project_id, item.source_id) == key

        self.versions = {k: item for k, item in self.versions.items() if not own(item)}
        self.bindings = {k: item for k, item in self.bindings.items() if not own(item)}
        self.sources[key] = replace(source, removed_at=datetime.now(UTC))

    async def list_bindings(self, *, workspace_id: str, project_id: str) -> list[Any]:
        return [
            item
            for item in self.bindings.values()
            if (item.workspace_id, item.project_id) == (workspace_id, project_id)
        ]

    async def put_binding(self, binding: Any) -> None:
        from backend.domain.sources import replaces

        self.bindings = {
            key: item for key, item in self.bindings.items() if not replaces(item, binding)
        }
        self.bindings[(binding.workspace_id, binding.project_id, binding.id)] = binding

    async def set_examples(self, binding: Any) -> bool:
        key = (binding.workspace_id, binding.project_id, binding.id)
        if key not in self.bindings:
            return False
        self.bindings[key] = binding
        return True

    async def delete_binding(self, *, workspace_id: str, project_id: str, binding_id: str) -> bool:
        return self.bindings.pop((workspace_id, project_id, binding_id), None) is not None

    async def delete_project(self, workspace_id: str, project_id: str) -> int:
        scope = (workspace_id, project_id)
        doomed = [key for key in self.sources if key[:2] == scope]
        for store in (self.sources, self.versions, self.bindings):
            for key in [key for key in store if key[:2] == scope]:
                del store[key]
        return len(doomed)

    async def delete_workspace(self, workspace_id: str) -> int:
        doomed = [key for key in self.sources if key[0] == workspace_id]
        for store in (self.sources, self.versions, self.bindings):
            for key in [key for key in store if key[0] == workspace_id]:
                del store[key]
        return len(doomed)


class InMemoryRuleStore:
    """Story rules with versions, as PostgreSQL keeps them."""

    def __init__(self) -> None:
        # Keyed by (workspace, project, id), as the tables are.
        self.rules: dict[tuple[str, str, str], Any] = {}
        # → every version's (body, examples), oldest first
        self.versions: dict[tuple[str, str, str], list[tuple[Any, list[Any]]]] = {}

    async def list_rules(self, *, workspace_id: str, project_id: str) -> list[Any]:
        return [rule for key, rule in self.rules.items() if key[:2] == (workspace_id, project_id)]

    async def get_rule(self, *, workspace_id: str, project_id: str, rule_id: str) -> Any:
        return self.rules.get((workspace_id, project_id, rule_id))

    async def list_versions(
        self, *, workspace_id: str, project_id: str, rule_id: str
    ) -> list[tuple[int, Any, list[Any]]]:
        found = self.versions.get((workspace_id, project_id, rule_id), [])
        return [(number + 1, body, examples) for number, (body, examples) in enumerate(found)]

    async def add_rule(
        self,
        *,
        workspace_id: str,
        project_id: str,
        rule_id: str,
        title: str,
        sentence: str,
        body: Any,
        examples: list[Any],
        actor_user_id: str | None,
    ) -> None:
        from backend.domain.rules import RuleStatus, StoryRule

        now = datetime.now(UTC)
        key = (workspace_id, project_id, rule_id)
        self.versions[key] = [(body, list(examples))]
        self.rules[key] = StoryRule(
            workspace_id=workspace_id,
            project_id=project_id,
            id=rule_id,
            title=title,
            sentence=sentence,
            status=RuleStatus.ON,
            version=1,
            body=body,
            examples=list(examples),
            created_by=actor_user_id,
            created_at=now,
            updated_at=now,
        )

    async def change_rule(
        self,
        *,
        workspace_id: str,
        project_id: str,
        rule_id: str,
        title: str | None = None,
        sentence: str | None = None,
        status: Any = None,
        body: Any = None,
        examples: list[Any] | None = None,
        actor_user_id: str | None = None,
        enforcement: Any = None,
    ) -> None:
        from dataclasses import replace

        key = (workspace_id, project_id, rule_id)
        rule = self.rules.get(key)
        if rule is None:
            return
        changes: dict[str, Any] = {"updated_at": datetime.now(UTC)}
        if title is not None:
            changes["title"] = title
        if sentence is not None:
            changes["sentence"] = sentence
        if status is not None:
            changes["status"] = status
        if enforcement is not None:
            changes["enforcement"] = enforcement
        if body is not None:
            self.versions[key].append((body, list(examples or [])))
            changes.update(
                body=body, examples=list(examples or []), version=len(self.versions[key])
            )
        self.rules[key] = replace(rule, **changes)

    async def remove_rule(self, *, workspace_id: str, project_id: str, rule_id: str) -> bool:
        key = (workspace_id, project_id, rule_id)
        self.versions.pop(key, None)
        return self.rules.pop(key, None) is not None

    async def delete_project(self, workspace_id: str, project_id: str) -> int:
        doomed = [key for key in self.rules if key[:2] == (workspace_id, project_id)]
        for key in doomed:
            del self.rules[key]
            self.versions.pop(key, None)
        return len(doomed)

    async def delete_workspace(self, workspace_id: str) -> int:
        doomed = [key for key in self.rules if key[0] == workspace_id]
        for key in doomed:
            del self.rules[key]
            self.versions.pop(key, None)
        return len(doomed)


class InMemoryStoryClockStore:
    """The unit each story counts time in."""

    def __init__(self) -> None:
        self.units: dict[tuple[str, str], str] = {}
        self.how_long: dict[tuple[str, str], tuple[Any, int]] = {}
        self.scales: dict[tuple[str, str], tuple[Any, int]] = {}

    async def get_unit(self, *, workspace_id: str, project_id: str) -> str | None:
        return self.units.get((workspace_id, project_id))

    async def set_unit(self, *, workspace_id: str, project_id: str, unit: str) -> None:
        self.units[(workspace_id, project_id)] = unit

    # The author's travel times and lengths (CON-003), with a version per story.
    async def get_how_long(self, *, workspace_id: str, project_id: str) -> tuple[Any, int]:
        table, version = self.how_long.get((workspace_id, project_id), ({}, 0))
        return copy.deepcopy(table), version

    async def set_how_long(
        self, *, workspace_id: str, project_id: str, table: Any, expected_version: int
    ) -> int | None:
        _, version = self.how_long.get((workspace_id, project_id), ({}, 0))
        if version != expected_version:
            return None
        self.how_long[(workspace_id, project_id)] = (copy.deepcopy(table), version + 1)
        return version + 1

    # The story's shared scales (STY-006), with a version per story.
    async def get_scales(self, *, workspace_id: str, project_id: str) -> tuple[Any, int]:
        scales, version = self.scales.get((workspace_id, project_id), ({}, 0))
        return copy.deepcopy(scales), version

    async def set_scales(
        self, *, workspace_id: str, project_id: str, scales: Any, expected_version: int
    ) -> int | None:
        _, version = self.scales.get((workspace_id, project_id), ({}, 0))
        if version != expected_version:
            return None
        self.scales[(workspace_id, project_id)] = (copy.deepcopy(scales), version + 1)
        return version + 1


class InMemoryPassageStore:
    """The derived passage index, as pointers and the entries each names."""

    def __init__(self) -> None:
        self.passages: dict[tuple[str, str, str], list[Any]] = {}

    async def replace_source(self, *, workspace_id, project_id, source_id, passages) -> None:
        self.passages[(workspace_id, project_id, source_id)] = list(passages)

    async def forget_source(self, *, workspace_id, project_id, source_id) -> None:
        self.passages.pop((workspace_id, project_id, source_id), None)

    async def for_source(self, *, workspace_id, project_id, source_id) -> list[Any]:
        return list(self.passages.get((workspace_id, project_id, source_id), []))

    async def mentioning(self, *, workspace_id, project_id, entity_ids, limit=200) -> list[Any]:
        wanted = set(entity_ids)
        found = [
            passage
            for (ws, pj, _), items in sorted(self.passages.items())
            if ws == workspace_id and pj == project_id
            for passage in items
            if wanted & set(passage.entity_ids)
        ]
        return found[:limit]

    async def indexed_versions(self, *, workspace_id, project_id) -> dict[str, tuple[str, int]]:
        return {
            source_id: (items[0].version_id, min(item.indexed_revision for item in items))
            for (ws, pj, source_id), items in self.passages.items()
            if ws == workspace_id and pj == project_id and items
        }


class InMemoryPassageQueue:
    """The passage index's waiting list (CTX-005), as PostgreSQL keeps it."""

    def __init__(self, clock: Any = None) -> None:
        import time as _time

        self._now = clock or _time.monotonic
        # (workspace, project, source) -> row
        self.rows: dict[tuple[str, str, str], dict[str, Any]] = {}

    async def request(self, *, workspace_id, project_id, source_id, gone=False) -> None:
        key = (workspace_id, project_id, source_id)
        row = self.rows.get(key)
        if row is None:
            self.rows[key] = {
                "gone": gone,
                "generation": 1,
                "first": self._now(),
                "claimed_until": None,
                "attempts": 0,
                "error": None,
            }
            return
        if row["attempts"] > 0:
            # A fresh change is read soon, never after an old failure's wait.
            row["claimed_until"] = None
        row.update(gone=gone, generation=row["generation"] + 1, attempts=0, error=None)

    def _ready(self, row: dict[str, Any]) -> bool:
        return row["claimed_until"] is None or row["claimed_until"] < self._now()

    async def claim(self, *, workspace_id, project_id, limit, lease_seconds) -> list[Any]:
        from backend.domain.context import WaitingText

        found = sorted(
            (
                (row["first"], key[2], row)
                for key, row in self.rows.items()
                if key[:2] == (workspace_id, project_id) and self._ready(row)
            ),
            key=lambda item: (item[0], item[1]),
        )[:limit]
        out = []
        for _, source_id, row in found:
            row["claimed_until"] = self._now() + lease_seconds
            out.append(WaitingText(source_id, row["gone"], row["generation"], row["attempts"]))
        return sorted(out, key=lambda item: item.source_id)

    async def done(self, *, workspace_id, project_id, source_id, generation) -> bool:
        key = (workspace_id, project_id, source_id)
        row = self.rows.get(key)
        if row is None:
            return False
        if row["generation"] == generation:
            del self.rows[key]
            return True
        row["claimed_until"] = None
        return False

    async def failed(
        self, *, workspace_id, project_id, source_id, generation, error, retry_after_seconds
    ) -> bool:
        row = self.rows.get((workspace_id, project_id, source_id))
        if row is None:
            return False
        if row["generation"] != generation:
            row["claimed_until"] = None  # a newer change: read it soon
            return False
        row["attempts"] += 1
        row["error"] = error
        row["claimed_until"] = self._now() + retry_after_seconds
        return True

    async def waiting(self, *, workspace_id, project_id) -> Any:
        from backend.domain.context import IndexWaiting

        rows = [row for key, row in self.rows.items() if key[:2] == (workspace_id, project_id)]
        if not rows:
            return IndexWaiting()
        return IndexWaiting(
            count=len(rows),
            oldest_seconds=max(0.0, self._now() - min(row["first"] for row in rows)),
            failing=sum(1 for row in rows if row["attempts"] > 0),
        )

    async def lag(self) -> Any:
        from backend.domain.context import IndexWaiting

        rows = list(self.rows.values())
        if not rows:
            return IndexWaiting()
        return IndexWaiting(
            count=len(rows),
            oldest_seconds=max(0.0, self._now() - min(row["first"] for row in rows)),
            failing=sum(1 for row in rows if row["attempts"] > 0),
        )

    async def stories_waiting(self, *, limit) -> list[tuple[str, str]]:
        oldest: dict[tuple[str, str], float] = {}
        for key, row in self.rows.items():
            if self._ready(row):
                scope = key[:2]
                oldest[scope] = min(oldest.get(scope, row["first"]), row["first"])
        return [scope for scope, _ in sorted(oldest.items(), key=lambda item: item[1])][:limit]


class InMemorySceneSummaryStore:
    """Summaries of scenes' prose (CTX-006)."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], Any] = {}

    def _retire_accepted(self, workspace_id, project_id, summary) -> None:
        from dataclasses import replace as _replace

        from backend.domain.context import SummaryState

        for key, row in list(self.rows.items()):
            if (
                key[:2] == (workspace_id, project_id)
                and row.source_id == summary.source_id
                and row.branch_id == summary.branch_id
                and row.state is SummaryState.ACCEPTED
                and row.id != summary.id
            ):
                self.rows[key] = _replace(row, state=SummaryState.REPLACED)

    async def add(self, *, workspace_id, project_id, summary) -> None:
        from backend.domain.context import SummaryState

        if summary.state is SummaryState.ACCEPTED:
            self._retire_accepted(workspace_id, project_id, summary)
        self.rows[(workspace_id, project_id, summary.id)] = summary

    async def decide(
        self, *, workspace_id, project_id, summary_id, state, body, origin, decided_by, decided_at
    ) -> Any:
        from dataclasses import replace as _replace

        from backend.domain.context import SummaryState

        key = (workspace_id, project_id, summary_id)
        row = self.rows.get(key)
        if row is None or row.state is not SummaryState.PROPOSED:
            return None
        if state is SummaryState.ACCEPTED:
            self._retire_accepted(workspace_id, project_id, row)
        updated = _replace(
            row,
            state=state,
            body=body if body is not None else row.body,
            origin=origin or row.origin,
            decided_by=decided_by,
            decided_at=decided_at,
        )
        self.rows[key] = updated
        return updated

    async def get(self, *, workspace_id, project_id, summary_id) -> Any:
        return self.rows.get((workspace_id, project_id, summary_id))

    async def for_sources(self, *, workspace_id, project_id, source_ids) -> list[Any]:
        from backend.domain.context import SummaryState

        wanted = set(source_ids)
        return sorted(
            (
                row
                for key, row in self.rows.items()
                if key[:2] == (workspace_id, project_id)
                and row.source_id in wanted
                and row.state in (SummaryState.PROPOSED, SummaryState.ACCEPTED)
            ),
            key=lambda row: (row.created_at, row.id),
        )


class InMemoryPartSummaryStore:
    """Summaries of whole arcs and beats (CTX-006)."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], Any] = {}

    def _retire_accepted(self, workspace_id, project_id, summary) -> None:
        from dataclasses import replace as _replace

        from backend.domain.context import SummaryState

        for key, row in list(self.rows.items()):
            if (
                key[:2] == (workspace_id, project_id)
                and row.plan_id == summary.plan_id
                and row.branch_id == summary.branch_id
                and row.state is SummaryState.ACCEPTED
                and row.id != summary.id
            ):
                self.rows[key] = _replace(row, state=SummaryState.REPLACED)

    async def add(self, *, workspace_id, project_id, summary) -> None:
        from backend.domain.context import SummaryState

        if summary.state is SummaryState.ACCEPTED:
            self._retire_accepted(workspace_id, project_id, summary)
        self.rows[(workspace_id, project_id, summary.id)] = summary

    async def decide(
        self, *, workspace_id, project_id, summary_id, state, body, origin, decided_by, decided_at
    ) -> Any:
        from dataclasses import replace as _replace

        from backend.domain.context import SummaryState

        key = (workspace_id, project_id, summary_id)
        row = self.rows.get(key)
        if row is None or row.state is not SummaryState.PROPOSED:
            return None
        if state is SummaryState.ACCEPTED:
            self._retire_accepted(workspace_id, project_id, row)
        updated = _replace(
            row,
            state=state,
            body=body if body is not None else row.body,
            origin=origin or row.origin,
            decided_by=decided_by,
            decided_at=decided_at,
        )
        self.rows[key] = updated
        return updated

    async def get(self, *, workspace_id, project_id, summary_id) -> Any:
        return self.rows.get((workspace_id, project_id, summary_id))

    async def for_plans(self, *, workspace_id, project_id, plan_ids) -> list[Any]:
        from backend.domain.context import SummaryState

        wanted = set(plan_ids)
        return sorted(
            (
                row
                for key, row in self.rows.items()
                if key[:2] == (workspace_id, project_id)
                and row.plan_id in wanted
                and row.state in (SummaryState.PROPOSED, SummaryState.ACCEPTED)
            ),
            key=lambda row: (row.created_at, row.id),
        )


class InMemoryManifestStore:
    def __init__(self) -> None:
        self.saved: dict[tuple[str, str, str], Any] = {}

    async def save(self, *, workspace_id, project_id, manifest_id, **fields) -> None:
        from backend.domain.context import SavedManifest
        from backend.domain.identity import utcnow

        self.saved[(workspace_id, project_id, manifest_id)] = SavedManifest(
            id=manifest_id,
            purpose=fields["purpose"],
            plan_id=fields["plan_id"],
            policy_version=fields["policy_version"],
            checksum=fields["checksum"],
            words=fields["words"],
            budget=fields["budget"],
            manifest=fields["manifest"],
            created_by=fields["created_by"],
            created_at=utcnow(),
            branch_id=fields.get("branch_id"),
        )

    async def get(self, *, workspace_id, project_id, manifest_id) -> Any:
        return self.saved.get((workspace_id, project_id, manifest_id))


class InMemoryJobStreamStore:
    """Drafts while they are written (SCN-002), as the PostgreSQL store keeps them."""

    def __init__(self, jobs: Any = None) -> None:
        self.rows: dict[tuple[str, str, str, int], dict[str, Any]] = {}
        self.appends = 0
        self._jobs = jobs

    async def begin(
        self, *, workspace_id: str, project_id: str, job_id: str, number: int, attempt: int = 0
    ) -> None:
        found = self.rows.get((workspace_id, project_id, job_id, number))
        if found is not None and found["attempt"] > attempt:
            return  # a worker that lost the job never starts the newer one's words again
        self.rows[(workspace_id, project_id, job_id, number)] = {
            "pieces": [],
            "chars": 0,
            "done": False,
            "attempt": attempt,
        }

    async def append(
        self,
        *,
        workspace_id: str,
        project_id: str,
        job_id: str,
        number: int,
        piece: str,
        attempt: int = 0,
    ) -> None:
        row = self.rows.get((workspace_id, project_id, job_id, number))
        if row is None or row["done"] or not piece or row["attempt"] != attempt:
            return
        self.appends += 1
        row["pieces"].append(piece)
        row["chars"] += len(piece)

    async def end(
        self,
        *,
        workspace_id: str,
        project_id: str,
        job_id: str,
        number: int | None = None,
        attempt: int | None = None,
    ) -> None:
        for (w, p, j, n), row in self.rows.items():
            if (w, p, j) != (workspace_id, project_id, job_id) or number not in (None, n):
                continue
            if attempt is not None and (
                row["attempt"] > attempt if number is None else row["attempt"] != attempt
            ):
                continue
            row["pieces"] = []
            row["done"] = True

    def words(self, workspace_id: str, project_id: str, job_id: str, number: int) -> str:
        row = self.rows.get((workspace_id, project_id, job_id, number))
        return "".join(row["pieces"]) if row else ""

    async def read(
        self, *, workspace_id: str, project_id: str, job_id: str, offsets: dict[int, int]
    ) -> list[Any]:
        from backend.adapters.stream_store import StreamPart

        found = []
        for (w, p, j, n), row in sorted(self.rows.items()):
            if (w, p, j) != (workspace_id, project_id, job_id):
                continue
            words = "".join(row["pieces"])
            offset = min(row["chars"], max(0, offsets.get(n, 0)))
            found.append(
                StreamPart(
                    number=n,
                    chars=row["chars"],
                    text=words[offset:],
                    offset=offset,
                    done=row["done"],
                )
            )
        return found

    async def sweep(self) -> int:
        ended = [
            key
            for key in self.rows
            if self._jobs is not None
            and (job := self._jobs.jobs.get(key[:3])) is not None
            and job.status.finished
        ]
        for key in ended:
            del self.rows[key]
        return len(ended)


class InMemoryJobStore:
    """Jobs as the PostgreSQL store keeps them, with leases and cancellation."""

    def __init__(self) -> None:
        self.jobs: dict[tuple[str, str, str], Any] = {}
        self.owners: dict[tuple[str, str, str], str | None] = {}

    def _key(self, job) -> tuple[str, str, str]:
        return (job.workspace_id, job.project_id, job.id)

    async def create(self, job):
        if job.idempotency_key:
            for existing in self.jobs.values():
                if (
                    existing.workspace_id == job.workspace_id
                    and existing.project_id == job.project_id
                    and existing.actor_user_id == job.actor_user_id
                    and existing.idempotency_key == job.idempotency_key
                ):
                    return existing
        self.jobs[self._key(job)] = job
        return job

    async def get(self, *, workspace_id, project_id, job_id):
        return self.jobs.get((workspace_id, project_id, job_id))

    async def waiting(self):
        """(how many jobs wait to start, seconds the oldest has waited), across every story."""
        from backend.domain.identity import utcnow
        from backend.domain.jobs import JobStatus

        queued = [job for job in self.jobs.values() if job.status is JobStatus.QUEUED]
        if not queued:
            return 0, None
        oldest = min(job.created_at for job in queued)
        return len(queued), max(0.0, (utcnow() - oldest).total_seconds())

    async def find_by_key(self, *, workspace_id, project_id, actor_user_id, idempotency_key):
        if not idempotency_key:
            return None
        for job in self.jobs.values():
            if (job.workspace_id, job.project_id, job.actor_user_id, job.idempotency_key) == (
                workspace_id,
                project_id,
                actor_user_id,
                idempotency_key,
            ):
                return job
        return None

    async def count_active(self, *, workspace_id, project_id, actor_user_id):
        from backend.domain.jobs import UNCOUNTED_KINDS

        return sum(
            1
            for job in self.jobs.values()
            if (job.workspace_id, job.project_id, job.actor_user_id)
            == (workspace_id, project_id, actor_user_id)
            and not job.status.finished
            and job.kind not in UNCOUNTED_KINDS
        )

    async def recent(self, *, workspace_id, project_id, kind, plan_id, limit=10, branch_id="any"):
        found = [
            job
            for job in self.jobs.values()
            if (job.workspace_id, job.project_id, job.kind) == (workspace_id, project_id, kind)
            and job.payload.get("plan_id") == plan_id
            and (branch_id == "any" or job.payload.get("branch_id") == branch_id)
        ]
        return sorted(found, key=lambda job: job.created_at, reverse=True)[:limit]

    async def claim(self, *, owner):
        from dataclasses import replace

        from backend.domain.jobs import JobStatus

        for key, job in sorted(self.jobs.items(), key=lambda pair: pair[1].created_at):
            if job.status is JobStatus.QUEUED:
                claimed = replace(job, status=JobStatus.RUNNING, attempts=job.attempts + 1)
                self.jobs[key] = claimed
                self.owners[key] = owner
                return claimed
        return None

    async def heartbeat(self, job, *, owner, stage, progress):
        from dataclasses import replace

        from backend.domain.jobs import JobStatus, LeaseLost

        key = self._key(job)
        current = self.jobs[key]
        if self.owners.get(key) != owner or current.status is not JobStatus.RUNNING:
            raise LeaseLost(job.id)
        self.jobs[key] = replace(current, stage=stage, progress=progress)
        return current.cancel_requested

    async def add_usage(
        self,
        job,
        *,
        owner,
        prompt_tokens,
        completion_tokens,
        cached_prompt_tokens=0,
        cost_usd=None,
    ):
        from dataclasses import replace

        key = self._key(job)
        current = self.jobs[key]
        if self.owners.get(key) != owner:
            return
        cost = current.provider_cost_usd
        if cost_usd is not None:
            cost = (cost or 0.0) + cost_usd
        self.jobs[key] = replace(
            current,
            prompt_tokens=current.prompt_tokens + prompt_tokens,
            completion_tokens=current.completion_tokens + completion_tokens,
            cached_prompt_tokens=current.cached_prompt_tokens + cached_prompt_tokens,
            provider_cost_usd=cost,
        )

    async def requeue(self, job, *, owner):
        from dataclasses import replace

        from backend.domain.jobs import JobStatus

        key = self._key(job)
        current = self.jobs[key]
        if self.owners.get(key) != owner or current.status is not JobStatus.RUNNING:
            return
        self.jobs[key] = replace(
            current, status=JobStatus.QUEUED, attempts=max(current.attempts - 1, 0)
        )
        self.owners[key] = None

    async def finish(self, job, *, owner, status, result=None, error_code=None, error_message=None):
        from dataclasses import replace

        from backend.domain.jobs import JobStatus

        key = self._key(job)
        current = self.jobs[key]
        if current.status is not JobStatus.RUNNING or self.owners.get(key) != owner:
            # A job that has already ended keeps its ending, and a worker that lost the
            # job never ends it, as in PostgreSQL.
            return current
        self.jobs[key] = replace(
            current,
            status=status,
            result=result or {},
            error_code=error_code,
            error_message=error_message,
            progress=100 if status is JobStatus.SUCCEEDED else current.progress,
            finished_at=utcnow(),
        )
        self.owners[key] = None
        return self.jobs[key]

    async def request_cancel(self, *, workspace_id, project_id, job_id):
        from dataclasses import replace

        from backend.domain.jobs import JobStatus

        key = (workspace_id, project_id, job_id)
        current = self.jobs.get(key)
        if current is None:
            return None
        if current.status.finished:
            return current
        queued = current.status is JobStatus.QUEUED
        self.jobs[key] = replace(
            current,
            cancel_requested=True,
            status=JobStatus.CANCELLED if queued else current.status,
            finished_at=utcnow() if queued else current.finished_at,
        )
        return self.jobs[key]


class InMemoryStudioStore:
    def __init__(self) -> None:
        self.manuscripts: dict[tuple[str, str, str, str], str] = {}
        self.drafts_by_id: dict[tuple[str, str, str], Any] = {}
        self.assistant_findings: list[tuple[str, str, Any]] = []
        self.voices: dict[tuple[str, str], str] = {}
        self.saved_readings: list[Any] = []
        self.saved_dismissals: list[Any] = []

    async def manuscript(self, *, workspace_id, project_id, branch_id, plan_id):
        return self.manuscripts.get((workspace_id, project_id, branch_id, plan_id))

    async def manuscripts_for(self, *, workspace_id, project_id, branch_id):
        return sorted(
            (plan, value)
            for (ws, pj, branch, plan), value in self.manuscripts.items()
            if (ws, pj, branch) == (workspace_id, project_id, branch_id)
        )

    async def link(self, *, workspace_id, project_id, branch_id, plan_id, source_id):
        self.manuscripts[(workspace_id, project_id, branch_id, plan_id)] = source_id

    async def relink(
        self, *, workspace_id, project_id, from_source_id, to_source_id, except_branch_id
    ):
        for (ws, pj, branch, plan), value in list(self.manuscripts.items()):
            if (ws, pj) == (workspace_id, project_id) and value == from_source_id:
                if branch != except_branch_id:
                    self.manuscripts[(ws, pj, branch, plan)] = to_source_id

    async def linked_elsewhere(self, *, workspace_id, project_id, branch_id, source_id):
        return any(
            ws == workspace_id and pj == project_id and branch != branch_id and value == source_id
            for (ws, pj, branch, _), value in self.manuscripts.items()
        )

    async def add_draft(self, draft):
        self.drafts_by_id[(draft.workspace_id, draft.project_id, draft.id)] = draft

    async def drafts(self, *, workspace_id, project_id, branch_id, plan_id):
        return sorted(
            (
                draft
                for draft in self.drafts_by_id.values()
                if (draft.workspace_id, draft.project_id, draft.plan_id, draft.branch_id)
                == (workspace_id, project_id, plan_id, branch_id)
                and draft.status == "proposed"
            ),
            key=lambda draft: (draft.created_at, draft.number),
        )

    async def get_draft(self, *, workspace_id, project_id, draft_id):
        return self.drafts_by_id.get((workspace_id, project_id, draft_id))

    async def drafts_for_job(self, *, workspace_id, project_id, job_id):
        return sorted(
            (
                draft
                for draft in self.drafts_by_id.values()
                if (draft.workspace_id, draft.project_id, draft.job_id)
                == (workspace_id, project_id, job_id)
            ),
            key=lambda draft: draft.number,
        )

    async def mark_kept(self, *, workspace_id, project_id, draft_id, version_id):
        from dataclasses import replace

        key = (workspace_id, project_id, draft_id)
        self.drafts_by_id[key] = replace(
            self.drafts_by_id[key], status="kept", kept_version_id=version_id, content=""
        )

    async def discard(self, *, workspace_id, project_id, draft_id):
        self.drafts_by_id.pop((workspace_id, project_id, draft_id), None)
        self.assistant_findings = [
            item
            for item in self.assistant_findings
            if not (item[2].target_kind == "draft" and item[2].target_id == draft_id)
        ]

    async def add_findings(self, *, workspace_id, project_id, findings):
        self.assistant_findings.extend((workspace_id, project_id, f) for f in findings)

    async def findings(self, *, workspace_id, project_id, target_kind, target_id):
        return [
            f
            for ws, pj, f in self.assistant_findings
            if (ws, pj, f.target_kind, f.target_id)
            == (workspace_id, project_id, target_kind, target_id)
        ]

    async def findings_for_job(self, *, workspace_id, project_id, job_id):
        return [
            f
            for ws, pj, f in self.assistant_findings
            if (ws, pj, f.job_id) == (workspace_id, project_id, job_id)
        ]

    async def save_reading(self, reading):
        for n, existing in enumerate(self.saved_readings):
            if (existing.workspace_id, existing.project_id, existing.branch_id, existing.id) == (
                reading.workspace_id,
                reading.project_id,
                reading.branch_id,
                reading.id,
            ):
                self.saved_readings[n] = reading
                return
        self.saved_readings.append(reading)

    async def add_reading(self, reading):
        await self.save_reading(reading)

    async def readings(self, *, workspace_id, project_id, branch_id, plan_id, limit=50):
        found = [
            r
            for r in self.saved_readings
            if (r.workspace_id, r.project_id, r.branch_id, r.plan_id)
            == (workspace_id, project_id, branch_id, plan_id)
        ]
        return list(reversed(found))[:limit]

    async def readings_in(self, *, workspace_id, project_id, branch_id):
        found = [
            r
            for r in self.saved_readings
            if (r.workspace_id, r.project_id, r.branch_id) == (workspace_id, project_id, branch_id)
        ]
        return sorted(found, key=lambda r: (r.created_at, r.id))

    async def dismissals_in(self, *, workspace_id, project_id, branch_id):
        found = [
            d
            for d in self.saved_dismissals
            if (d.workspace_id, d.project_id, d.branch_id) == (workspace_id, project_id, branch_id)
        ]
        return sorted(found, key=lambda d: (d.created_at, d.id))

    async def restore_dismissal(self, dismissal):
        key = (dismissal.workspace_id, dismissal.project_id, dismissal.branch_id, dismissal.id)
        if any(
            (d.workspace_id, d.project_id, d.branch_id, d.id) == key for d in self.saved_dismissals
        ):
            return False
        self.saved_dismissals.append(dismissal)
        return True

    async def restore_reading(self, reading):
        key = (reading.workspace_id, reading.project_id, reading.branch_id, reading.id)
        if any(
            (r.workspace_id, r.project_id, r.branch_id, r.id) == key for r in self.saved_readings
        ):
            return False
        self.saved_readings.append(reading)
        return True

    async def last_reading(self, *, workspace_id, project_id, branch_id, plan_id):
        for reading in await self.readings(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id, plan_id=plan_id
        ):
            if reading.status == "complete":
                return reading
        return None

    async def dismissals(self, *, workspace_id, project_id, branch_id, plan_id):
        return [
            d
            for d in self.saved_dismissals
            if (d.workspace_id, d.project_id, d.branch_id, d.plan_id)
            == (workspace_id, project_id, branch_id, plan_id)
            and d.withdrawn_at is None
        ]

    async def add_dismissal(self, dismissal):
        from dataclasses import replace

        for n, existing in enumerate(self.saved_dismissals):
            if (
                (existing.workspace_id, existing.project_id, existing.branch_id, existing.plan_id)
                == (
                    dismissal.workspace_id,
                    dismissal.project_id,
                    dismissal.branch_id,
                    dismissal.plan_id,
                )
                and existing.fingerprint == dismissal.fingerprint
                and existing.withdrawn_at is None
            ):
                self.saved_dismissals[n] = replace(existing, withdrawn_at=dismissal.created_at)
        self.saved_dismissals.append(dismissal)

    async def withdraw_dismissal(
        self, *, workspace_id, project_id, branch_id, plan_id, dismissal_id, at
    ):
        from dataclasses import replace

        for n, existing in enumerate(self.saved_dismissals):
            if (
                (existing.workspace_id, existing.project_id, existing.branch_id, existing.plan_id)
                == (workspace_id, project_id, branch_id, plan_id)
                and existing.id == dismissal_id
                and existing.withdrawn_at is None
            ):
                self.saved_dismissals[n] = replace(existing, withdrawn_at=at)
                return True
        return False

    async def voice(self, *, workspace_id, project_id):
        return self.voices.get((workspace_id, project_id), "")

    async def set_voice(self, *, workspace_id, project_id, notes):
        self.voices[(workspace_id, project_id)] = notes


class InMemoryAccessKeyStore:
    """Access keys for other assistants (QRY-003): digests and use counts only."""

    def __init__(self) -> None:
        self.keys: dict[str, Any] = {}
        self.counts: dict[tuple[str, str, str], list[int]] = {}
        self.touches = 0

    async def create(self, key: Any) -> Any:
        if any(item.token_hash == key.token_hash for item in self.keys.values()):
            raise ValueError("duplicate key digest")
        self.keys[key.id] = key.model_copy()
        return key

    async def get(self, *, workspace_id: str, key_id: str) -> Any | None:
        key = self.keys.get(key_id)
        return key.model_copy() if key is not None and key.workspace_id == workspace_id else None

    async def get_by_hash(self, token_hash: str) -> Any | None:
        for key in self.keys.values():
            if key.token_hash == token_hash:
                return key.model_copy()
        return None

    async def list_keys(self, *, workspace_id: str, user_id: str | None = None) -> list[Any]:
        found = [
            key.model_copy()
            for key in self.keys.values()
            if key.workspace_id == workspace_id and (user_id is None or key.user_id == user_id)
        ]
        return sorted(found, key=lambda key: (key.created_at, key.id), reverse=True)

    async def revoke(self, *, key_id: str, reason: str, at: datetime) -> bool:
        from backend.domain.access_keys import RevokedBecause

        key = self.keys.get(key_id)
        if key is None or key.revoked_at is not None:
            return False
        key.revoked_at = at
        key.revoked_reason = RevokedBecause(reason)
        return True

    async def revoke_for_member(
        self, *, workspace_id: str, user_id: str, reason: str, at: datetime
    ) -> list[str]:
        stopped = []
        for key in list(self.keys.values()):
            if key.workspace_id == workspace_id and key.user_id == user_id:
                if await self.revoke(key_id=key.id, reason=reason, at=at):
                    stopped.append(key.id)
        return sorted(stopped)

    async def touch(self, *, key_id: str, at: datetime) -> None:
        key = self.keys.get(key_id)
        if key is not None and (
            key.last_used_at is None or (at - key.last_used_at).total_seconds() >= 60
        ):
            key.last_used_at = at
            self.touches += 1

    async def record_use(
        self, *, key_id: str, workspace_id: str, day: str, tool: str, refused: bool
    ) -> None:
        count = self.counts.setdefault((key_id, day, tool), [0, 0])
        count[0] += 1
        count[1] += 1 if refused else 0

    async def uses(self, *, key_id: str, since_day: str) -> list[Any]:
        from backend.domain.access_keys import AccessKeyUse

        rows = [
            AccessKeyUse(day=day, tool=tool, calls=calls, refused=refused)
            for (key, day, tool), (calls, refused) in self.counts.items()
            if key == key_id and day >= since_day
        ]
        rows.sort(key=lambda row: row.tool)
        return sorted(rows, key=lambda row: row.day, reverse=True)

    async def delete_workspace(self, workspace_id: str) -> int:
        gone = [key_id for key_id, key in self.keys.items() if key.workspace_id == workspace_id]
        for key_id in gone:
            del self.keys[key_id]
        return len(gone)

    async def delete_project(self, workspace_id: str, project_id: str) -> int:
        gone = [
            key_id
            for key_id, key in self.keys.items()
            if key.workspace_id == workspace_id and key.project_id == project_id
        ]
        for key_id in gone:
            del self.keys[key_id]
        return len(gone)


class InMemoryConsistencyRunStore:
    """Checks kept as runs (CON-001), and the check cache (CON-007), in memory."""

    def __init__(self) -> None:
        self.runs: list[Any] = []
        # (workspace, project, plan, check, fingerprint) -> (order, findings)
        self.cache: dict[tuple[str, str, str, str, str], tuple[int, list[Any]]] = {}
        self._tick = 0

    async def add(self, run: Any) -> bool:
        from dataclasses import replace
        from datetime import UTC, datetime, timedelta

        if run.job_id is not None and any(
            (item.workspace_id, item.project_id, item.branch_id, item.job_id)
            == (run.workspace_id, run.project_id, run.branch_id, run.job_id)
            for item in self.runs
        ):
            return False
        if any(
            (item.workspace_id, item.project_id, item.branch_id, item.id)
            == (run.workspace_id, run.project_id, run.branch_id, run.id)
            for item in self.runs
        ):
            return False
        # Strictly later than the run before, so newest-first order is stable in tests.
        now = datetime.now(UTC)
        if self.runs and self.runs[-1].created_at is not None and now <= self.runs[-1].created_at:
            now = self.runs[-1].created_at + timedelta(microseconds=1)
        self.runs.append(replace(run, created_at=run.created_at or now))
        return True

    def _one(self, workspace_id: str, project_id: str, branch_id: str | None, test: Any) -> Any:
        found = [
            run
            for run in self.runs
            if (run.workspace_id, run.project_id) == (workspace_id, project_id)
            and (branch_id is None or run.branch_id == branch_id)
            and test(run)
        ]
        found.sort(key=lambda run: run.created_at)
        return found[0] if found else None

    async def get(
        self, *, workspace_id: str, project_id: str, run_id: str, branch_id: str | None = None
    ) -> Any:
        return self._one(workspace_id, project_id, branch_id, lambda run: run.id == run_id)

    async def for_job(
        self, *, workspace_id: str, project_id: str, job_id: str, branch_id: str | None = None
    ) -> Any:
        return self._one(workspace_id, project_id, branch_id, lambda run: run.job_id == job_id)

    async def recent(
        self,
        *,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        plan_id: str | None = None,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[Any]:
        found = [
            run
            for run in self.runs
            if (run.workspace_id, run.project_id, run.branch_id)
            == (workspace_id, project_id, branch_id)
            and (plan_id is None or run.touches(plan_id))
            and (kind is None or run.kind == kind)
        ]
        found.sort(key=lambda run: (run.created_at, run.id), reverse=True)
        return found[:limit]

    async def all_in(
        self, *, workspace_id: str, project_id: str, branch_id: str, limit: int = 2_000
    ) -> list[Any]:
        found = [
            run
            for run in self.runs
            if (run.workspace_id, run.project_id, run.branch_id)
            == (workspace_id, project_id, branch_id)
        ]
        found.sort(key=lambda run: (run.created_at, run.id))
        return found[:limit]

    async def cached(
        self, *, workspace_id: str, project_id: str, plan_id: str, keys: list[tuple[str, str]]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        found: dict[tuple[str, str], dict[str, Any]] = {}
        for check, fingerprint in keys:
            kept = self.cache.get((workspace_id, project_id, plan_id, check, fingerprint))
            if kept is not None:
                found[(check, fingerprint)] = {"findings": list(kept[1])}
        return found

    async def keep_cached(
        self,
        *,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        entries: list[dict[str, Any]],
        keep: int,
    ) -> None:
        for entry in entries:
            self._tick += 1
            key = (workspace_id, project_id, plan_id, entry["check"], entry["fingerprint"])
            self.cache[key] = (self._tick, list(entry["findings"]))
            mine = sorted(
                ((order, k) for k, (order, _) in self.cache.items() if k[:4] == key[:4]),
                reverse=True,
            )
            for _, old in mine[keep:]:
                del self.cache[old]

    async def delete_project(self, *, workspace_id: str, project_id: str) -> None:
        self.runs = [
            run
            for run in self.runs
            if (run.workspace_id, run.project_id) != (workspace_id, project_id)
        ]
        self.cache = {k: v for k, v in self.cache.items() if k[:2] != (workspace_id, project_id)}


class InMemoryReviewDismissalStore:
    """Advisory findings dismissed on a proposal in review (CON-016), in memory."""

    def __init__(self) -> None:
        self.dismissals: list[Any] = []
        # What the review screen showed: (workspace, project, proposal, key) → kind.
        self.seen: dict[tuple[str, str, str, str], str] = {}

    async def for_mutation(
        self, *, workspace_id: str, project_id: str, mutation_id: str
    ) -> list[Any]:
        return [
            item
            for item in self.dismissals
            if (item.workspace_id, item.project_id, item.mutation_id)
            == (workspace_id, project_id, mutation_id)
        ]

    async def add(self, dismissal: Any) -> None:
        from dataclasses import replace

        self.dismissals = [
            replace(item, withdrawn_at=dismissal.created_at)
            if (item.workspace_id, item.project_id, item.mutation_id, item.fingerprint)
            == (
                dismissal.workspace_id,
                dismissal.project_id,
                dismissal.mutation_id,
                dismissal.fingerprint,
            )
            and item.withdrawn_at is None
            else item
            for item in self.dismissals
        ]
        self.dismissals.append(dismissal)

    async def withdraw(
        self, *, workspace_id: str, project_id: str, mutation_id: str, dismissal_id: str, at: Any
    ) -> bool:
        from dataclasses import replace

        for index, item in enumerate(self.dismissals):
            if (item.workspace_id, item.project_id, item.mutation_id, item.id) == (
                workspace_id,
                project_id,
                mutation_id,
                dismissal_id,
            ) and item.withdrawn_at is None:
                self.dismissals[index] = replace(item, withdrawn_at=at)
                return True
        return False

    async def see(
        self, *, workspace_id: str, project_id: str, mutation_id: str, found: list[Any]
    ) -> None:
        for fingerprint, code in found:
            self.seen.setdefault((workspace_id, project_id, mutation_id, fingerprint), code)

    async def seen_counts(self, *, workspace_id: str, project_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for (w, p, _m, _f), code in self.seen.items():
            if (w, p) == (workspace_id, project_id):
                counts[code] = counts.get(code, 0) + 1
        return counts

    async def restore(self, dismissal: Any) -> bool:
        if any(
            (item.workspace_id, item.project_id, item.id)
            == (dismissal.workspace_id, dismissal.project_id, dismissal.id)
            or (
                dismissal.withdrawn_at is None
                and item.withdrawn_at is None
                and (item.workspace_id, item.project_id, item.mutation_id, item.fingerprint)
                == (
                    dismissal.workspace_id,
                    dismissal.project_id,
                    dismissal.mutation_id,
                    dismissal.fingerprint,
                )
            )
            for item in self.dismissals
        ):
            return False
        self.dismissals.append(dismissal)
        return True

    async def for_project(self, *, workspace_id: str, project_id: str) -> list[Any]:
        return [
            item
            for item in self.dismissals
            if (item.workspace_id, item.project_id) == (workspace_id, project_id)
        ]


class InMemoryScopedDismissalStore:
    """Dismissals for a character or a stretch of the story (CON-016), in memory."""

    def __init__(self) -> None:
        self.items: list[Any] = []

    async def list(self, *, workspace_id: str, project_id: str, branch_id: str) -> list[Any]:
        return sorted(
            (
                item
                for item in self.items
                if (item.workspace_id, item.project_id, item.branch_id)
                == (workspace_id, project_id, branch_id)
            ),
            key=lambda item: (item.created_at, item.id),
        )

    async def count(self, *, workspace_id: str, project_id: str, branch_id: str) -> int:
        return len(
            await self.list(workspace_id=workspace_id, project_id=project_id, branch_id=branch_id)
        )

    async def add(self, item: Any) -> None:
        self.items.append(item)

    async def restore(self, item: Any) -> bool:
        if any(
            (other.workspace_id, other.project_id, other.branch_id, other.id)
            == (item.workspace_id, item.project_id, item.branch_id, item.id)
            for other in self.items
        ):
            return False
        self.items.append(item)
        return True

    async def withdraw(
        self, *, workspace_id: str, project_id: str, branch_id: str, dismissal_id: str, at: Any
    ) -> bool:
        from dataclasses import replace

        for index, item in enumerate(self.items):
            if (item.workspace_id, item.project_id, item.branch_id, item.id) == (
                workspace_id,
                project_id,
                branch_id,
                dismissal_id,
            ) and item.withdrawn_at is None:
                self.items[index] = replace(item, withdrawn_at=at)
                return True
        return False


class InMemoryObservationStore:
    """What a model saw in pictures (AST-005) and each picture's context choice."""

    def __init__(self) -> None:
        self.observations: dict[tuple[str, str, str], Any] = {}
        self.order: list[tuple[str, str, str]] = []
        self.choices: dict[tuple[str, str, str], Any] = {}

    async def add(self, observations: Any) -> None:
        for observation in observations:
            key = (observation.workspace_id, observation.project_id, observation.id)
            if key in self.observations:
                continue
            self.observations[key] = observation
            self.order.append(key)

    async def save(self, observation: Any) -> None:
        key = (observation.workspace_id, observation.project_id, observation.id)
        if key in self.observations:
            self.observations[key] = observation

    async def get(self, *, workspace_id: str, project_id: str, observation_id: str) -> Any:
        return self.observations.get((workspace_id, project_id, observation_id))

    async def for_assets(self, *, workspace_id: str, project_id: str, asset_ids: Any) -> list[Any]:
        wanted = set(asset_ids)
        return [
            self.observations[key]
            for key in self.order
            if key[:2] == (workspace_id, project_id) and self.observations[key].asset_id in wanted
        ]

    async def all(self, *, workspace_id: str, project_id: str) -> list[Any]:
        return [
            self.observations[key] for key in self.order if key[:2] == (workspace_id, project_id)
        ]

    async def get_choices(self, *, workspace_id: str, project_id: str, asset_ids: Any) -> dict:
        return {
            asset_id: self.choices[(workspace_id, project_id, asset_id)]
            for asset_id in asset_ids
            if (workspace_id, project_id, asset_id) in self.choices
        }

    async def set_choice(
        self, *, workspace_id: str, project_id: str, asset_id: str, choice: Any
    ) -> bool:
        self.choices[(workspace_id, project_id, asset_id)] = choice
        return True

    async def delete_for_asset(self, *, workspace_id: str, project_id: str, asset_id: str) -> int:
        doomed = [
            key
            for key in self.order
            if key[:2] == (workspace_id, project_id) and self.observations[key].asset_id == asset_id
        ]
        for key in doomed:
            del self.observations[key]
        self.order = [key for key in self.order if key not in set(doomed)]
        return len(doomed)

    async def delete_project(self, *, workspace_id: str, project_id: str) -> int:
        doomed = [key for key in self.order if key[:2] == (workspace_id, project_id)]
        for key in doomed:
            del self.observations[key]
        self.order = [key for key in self.order if key[:2] != (workspace_id, project_id)]
        for key in [key for key in self.choices if key[:2] == (workspace_id, project_id)]:
            del self.choices[key]
        return len(doomed)


# ------------------------------------------------- picture processing (AST-003, area A)


class InMemoryMediaProcessingStore:
    """Processing state, smaller copies and segments of picture versions, in memory.

    Reads picture versions from the ``InMemoryMediaStore`` it is given, as the PostgreSQL
    store joins ``media_asset_versions``."""

    def __init__(self, media: Any = None) -> None:
        self.media = media
        self.states: dict[tuple[str, str, str], Any] = {}
        self.variants: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.segments: dict[tuple[str, str, str], Any] = {}

    async def get_processing(self, *, workspace_id, project_id, version_id):
        return self.states.get((workspace_id, project_id, version_id))

    async def list_processing(self, *, workspace_id, project_id, version_ids):
        return {
            vid: self.states[(workspace_id, project_id, vid)]
            for vid in version_ids
            if (workspace_id, project_id, vid) in self.states
        }

    async def queue(
        self, *, workspace_id, project_id, version_id, asset_id, checksum, processor_version, job_id
    ):
        from backend.domain.media import MediaProcessing, ProcessingStatus

        key = (workspace_id, project_id, version_id)
        old = self.states.get(key)
        state = MediaProcessing(
            workspace_id=workspace_id,
            project_id=project_id,
            asset_version_id=version_id,
            asset_id=asset_id,
            status=ProcessingStatus.QUEUED,
            processor_version=processor_version,
            source_checksum=checksum,
            attempts=old.attempts if old is not None else 0,
            width=old.width if old is not None else None,
            height=old.height if old is not None else None,
            detected_format=old.detected_format if old is not None else None,
            job_id=job_id,
            queued_at=datetime.now(UTC),
        )
        self.states[key] = state
        return state

    async def start(self, *, workspace_id, project_id, version_id):
        from backend.domain.media import ProcessingStatus

        key = (workspace_id, project_id, version_id)
        state = self.states.get(key)
        if state is None:
            return None
        self.states[key] = state.model_copy(
            update={
                "status": ProcessingStatus.PROCESSING,
                "attempts": state.attempts + 1,
                "failure_code": None,
                "started_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        )
        return self.states[key]

    async def fail(self, *, workspace_id, project_id, version_id, failure_code):
        from backend.domain.media import ProcessingStatus

        key = (workspace_id, project_id, version_id)
        if key in self.states:
            self.states[key] = self.states[key].model_copy(
                update={
                    "status": ProcessingStatus.FAILED,
                    "failure_code": failure_code,
                    "finished_at": datetime.now(UTC),
                }
            )

    async def complete(
        self,
        *,
        workspace_id,
        project_id,
        version_id,
        width,
        height,
        detected_format,
        variants,
        whole,
    ):
        from backend.domain.media import ProcessingStatus

        key = (workspace_id, project_id, version_id)
        old = self.variants.get(key, {})
        self.variants[key] = {variant.kind.value: variant for variant in variants}
        existing = next(
            (
                s
                for s in self.segments.values()
                if (s.workspace_id, s.project_id, s.asset_version_id) == key
                and s.kind.value == "whole"
            ),
            None,
        )
        if existing is not None:
            self.segments[(workspace_id, project_id, existing.id)] = existing.model_copy(
                update={"width": whole.width, "height": whole.height, "retired_at": None}
            )
        else:
            self.segments[(workspace_id, project_id, whole.id)] = whole
        if key in self.states:
            self.states[key] = self.states[key].model_copy(
                update={
                    "status": ProcessingStatus.READY,
                    "failure_code": None,
                    "width": width,
                    "height": height,
                    "detected_format": detected_format,
                    "finished_at": datetime.now(UTC),
                }
            )
        kept = {variant.object_key for variant in variants}
        return [v.object_key for v in old.values() if v.object_key not in kept]

    async def get_variant(self, *, workspace_id, project_id, version_id, kind):
        return self.variants.get((workspace_id, project_id, version_id), {}).get(kind.value)

    async def list_variants(self, *, workspace_id, project_id, version_ids):
        return {
            vid: sorted(
                self.variants[(workspace_id, project_id, vid)].values(),
                key=lambda v: v.kind.value,
            )
            for vid in version_ids
            if self.variants.get((workspace_id, project_id, vid))
        }

    async def variant_object_keys(self, *, workspace_id, project_id):
        return {
            variant.object_key
            for (w, p, _), found in self.variants.items()
            if (w, p) == (workspace_id, project_id)
            for variant in found.values()
        }

    async def known_object_keys(self, *, workspace_id, project_id):
        keys = await self.variant_object_keys(workspace_id=workspace_id, project_id=project_id)
        if self.media is not None:
            keys |= {
                version.object_key
                for (w, p, _), version in self.media.versions.items()
                if (w, p) == (workspace_id, project_id)
            }
        return keys

    async def remove_variants(self, *, workspace_id, project_id, version_id):
        key = (workspace_id, project_id, version_id)
        removed = [v.object_key for v in self.variants.pop(key, {}).values()]
        for skey, segment in list(self.segments.items()):
            if (segment.workspace_id, segment.project_id, segment.asset_version_id) == key:
                self.segments[skey] = segment.model_copy(
                    update={"retired_at": segment.retired_at or datetime.now(UTC)}
                )
        self.states.pop(key, None)
        return removed

    async def list_segments(self, *, workspace_id, project_id, version_id):
        found = [
            s
            for s in self.segments.values()
            if (s.workspace_id, s.project_id, s.asset_version_id)
            == (workspace_id, project_id, version_id)
            and s.retired_at is None
        ]
        return sorted(found, key=lambda s: (s.kind.value != "whole", s.created_at, s.id))

    async def list_regions(self, *, workspace_id, project_id):
        return sorted(
            (
                s
                for s in self.segments.values()
                if (s.workspace_id, s.project_id) == (workspace_id, project_id)
                and s.kind.value == "region"
                and s.retired_at is None
            ),
            key=lambda s: (s.created_at, s.id),
        )

    async def add_segment(self, segment):
        key = (segment.workspace_id, segment.project_id, segment.id)
        if key in self.segments:
            self.segments[key] = self.segments[key].model_copy(
                update={"label": segment.label, "entity_id": segment.entity_id}
            )
        else:
            self.segments[key] = segment
        return self.segments[key]

    async def get_segment(self, *, workspace_id, project_id, segment_id):
        return self.segments.get((workspace_id, project_id, segment_id))

    async def update_segment(self, segment):
        key = (segment.workspace_id, segment.project_id, segment.id)
        current = self.segments.get(key)
        if current is None or current.kind.value != "region" or current.retired_at is not None:
            return None
        self.segments[key] = current.model_copy(
            update={
                field: getattr(segment, field)
                for field in ("x", "y", "width", "height", "label", "entity_id", "content_hash")
            }
        )
        return self.segments[key]

    async def retire_segment(self, *, workspace_id, project_id, segment_id):
        key = (workspace_id, project_id, segment_id)
        segment = self.segments.get(key)
        if segment is None or segment.kind.value != "region" or segment.retired_at is not None:
            return None
        self.segments[key] = segment.model_copy(update={"retired_at": datetime.now(UTC)})
        return self.segments[key]

    async def workspace_bytes(self, *, workspace_id):
        # Each stored file once, however many records use it.
        files: dict[tuple[str, str], int] = {}
        for (w, p, _), found in self.variants.items():
            if w == workspace_id:
                for variant in found.values():
                    files[(p, variant.object_key)] = variant.byte_length
        if self.media is not None:
            for (w, p, _), version in self.media.versions.items():
                if w == workspace_id and version.deleted_at is None:
                    files[(p, version.object_key)] = version.byte_length
        return sum(files.values())

    async def lag(self):
        now = datetime.now(UTC)
        waiting = [s for s in self.states.values() if s.status.value in ("queued", "processing")]
        oldest = max(((now - s.queued_at).total_seconds() for s in waiting), default=None)
        return {
            "waiting": len(waiting),
            "oldest_seconds": oldest,
            "failed": sum(1 for s in self.states.values() if s.status.value == "failed"),
        }

    async def pending_versions(
        self,
        *,
        processor_version,
        content_types,
        workspace_id=None,
        project_id=None,
        limit=500,
        include_failed=False,
        stale_seconds=3600,
    ):
        from backend.adapters.media_store import PendingVersion

        if self.media is None:
            return []
        now = datetime.now(UTC)
        found = []
        for (w, p, vid), version in sorted(
            self.media.versions.items(), key=lambda item: item[1].created_at
        ):
            if workspace_id is not None and w != workspace_id:
                continue
            if project_id is not None and p != project_id:
                continue
            asset = self.media.assets.get((w, p, version.asset_id))
            if asset is None or asset.lifecycle.value != "active":
                continue
            if version.retired_revision is not None or version.content_type not in content_types:
                continue
            state = self.states.get((w, p, vid))
            due = (
                state is None
                or (
                    state.status.value in ("queued", "processing")
                    and (now - state.updated_at).total_seconds() > stale_seconds
                )
                or (
                    state.status.value in ("ready", "failed")
                    and (
                        state.processor_version != processor_version
                        or state.source_checksum != version.checksum
                    )
                )
                or (include_failed and state.status.value == "failed")
            )
            if not due:
                continue
            actor = next(
                (
                    event.actor_user_id
                    for event in self.media.events
                    if event.asset_version_id == vid and event.actor_user_id
                ),
                None,
            )
            found.append(
                PendingVersion(
                    workspace_id=w,
                    project_id=p,
                    asset_id=version.asset_id,
                    asset_version_id=vid,
                    checksum=version.checksum,
                    actor_user_id=actor,
                )
            )
        return found[:limit]

    async def retired_with_variants(self, *, limit=1000):
        found = []
        for (w, p, vid), copies in self.variants.items():
            if not copies or self.media is None:
                continue
            version = self.media.versions.get((w, p, vid))
            asset = self.media.assets.get((w, p, version.asset_id)) if version else None
            if version is None or asset is None:
                continue
            if version.retired_revision is not None or asset.lifecycle.value != "active":
                found.append((w, p, vid))
        return found[:limit]

    async def projects_with_media(self):
        if self.media is None:
            return []
        return sorted({(w, p) for (w, p, _) in self.media.assets})

    async def health(self):
        return True


class FakeImageProcessor:
    """Stands in for the separate, limited decoder in service tests: answers from a script
    (an ``ImageRejected`` code, or a size) without decoding anything."""

    version = "pictures-1"

    def __init__(self) -> None:
        self.calls = 0
        # Codes to fail with, in turn; then success.
        self.failures: list[str] = []
        self.size = (640, 480)

    def capability(self) -> dict[str, Any]:
        return {"available": True, "version": "fake", "isolated": False}

    async def process(self, data: bytes) -> Any:
        from backend.adapters.image_processor import (
            VARIANT_SIZES,
            ImageRejected,
            ProcessedImage,
            Rendition,
        )

        self.calls += 1
        if self.failures:
            raise ImageRejected(self.failures.pop(0))
        width, height = self.size
        renditions = []
        for kind, side in sorted(VARIANT_SIZES.items(), key=lambda item: item[1]):
            scale = min(1.0, side / max(width, height))
            renditions.append(
                Rendition(
                    kind=kind,
                    width=max(1, round(width * scale)),
                    height=max(1, round(height * scale)),
                    data=b"RIFF\x00\x00\x00\x00WEBP"
                    + kind.encode()
                    + hashlib.sha256(data).digest(),
                )
            )
        return ProcessedImage(
            width=width, height=height, detected_format="PNG", renditions=tuple(renditions)
        )


class InMemoryConnectorStore:
    """Connected computers, codes and the request queue (SCN-002A), in memory.

    Guarded by a lock, so a test can drive the site from one thread while a server on
    another answers the connector."""

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self.codes: dict[str, Any] = {}
        self.devices_by_id: dict[str, Any] = {}
        self.requests: dict[str, Any] = {}

    async def add_code(self, code: Any) -> bool:
        with self._lock:
            if any(c.user_code == code.user_code for c in self.codes.values()):
                return False
            self.codes[code.device_code_hash] = copy.deepcopy(code)
            return True

    async def code_by_device_hash(self, device_code_hash: str) -> Any | None:
        with self._lock:
            found = self.codes.get(device_code_hash)
            return copy.deepcopy(found)

    async def code_by_user_code(self, user_code: str) -> Any | None:
        with self._lock:
            found = next((c for c in self.codes.values() if c.user_code == user_code), None)
            return copy.deepcopy(found)

    async def decide_code(
        self, *, user_code: str, approve: bool, workspace_id: str, decided_by: str, at: datetime
    ) -> Any | None:
        from backend.domain.connector import CodeStatus

        with self._lock:
            code = next((c for c in self.codes.values() if c.user_code == user_code), None)
            if code is None or code.status is not CodeStatus.PENDING or at >= code.expires_at:
                return None
            code.status = CodeStatus.APPROVED if approve else CodeStatus.DENIED
            code.workspace_id, code.decided_by, code.decided_at = workspace_id, decided_by, at
            return copy.deepcopy(code)

    async def touch_code(self, *, device_code_hash: str, at: datetime, interval: int) -> None:
        with self._lock:
            code = self.codes.get(device_code_hash)
            if code is not None:
                code.last_polled_at, code.interval_seconds = at, interval

    async def issue_code(self, *, device_code_hash: str, device: Any) -> bool:
        from backend.domain.connector import CodeStatus

        with self._lock:
            code = self.codes.get(device_code_hash)
            if code is None or code.status is not CodeStatus.APPROVED:
                return False
            code.status, code.device_id = CodeStatus.ISSUED, device.id
            self.devices_by_id[device.id] = copy.deepcopy(device)
            return True

    async def device(self, device_id: str) -> Any | None:
        with self._lock:
            return copy.deepcopy(self.devices_by_id.get(device_id))

    async def device_by_credential(self, credential_hash: str) -> Any | None:
        with self._lock:
            found = next(
                (
                    d
                    for d in self.devices_by_id.values()
                    if credential_hash in (d.credential_hash, d.previous_hash)
                ),
                None,
            )
            return copy.deepcopy(found)

    async def devices(self, workspace_id: str) -> list[Any]:
        with self._lock:
            found = [d for d in self.devices_by_id.values() if d.workspace_id == workspace_id]
            return copy.deepcopy(sorted(found, key=lambda d: d.created_at, reverse=True))

    async def seen(
        self, *, device_id: str, at: datetime, version: str, capabilities: dict | None
    ) -> None:
        from backend.domain.connector import DeviceCapabilities, DeviceStatus

        with self._lock:
            device = self.devices_by_id.get(device_id)
            if device is None or device.status is not DeviceStatus.ACTIVE:
                return
            device.last_seen_at, device.connector_version = at, version
            if capabilities is not None:
                device.capabilities = DeviceCapabilities.from_json(capabilities)

    async def rotate(
        self,
        *,
        device_id: str,
        expected_hash: str,
        new_hash: str,
        previous_hash: str,
        at: datetime,
        grace_until: datetime,
        only_if_unused: bool = False,
    ) -> bool:
        from backend.domain.connector import DeviceStatus

        with self._lock:
            device = self.devices_by_id.get(device_id)
            if (
                device is None
                or device.status is not DeviceStatus.ACTIVE
                or device.credential_hash != expected_hash
                or (only_if_unused and device.credential_last_used_at is not None)
            ):
                return False
            device.previous_hash, device.previous_valid_until = previous_hash, grace_until
            device.credential_hash, device.credential_issued_at = new_hash, at
            device.credential_last_used_at = None
            return True

    async def credential_used(self, *, device_id: str, credential_hash: str, at: datetime) -> None:
        from backend.domain.connector import DeviceStatus

        with self._lock:
            device = self.devices_by_id.get(device_id)
            if (
                device is not None
                and device.status is DeviceStatus.ACTIVE
                and device.credential_hash == credential_hash
            ):
                device.credential_last_used_at = at

    async def revoke(
        self, *, workspace_id: str, device_id: str, reason: str, by: str | None, at: datetime
    ) -> Any | None:
        from backend.domain.connector import DeviceStatus, RequestStatus, RevokedBecause

        with self._lock:
            device = self.devices_by_id.get(device_id)
            if (
                device is None
                or device.workspace_id != workspace_id
                or device.status is not DeviceStatus.ACTIVE
            ):
                return None
            device.status, device.revoked_reason = DeviceStatus.REVOKED, RevokedBecause(reason)
            device.revoked_by, device.revoked_at, device.in_use = by, at, False
            device.previous_hash = device.previous_valid_until = None
            for request in self.requests.values():
                if request.device_id == device_id and request.status in (
                    RequestStatus.WAITING,
                    RequestStatus.TAKEN,
                ):
                    request.status, request.failure = RequestStatus.FAILED, "disconnected"
                    request.payload = request.result = None
                    request.finished_at = at
            return copy.deepcopy(device)

    async def set_in_use(self, *, workspace_id: str, device_id: str | None) -> None:
        from backend.domain.connector import DeviceStatus

        with self._lock:
            for device in self.devices_by_id.values():
                if device.workspace_id == workspace_id:
                    device.in_use = device.id == device_id and device.status is DeviceStatus.ACTIVE

    async def in_use(self, workspace_id: str) -> Any | None:
        from backend.domain.connector import DeviceStatus

        with self._lock:
            found = next(
                (
                    d
                    for d in self.devices_by_id.values()
                    if d.workspace_id == workspace_id
                    and d.in_use
                    and d.status is DeviceStatus.ACTIVE
                ),
                None,
            )
            return copy.deepcopy(found)

    async def enqueue(self, request: Any) -> None:
        with self._lock:
            self.requests[request.id] = copy.deepcopy(request)

    async def take_next(self, *, device_id: str, at: datetime, since: datetime) -> Any | None:
        from backend.domain.connector import DeviceStatus, RequestStatus

        with self._lock:
            device = self.devices_by_id.get(device_id)
            if device is None or device.status is not DeviceStatus.ACTIVE:
                return None
            waiting = sorted(
                (
                    r
                    for r in self.requests.values()
                    if r.device_id == device_id
                    and r.status is RequestStatus.WAITING
                    and r.created_at > since
                ),
                key=lambda r: r.created_at,
            )
            if not waiting:
                return None
            taken = waiting[0]
            taken.status, taken.taken_at = RequestStatus.TAKEN, at
            return copy.deepcopy(taken)

    async def request(self, request_id: str) -> Any | None:
        with self._lock:
            return copy.deepcopy(self.requests.get(request_id))

    async def answer(
        self,
        *,
        device_id: str,
        request_id: str,
        status: str,
        result: dict | None,
        result_status: int | None,
        failure: str | None,
        model: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        at: datetime,
    ) -> bool:
        from backend.domain.connector import RequestStatus

        with self._lock:
            request = self.requests.get(request_id)
            if (
                request is None
                or request.device_id != device_id
                or request.status is not RequestStatus.TAKEN
            ):
                return False
            request.status = RequestStatus(status)
            request.result, request.result_status = copy.deepcopy(result), result_status
            request.failure, request.model = failure, model or request.model
            request.prompt_tokens, request.completion_tokens = prompt_tokens, completion_tokens
            request.finished_at, request.payload = at, None
            return True

    async def close(self, *, request_id: str, failure: str | None, at: datetime) -> None:
        from backend.domain.connector import RequestStatus

        with self._lock:
            request = self.requests.get(request_id)
            if request is None:
                return
            request.payload = request.result = None
            if request.status in (RequestStatus.WAITING, RequestStatus.TAKEN):
                request.status, request.failure = RequestStatus.EXPIRED, failure
            request.finished_at = request.finished_at or at

    async def sweep(self, *, at: datetime, open_before: datetime, read_before: datetime) -> int:
        from backend.domain.connector import RequestStatus

        cleared = 0
        with self._lock:
            for request in self.requests.values():
                if request.payload is None and request.result is None:
                    continue
                if request.status in (RequestStatus.WAITING, RequestStatus.TAKEN):
                    if request.created_at >= open_before:
                        continue
                    request.status, request.failure = RequestStatus.EXPIRED, "abandoned"
                elif request.finished_at is None or request.finished_at >= read_before:
                    continue
                request.payload = request.result = None
                request.finished_at = request.finished_at or at
                cleared += 1
        return cleared

    async def delete_project(self, workspace_id: str, project_id: str) -> int:
        with self._lock:
            gone = [
                key
                for key, request in self.requests.items()
                if request.workspace_id == workspace_id and request.project_id == project_id
            ]
            for key in gone:
                del self.requests[key]
            return len(gone)

    async def delete_workspace(self, workspace_id: str) -> int:
        with self._lock:
            removed = 0
            for table in (self.requests, self.devices_by_id):
                for key in [k for k, v in table.items() if v.workspace_id == workspace_id]:
                    del table[key]
                    removed += 1
            for key in [k for k, v in self.codes.items() if v.workspace_id == workspace_id]:
                del self.codes[key]
                removed += 1
            return removed


class InMemoryConversationStore:
    """In-memory ``PostgresConversationStore``: kept threads and suggestion runs (0054)."""

    def __init__(self) -> None:
        from backend.domain.conversations import SuggestionRun, Thread

        self.threads: dict[tuple[str, str, str], Thread] = {}
        self.runs: dict[tuple[str, str, str], SuggestionRun] = {}

    async def find_thread(self, *, workspace_id, project_id, actor_user_id, kind, branch_id):
        for (w, p, _), thread in self.threads.items():
            if (
                (w, p) == (workspace_id, project_id)
                and thread.actor_user_id == actor_user_id
                and thread.kind == kind
                and (thread.branch_id or "") == (branch_id or "")
            ):
                return thread.model_copy(deep=True)
        return None

    async def start_thread(self, thread):
        existing = await self.find_thread(
            workspace_id=thread.workspace_id,
            project_id=thread.project_id,
            actor_user_id=thread.actor_user_id,
            kind=thread.kind,
            branch_id=thread.branch_id,
        )
        if existing is not None:
            return existing
        self.threads[(thread.workspace_id, thread.project_id, thread.id)] = thread.model_copy(
            deep=True
        )
        return thread.model_copy(deep=True)

    async def add_turns(self, *, workspace_id, project_id, thread_id, turns, now):
        from backend.domain.conversations import MAX_TURNS, pinned

        thread = self.threads.get((workspace_id, project_id, thread_id))
        if thread is None:
            return
        last = max((turn.position for turn in thread.turns), default=0)
        added = [
            turn.model_copy(update={"position": last + offset, "created_at": now})
            for offset, turn in enumerate(turns, start=1)
        ]
        cut = last + len(added) - MAX_TURNS
        kept = [
            turn
            for turn in [*thread.turns, *added]
            if turn.position > cut or pinned(thread.kind, turn)
        ]
        self.threads[(workspace_id, project_id, thread_id)] = thread.model_copy(
            update={"turns": kept, "updated_at": now}
        )

    async def add_tokens(self, *, workspace_id, project_id, thread_id, tokens):
        thread = self.threads.get((workspace_id, project_id, thread_id))
        if thread is not None:
            self.threads[(workspace_id, project_id, thread_id)] = thread.model_copy(
                update={"tokens_used": thread.tokens_used + max(0, tokens)}
            )

    async def delete_thread(self, *, workspace_id, project_id, thread_id):
        return 1 if self.threads.pop((workspace_id, project_id, thread_id), None) else 0

    async def delete_member_threads(self, *, workspace_id, actor_user_id):
        gone = [
            key
            for key, thread in self.threads.items()
            if key[0] == workspace_id and thread.actor_user_id == actor_user_id
        ]
        for key in gone:
            del self.threads[key]
        return len(gone)

    async def list_threads(self, *, workspace_id, project_id):
        return sorted(
            (
                thread.model_copy(deep=True)
                for (w, p, _), thread in self.threads.items()
                if (w, p) == (workspace_id, project_id)
            ),
            key=lambda thread: (thread.created_at, thread.id),
        )

    async def add_run(self, run):
        self.runs.setdefault((run.workspace_id, run.project_id, run.id), run)
        return run

    async def list_runs(self, *, workspace_id, project_id, kind, limit, branch_id=None):
        found = [
            run
            for (w, p, _), run in self.runs.items()
            if (w, p) == (workspace_id, project_id)
            and (kind is None or run.kind == kind)
            and (run.branch_id or "") == (branch_id or "")
        ]
        found.sort(key=lambda run: (run.created_at, run.id), reverse=True)
        return found[:limit]

    async def settle_run(
        self, *, workspace_id, project_id, run_id, kept, settled_at, kept_for_you=None
    ):
        from backend.domain.conversations import KEPT_FOR_YOU

        key = (workspace_id, project_id, run_id)
        run = self.runs.get(key)
        if run is not None and run.settled_at is None:
            details = dict(run.details)
            if kept_for_you is not None:
                details[KEPT_FOR_YOU] = kept_for_you
            self.runs[key] = run.model_copy(
                update={"kept": kept, "settled_at": settled_at, "details": details}
            )

    def delete_project(self, workspace_id, project_id):
        for store in (self.threads, self.runs):
            for key in [k for k in store if k[:2] == (workspace_id, project_id)]:
                del store[key]
