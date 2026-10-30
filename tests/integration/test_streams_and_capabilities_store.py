"""PostgreSQL stores of migration 0059: a draft's words while written (SCN-002), and what
providers' lists say each model can take (CTX-013)."""

from __future__ import annotations

import time

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.adapters.job_store import PostgresJobStore
from backend.adapters.model_capability_store import PostgresModelCapabilityStore
from backend.adapters.stream_store import PostgresJobStreamStore
from backend.core.database import create_engine, create_session_factory, session_scope
from backend.domain.identity import AuthMode
from backend.domain.jobs import AUTHORIZATION_POLICY_VERSION, Job, JobStatus
from backend.domain.model_capabilities import ModelCapability

pytestmark = pytest.mark.integration

WS, PR = "ws_stream_it", "pr_stream_it"


@pytest_asyncio.fixture
async def stack(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    try:
        yield PostgresJobStore(factory), PostgresJobStreamStore(factory), factory
    finally:
        await engine.dispose()


def _job(job_id: str) -> Job:
    return Job(
        workspace_id=WS,
        project_id=PR,
        id=job_id,
        kind="scene_draft",
        status=JobStatus.QUEUED,
        actor_user_id="usr_a",
        auth_mode=AuthMode.LOCAL,
        policy_version=AUTHORIZATION_POLICY_VERSION,
        role="owner",
        payload={"plan_id": "plan_1", "variants": 2},
        correlation_id="corr_1",
    )


KEY = {"workspace_id": WS, "project_id": PR, "job_id": "job_s1"}


@pytest.mark.asyncio
async def test_words_are_appended_read_from_an_offset_and_cleared(stack):
    jobs, streams, factory = stack
    await jobs.create(_job("job_s1"))
    await streams.begin(**KEY, number=1)
    await streams.begin(**KEY, number=2)
    await streams.append(**KEY, number=1, piece="Tobin waited ")
    await streams.append(**KEY, number=1, piece="at the quay — ")
    await streams.append(**KEY, number=1, piece="café lamps.")
    await streams.append(**KEY, number=2, piece="")

    parts = await streams.read(**KEY, offsets={})
    assert [(p.number, p.offset, p.chars, p.text, p.done) for p in parts] == [
        (1, 0, 38, "Tobin waited at the quay — café lamps.", False),
        (2, 0, 0, "", False),
    ]
    # Offsets count characters, as Python does, whatever their bytes.
    later = await streams.read(**KEY, offsets={1: 27, 2: 0})
    assert later[0].text == "café lamps." and later[0].offset == 27
    # An offset past the end (a draft started again) is brought back to the end.
    beyond = await streams.read(**KEY, offsets={1: 500})
    assert beyond[0].offset == 38 and beyond[0].text == ""

    await streams.end(**KEY, number=1)
    ended = await streams.read(**KEY, offsets={})
    assert ended[0].done and ended[0].text == "" and not ended[1].done
    # Nothing is added to a stored draft's passing copy.
    await streams.append(**KEY, number=1, piece="more")
    assert (await streams.read(**KEY, offsets={}))[0].text == ""

    # Started again by a worker that took the job over: from nothing.
    await streams.append(**KEY, number=2, piece="Second.")
    await streams.begin(**KEY, number=2)
    assert (await streams.read(**KEY, offsets={2: 3}))[1].offset == 0

    # Another story's words are never read.
    other = await streams.read(workspace_id="ws_other", project_id=PR, job_id="job_s1", offsets={})
    assert other == []


@pytest.mark.asyncio
async def test_the_sweep_clears_words_of_ended_work_and_rows_go_with_their_job(stack):
    jobs, streams, factory = stack
    await jobs.create(_job("job_s2"))
    key = {**KEY, "job_id": "job_s2"}
    await streams.begin(**key, number=1)
    await streams.append(**key, number=1, piece="Left by a server that stopped.")
    assert await streams.sweep() == 0  # still queued: nothing to clear
    async with session_scope(factory) as db:
        await db.execute(
            text("UPDATE jobs SET status = 'failed' WHERE workspace_id = :w AND id = 'job_s2'"),
            {"w": WS},
        )
    # The rows of ended work are deleted, not only cleared.
    assert await streams.sweep() == 1
    assert await streams.read(**key, offsets={}) == []
    assert await streams.sweep() == 0
    await streams.begin(**key, number=2)  # a row again, to see it go with its job
    async with session_scope(factory) as db:
        await db.execute(text("DELETE FROM jobs WHERE id = 'job_s2'"))
        left = (
            await db.execute(text("SELECT count(*) FROM job_streams WHERE job_id = 'job_s2'"))
        ).scalar_one()
    assert left == 0


@pytest.mark.asyncio
async def test_a_worker_that_lost_the_job_never_touches_the_newer_ones_words(stack):
    jobs, streams, _factory = stack
    await jobs.create(_job("job_s3"))
    key = {**KEY, "job_id": "job_s3", "number": 1}
    await streams.begin(**key, attempt=1)
    await streams.append(**key, piece="Old words. ", attempt=1)
    # Taken over: attempt 2 starts the draft again from nothing.
    await streams.begin(**key, attempt=2)
    await streams.append(**key, piece="New words. ", attempt=2)
    # The old worker, still running, adds nothing, starts nothing again, ends nothing.
    await streams.append(**key, piece="Late words. ", attempt=1)
    await streams.begin(**key, attempt=1)
    await streams.end(**{**key, "number": None}, attempt=1)
    await streams.end(**key, attempt=1)
    part = (await streams.read(**KEY | {"job_id": "job_s3"}, offsets={}))[0]
    assert (part.text, part.chars, part.done) == ("New words. ", 11, False)
    # Nothing new since the offset: no words read, only the count.
    same = (await streams.read(**KEY | {"job_id": "job_s3"}, offsets={1: 11}))[0]
    assert (same.text, same.offset) == ("", 11)
    # The newer worker's own end (or the whole job's) does end it.
    await streams.end(**{**key, "number": None}, attempt=2)
    ended = (await streams.read(**KEY | {"job_id": "job_s3"}, offsets={}))[0]
    assert ended.done and ended.text == ""


@pytest.mark.asyncio
async def test_a_providers_list_is_kept_whole_and_replaced_whole(stack):
    _jobs, _streams, factory = stack
    store = PostgresModelCapabilityStore(factory)
    assert await store.load("openrouter") == ([], 0.0)
    first = [
        ModelCapability("openrouter", "a/one", True, None, 1000, 200, True, "provider"),
        ModelCapability("openrouter", "a/two", False, None, None, None, None, "provider"),
    ]
    await store.save("openrouter", first)
    found, at = await store.load("openrouter")
    assert sorted(item.model for item in found) == ["a/one", "a/two"]
    one = next(item for item in found if item.model == "a/one")
    assert one.sees_images is True and one.context_tokens == 1000 and one.max_output_tokens == 200
    assert abs(at - time.time()) < 120
    await store.save("openrouter", [first[1]])
    found, _ = await store.load("openrouter")
    assert [item.model for item in found] == ["a/two"]
