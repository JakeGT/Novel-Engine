"""Application/domain ports.

Services depend on these protocols rather than concrete SDKs, drivers, or
process-global singletons. Adapters in ``backend.adapters`` implement them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from backend.domain.archive import ArchiveImport, ImportStatus
from backend.domain.canon.assertions import Belief, Fact
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import Event
from backend.domain.canon.plans import PlanItem
from backend.domain.canon.revisions import CanonRevision, Provenance
from backend.domain.commit import ApplyOutcome, CommitMarker
from backend.domain.credentials import Credential, CredentialProvider
from backend.domain.embedding import (
    CaptionSource,
    EmbeddingHealth,
    EmbeddingInput,
    EmbeddingSpace,
    EmbeddingTask,
    EmbeddingVector,
    Generation,
    PictureSource,
    SearchGate,
    StoredHit,
    VectorRow,
)
from backend.domain.history import CanonTag, CanonTagHistoryEntry
from backend.domain.identity import IdentityClaims, LoginAttempt, Role, Session, User
from backend.domain.journal import (
    Branch,
    BranchReservation,
    CommitAttempt,
    CommitEnvelope,
    MutationOperation,
    MutationSet,
    MutationStatus,
    MutationTransition,
    OperationDecision,
    OperationRevision,
    OutboxMessage,
    ReviewDecision,
)
from backend.domain.media import (
    AssetLifecycle,
    AssetLink,
    MediaAsset,
    MediaAssetVersion,
    MediaLifecycleEvent,
)
from backend.domain.objects import ObjectStat
from backend.domain.operations import CanonOperation
from backend.domain.projection import EvidenceRecord, ProjectionWatermark, RecordVersion
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
        # Metadata filters (CTX-004): key -> the values it may take.
        where: dict[str, Sequence[str]] | None = None,
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
class MultimodalEmbeddingPort(Protocol):
    """Typed text/image/audio/video embedding with provenance (CTX-011, ADR-010).

    ``embed`` returns one normalized vector per input, each carrying the model,
    revision, dimension, task and modality it was made with. An adapter refuses an
    input it cannot embed (``UnsupportedInputError``) rather than dropping or guessing
    it; audio, video and interleaved inputs are refused by every current adapter.
    ``health`` never forces a model download.
    """

    @property
    def space(self) -> EmbeddingSpace: ...

    def available(self) -> bool: ...

    def loaded(self) -> bool: ...

    def health(self) -> EmbeddingHealth: ...

    def embed(
        self, inputs: Sequence[EmbeddingInput], *, task: EmbeddingTask
    ) -> list[EmbeddingVector]: ...


@runtime_checkable
class EmbeddingIndexStore(Protocol):
    """Generation-versioned vectors (CTX-012). Synchronous; services call it in a thread.

    Every read and write names one generation, and a query never reads two. Deletes by
    id, asset or scope cover every generation, so a picture or project removed is gone
    from spaces being built and retired spaces too.
    """

    def active(self) -> Generation | None: ...

    def building(self) -> Generation | None: ...

    def get(self, generation_id: str) -> Generation | None: ...

    def list_generations(self) -> list[Generation]: ...

    def create_building(self, space: EmbeddingSpace, *, note: str | None = None) -> Generation: ...

    def request_cancel(self, generation_id: str) -> bool: ...

    def activate(self, generation_id: str) -> Generation: ...

    def rollback(self) -> Generation: ...

    def discard(self, generation_id: str) -> int: ...

    def upsert(
        self, generation_id: str, rows: Sequence[VectorRow], *, workspace_id: str, project_id: str
    ) -> int: ...

    def hashes(
        self, generation_id: str, *, workspace_id: str, project_id: str
    ) -> dict[str, str]: ...

    def text_rows(
        self, generation_id: str, *, workspace_id: str, project_id: str
    ) -> list[VectorRow]: ...

    def search(
        self,
        generation_id: str,
        values: Sequence[float],
        *,
        workspace_id: str,
        project_id: str,
        gate: SearchGate,
        limit: int,
    ) -> list[StoredHit]: ...

    def eligible_assets(
        self, asset_ids: Sequence[str], *, workspace_id: str, project_id: str, gate: SearchGate
    ) -> set[str]: ...

    def delete_ids(self, ids: Sequence[str], *, workspace_id: str, project_id: str) -> int: ...

    def delete_ids_in(
        self, generation_id: str, ids: Sequence[str], *, workspace_id: str, project_id: str
    ) -> int: ...

    def delete_kinds_except(
        self,
        generation_id: str,
        kinds: Sequence[str],
        keep_ids: Sequence[str],
        *,
        workspace_id: str,
        project_id: str,
    ) -> int: ...

    def delete_assets(
        self,
        *,
        workspace_id: str,
        project_id: str,
        asset_ids: Sequence[str] = (),
        asset_version_ids: Sequence[str] = (),
    ) -> int: ...

    def delete_project(self, *, workspace_id: str, project_id: str) -> int: ...

    def delete_workspace(self, *, workspace_id: str) -> int: ...

    def pictures_ready(self, *, workspace_id: str, project_id: str) -> list[PictureSource]: ...

    def accepted_captions(self, *, workspace_id: str, project_id: str) -> list[CaptionSource]: ...

    def projects(self) -> list[tuple[str, str]]: ...

    def count(self, generation_id: str, *, workspace_id: str | None = None) -> int: ...

    def ping(self) -> bool: ...


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
    # Of the prompt tokens, how many the provider read back from its cache (CTX-015).
    # Already counted in ``prompt_tokens``; 0 when the provider does not say.
    cached_prompt_tokens: int = 0
    # What the provider says the call cost, in US dollars (OpenRouter's ``usage.cost``);
    # None when it does not say. Never worked out here from a price list (OPS-002).
    cost_usd: float | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class StructuredResponse:
    parsed: Any
    model: str = ""
    usage: AIUsage = field(default_factory=AIUsage)


@dataclass
class TextResponse:
    """Prose from a model, with what it cost."""

    text: str
    model: str = ""
    usage: AIUsage = field(default_factory=AIUsage)
    # True when the model stopped at the length limit rather than finishing.
    truncated: bool = False


@dataclass(frozen=True)
class ProviderCapabilities:
    """Typed provider/model capability description (CTX-013).

    A route that can see pictures lists ``"image"`` among its input modalities and
    declares how many pictures one request may carry, how large each may be and which
    types it takes. A route that declares none of this is text only, and the gateway
    refuses a picture for it before anything is sent.
    """

    provider: str
    model: str
    input_modalities: frozenset[str] = frozenset({"text"})
    output_modalities: frozenset[str] = frozenset({"text"})
    structured_output: bool = True
    max_context_tokens: int | None = None
    supports_streaming: bool = False
    max_images: int = 0
    max_image_bytes: int = 0
    image_types: frozenset[str] = frozenset()

    @property
    def sees_images(self) -> bool:
        return "image" in self.input_modalities and self.max_images > 0


@dataclass(frozen=True)
class ImageInput:
    """One picture sent with a request: its exact bytes and where they came from.

    ``ref`` names the picture and version in the request's own words (``picture 1``),
    so the model can cite it; the bytes are never logged."""

    data: bytes
    content_type: str
    ref: str
    asset_id: str = ""
    version_id: str = ""
    # "original" or the smaller copy's kind ("web").
    rendition: str = "original"
    checksum: str = ""


class AIError(RuntimeError):
    """Base class for normalized AI gateway failures."""


class AIProviderError(AIError):
    """Provider refused, timed out, or returned a transport/server error."""


class AIInvalidOutputError(AIError):
    """Provider returned output that could not be parsed into the requested schema."""


class AIUnsupportedInputError(AIError):
    """The route cannot take what was asked to be sent (a picture to a text-only model,
    too many or too large pictures). Raised before anything is sent or spent."""


@runtime_checkable
class AIGateway(Protocol):
    def is_available(self) -> bool: ...

    def capabilities(self, *, base_url: str | None = None) -> ProviderCapabilities:
        """What the route a request will use can take: the configured one, or the
        workspace's own provider at ``base_url``."""
        ...

    async def generate_structured(
        self,
        *,
        messages: list[dict[str, str]],
        response_format: type,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        images: Sequence[ImageInput] = (),
    ) -> StructuredResponse:
        """``images`` go with the last user message; a route that cannot take them
        raises ``AIUnsupportedInputError`` before anything is sent."""
        ...

    async def generate_text(
        self,
        *,
        messages: list[dict[str, str]],
        max_tokens: int,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        images: Sequence[ImageInput] = (),
    ) -> TextResponse:
        """Prose, not structured output: a scene draft (ADR-022). ``images`` as for
        ``generate_structured``: refused before sending when the route cannot take them."""
        ...

    async def list_models(self, *, api_key: str, base_url: str) -> list[str]: ...

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
        id_token: str | None = None,
    ) -> Session: ...

    async def get(self, session_id: str) -> Session | None: ...

    async def has_successor(self, session_id: str) -> bool:
        """Whether this session has already been replaced by a newer one."""
        ...

    async def expire_within(self, session_id: str, seconds: int) -> None:
        """Let a replaced session live only a little longer, never longer than it had."""
        ...

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
    """Authoritative commit journal (ADR-012; workflow inherited from ADR-011).

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

    async def working_branch(self, *, workspace_id: str, project_id: str) -> Branch:
        """The what-if the current request works in, or the main story."""
        ...

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
        intent: str = "edit",
    ) -> MutationSet: ...

    async def get_mutation(
        self, *, workspace_id: str, project_id: str, mutation_id: str
    ) -> MutationSet | None: ...

    async def list_mutations(
        self,
        *,
        workspace_id: str,
        project_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MutationSet]: ...

    async def transition_mutation(
        self,
        *,
        mutation_id: str,
        to_status: MutationStatus,
        actor_user_id: str | None,
        reason: str,
        correlation_id: str = "",
    ) -> MutationSet: ...

    async def list_transitions(self, *, mutation_id: str) -> list[MutationTransition]: ...

    async def retarget_mutation(
        self,
        *,
        mutation_id: str,
        expected_head: int,
        actor_user_id: str | None,
        reason: str,
        keep_approval: bool = False,
    ) -> MutationSet: ...

    async def record_operations(
        self, *, mutation_id: str, operations: Sequence[MutationOperation]
    ) -> None: ...

    async def list_operations(self, *, mutation_id: str) -> list[MutationOperation]: ...

    async def revise_operation(
        self,
        *,
        mutation_id: str,
        ordinal: int,
        operation_type: str,
        payload: dict[str, Any],
        actor_user_id: str | None,
        reason: str,
    ) -> MutationOperation: ...

    async def list_operation_revisions(self, *, mutation_id: str) -> list[OperationRevision]: ...

    async def record_operation_decision(
        self,
        *,
        mutation_id: str,
        ordinal: int,
        decision: str,
        actor_user_id: str | None = None,
        note: str | None = None,
    ) -> OperationDecision: ...

    async def record_operation_decisions(
        self,
        *,
        mutation_id: str,
        decisions: Sequence[tuple[int, str, str | None]],
        actor_user_id: str | None = None,
    ) -> list[OperationDecision]: ...

    async def list_operation_decisions(self, *, mutation_id: str) -> list[OperationDecision]: ...

    async def record_decision(
        self,
        *,
        mutation_id: str,
        decision: str,
        actor_user_id: str | None = None,
        note: str | None = None,
        transition_to: MutationStatus | None = None,
        correlation_id: str = "",
    ) -> ReviewDecision: ...

    async def list_decisions(self, *, mutation_id: str) -> list[ReviewDecision]: ...

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
        self, *, workspace_id: str, project_id: str, limit: int = 100, pending_only: bool = False
    ) -> list[OutboxMessage]: ...

    async def projects_with_pending_outbox(self, *, limit: int = 50) -> list[tuple[str, str]]:
        """(workspace, project) pairs with messages not yet delivered, oldest first."""
        ...

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

    async def purge(self, *, workspace_id: str, project_id: str | None = None) -> int:
        """Erase every projected canon node for a deleted project or workspace."""
        ...

    async def list_fact_edges(
        self, *, workspace_id: str, project_id: str
    ) -> list[tuple[str, str, str, str]]:
        """Projected ``(fact_id, subject_id, predicate, object_id)`` relationship shortcuts."""
        ...

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
        origin: str = "author",
    ) -> int: ...

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
        """Record (or resume) an archive import before its first durable write."""
        ...

    async def update_import(
        self,
        *,
        import_id: str,
        stage: str,
        commits_applied: int | None = None,
        object_keys: Sequence[str] | None = None,
        created_records: dict[str, list[str]] | None = None,
    ) -> None: ...

    async def finish_import(
        self, *, import_id: str, status: ImportStatus, error: str | None = None
    ) -> ArchiveImport: ...

    async def record_removed_import(self, record: ArchiveImport) -> ArchiveImport: ...

    async def list_imports(self, *, workspace_id: str, project_id: str) -> list[ArchiveImport]: ...

    async def purge_project(self, *, workspace_id: str, project_id: str) -> int:
        """Erase every canon, journal, schema, and media row a project owns."""
        ...

    async def purge_workspace(self, *, workspace_id: str) -> int: ...

    async def purge_import(
        self,
        *,
        workspace_id: str,
        project_id: str,
        created_records: dict[str, list[str]],
    ) -> int:
        """Erase the canon an import wrote plus exactly the supporting records it created."""
        ...

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

    async def get_entity_by_slug(
        self, *, workspace_id: str, project_id: str, slug: str
    ) -> CanonEntity | None: ...

    async def get_entity_by_external_id(
        self, *, workspace_id: str, project_id: str, namespace: str, value: str
    ) -> CanonEntity | None: ...

    async def find_entities_by_name(
        self, *, workspace_id: str, project_id: str, entity_type: str, name: str
    ) -> list[CanonEntity]: ...

    async def list_active_references(
        self, *, workspace_id: str, project_id: str, entity_id: str
    ) -> list[tuple[str, str]]:
        """``(record_type, record_id)`` of live records that reference the entity."""
        ...

    async def count_active_references(
        self, *, workspace_id: str, project_id: str, entity_id: str
    ) -> int: ...

    async def list_evidence(
        self,
        *,
        workspace_id: str,
        project_id: str,
        record_type: str | None = None,
        record_id: str | None = None,
        commit_id: str | None = None,
    ) -> list[EvidenceRecord]: ...

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
        object_entity_id: str | None = None,
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

    async def get_plan_item(
        self, *, workspace_id: str, project_id: str, plan_id: str
    ) -> PlanItem | None: ...

    async def list_plan_items(
        self,
        *,
        workspace_id: str,
        project_id: str,
        include_retired: bool = False,
        limit: int = 2000,
    ) -> list[PlanItem]: ...

    async def list_events(
        self,
        *,
        workspace_id: str,
        project_id: str,
        include_retired: bool = False,
        limit: int = 50,
        offset: int = 0,
        entity_id: str | None = None,
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
        target_record_id: str | None = None,
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
        self,
        *,
        workspace_id: str,
        project_id: str,
        name: str,
        actor_user_id: str | None = None,
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
        actor_user_id: str | None = None,
    ) -> MediaAssetVersion: ...

    async def get_version(
        self, *, workspace_id: str, project_id: str, version_id: str
    ) -> MediaAssetVersion | None: ...

    async def list_versions(
        self, *, workspace_id: str, project_id: str, asset_id: str
    ) -> list[MediaAssetVersion]: ...

    async def list_versions_for_assets(
        self, *, workspace_id: str, project_id: str, asset_ids: list[str]
    ) -> dict[str, list[MediaAssetVersion]]:
        """Every version of each named asset, oldest first, keyed by asset."""
        ...

    async def discard_asset(self, *, workspace_id: str, project_id: str, asset_id: str) -> None:
        """Remove an asset no canon record refers to, with its versions and events."""
        ...

    async def retire_asset(
        self,
        *,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        retired_revision: int | None = None,
        actor_user_id: str | None = None,
    ) -> MediaAsset | None: ...

    async def retire_version(
        self,
        *,
        workspace_id: str,
        project_id: str,
        version_id: str,
        retired_revision: int | None = None,
        actor_user_id: str | None = None,
    ) -> MediaAssetVersion | None: ...

    async def delete_original(
        self,
        *,
        workspace_id: str,
        project_id: str,
        version_id: str,
        retired_revision: int | None = None,
        actor_user_id: str | None = None,
    ) -> MediaAssetVersion | None: ...

    async def delete_asset(
        self,
        *,
        workspace_id: str,
        project_id: str,
        asset_id: str,
        retired_revision: int | None = None,
        actor_user_id: str | None = None,
    ) -> MediaAsset | None: ...

    async def hold_for_deletion(
        self, *, workspace_id: str, project_id: str, asset_id: str, lifecycle: AssetLifecycle
    ) -> bool:
        """Mark an asset deleted before its files go, only if it is still in ``lifecycle``
        (nothing recorded yet); False when it changed (AST-008)."""
        ...

    async def release_hold(
        self, *, workspace_id: str, project_id: str, asset_id: str, lifecycle: AssetLifecycle
    ) -> None:
        """Put back an asset held for deletion whose files were never touched."""
        ...

    async def object_keys_in_use(
        self, *, workspace_id: str, project_id: str, object_keys: list[str], except_asset_id: str
    ) -> set[str]:
        """Which of these stored files another asset's version (file not deleted), or a
        smaller copy of one, still uses."""
        ...

    async def import_asset(self, asset: MediaAsset) -> MediaAsset: ...

    async def import_version(self, version: MediaAssetVersion) -> MediaAssetVersion: ...

    async def list_lifecycle_events(
        self, *, workspace_id: str, project_id: str, asset_id: str
    ) -> list[MediaLifecycleEvent]:
        """Append-only audit of registration, import, and retirement for one asset."""
        ...

    async def health(self) -> bool: ...


@runtime_checkable
class MediaProcessingStorePort(Protocol):
    """Processing state, rebuildable smaller copies and segments of picture versions
    (AST-003). Copies live in the object store; these rows say which keys are copies."""

    async def get_processing(
        self, *, workspace_id: str, project_id: str, version_id: str
    ) -> Any | None: ...

    async def list_processing(
        self, *, workspace_id: str, project_id: str, version_ids: list[str]
    ) -> dict[str, Any]: ...

    async def queue(
        self,
        *,
        workspace_id: str,
        project_id: str,
        version_id: str,
        asset_id: str,
        checksum: str,
        processor_version: str,
        job_id: str | None,
    ) -> Any: ...

    async def start(self, *, workspace_id: str, project_id: str, version_id: str) -> Any | None: ...

    async def fail(
        self, *, workspace_id: str, project_id: str, version_id: str, failure_code: str
    ) -> None: ...

    async def complete(
        self,
        *,
        workspace_id: str,
        project_id: str,
        version_id: str,
        width: int,
        height: int,
        detected_format: str,
        variants: list[Any],
        whole: Any,
    ) -> list[str]:
        """Record copies, the whole-picture segment and readiness; replaced copies' keys."""
        ...

    async def get_variant(
        self, *, workspace_id: str, project_id: str, version_id: str, kind: Any
    ) -> Any | None: ...

    async def list_variants(
        self, *, workspace_id: str, project_id: str, version_ids: list[str]
    ) -> dict[str, list[Any]]: ...

    async def variant_object_keys(self, *, workspace_id: str, project_id: str) -> set[str]: ...

    async def known_object_keys(self, *, workspace_id: str, project_id: str) -> set[str]: ...

    async def remove_variants(
        self, *, workspace_id: str, project_id: str, version_id: str
    ) -> list[str]: ...

    async def list_segments(
        self, *, workspace_id: str, project_id: str, version_id: str
    ) -> list[Any]: ...

    async def list_regions(self, *, workspace_id: str, project_id: str) -> list[Any]: ...

    async def add_segment(self, segment: Any) -> Any: ...

    async def retire_segment(
        self, *, workspace_id: str, project_id: str, segment_id: str
    ) -> Any | None: ...

    async def workspace_bytes(self, *, workspace_id: str) -> int: ...

    async def lag(self) -> dict[str, Any]: ...

    async def pending_versions(
        self,
        *,
        processor_version: str,
        content_types: list[str],
        workspace_id: str | None = None,
        project_id: str | None = None,
        limit: int = 500,
        include_failed: bool = False,
        stale_seconds: int = 3600,
    ) -> list[Any]: ...

    async def retired_with_variants(self, *, limit: int = 1000) -> list[tuple[str, str, str]]: ...

    async def projects_with_media(self) -> list[tuple[str, str]]: ...

    async def health(self) -> bool: ...


