"""The author's notices (CON-011) in PostgreSQL: kept once per audit, listed newest first for
their author only, marked read, and deleted with the project."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.notice_store import PostgresNoticeStore
from backend.core.database import create_engine, create_session_factory, session_scope
from backend.domain.notices import Notice

pytestmark = pytest.mark.integration

WS, PR = "ws_notice_it", "pr_notice_it"


@pytest_asyncio.fixture
async def stack(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    try:
        yield PostgresNoticeStore(factory), PostgresCanonRepository(factory), factory
    finally:
        await engine.dispose()


def _notice(notice_id: str, job_id: str, user: str = "usr_author") -> Notice:
    return Notice(
        workspace_id=WS,
        project_id=PR,
        id=notice_id,
        user_id=user,
        kind="audit_finished",
        job_id=job_id,
        status="succeeded",
        schedule_id="sch_1",
        plan_id="pln_beat",
        scenes=4,
        notes=3,
        checks=2,
        prompt_tokens=1000,
        completion_tokens=40,
        cost_usd=0.012,
    )


@pytest.mark.asyncio
async def test_a_notice_is_kept_once_listed_for_its_author_and_marked_read(stack) -> None:
    store, repository, factory = stack
    assert await store.add(_notice("ntc_1", "job_1"))
    # The same audit's end seen again keeps the first.
    assert not await store.add(_notice("ntc_2", "job_1"))
    assert await store.add(_notice("ntc_3", "job_2"))
    assert await store.add(_notice("ntc_4", "job_3", user="usr_other"))

    mine = await store.for_user(workspace_id=WS, project_id=PR, user_id="usr_author", limit=10)
    assert [item.id for item in mine] == ["ntc_3", "ntc_1"]
    assert mine[0].tokens == 1040 and mine[0].cost_usd == pytest.approx(0.012)
    one = await store.for_user(
        workspace_id=WS, project_id=PR, user_id="usr_author", limit=10, job_id="job_1"
    )
    assert [item.id for item in one] == ["ntc_1"]

    at = datetime.now(UTC)
    assert await store.mark_seen(
        workspace_id=WS, project_id=PR, user_id="usr_author", notice_id="ntc_1", at=at
    )
    # Someone else's notice cannot be marked by this author.
    assert not await store.mark_seen(
        workspace_id=WS, project_id=PR, user_id="usr_author", notice_id="ntc_4", at=at
    )
    (seen,) = await store.for_user(
        workspace_id=WS, project_id=PR, user_id="usr_author", limit=10, job_id="job_1"
    )
    assert seen.seen_at is not None

    await repository.purge_project(workspace_id=WS, project_id=PR)
    async with session_scope(factory) as db:
        left = (
            await db.execute(
                text("SELECT count(*) FROM author_notices WHERE workspace_id = :w"), {"w": WS}
            )
        ).scalar_one()
    assert left == 0


@pytest.mark.asyncio
async def test_only_unread_notices_are_filtered_before_the_limit(stack) -> None:
    store, _repository, _factory = stack
    assert await store.add(_notice("ntc_old", "job_old"))
    for n in range(3):
        assert await store.add(_notice(f"ntc_{n}", f"job_{n}"))
        assert await store.mark_seen(
            workspace_id=WS,
            project_id=PR,
            user_id="usr_author",
            notice_id=f"ntc_{n}",
            at=datetime.now(UTC),
        )
    unread = await store.for_user(
        workspace_id=WS, project_id=PR, user_id="usr_author", limit=2, unseen_only=True
    )
    # The newest two are read; the older unread one is still found.
    assert [item.id for item in unread] == ["ntc_old"]
