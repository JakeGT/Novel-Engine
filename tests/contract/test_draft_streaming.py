"""Drafts shown as they are written (SCN-002): streamed, stored whole once, stoppable."""

from __future__ import annotations

import asyncio
import json
from functools import partial

import pytest

from backend.domain.streaming import StreamBuffer
from backend.ports import AIUsage, TextResponse
from tests.contract.test_studio import _draft, _job, _run, _scene_world

pytestmark = pytest.mark.contract

DRAFT = (
    "Tobin watched the water at Lantern Harbor while the tide came in. Mara did not come, "
    "and the lamps along the quay went out one by one."
)


def _events(body: str) -> list[tuple[str, dict]]:
    found = []
    for block in body.split("\n\n"):
        lines = [line for line in block.splitlines() if line and not line.startswith(":")]
        if not lines:
            continue
        name = next(line[7:] for line in lines if line.startswith("event: "))
        data = next(line[6:] for line in lines if line.startswith("data: "))
        found.append((name, json.loads(data)))
    return found


def _small_batches(monkeypatch) -> None:
    # Every few characters is passed on, so a short draft streams in several writes.
    monkeypatch.setattr(
        "backend.services.studio_service.StreamBuffer",
        partial(StreamBuffer, flush_chars=20, flush_seconds=60.0),
    )


def test_a_draft_streams_in_pieces_and_is_stored_whole_once(
    auth_client, fake_container, monkeypatch
):
    _small_batches(monkeypatch)
    w, scene = _scene_world(auth_client, fake_container)
    streams = fake_container.job_streams
    seen: list[str] = []
    original = streams.append

    async def watch(**kwargs):
        seen.append(kwargs["piece"])
        await original(**kwargs)

    monkeypatch.setattr(streams, "append", watch)
    fake_container.ai.queue(DRAFT)
    started = _draft(auth_client, w, scene)
    assert started.status_code == 202, started.text
    job_id = started.json()["id"]
    assert [job.status.value for job in _run(fake_container)] == ["succeeded"]

    assert fake_container.ai.calls[-1]["streamed"] is True
    # Passed on in several batches that together are the whole draft, in order.
    assert len(seen) > 2 and "".join(seen) == DRAFT
    # The passing copy is cleared once the draft is stored whole.
    assert streams.words(*_ids(fake_container, job_id), job_id, 1) == ""
    state = auth_client.get(f"{w['base']}/plan/{scene['id']}/studio").json()
    assert state["drafts"][0]["content"].startswith("Tobin watched the water")
    # Usage is recorded once, as the provider reported it.
    keys = [r.idempotency_key for r in fake_container.usage.records]
    assert keys == [f"{job_id}:1:1"]
    assert _job(auth_client, w, job_id)["completion_tokens"] == len(DRAFT.split())


def _ids(container, job_id: str) -> tuple[str, str]:
    workspace_id, project_id, _ = next(key for key in container.jobs.jobs if key[2] == job_id)
    return workspace_id, project_id


def test_the_stream_endpoint_sends_words_progress_and_the_end(
    auth_client, fake_container, monkeypatch
):
    w, scene = _scene_world(auth_client, fake_container)
    fake_container.ai.queue(DRAFT)
    job_id = _draft(auth_client, w, scene).json()["id"]
    ws, ps = _ids(fake_container, job_id)
    streams = fake_container.job_streams
    asyncio.run(streams.begin(workspace_id=ws, project_id=ps, job_id=job_id, number=1))
    asyncio.run(
        streams.append(
            workspace_id=ws, project_id=ps, job_id=job_id, number=1, piece="Tobin watched "
        )
    )
    # Only what was not sent yet: from the offset asked about.
    parts = asyncio.run(streams.read(workspace_id=ws, project_id=ps, job_id=job_id, offsets={1: 6}))
    assert [(p.number, p.offset, p.text) for p in parts] == [(1, 6, "watched ")]

    # While it waits: the words so far and where it is, then "open me again" when the
    # stream has lasted long enough.
    monkeypatch.setattr("backend.api.v1.studio.STREAM_SECONDS", 0.0)
    waiting = _events(auth_client.get(f"{w['base']}/jobs/{job_id}/stream").text)
    assert waiting[0] == ("text", {"number": 1, "offset": 0, "text": "Tobin watched "})
    assert [name for name, _ in waiting] == ["text", "job", "again"]
    assert waiting[1][1]["status"] == "queued"

    assert [job.status.value for job in _run(fake_container)] == ["succeeded"]
    got = auth_client.get(f"{w['base']}/jobs/{job_id}/stream")
    assert got.status_code == 200, got.text
    assert got.headers["content-type"].startswith("text/event-stream")
    events = _events(got.text)
    assert events[-1] == ("end", {"status": "succeeded"})
    assert ("draft_done", {"number": 1}) in events
    job_event = next(data for name, data in events if name == "job")
    assert job_event["status"] == "succeeded" and job_event["progress"] == 100
    # The stored draft's words are not in the stream any more: they are in the studio.
    assert "Tobin" not in got.text


