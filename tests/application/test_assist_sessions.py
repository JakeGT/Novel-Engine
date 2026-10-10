"""Who may see and change a conversation with the assistant."""

import pytest

from backend.adapters.purge import StoreWorkspacePurge
from backend.domain.assist import AssistAnswer, AssistKind, AssistStatus
from backend.domain.errors import ConflictError, ForbiddenError, NotFoundError
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.tenancy import Permission
from backend.services.assist_session_service import AssistSessionService
from tests.doubles import InMemoryAssistSessionStore

pytestmark = pytest.mark.application

WS, PROJECT = "ws_1", "prj_1"


class _Tenancy:
    """Readers may read; only the named writers may write."""

    def __init__(self, writers: set[str], readers: set[str]) -> None:
        self.writers, self.readers = writers, readers

    async def require_project(self, scope, workspace_id, project_id, permission) -> None:
        allowed = self.writers if permission is Permission.PROJECT_WRITE else self.readers
        if workspace_id != WS or project_id != PROJECT or scope.actor_user_id not in allowed:
            raise ForbiddenError("Not allowed.")


def _scope(user: str) -> AccessScope:
    return AccessScope(actor_user_id=user, auth_mode=AuthMode.HOSTED)


def _service(store: InMemoryAssistSessionStore | None = None) -> AssistSessionService:
    return AssistSessionService(
        store or InMemoryAssistSessionStore(),
        tenancy=_Tenancy(writers={"ana", "ben"}, readers={"ana", "ben", "reader"}),
    )


@pytest.mark.asyncio
async def test_an_unfinished_conversation_belongs_to_its_author_alone() -> None:
    service = _service()
    session = await service.begin(
        _scope("ana"), WS, PROJECT, kind=AssistKind.CREATE, description="A secret idea."
    )

    assert [s.id for s in await service.list_open(_scope("ana"), WS, PROJECT)] == [session.id]
    # Another editor of the same project neither lists it, reads it, nor alters it.
    assert await service.list_open(_scope("ben"), WS, PROJECT) == []
    with pytest.raises(NotFoundError):
        await service.get(_scope("ben"), WS, PROJECT, session.id)
    with pytest.raises(NotFoundError):
        await service.save_answers(_scope("ben"), WS, PROJECT, session.id, [])
    with pytest.raises(NotFoundError):
        await service.begin(
            _scope("ben"), WS, PROJECT, kind=AssistKind.CREATE, session_id=session.id
        )
    with pytest.raises(NotFoundError):
        await service.close(_scope("ben"), WS, PROJECT, session.id)


@pytest.mark.asyncio
async def test_a_proposed_conversation_is_evidence_for_every_reader_and_is_fixed() -> None:
    service = _service()
    answer = AssistAnswer(question_id="q1", question="Where?", answer="The harbor.")
    session = await service.begin(
        _scope("ana"), WS, PROJECT, kind=AssistKind.CREATE, description="Mara.", answers=[answer]
    )
    await service.proposed(_scope("ana"), WS, PROJECT, session.id, mutation_id="mut_1")

    seen = await service.for_mutation(_scope("reader"), WS, PROJECT, "mut_1")
    assert seen is not None and seen.status is AssistStatus.PROPOSED and seen.answers == [answer]
    assert (await service.get(_scope("reader"), WS, PROJECT, session.id)).id == session.id
    assert await service.for_mutation(_scope("reader"), WS, PROJECT, "mut_other") is None
    # Evidence does not change after the fact, not even by its author.
    with pytest.raises(ConflictError):
        await service.save_answers(_scope("ana"), WS, PROJECT, session.id, [])
    with pytest.raises(ConflictError):
        await service.proposed(_scope("ana"), WS, PROJECT, session.id, mutation_id="mut_2")


@pytest.mark.asyncio
async def test_conversations_follow_project_permissions_and_scope() -> None:
    store = InMemoryAssistSessionStore()
    service = _service(store)
    session = await service.begin(_scope("ana"), WS, PROJECT, kind=AssistKind.CHANGE)
    assert session.id.startswith("chg_")

    # A reader cannot start one; an outsider cannot even list.
    with pytest.raises(ForbiddenError):
        await service.begin(_scope("reader"), WS, PROJECT, kind=AssistKind.CREATE)
    with pytest.raises(ForbiddenError):
        await service.list_open(_scope("stranger"), WS, PROJECT)
    # The same id is not reachable through another project.
    with pytest.raises(ForbiddenError):
        await service.get(_scope("ana"), WS, "prj_other", session.id)
    assert await store.get(workspace_id=WS, project_id="prj_other", session_id=session.id) is None


@pytest.mark.asyncio
async def test_deleting_a_project_or_workspace_removes_its_conversations() -> None:
    class _Nothing:
        def __getattr__(self, name):
            return lambda *args, **kwargs: 0

    class _AsyncNothing:
        async def delete_workspace(self, workspace_id: str) -> int:
            return 0

    store = InMemoryAssistSessionStore()
    service = AssistSessionService(store, local_mode=True)
    for workspace, project in ((WS, PROJECT), (WS, "prj_2"), ("ws_2", "prj_3")):
        await service.begin(_scope("ana"), workspace, project, kind=AssistKind.CREATE)
    purge = StoreWorkspacePurge(
        graph=_Nothing(),
        vectors=_Nothing(),
        credentials=_AsyncNothing(),
        usage=_AsyncNothing(),
        objects=_Nothing(),
        artifacts=_Nothing(),
        assist=store,
    )

    assert (await purge.purge_project(WS, PROJECT))["assist_sessions"] == 1
    assert {s.project_id for s in store.sessions.values()} == {"prj_2", "prj_3"}
    assert (await purge.purge_workspace(WS))["assist_sessions"] == 1
    assert {s.workspace_id for s in store.sessions.values()} == {"ws_2"}


@pytest.mark.asyncio
async def test_an_unfinished_conversation_left_untouched_for_a_month_is_erased() -> None:
    from datetime import UTC, datetime, timedelta

    store = InMemoryAssistSessionStore()
    clock = [datetime(2026, 10, 1, tzinfo=UTC)]
    service = AssistSessionService(
        store,
        tenancy=_Tenancy(writers={"ana"}, readers={"ana"}),
        now=lambda: clock[0],
    )
    old = await service.begin(
        _scope("ana"), WS, PROJECT, kind=AssistKind.CREATE, description="An old idea."
    )
    # One that led to changes is evidence, and is kept however old it gets.
    used = await service.begin(
        _scope("ana"), WS, PROJECT, kind=AssistKind.CREATE, description="A used idea."
    )
    await service.proposed(_scope("ana"), WS, PROJECT, used.id, mutation_id="mut_1")

    clock[0] += timedelta(days=29)
    assert [s.id for s in await service.list_open(_scope("ana"), WS, PROJECT)] == [old.id]

    clock[0] += timedelta(days=2)
    assert await service.list_open(_scope("ana"), WS, PROJECT) == []
    assert old.id not in store.sessions and used.id in store.sessions
