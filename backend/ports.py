"""Application/domain ports.

Services depend on these protocols rather than concrete SDKs, drivers, or
process-global singletons. Adapters in ``backend.adapters`` implement them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from backend.domain.credentials import Credential, CredentialProvider
from backend.domain.identity import IdentityClaims, LoginAttempt, Role, Session, User
from backend.domain.objects import ObjectStat
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