def test_a_stream_of_someone_elses_job_is_not_found(auth_client, fake_container):
    w, _scene = _scene_world(auth_client, fake_container)
    got = auth_client.get(f"{w['base']}/jobs/job_nothing/stream")
    assert got.status_code == 404, got.text
    assert got.headers["content-type"].startswith("application/problem+json")


def test_stopping_mid_stream_keeps_nothing_and_records_an_estimate_once(
    auth_client, fake_container, monkeypatch
):
    _small_batches(monkeypatch)
    monkeypatch.setattr("backend.services.studio_service.CANCEL_CHECK_SECONDS", 0.0)
    w, scene = _scene_world(auth_client, fake_container)
    fake_container.ai.queue(DRAFT)
    job_id = _draft(auth_client, w, scene).json()["id"]
    streams = fake_container.job_streams
    original = streams.append

    async def stop_after_first(**kwargs):
        await original(**kwargs)
        # The author presses Stop while the first words are showing.
        ws, ps = _ids(fake_container, job_id)
        await fake_container.jobs.request_cancel(workspace_id=ws, project_id=ps, job_id=job_id)

    monkeypatch.setattr(streams, "append", stop_after_first)
    assert [job.status.value for job in _run(fake_container)] == ["cancelled"]
    state = auth_client.get(f"{w['base']}/plan/{scene['id']}/studio").json()
    assert state["drafts"] == []
    assert streams.words(*_ids(fake_container, job_id), job_id, 1) == ""
    # What the provider wrote before the stop was charged: an estimate, recorded once.
    records = fake_container.usage.records
    assert [r.idempotency_key for r in records] == [f"{job_id}:1:1"]
    assert records[0].prompt_tokens > 0 and 0 < records[0].completion_tokens < len(DRAFT.split())
    job = _job(auth_client, w, job_id)
    assert job["status"] == "cancelled" and job["completion_tokens"] == records[0].completion_tokens


