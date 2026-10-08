"""Application/domain ports.

Services depend on these protocols rather than concrete SDKs, drivers, or
process-global singletons. Adapters in ``backend.adapters`` implement them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from backend.domain.canon.assertions import Belief, Fact
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import Event
from backend.domain.canon.revisions import CanonRevision, Provenance
from backend.domain.commit import ApplyOutcome, CommitMarker
from backend.domain.credentials import Credential, CredentialProvider
from backend.domain.history import CanonTag, CanonTagHistoryEntry
from backend.domain.identity import IdentityClaims, LoginAttempt, Role, Session, User
from backend.domain.journal import (
    Branch,
    BranchReservation,
    CommitAttempt,
    CommitEnvelope,
    MutationOperation,
    MutationSet,
    OutboxMessage,
    ReviewDecision,
)
from backend.domain.media import AssetLink, MediaAsset, MediaAssetVersion
from backend.domain.objects import ObjectStat
from backend.domain.operations import CanonOperation
from backend.domain.projection import ProjectionWatermark, RecordVersion
from backend.domain.schemas import SchemaVersion
from backend.domain.tenancy import AuditEvent, Invitation, Membership, Project, Workspace
from backend.domain.usage import UsageRecord, UsageTotals
from backend.graph.schema import Edge, Node


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class IdGenerator(Protocol):
    def new_id(self, kind: str | None = None) -> str: ...


@runtime_checkable
class GraphRepository(Protocol):
    """Canonical story graph. Every operation is confined to one workspace and project."""

    def add_node(self, node: Node) -> None: ...

    def get_node(self, node_id: str, *, workspace_id: str, project_id: str) -> Node | None: ...

    def update_node(self, node: Node) -> Node | None: ...

    def add_edge(self, edge: Edge) -> None: ...

    def get_edges(self, node_id: str, *, workspace_id: str, project_id: str) -> list[Edge]: ...

    def get_nodes(
        self,
        *,
        workspace_id: str,
        project_id: str,
        node_type: str | None = None,
        node_id: str | None = None,
    ) -> list[Node]: ...

    def search_nodes_by_name(
        self,
        query: str,
        *,
        workspace_id: str,
        project_id: str,
        type: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def search_semantic_nodes(
        self,
        embedding: Sequence[float],
        *,
        workspace_id: str,
        project_id: str,
        top_k: int = 5,
        min_score: float = 0.5,
    ) -> list[str]: ...

    def traverse(
        self,
        anchor_ids: Sequence[str],
        policy: Any,
        *,
        workspace_id: str,
        project_id: str,
    ) -> list[dict[str, Any]]: ...

    def delete_node(self, node_id: str, *, workspace_id: str, project_id: str) -> bool: ...

    def delete_workspace_nodes(self, workspace_id: str) -> int: ...

    def delete_project_nodes(self, workspace_id: str, project_id: str) -> int: ...

    def ping(self) -> bool: ...

    def close(self) -> None: ...


@runtime_checkable
class VectorStorePort(Protocol):
    def add_documents(
        self,
        documents: list[str],
        metadatas: list[dict[str, Any]],
        ids: list[str],
        *,
        workspace_id: str,
        project_id: str,
    ) -> None: ...

    def query(
        self,
        query_text: str,
        *,
        workspace_id: str,
        project_id: str,
        n_results: int = 5,
    ) -> list[dict[str, Any]]: ...

    def delete(self, ids: Sequence[str], *, workspace_id: str, project_id: str) -> None: ...

    def delete_workspace(self, *, workspace_id: str) -> int: ...

    def delete_project(self, *, workspace_id: str, project_id: str) -> int: ...

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

    def remove_custom_node_type(self, node_type: str) -> None: ...

    def remove_custom_edge_type(self, edge_type: str) -> None: ...

    def get_all_node_types(self) -> list[str]: ...

    def get_all_edge_types(self) -> list[str]: ...

    @property
    def custom_node_types(self) -> set[str]: ...

    @property
    def custom_edge_types(self) -> set[str]: ...


@runtime_checkable
class SchemaVersionStorePort(Protocol):
    """Immutable, project-scoped schema versions (draft → published)."""

    async def create_draft(
        self,
        *,
        workspace_id: str,
        project_id: str,
        definition: dict[str, Any],
        content_hash: str,
        compatibility: str,
        created_by_user_id: str | None,
    ) -> SchemaVersion: ...

    async def get(
        self, *, workspace_id: str, project_id: str, version_id: str
    ) -> SchemaVersion | None: ...

    async def get_published(
        self, *, workspace_id: str, project_id: str
    ) -> SchemaVersion | None: ...

    async def list(self, *, workspace_id: str, project_id: str) -> list[SchemaVersion]: ...

    async def publish(
        self, *, workspace_id: str, project_id: str, version_id: str
    ) -> SchemaVersion | None: ...


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


@dataclass(frozen=True)
class ProviderCapabilities:
    """Typed provider/model capability description (no media transfer).

    Later adapters add image/audio/video modalities and limits without changing
    configuration or diagnostics again. Multimodal payloads are Phase 4–5.
    """

    provider: str
    model: str
    input_modalities: frozenset[str] = frozenset({"text"})
    output_modalities: frozenset[str] = frozenset({"text"})
    structured_output: bool = True
    max_context_tokens: int | None = None
    supports_streaming: bool = False


class AIError(RuntimeError):
    """Base class for normalized AI gateway failures."""


class AIProviderError(AIError):
    """Provider refused, timed out, or returned a transport/server error."""


class AIInvalidOutputError(AIError):
    """Provider returned output that could not be parsed into the requested schema."""


@runtime_checkable
class AIGateway(Protocol):
    def is_available(self) -> bool: ...

    def capabilities(self) -> ProviderCapabilities: ...

    async def generate_structured(
        self,
        *,
        messages: list[dict[str, str]],
        response_format: type,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> StructuredResponse: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class ArtifactStore(Protocol):
    """Debug artifact store (text payloads). Product object storage is the ObjectStorePort."""

    def save(self, key: str, payload: str, *, workspace_id: str, project_id: str) -> None: ...

    def load(self, key: str, *, workspace_id: str, project_id: str) -> str | None: ...

    def delete_workspace(self, *, workspace_id: str) -> int: ...

    def delete_project(self, *, workspace_id: str, project_id: str) -> int: ...


@runtime_checkable
class ObjectStorePort(Protocol):
    """Immutable, project-scoped product object storage with server-generated keys."""

    def put(
        self,
        data: bytes,
        *,
        workspace_id: str,
        project_id: str,
        content_type: str = "application/octet-stream",
    ) -> ObjectStat: ...

    def open(self, key: str, *, workspace_id: str, project_id: str) -> bytes: ...

    def stat(self, key: str, *, workspace_id: str, project_id: str) -> ObjectStat | None: ...

    def delete(self, key: str, *, workspace_id: str, project_id: str) -> bool: ...
    def verify_checksum(self, key: str, *, workspace_id: str, project_id: str) -> bool: ...

    def list_objects(self, *, workspace_id: str, project_id: str) -> list[ObjectStat]: ...

    def delete_workspace(self, *, workspace_id: str) -> int: ...
    def delete_project(self, *, workspace_id: str, project_id: str) -> int: ...


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


@runtime_checkable
class IdentityProviderPort(Protocol):
    """Provider-neutral OIDC boundary. No vendor SDK object crosses this port."""

    def is_available(self) -> bool: ...

    async def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        redirect_uri: str,
    ) -> str: ...

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        expected_nonce: str,
    ) -> IdentityClaims: ...

    async def logout_url(
        self,
        *,
        post_logout_redirect_uri: str,
        id_token_hint: str | None = None,
        state: str | None = None,
    ) -> str: ...


@runtime_checkable
class IdentityStorePort(Protocol):
    async def get_or_create_user(self, claims: IdentityClaims) -> User: ...

    async def get_user(self, user_id: str) -> User | None: ...

    async def get_user_for_identity(self, issuer: str, subject: str) -> User | None: ...

    async def unlink_identity(self, issuer: str, subject: str) -> bool: ...


@runtime_checkable
class SessionStorePort(Protocol):
    async def create(
        self,
        *,
        user_id: str,
        ttl_seconds: int,
        csrf_token: str,
        rotated_from: str | None = None,
    ) -> Session: ...

    async def get(self, session_id: str) -> Session | None: ...

    async def revoke(self, session_id: str) -> bool: ...

    async def revoke_all_for_user(self, user_id: str) -> int: ...


@runtime_checkable
class RateLimiterPort(Protocol):
    """Shared sliding-window limiter usable across workers (durable backing store)."""

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool: ...


@runtime_checkable
class LoginAttemptStorePort(Protocol):
    async def create(
        self,
        *,
        state: str,
        nonce: str,
        code_verifier: str,
        redirect_uri: str,
        ttl_seconds: int,
    ) -> LoginAttempt: ...

    async def consume(self, state: str) -> LoginAttempt | None: ...


@runtime_checkable
class TenancyStorePort(Protocol):
    """Workspace, membership, project, invitation, and audit persistence.

    Implementations enforce the invariants (unique slug/membership/token hash,
    last-owner protection) and raise the typed tenancy errors from
    ``backend.domain.tenancy``.
    """

    async def create_workspace(self, *, slug: str, name: str) -> Workspace: ...

    async def get_workspace(self, workspace_id: str) -> Workspace | None: ...

    async def get_workspace_by_slug(self, slug: str) -> Workspace | None: ...

    async def list_workspaces_for_user(self, user_id: str) -> list[Workspace]: ...

    async def delete_workspace(self, workspace_id: str) -> bool: ...

    async def delete_project(self, workspace_id: str, project_id: str) -> bool: ...

    async def add_membership(
        self, *, workspace_id: str, user_id: str, role: Role
    ) -> Membership: ...

    async def get_membership(self, workspace_id: str, user_id: str) -> Membership | None: ...

    async def list_memberships(self, workspace_id: str) -> list[Membership]: ...

    async def set_role(self, *, workspace_id: str, user_id: str, role: Role) -> Membership: ...

    async def remove_membership(self, *, workspace_id: str, user_id: str) -> bool: ...

    async def transfer_ownership(
        self, *, workspace_id: str, from_user_id: str, to_user_id: str
    ) -> None: ...

    async def create_project(self, *, workspace_id: str, slug: str, name: str) -> Project: ...

    async def get_project(self, project_id: str) -> Project | None: ...

    async def list_projects(self, workspace_id: str) -> list[Project]: ...

    async def create_invitation(
        self,
        *,
        workspace_id: str,
        role: Role,
        token_hash: str,
        created_by_user_id: str,
        ttl_seconds: int,
        email: str | None = None,
    ) -> Invitation: ...

    async def get_invitation_by_token_hash(self, token_hash: str) -> Invitation | None: ...

    async def list_invitations(self, workspace_id: str) -> list[Invitation]: ...

    async def revoke_invitation(self, *, workspace_id: str, invitation_id: str) -> bool: ...

    async def accept_invitation(self, *, token_hash: str, user_id: str) -> Membership: ...

    async def record_audit(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        action: str,
        target_type: str,
        target_id: str,
        detail: dict[str, Any] | None = None,
        correlation_id: str = "",
    ) -> AuditEvent: ...


@runtime_checkable
class CredentialVaultPort(Protocol):
    """Workspace-scoped provider credential storage (envelope-encrypted at rest)."""

    @property
    def is_configured(self) -> bool: ...

    async def store(
        self,
        *,
        workspace_id: str,
        owner_user_id: str,
        provider: CredentialProvider,
        label: str,
        plaintext_key: str,
    ) -> Credential: ...

    async def list_for_workspace(self, workspace_id: str) -> list[Credential]: ...

    async def get(self, *, workspace_id: str, credential_id: str) -> Credential | None: ...

    async def rotate(
        self, *, workspace_id: str, credential_id: str, plaintext_key: str
    ) -> Credential: ...

    async def revoke(self, *, workspace_id: str, credential_id: str) -> Credential: ...

    async def delete(self, *, workspace_id: str, credential_id: str) -> bool: ...

    async def resolve_plaintext(
        self, *, workspace_id: str, credential_id: str, actor_user_id: str
    ) -> str: ...

    async def mark_used(self, *, workspace_id: str, credential_id: str) -> None: ...

    async def delete_workspace(self, workspace_id: str) -> int: ...


@runtime_checkable
class ProviderUsagePort(Protocol):
    """Immutable provider-usage records for limits, retries, and audit."""

    async def record(self, record: UsageRecord) -> UsageRecord: ...

    async def totals(
        self, *, workspace_id: str, actor_user_id: str, since: datetime
    ) -> UsageTotals: ...

    async def totals_for_job(self, *, idempotency_key: str) -> UsageTotals: ...

    async def has_idempotency_key(self, *, idempotency_key: str) -> bool: ...

    async def delete_workspace(self, workspace_id: str) -> int: ...


@runtime_checkable
class WorkspacePurgePort(Protocol):
    """Cascades workspace/project deletion across every non-relational store."""

    async def purge_workspace(self, workspace_id: str) -> dict[str, int]: ...

    async def purge_project(self, workspace_id: str, project_id: str) -> dict[str, int]: ...


@runtime_checkable
class CanonJournalPort(Protocol):
    """Authoritative commit journal (ADR-011).

    PostgreSQL owns branch heads, optimistic reservations, immutable commit
    envelopes, commit attempts, review decisions, and the transactional outbox.
    Implementations enforce: one root branch per project, at most one active
    reservation per branch, head-only-forward-from-expected, and commit-id
    immutability (same id with a different checksum is a conflict).
    """

    async def ensure_root_branch(
        self, *, workspace_id: str, project_id: str, name: str = "main"
    ) -> Branch: ...

    async def get_branch(
        self, *, workspace_id: str, project_id: str, branch_id: str
    ) -> Branch | None: ...

    async def list_branches(self, *, workspace_id: str, project_id: str) -> list[Branch]: ...

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
    ) -> MutationSet: ...

    async def get_mutation(
        self, *, workspace_id: str, project_id: str, mutation_id: str
    ) -> MutationSet | None: ...

    async def record_operations(
        self, *, mutation_id: str, operations: Sequence[MutationOperation]
    ) -> None: ...

    async def list_operations(self, *, mutation_id: str) -> list[MutationOperation]: ...

    async def record_decision(
        self,
        *,
        mutation_id: str,
        decision: str,
        actor_user_id: str | None = None,
        note: str | None = None,
    ) -> ReviewDecision: ...

    async def reserve_head(
        self,
        *,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        mutation_id: str,
        expected_revision: int,
    ) -> BranchReservation: ...

    async def append_envelope(self, envelope: CommitEnvelope) -> None: ...

    async def get_envelope(self, *, commit_id: str) -> CommitEnvelope | None: ...

    async def record_attempt(
        self, *, commit_id: str, status: str, detail: str | None = None
    ) -> CommitAttempt: ...

    async def finalize(self, *, commit_id: str): ...

    async def append_outbox(self, message: OutboxMessage) -> None: ...

    async def list_outbox(
        self, *, workspace_id: str, project_id: str, limit: int = 100
    ) -> list[OutboxMessage]: ...

    async def mark_outbox_published(self, *, message_id: str) -> None: ...

    async def mark_outbox_failed(self, *, message_id: str, error: str) -> None: ...

    async def count_failed_outbox(self, *, workspace_id: str, project_id: str) -> int: ...


@runtime_checkable
class CanonWriterPort(Protocol):
    """Applies an immutable commit envelope to the canonical graph.

    ``apply_commit`` must be idempotent by ``commit_id``: re-applying the same
    tuple is a no-op returning ``ALREADY_APPLIED``; the same ``commit_id`` with a
    different ``checksum`` is an integrity error. The marker write and the object
    writes happen in one transaction so a crash leaves either nothing or a
    complete, marker-backed commit.
    """

    async def apply_commit(
        self,
        *,
        commit_id: str,
        revision: int,
        checksum: str,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        operations: Sequence[Any],
        schema_version_id: str | None = None,
    ) -> ApplyOutcome: ...

    async def get_marker(self, commit_id: str) -> CommitMarker | None: ...

    async def discard(self, *, workspace_id: str, project_id: str, branch_id: str) -> int: ...

    async def get_entity(
        self, *, workspace_id: str, project_id: str, entity_id: str
    ) -> CanonEntity | None: ...

    async def list_entities(
        self,
        *,
        workspace_id: str,
        project_id: str,
        entity_type: str | None = None,
        include_retired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CanonEntity]: ...

    async def search_entities(
        self,
        *,
        workspace_id: str,
        project_id: str,
        query: str,
        entity_type: str | None = None,
        include_retired: bool = False,
        limit: int = 50,
    ) -> list[CanonEntity]: ...

    async def get_fact(
        self, *, workspace_id: str, project_id: str, fact_id: str
    ) -> Fact | None: ...

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
    ) -> list[Fact]: ...

    async def get_belief(
        self, *, workspace_id: str, project_id: str, belief_id: str
    ) -> Belief | None: ...

    async def list_beliefs(
        self,
        *,
        workspace_id: str,
        project_id: str,
        holder_entity_id: str | None = None,
        include_closed: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Belief]: ...

    async def get_event(
        self, *, workspace_id: str, project_id: str, event_id: str
    ) -> Event | None: ...

    async def list_events(
        self,
        *,
        workspace_id: str,
        project_id: str,
        include_retired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Event]: ...

    async def health(self) -> bool: ...


@runtime_checkable
class CanonRepositoryPort(Protocol):
    """Canonical PostgreSQL canon storage (ADR-012).

    ``apply_commit`` is the authority boundary: one transaction reserves the
    branch head, appends the immutable envelope, applies the validated operations
    to normalized/versioned tables, advances the head/revision, and enqueues the
    outbox. It is idempotent by ``commit_id`` and never depends on Neo4j.
    """

    async def apply_commit(
        self,
        *,
        envelope: CommitEnvelope,
        operations: Sequence[CanonOperation],
        validator: Any = None,
        allowed_entity_types: frozenset[str] | None = None,
        origin: str = "author",
    ) -> int: ...

    async def get_head(self, *, workspace_id: str, project_id: str, branch_id: str) -> int: ...

    async def get_entity(
        self, *, workspace_id: str, project_id: str, entity_id: str
    ) -> CanonEntity | None: ...

    async def list_entities(
        self,
        *,
        workspace_id: str,
        project_id: str,
        entity_type: str | None = None,
        include_retired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CanonEntity]: ...

    async def search_entities(
        self,
        *,
        workspace_id: str,
        project_id: str,
        query: str,
        entity_type: str | None = None,
        include_retired: bool = False,
        limit: int = 50,
    ) -> list[CanonEntity]: ...

    async def get_fact(
        self, *, workspace_id: str, project_id: str, fact_id: str
    ) -> Fact | None: ...

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
    ) -> list[Fact]: ...

    async def get_belief(
        self, *, workspace_id: str, project_id: str, belief_id: str
    ) -> Belief | None: ...

    async def list_beliefs(
        self,
        *,
        workspace_id: str,
        project_id: str,
        holder_entity_id: str | None = None,
        include_closed: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Belief]: ...

    async def get_event(
        self, *, workspace_id: str, project_id: str, event_id: str
    ) -> Event | None: ...

    async def list_events(
        self,
        *,
        workspace_id: str,
        project_id: str,
        include_retired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Event]: ...

    async def get_provenance(
        self, *, workspace_id: str, project_id: str, branch_id: str, provenance_id: str
    ) -> Provenance | None: ...

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
    ) -> list[RecordVersion]: ...

    async def get_envelope(self, commit_id: str) -> CommitEnvelope | None: ...

    async def list_commits(
        self,
        *,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        after_revision: int = 0,
        limit: int = 100,
    ) -> list[CanonRevision]: ...

    async def get_watermark(
        self, *, projection: str, workspace_id: str, project_id: str, branch_id: str
    ) -> ProjectionWatermark | None: ...

    async def set_watermark(self, watermark: ProjectionWatermark) -> None: ...

    async def create_tag(self, tag: CanonTag) -> CanonTag: ...

    async def get_tag(
        self, *, workspace_id: str, project_id: str, branch_id: str, name: str
    ) -> CanonTag | None: ...

    async def list_tags(
        self, *, workspace_id: str, project_id: str, branch_id: str
    ) -> list[CanonTag]: ...

    async def list_tag_history(
        self, *, workspace_id: str, project_id: str, branch_id: str, name: str
    ) -> list[CanonTagHistoryEntry]: ...

    async def delete_tag(
        self, *, workspace_id: str, project_id: str, branch_id: str, name: str
    ) -> bool: ...

    async def get_link(
        self, *, workspace_id: str, project_id: str, link_id: str
    ) -> AssetLink | None: ...

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
    ) -> list[AssetLink]: ...

    async def health(self) -> bool: ...


@runtime_checkable
class MediaStorePort(Protocol):
    """Project-scoped media identity metadata (PostgreSQL lifecycle authority).

    Bytes live in the scoped object store under opaque keys; this store holds only
    identity, version metadata, provenance, and lifecycle. It never implies any
    processing/scanning has occurred (Phase 4+).
    """

    async def create_asset(
        self, *, workspace_id: str, project_id: str, name: str
    ) -> MediaAsset: ...

    async def get_asset(
        self, *, workspace_id: str, project_id: str, asset_id: str
    ) -> MediaAsset | None: ...

    async def list_assets(
        self, *, workspace_id: str, project_id: str, limit: int = 100, offset: int = 0
    ) -> list[MediaAsset]: ...

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
    ) -> MediaAssetVersion: ...

    async def get_version(
        self, *, workspace_id: str, project_id: str, version_id: str
    ) -> MediaAssetVersion | None: ...

    async def list_versions(
        self, *, workspace_id: str, project_id: str, asset_id: str
    ) -> list[MediaAssetVersion]: ...

    async def import_asset(self, asset: MediaAsset) -> MediaAsset: ...

    async def import_version(self, version: MediaAssetVersion) -> MediaAssetVersion: ...

    async def health(self) -> bool: ...
