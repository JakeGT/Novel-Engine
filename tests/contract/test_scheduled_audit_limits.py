"""Audits on a schedule keep to what the author set, and an audit is read in its own line
of the story (CON-011, CON-018, ADR-020): the most for one run is a hard limit, what a
run read is kept only once it succeeded, a run claimed again after a stop reuses its
audit, and drift and correction plans read the what-if the audit read."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from backend.domain.identity import utcnow
from backend.domain.jobs import JobStatus
from backend.services.scene_checks import CheckNote
from tests.contract.test_studio import _save
from tests.contract.test_voice_and_audits import THIRD, _book, _note_answer, _prose, _run

pytestmark = pytest.mark.contract


def _audits(container) -> list:
    return sorted(
        (job for job in container.jobs.jobs.values() if job.kind == "story_audit"),
        key=lambda job: job.created_at,
    )


def _at(container, *, days: float) -> None:
    service = container.audit_schedules
    later = utcnow() + timedelta(days=days, minutes=1)
    service._clock = lambda: later  # noqa: SLF001 - the schedule's clock, moved on
    asyncio.run(service.tick())


def _schedule(client, w, beat, **body) -> dict:
    saved = client.put(
        f"{w['base']}/audit-schedules",
        json={"plan_id": beat["id"], "passes": [], "every_days": 7, "max_tokens": 0, **body},
    )
    assert saved.status_code == 200, saved.text
    return saved.json()


def _set_job(container, job) -> None:
    container.jobs.jobs[(job.workspace_id, job.project_id, job.id)] = job


def test_a_scheduled_run_that_grows_past_its_most_stops_and_keeps_what_it_finished(
    auth_client, fake_container
):
    w, beat, _scenes = _book(auth_client, fake_container, drifting=False)
    _schedule(auth_client, w, beat, passes=["clarity"], every_days=1, max_tokens=500_000)
    _at(fake_container, days=1)
    (job,) = _audits(fake_container)
    assert job.payload["schedule_id"] and job.payload["confirmed_tokens"] == 500_000
    # The story grew after the run started: planned again, a scene now needs more than
    # the most the author set. Nobody is there to ask, so nothing more is sent.
    _set_job(fake_container, replace(job, payload={**job.payload, "confirmed_tokens": 10}))
    for _ in range(4):
        fake_container.ai.queue(_note_answer(CheckNote(quote="the gulls", problem="Gulls.")))
    (done,) = _run(fake_container)
    assert done.status is JobStatus.FAILED and done.error_code == "refused"
    assert "the most for one run" in (done.error_message or "")
    assert fake_container.ai.calls == []


def test_a_run_that_failed_is_tried_again_though_nothing_changed(auth_client, fake_container):
    w, beat, _scenes = _book(auth_client, fake_container, drifting=False)
    _schedule(auth_client, w, beat)
    _at(fake_container, days=7)
    (first,) = _audits(fake_container)
    # The audit failed: what it would have read was never audited.
    _set_job(fake_container, replace(first, status=JobStatus.FAILED))
    _at(fake_container, days=14.1)
    audits = _audits(fake_container)
    assert len(audits) == 2 and audits[1].id != first.id
    (listed,) = auth_client.get(f"{w['base']}/audit-schedules").json()["items"]
    assert listed["last_job_id"] == audits[1].id

    # This one succeeds; with nothing written since, the next is skipped.
    _run(fake_container)
    _at(fake_container, days=21.2)
    assert len(_audits(fake_container)) == 2
    (listed,) = auth_client.get(f"{w['base']}/audit-schedules").json()["items"]
    assert listed["last_outcome"].startswith("Nothing written there had changed")


def test_a_run_still_under_way_is_not_started_twice(auth_client, fake_container):
    w, beat, _scenes = _book(auth_client, fake_container, drifting=False)
    _schedule(auth_client, w, beat)
    _at(fake_container, days=7)
    _at(fake_container, days=14.1)
    assert len(_audits(fake_container)) == 1


def test_a_run_claimed_again_after_a_stop_reuses_the_audit_it_started(auth_client, fake_container):
    w, beat, _scenes = _book(auth_client, fake_container, drifting=False)
    _schedule(auth_client, w, beat)
    service = fake_container.audit_schedules
    store = service._store  # noqa: SLF001 - the schedules as stored
    record = store.record_run

    async def stopped(**_kwargs) -> None:
        return None  # the server stopped before the run was recorded

    store.record_run = stopped
    _at(fake_container, days=7)
    store.record_run = record
    (first,) = _audits(fake_container)
    # Claimed again once the hold runs out: the same audit, not a second one.
    _at(fake_container, days=7.1)
    assert [job.id for job in _audits(fake_container)] == [first.id]
    (listed,) = auth_client.get(f"{w['base']}/audit-schedules").json()["items"]
    assert listed["last_job_id"] == first.id


def _what_if_scene(client, container, w, beat) -> tuple[str, dict]:
    """A what-if with a fifth scene that is only in it."""
    branch = client.post(f"{w['base']}/what-ifs", json={"name": "A fifth scene"}).json()["id"]
    header = {"X-Story-Branch": branch}
    head = client.get(f"{w['base']}/projection", headers=header).json()["head_revision"]
    made = client.post(
        f"{w['base']}/plan",
        json={
            "kind": "scene",
            "title": "Five",
            "parent_id": beat["id"],
            "expected_revision": int(head),
        },
        headers=header,
    )
    assert made.status_code == 201, made.text
    scene = made.json()
    assert _save(client, w, scene, _prose(THIRD), None, branch).status_code == 200
    return branch, scene


def test_drift_and_corrections_read_the_line_of_the_story_the_audit_read(
    auth_client, fake_container
):
    w, beat, _scenes = _book(auth_client, fake_container, drifting=False)
    branch, _five = _what_if_scene(auth_client, fake_container, w, beat)
    header = {"X-Story-Branch": branch}
    for index in range(5):
        fake_container.ai.queue(
            _note_answer(CheckNote(quote="the gulls", problem=f"Gulls again ({index})."))
        )
    started = auth_client.post(
        f"{w['base']}/audits",
        json={"plan_id": beat["id"], "passes": ["clarity"]},
        headers={**header, "X-Confirm-Tokens": "1000000"},
    )
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert done.status is JobStatus.SUCCEEDED, done.error_message
    job_id = started.json()["id"]

    # Asked from the main story, where the fifth scene is not.
    drift = auth_client.get(f"{w['base']}/audits/{job_id}/drift")
    assert drift.status_code == 200, drift.text
    assert [row["title"] for row in drift.json()["voice"]] == [
        "One",
        "Two",
        "Three",
        "Four",
        "Five",
    ]
    estimate = auth_client.get(f"{w['base']}/audits/{job_id}/corrections/estimate")
    assert estimate.status_code == 200, estimate.text
    assert estimate.json()["scenes"] == 5
