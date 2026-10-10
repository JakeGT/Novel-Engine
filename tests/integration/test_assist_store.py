from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio

from backend.adapters.assist_store import PostgresAssistSessionStore
from backend.core.database import create_engine, create_session_factory
from backend.domain.assist import (
    AssistAnswer,
    AssistKind,
    AssistQuestion,
    AssistSession,
    AssistStatus,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def store(disposable_database):
    engine = create_engine(disposable_database.settings())
    try:
        yield PostgresAssistSessionStore(create_session_factory(engine))
    finally:
        await engine.dispose()


def _session(session_id: str, **overrides) -> AssistSession:
    values = {
        "id": session_id,
        "workspace_id": "ws1",
        "project_id": "p1",
        "actor_user_id": "u1",
        "kind": AssistKind.CREATE,
        "description": "Mara is a pilot — “the Fox”.",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return AssistSession(**values)


@pytest.mark.asyncio
async def test_a_conversation_round_trips_and_updates_in_place(store) -> None:
    question = AssistQuestion(id="q1", question="Where does she live?", category="place")
    answer = AssistAnswer(question_id="q1", question="Where does she live?", answer="The harbor.")
    await store.save(_session("brf_1", entity_type="Character", name="Mara", questions=[question]))

    await store.save(
        _session(
            "brf_1",
            questions=[question],
            answers=[answer],
            notes=["One note"],
            understood="Understood.",
            status=AssistStatus.PROPOSED,
            mutation_id="mut_1",
            updated_at=NOW + timedelta(minutes=5),
        )
    )

    kept = await store.get(workspace_id="ws1", project_id="p1", session_id="brf_1")
    assert kept is not None
    assert kept.description == "Mara is a pilot — “the Fox”."
    assert kept.questions == [question] and kept.answers == [answer]
    assert kept.notes == ["One note"] and kept.understood == "Understood."
    assert kept.status is AssistStatus.PROPOSED and kept.mutation_id == "mut_1"
    assert kept.created_at == NOW and kept.updated_at == NOW + timedelta(minutes=5)
    found = await store.for_mutation(workspace_id="ws1", project_id="p1", mutation_id="mut_1")
    assert found is not None and found.id == "brf_1"
    assert (
        await store.for_mutation(workspace_id="ws1", project_id="p2", mutation_id="mut_1") is None
    )


@pytest.mark.asyncio
async def test_open_conversations_are_listed_per_author_newest_first(store) -> None:
    await store.save(_session("brf_old"))
    await store.save(_session("brf_new", updated_at=NOW + timedelta(hours=1)))
    await store.save(_session("brf_done", status=AssistStatus.PROPOSED, mutation_id="mut_9"))
    await store.save(_session("brf_theirs", actor_user_id="u2"))
    await store.save(_session("brf_elsewhere", project_id="p2"))

    mine = await store.list_open(workspace_id="ws1", project_id="p1", actor_user_id="u1", limit=10)

    assert [session.id for session in mine] == ["brf_new", "brf_old"]
    limited = await store.list_open(
        workspace_id="ws1", project_id="p1", actor_user_id="u1", limit=1
    )
    assert [session.id for session in limited] == ["brf_new"]


@pytest.mark.asyncio
async def test_a_conversation_cannot_be_read_or_overwritten_from_another_scope(store) -> None:
    await store.save(_session("brf_1"))

    assert await store.get(workspace_id="ws2", project_id="p1", session_id="brf_1") is None
    assert await store.get(workspace_id="ws1", project_id="p2", session_id="brf_1") is None
    # The same id arriving under another workspace changes nothing.
    await store.save(_session("brf_1", workspace_id="ws2", description="Overwritten"))
    kept = await store.get(workspace_id="ws1", project_id="p1", session_id="brf_1")
    assert kept is not None and kept.description.startswith("Mara")


@pytest.mark.asyncio
async def test_conversations_are_deleted_with_their_project_or_workspace(store) -> None:
    await store.save(_session("brf_1"))
    await store.save(_session("brf_2", project_id="p2"))
    await store.save(_session("brf_3", workspace_id="ws2"))

    # One conversation is erased only through its own scope.
    assert await store.delete(workspace_id="ws2", project_id="p1", session_id="brf_1") == 0
    assert await store.delete(workspace_id="ws1", project_id="p1", session_id="brf_1") == 1
    await store.save(_session("brf_1"))

    assert await store.delete_project("ws1", "p1") == 1
    assert await store.get(workspace_id="ws1", project_id="p1", session_id="brf_1") is None
    assert await store.delete_workspace("ws1") == 1
    assert await store.get(workspace_id="ws2", project_id="p1", session_id="brf_3") is not None