def test_a_stream_that_does_not_report_usage_records_an_estimate(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    fake_container.ai.stream_usage = False
    fake_container.ai.queue(DRAFT)
    job_id = _draft(auth_client, w, scene).json()["id"]
    assert [job.status.value for job in _run(fake_container)] == ["succeeded"]
    records = fake_container.usage.records
    assert len(records) == 1
    assert records[0].prompt_tokens > 0 and records[0].completion_tokens >= len(DRAFT.split())
    assert _job(auth_client, w, job_id)["completion_tokens"] == records[0].completion_tokens


def test_a_route_that_does_not_stream_answers_whole(auth_client, fake_container, monkeypatch):
    w, scene = _scene_world(auth_client, fake_container)
    fake_container.ai.streams = False  # as a computer through the connector answers
    pieces: list[str] = []
    original = fake_container.job_streams.append

    async def watch(**kwargs):
        pieces.append(kwargs["piece"])
        await original(**kwargs)

    monkeypatch.setattr(fake_container.job_streams, "append", watch)
    fake_container.ai.queue(
        TextResponse(text=DRAFT, model="m", usage=AIUsage(prompt_tokens=9, completion_tokens=7))
    )
    _draft(auth_client, w, scene)
    assert [job.status.value for job in _run(fake_container)] == ["succeeded"]
    assert fake_container.ai.calls[-1]["streamed"] is False
    assert pieces == []
    assert [(r.prompt_tokens, r.completion_tokens) for r in fake_container.usage.records] == [
        (9, 7)
    ]


def test_a_stream_that_fails_part_way_records_an_estimate_once(auth_client, fake_container):
    """M5: the connection drops (or the provider errs) after some words: the provider
    charged for them, so an estimate is recorded once, as for a stop."""
    from backend.ports import AIProviderError

    w, scene = _scene_world(auth_client, fake_container)
    fake_container.ai.stream_error = AIProviderError("model provider request failed")
    fake_container.ai.queue(DRAFT)
    job_id = _draft(auth_client, w, scene).json()["id"]
    assert [job.status.value for job in _run(fake_container)] == ["failed"]
    records = fake_container.usage.records
    assert [r.idempotency_key for r in records] == [f"{job_id}:1:1"]
    assert records[0].prompt_tokens > 0 and 0 < records[0].completion_tokens < len(DRAFT.split())
    assert _job(auth_client, w, job_id)["completion_tokens"] == records[0].completion_tokens
    assert auth_client.get(f"{w['base']}/plan/{scene['id']}/studio").json()["drafts"] == []


def test_a_failing_passing_copy_never_fails_a_paid_draft(auth_client, fake_container, monkeypatch):
    """LOW: the table the words are shown from cannot be written: the draft is still
    written, stored and paid for once."""
    w, scene = _scene_world(auth_client, fake_container)

    async def broken(**_kwargs):
        raise RuntimeError("unreachable")

    for action in ("begin", "append", "end"):
        monkeypatch.setattr(fake_container.job_streams, action, broken)
    fake_container.ai.queue(DRAFT)
    job_id = _draft(auth_client, w, scene).json()["id"]
    assert [job.status.value for job in _run(fake_container)] == ["succeeded"]
    state = auth_client.get(f"{w['base']}/plan/{scene['id']}/studio").json()
    assert state["drafts"][0]["content"].startswith("Tobin watched the water")
    assert [r.idempotency_key for r in fake_container.usage.records] == [f"{job_id}:1:1"]


def test_a_worker_that_lost_the_job_never_ends_the_newer_workers_words(auth_client, fake_container):
    """LOW: the worker on attempt 1 stops after the job was taken over by attempt 2: the
    words attempt 2 is writing stay, and attempt 1 cannot add to them."""
    from dataclasses import replace

    w, scene = _scene_world(auth_client, fake_container)
    fake_container.ai.queue(DRAFT)
    job_id = _draft(auth_client, w, scene).json()["id"]
    ws, ps = _ids(fake_container, job_id)
    streams = fake_container.job_streams
    key = {"workspace_id": ws, "project_id": ps, "job_id": job_id, "number": 1}
    asyncio.run(streams.begin(**key, attempt=2))
    asyncio.run(streams.append(**key, piece="New words ", attempt=2))
    asyncio.run(streams.append(**key, piece="old words ", attempt=1))
    asyncio.run(streams.begin(**key, attempt=1))  # never starts the newer one's again
    old = replace(fake_container.jobs.jobs[(ws, ps, job_id)], attempts=1)
    asyncio.run(fake_container.studio_service._end_streams(old))
    assert streams.words(ws, ps, job_id, 1) == "New words "
    newer = replace(old, attempts=2)
    asyncio.run(fake_container.studio_service._end_streams(newer))
    assert streams.words(ws, ps, job_id, 1) == ""


def test_the_sweep_deletes_the_rows_of_ended_work(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    fake_container.ai.queue(DRAFT)
    job_id = _draft(auth_client, w, scene).json()["id"]
    assert [job.status.value for job in _run(fake_container)] == ["succeeded"]
    ws, ps = _ids(fake_container, job_id)
    assert (ws, ps, job_id, 1) in fake_container.job_streams.rows
    assert asyncio.run(fake_container.job_streams.sweep()) == 1
    assert (ws, ps, job_id, 1) not in fake_container.job_streams.rows


# ------------------------------------------------------------------- open streams


def test_too_many_open_streams_are_refused_plainly(auth_client, fake_container, monkeypatch):
    """M3: a person (or one piece of work) may have only a few streams open at once; one
    more is refused with 429 in plain words, and a stream that ends lets its place go."""
    from backend.api.v1 import studio as studio_api

    w, scene = _scene_world(auth_client, fake_container)
    fake_container.ai.queue(DRAFT)
    job_id = _draft(auth_client, w, scene).json()["id"]
    ws, ps = _ids(fake_container, job_id)
    person = (ws, fake_container.jobs.jobs[(ws, ps, job_id)].actor_user_id)
    held = studio_api.open_streams
    now = asyncio.run(_now())
    taken = [
        held.take(("ws", f"someone_{i}"), (ws, ps, job_id), now)
        for i in range(studio_api.MAX_STREAMS_PER_JOB)
    ]
    try:
        refused = auth_client.get(f"{w['base']}/jobs/{job_id}/stream")
        assert refused.status_code == 429, refused.text
        assert refused.headers["content-type"].startswith("application/problem+json")
        assert "too many pages" in refused.json()["detail"]
    finally:
        for token in taken:
            held.let_go(token)
    taken = [
        held.take(person, ("ws", "pr", f"job_{i}"), now)
        for i in range(studio_api.MAX_STREAMS_PER_PERSON)
    ]
    try:
        assert auth_client.get(f"{w['base']}/jobs/{job_id}/stream").status_code == 429
    finally:
        for token in taken:
            held.let_go(token)
    # Places are let go: when a stream ends, and when one never started has lasted its time.
    assert [job.status.value for job in _run(fake_container)] == ["succeeded"]
    for _ in range(studio_api.MAX_STREAMS_PER_JOB + 1):
        assert auth_client.get(f"{w['base']}/jobs/{job_id}/stream").status_code == 200
    assert not any(place[1] == (ws, ps, job_id) for place in held._open.values())
    stale = held.take(person, (ws, ps, job_id), now)  # a stream that never started
    later = held.take(person, ("ws", "pr", "later"), now + studio_api.STREAM_SECONDS + 61)
    assert stale not in held._open
    held.let_go(later)


async def _now() -> float:
    return asyncio.get_running_loop().time()


def test_a_stream_ends_plainly_when_access_is_withdrawn(auth_client, fake_container, monkeypatch):
    """LOW: the half-minute check finds the work may no longer be read: an ``end`` event,
    not a broken connection."""
    from backend.domain.errors import ForbiddenError

    monkeypatch.setattr("backend.api.v1.studio.RECHECK_SECONDS", 0.0)
    monkeypatch.setattr("backend.api.v1.studio.POLL_SECONDS", 0.0)
    w, scene = _scene_world(auth_client, fake_container)
    fake_container.ai.queue(DRAFT)
    job_id = _draft(auth_client, w, scene).json()["id"]
    service = fake_container.studio_service
    original = service.stream
    calls = []

    async def withdrawn(*args, **kwargs):
        calls.append(kwargs.get("authorize", True))
        if len(calls) > 1:
            raise ForbiddenError("You no longer have access to this story.")
        return await original(*args, **kwargs)

    monkeypatch.setattr(service, "stream", withdrawn)
    events = _events(auth_client.get(f"{w['base']}/jobs/{job_id}/stream").text)
    assert [name for name, _ in events] == ["job", "end"]
    assert events[-1][1] == {"status": "queued", "reason": "unavailable"}


def test_a_stream_stops_when_its_session_has_ended(auth_client, fake_container, monkeypatch):
    """LOW: signed out while the stream is open: nothing more is sent on that session; the
    browser is asked to open it again (and is then asked to sign in)."""
    import time

    monkeypatch.setattr("backend.api.v1.studio.RECHECK_SECONDS", 0.0)
    monkeypatch.setattr("backend.api.v1.studio.POLL_SECONDS", 0.0)
    monkeypatch.setattr("backend.api.v1.studio.STREAM_SECONDS", 5.0)
    w, scene = _scene_world(auth_client, fake_container)
    fake_container.ai.queue(DRAFT)
    job_id = _draft(auth_client, w, scene).json()["id"]
    cookie = auth_client.cookies.get(fake_container.settings.SESSION_COOKIE_NAME)
    auth = fake_container.auth_service
    assert asyncio.run(auth.signed_in(cookie)) is True
    assert asyncio.run(auth.signed_in(None)) is False

    async def signed_out(_session):
        return False

    monkeypatch.setattr(auth, "signed_in", signed_out)
    started = time.perf_counter()
    events = _events(auth_client.get(f"{w['base']}/jobs/{job_id}/stream").text)
    assert [name for name, _ in events] == ["job", "again"]
    assert time.perf_counter() - started < 3.0