@runtime_checkable
class AccessKeyStorePort(Protocol):
    """Personal access keys for other assistants (QRY-003). Digests only, never keys."""

    async def create(self, key: Any) -> Any: ...

    async def get(self, *, workspace_id: str, key_id: str) -> Any | None: ...

    async def get_by_hash(self, token_hash: str) -> Any | None: ...

    async def list_keys(self, *, workspace_id: str, user_id: str | None = None) -> list[Any]:
        """Keys in a workspace, newest first; one holder's when ``user_id`` is given."""
        ...

    async def revoke(self, *, key_id: str, reason: str, at: datetime) -> bool:
        """Stop a key working; False when it had already stopped."""
        ...

    async def revoke_for_member(
        self, *, workspace_id: str, user_id: str, reason: str, at: datetime
    ) -> list[str]:
        """Stop every working key one member holds in a workspace; the ids stopped."""
        ...

    async def touch(self, *, key_id: str, at: datetime) -> None:
        """Note when a key was last used (at most about once a minute is written)."""
        ...

    async def record_use(
        self, *, key_id: str, workspace_id: str, day: str, tool: str, refused: bool
    ) -> None: ...

    async def uses(self, *, key_id: str, since_day: str) -> list[Any]: ...

    async def delete_workspace(self, workspace_id: str) -> int: ...

    async def delete_project(self, workspace_id: str, project_id: str) -> int: ...
