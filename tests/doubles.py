"""Deterministic in-memory test doubles for Novel Engine application tests.

These fakes model application/domain ports in plain terms. They intentionally do
not mock Neo4j driver calls, OpenAI SDK internals, or filesystem layouts.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from backend.domain.credentials import (
    Credential,
    CredentialNotFound,
    CredentialProvider,
    CredentialStatus,
    CredentialUnavailable,
)
from backend.domain.identity import (
    IdentityClaims,
    InvalidIdentityToken,
    LoginAttempt,
    Role,
    Session,
    User,
    utcnow,
)
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
    """Graph repository fake with explicit workspace + project scoping."""

    def __init__(self, semantic_anchors: list[str] | None = None) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.semantic_anchors = list(semantic_anchors or [])
        self.close_calls = 0

    def add_node(self, node: Node) -> None:
        existing = self.nodes.get(node.id)
        if existing is not None:
            if existing.workspace_id != node.workspace_id or existing.project_id != node.project_id:
                # A node id is owned by one workspace/project; never mutate another's.
                return
            merged = dict(existing.properties)
            merged.update(node.properties)
            node = node.model_copy(
                update={
                    "properties": merged,
                    "embedding": node.embedding or existing.embedding,
                }
            )
        self.nodes[node.id] = node

    def get_node(self, node_id: str, *, workspace_id: str, project_id: str) -> Node | None:
        node = self.nodes.get(node_id)
        if node is not None and node.workspace_id == workspace_id and node.project_id == project_id:
            return node
        return None

    def update_node(self, node: Node) -> Node | None:
        existing = self.nodes.get(node.id)
        if (
            existing is None
            or existing.workspace_id != node.workspace_id
            or existing.project_id != node.project_id
        ):
            return None
        self.add_node(node)
        return self.nodes[node.id]

    def add_edge(self, edge: Edge) -> None:
        source = self.nodes.get(edge.source)
        target = self.nodes.get(edge.target)
        if (
            source is None
            or target is None
            or source.workspace_id != edge.workspace_id
            or target.workspace_id != edge.workspace_id
            or source.project_id != edge.project_id
            or target.project_id != edge.project_id
        ):
            return
        for existing in self.edges:
            if (existing.source, existing.target, existing.type) == (
                edge.source,
                edge.target,
                edge.type,
            ):
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
            for node in self.nodes.values()
            if node.workspace_id == workspace_id
            and node.project_id == project_id
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
        for node in self.nodes.values():
            if node.workspace_id != workspace_id or node.project_id != project_id:
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
        scoped = [
            node_id
            for node_id in self.semantic_anchors
            if node_id not in self.nodes
            or (
                self.nodes[node_id].workspace_id == workspace_id
                and self.nodes[node_id].project_id == project_id
            )
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
        node = self.nodes.get(node_id)
        if node is None or node.workspace_id != workspace_id or node.project_id != project_id:
            return False
        del self.nodes[node_id]
        self.edges = [
            edge for edge in self.edges if edge.source != node_id and edge.target != node_id
        ]
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
    workspace_id: str = ""
    project_id: str = ""


class InMemoryVectorStore:
    """Vector store fake with deterministic scoring supplied by the test."""

    def __init__(self, results: list[dict[str, Any]] | None = None) -> None:
        self.documents: dict[str, _StoredDocument] = {}
        self.results = list(results or [])
        self.queries: list[str] = []

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
    ) -> list[dict[str, Any]]:
        self.queries.append(query_text)
        scoped = [
            result
            for result in self.results
            if result.get("id") not in self.documents
            or (
                self.documents[result["id"]].workspace_id == workspace_id
                and self.documents[result["id"]].project_id == project_id
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
        return self._claims

    async def logout_url(
        self,
        *,
        post_logout_redirect_uri: str,
        id_token_hint: str | None = None,
        state: str | None = None,
    ) -> str:
        return f"https://idp.test/end-session?post_logout_redirect_uri={post_logout_redirect_uri}"

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
        updated = credential.model_copy(
            update={
                "key_fingerprint": self._key_fingerprint(plaintext_key),
                "key_last4": self._key_last4(plaintext_key),
                "rotated_at": datetime.now(UTC),
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
        )
