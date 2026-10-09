"""First-workspace provisioning for a site nobody can yet get into."""

import pytest

from backend.devtools.provision import ProvisionError, find_owner, provision
from backend.domain.identity import User
from backend.domain.tenancy import Role
from tests.doubles import InMemoryTenancyStore

pytestmark = pytest.mark.application

JAKE = User(id="usr_1", email="Jake@Example.com", display_name="Jake")
OTHER = User(id="usr_2", email="other@example.com")
GONE = User(id="usr_3", email="gone@example.com", is_active=False)
ARGS = {
    "workspace_slug": "studio",
    "workspace_name": "Studio",
    "project_slug": "first-novel",
    "project_name": "First novel",
}


def test_owner_must_be_one_active_user_who_has_signed_in() -> None:
    assert find_owner([JAKE, OTHER, GONE], " jake@example.com ").id == "usr_1"
    with pytest.raises(ProvisionError, match="sign in once"):
        find_owner([JAKE], "nobody@example.com")
    with pytest.raises(ProvisionError, match="sign in once"):
        find_owner([GONE], "gone@example.com")
    with pytest.raises(ProvisionError, match="more than one"):
        find_owner([JAKE, User(id="usr_9", email="jake@example.com")], "jake@example.com")


@pytest.mark.asyncio
async def test_provisions_an_owned_workspace_and_project_once() -> None:
    store = InMemoryTenancyStore()

    first = await provision(store, JAKE, **ARGS)
    again = await provision(store, JAKE, **ARGS)

    assert first.created_workspace and first.created_project
    assert not again.created_workspace and not again.created_project
    assert (again.workspace_id, again.project_id) == (first.workspace_id, first.project_id)
    membership = await store.get_membership(first.workspace_id, JAKE.id)
    assert membership.role is Role.OWNER
    assert len(await store.list_projects(first.workspace_id)) == 1
    audit = [event for event in store.audit if event.action == "workspace.provisioned"]
    assert len(audit) == 1 and audit[0].actor_user_id == "operator:cli"
    assert audit[0].detail["owner_user_id"] == JAKE.id


@pytest.mark.asyncio
async def test_never_grants_access_to_someone_elses_workspace() -> None:
    store = InMemoryTenancyStore()
    owned = await provision(store, JAKE, **ARGS)

    with pytest.raises(ProvisionError, match="does not own it"):
        await provision(store, OTHER, **ARGS)
    assert await store.get_membership(owned.workspace_id, OTHER.id) is None

    # An editor of the workspace is not silently promoted either.
    await store.add_membership(workspace_id=owned.workspace_id, user_id=OTHER.id, role=Role.EDITOR)
    with pytest.raises(ProvisionError):
        await provision(store, OTHER, **ARGS)
    assert (await store.get_membership(owned.workspace_id, OTHER.id)).role is Role.EDITOR


@pytest.mark.asyncio
async def test_rejects_unsafe_slugs() -> None:
    store = InMemoryTenancyStore()
    for bad in ("Studio", "../etc", "a b", ""):
        with pytest.raises(ProvisionError, match="invalid slug"):
            await provision(store, JAKE, **{**ARGS, "workspace_slug": bad})
    assert store.workspaces == {}
