"""The assistant's kept threads stay their author's (AST-EXP-017): an archive carries only
the exporter's own, restoring never writes into anyone else's, a removed member's are
erased; and how suggestions fared is told per line of the story, with what a rule kept
counted apart from what the author kept (AST-EXP-007, AST-EXP-015)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from backend.domain.branching import working_in
from backend.domain.conversations import MAX_TURNS, RunKind, ThreadKind
from backend.domain.delegation import kept_note
from backend.domain.identity import AccessScope, AuthMode, IdentityClaims, Role
from backend.domain.journal import MutationStatus
from backend.services.conversation_service import (
    ConversationService,
    assistant_turn,
    author_turn,
)
from backend.services.tenancy_service import TenancyService
from tests.doubles import InMemoryConversationStore, InMemoryIdentityStore, InMemoryTenancyStore

pytestmark = pytest.mark.application

WS, PR = "ws_1", "pr_1"


def _scope(actor: str) -> AccessScope:
    return AccessScope(actor_user_id=actor, auth_mode=AuthMode.LOCAL)


async def _no_mutation(_commit_id):
    return None


async def _talk(service: ConversationService, actor: str, words: str) -> None:
    await service.record(
        _scope(actor), WS, PR, ThreadKind.CHANGE, [author_turn(words), assistant_turn("Noted.")]
    )


@pytest.mark.asyncio
async def test_an_archive_carries_only_the_exporting_author_s_threads() -> None:
    service = ConversationService(InMemoryConversationStore(), local_mode=True)
    await _talk(service, "usr_owner", "The owner's plan.")
    await _talk(service, "usr_editor", "The editor's private worry.")

    exported = await service.export_threads(WS, PR, {}, actor_user_id="usr_owner")
    assert [thread["actor_user_id"] for thread in exported] == ["usr_owner"]
    assert "private worry" not in str(exported)
    assert await service.export_threads(WS, PR, {}, actor_user_id=None) == []


@pytest.mark.asyncio
async def test_restoring_never_writes_into_someone_else_s_thread() -> None:
    store = InMemoryConversationStore()
    service = ConversationService(store, local_mode=True)
    await _talk(service, "usr_editor", "Mine.")
    exported = await service.export_threads(WS, PR, {}, actor_user_id="usr_owner")
    assert exported == []
    # An edited archive names the editor as the author of a planted thread.
    planted = [
        {
            "kind": "change",
            "actor_user_id": "usr_editor",
            "branch_id": None,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "turns": [{"role": "author", "body": "Planted words.", "commit_id": None}],
        }
    ]
    restored = await service.restore_threads(
        WS, PR, planted, {}, _no_mutation, actor_user_id="usr_owner"
    )
    assert restored == 1
    editor = await service.thread(_scope("usr_editor"), WS, PR, ThreadKind.CHANGE)
    assert [turn.body for turn in editor.turns] == ["Mine.", "Noted."]
    owner = await service.thread(_scope("usr_owner"), WS, PR, ThreadKind.CHANGE)
    assert [turn.body for turn in owner.turns] == ["Planted words."]
    # Nobody restoring: nothing is restored.
    assert (
        await service.restore_threads(WS, "pr_2", planted, {}, _no_mutation, actor_user_id=None)
        == 0
    )


@pytest.mark.asyncio
async def test_an_older_archive_with_several_authors_gives_back_only_the_restorer_s() -> None:
    source = ConversationService(InMemoryConversationStore(), local_mode=True)
    await _talk(source, "usr_owner", "The owner's plan.")
    await _talk(source, "usr_editor", "The editor's private worry.")
    # Archives made before export was limited held everyone's threads.
    everyone = [
        *await source.export_threads(WS, PR, {}, actor_user_id="usr_editor"),
        *await source.export_threads(WS, PR, {}, actor_user_id="usr_owner"),
    ]
    store = InMemoryConversationStore()
    target = ConversationService(store, local_mode=True)
    assert (
        await target.restore_threads(WS, PR, everyone, {}, _no_mutation, actor_user_id="usr_owner")
        == 1
    )
    (thread,) = await store.list_threads(workspace_id=WS, project_id=PR)
    assert thread.actor_user_id == "usr_owner"
    assert "private worry" not in thread.model_dump_json()


@pytest.mark.asyncio
async def test_removing_a_member_erases_their_threads(settings_factory) -> None:
    settings = settings_factory(
        APP_ENV="test",
        AUTH_MODE="hosted",
        OIDC_ISSUER="https://auth.example.test/application/o/novel/",
        OIDC_CLIENT_ID="client",
        OIDC_CLIENT_SECRET="secret",
        OIDC_REDIRECT_URI="https://novel.example.test/auth/callback",
    )
    tenancy_store = InMemoryTenancyStore()
    identity = InMemoryIdentityStore()
    tenancy = TenancyService(tenancy_store, identity_store=identity, settings=settings)
    users = {}
    for name in ("owner", "editor"):
        user = await identity.get_or_create_user(
            IdentityClaims(issuer="local", subject=name, display_name=name.title())
        )
        users[name] = AccessScope(actor_user_id=user.id, auth_mode=AuthMode.HOSTED)
    workspace = await tenancy_store.create_workspace(slug="ws", name="WS")
    for name, role in (("owner", Role.OWNER), ("editor", Role.EDITOR)):
        await tenancy_store.add_membership(
            workspace_id=workspace.id, user_id=users[name].actor_user_id, role=role
        )
    store = InMemoryConversationStore()
    # Built like the container builds it: attached to tenancy on its own.
    service = ConversationService(store, tenancy=tenancy, local_mode=True)
    for name in ("owner", "editor"):
        for project in ("pr_a", "pr_b"):
            await service.record(
                users[name],
                workspace.id,
                project,
                ThreadKind.PREMISE,
                [author_turn(f"{name}'s story")],
            )

    assert await tenancy.remove_member(users["owner"], workspace.id, users["editor"].actor_user_id)
    left = [thread.actor_user_id for thread in store.threads.values()]
    assert left == [users["owner"].actor_user_id] * 2


@pytest.mark.asyncio
async def test_runs_are_told_for_the_line_the_author_works_in() -> None:
    service = ConversationService(InMemoryConversationStore(), local_mode=True)
    await service.record_run(_scope("usr_a"), WS, PR, kind=RunKind.LINKS, suggested=2)
    with working_in("branch_whatif"):
        await service.record_run(_scope("usr_a"), WS, PR, kind=RunKind.LINKS, suggested=5)
        assert [run.suggested for run in await service.runs(_scope("usr_a"), WS, PR)] == [5]
    assert [run.suggested for run in await service.runs(_scope("usr_a"), WS, PR)] == [2]


class _Journal:
    """Only what settling a run reads: one committed suggestion of three changes."""

    def __init__(self, note: str | None) -> None:
        self.note = note

    async def get_mutation(self, *, workspace_id, project_id, mutation_id):
        return SimpleNamespace(status=MutationStatus.COMMITTED)

    async def list_operations(self, *, mutation_id):
        return [SimpleNamespace(ordinal=n, revision=1) for n in (1, 2, 3)]

    async def list_operation_decisions(self, *, mutation_id):
        return []

    async def list_decisions(self, *, mutation_id):
        return [SimpleNamespace(decision="approved", note=self.note)]


@pytest.mark.asyncio
async def test_what_a_rule_kept_is_counted_apart_from_what_the_author_kept() -> None:
    service = ConversationService(
        InMemoryConversationStore(), journal=_Journal(kept_note(["new_entry"])), local_mode=True
    )
    await service.record_run(
        _scope("usr_a"), WS, PR, kind=RunKind.LINKS, suggested=3, mutation_id="mut_1"
    )
    (run,) = await service.runs(_scope("usr_a"), WS, PR)
    assert (run.kept, run.kept_for_you, run.precision) == (0, 3, None)
    assert run.settled_at is not None
    # Read again from the store: still apart.
    (again,) = await service.runs(_scope("usr_a"), WS, PR)
    assert (again.kept, again.kept_for_you) == (0, 3)

    by_hand = ConversationService(
        InMemoryConversationStore(), journal=_Journal("Looks right."), local_mode=True
    )
    await by_hand.record_run(
        _scope("usr_a"), WS, PR, kind=RunKind.LINKS, suggested=3, mutation_id="mut_1"
    )
    (run,) = await by_hand.runs(_scope("usr_a"), WS, PR)
    assert (run.kept, run.kept_for_you, run.precision) == (3, None, 1.0)


@pytest.mark.asyncio
async def test_a_long_premise_thread_never_lets_go_of_the_story_it_was_told() -> None:
    store = InMemoryConversationStore()
    service = ConversationService(store, local_mode=True)
    scope = _scope("usr_a")
    await service.record(
        scope,
        WS,
        PR,
        ThreadKind.PREMISE,
        [author_turn("Wren lights the lamps."), assistant_turn("Ok.")],
    )
    for n in range(MAX_TURNS):
        await service.record(
            scope,
            WS,
            PR,
            ThreadKind.PREMISE,
            [author_turn("", [(f"Q{n}?", "A.")]), assistant_turn("Ok.")],
        )
    thread = await service.thread(scope, WS, PR, ThreadKind.PREMISE)
    assert thread.turns[0].body == "Wren lights the lamps."
    assert len(thread.turns) == MAX_TURNS + 1
    # A Change anything thread keeps only the newest.
    for n in range(MAX_TURNS // 2 + 1):
        await _talk(service, "usr_a", f"Change {n}.")
    change = await service.thread(scope, WS, PR, ThreadKind.CHANGE)
    assert len(change.turns) == MAX_TURNS and change.turns[0].body == "Change 1."
