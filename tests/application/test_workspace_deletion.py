import pytest

from backend.adapters.artifacts import NullArtifactStore
from backend.adapters.objects import NullObjectStore
from backend.adapters.purge import StoreWorkspacePurge
from backend.domain.credentials import CredentialProvider
from backend.domain.identity import AccessScope, AuthMode, IdentityClaims
from backend.graph.schema import Node
from backend.services.tenancy_service import TenancyService
from tests.doubles import (
    InMemoryCredentialVault,
    InMemoryGraphRepository,
    InMemoryIdentityStore,
    InMemoryProviderUsageStore,
    InMemoryRateLimiter,
    InMemorySessionStore,
    InMemoryTenancyStore,
    InMemoryVectorStore,
)

pytestmark = pytest.mark.application


async def _setup():
    graph = InMemoryGraphRepository()
    vectors = InMemoryVectorStore()
    vault = InMemoryCredentialVault()
    usage = InMemoryProviderUsageStore()
    tenancy = InMemoryTenancyStore()
    identity = InMemoryIdentityStore()
    purger = StoreWorkspacePurge(
        graph=graph,
        vectors=vectors,
        credentials=vault,
        usage=usage,
        objects=NullObjectStore(),
        artifacts=NullArtifactStore(),
    )
    service = TenancyService(
        tenancy,
        identity_store=identity,
        session_store=InMemorySessionStore(),
        rate_limiter=InMemoryRateLimiter(),
        purger=purger,
    )
    owner = await identity.get_or_create_user(IdentityClaims(issuer="local", subject="owner"))
    scope = AccessScope(actor_user_id=owner.id, auth_mode=AuthMode.LOCAL)
    return graph, vectors, vault, service, owner, scope


@pytest.mark.asyncio
async def test_workspace_deletion_cascades_across_stores() -> None:
    graph, vectors, vault, service, owner, scope = await _setup()
    workspace = await service.create_workspace(scope, slug="cascade", name="Cascade")
    project = await service.create_project(scope, workspace.id, slug="novel", name="Novel")

    graph.add_node(
        Node(
            id="n1",
            type="Character",
            properties={},
            workspace_id=workspace.id,
            project_id=project.id,
        )
    )
    vectors.add_documents(["doc"], [{}], ["d1"], workspace_id=workspace.id, project_id=project.id)
    await vault.store(
        workspace_id=workspace.id,
        owner_user_id=owner.id,
        provider=CredentialProvider.OPENROUTER,
        label="key",
        plaintext_key="sk-test-0123456789",
    )

    assert await service.delete_workspace(scope, workspace.id) is True

    assert graph.get_node("n1", workspace_id=workspace.id, project_id=project.id) is None
    assert vectors.documents == {}
    assert await vault.list_for_workspace(workspace.id) == []


@pytest.mark.asyncio
async def test_project_deletion_is_scoped_to_its_project() -> None:
    graph, vectors, _vault, service, owner, scope = await _setup()
    workspace = await service.create_workspace(scope, slug="proj", name="Proj")
    keep = await service.create_project(scope, workspace.id, slug="keep", name="Keep")
    drop = await service.create_project(scope, workspace.id, slug="drop", name="Drop")

    graph.add_node(
        Node(
            id="n_keep",
            type="Character",
            properties={},
            workspace_id=workspace.id,
            project_id=keep.id,
        )
    )
    graph.add_node(
        Node(
            id="n_drop",
            type="Character",
            properties={},
            workspace_id=workspace.id,
            project_id=drop.id,
        )
    )
    vectors.add_documents(["a"], [{}], ["d_keep"], workspace_id=workspace.id, project_id=keep.id)
    vectors.add_documents(["b"], [{}], ["d_drop"], workspace_id=workspace.id, project_id=drop.id)

    assert await service.delete_project(scope, workspace.id, drop.id) is True

    assert graph.get_node("n_drop", workspace_id=workspace.id, project_id=drop.id) is None
    assert graph.get_node("n_keep", workspace_id=workspace.id, project_id=keep.id) is not None
    assert set(vectors.documents) == {"d_keep"}
