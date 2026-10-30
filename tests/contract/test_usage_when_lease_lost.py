"""A model call that finished is counted even when the job's lease is lost just after it
(another worker took the job over): the voice check of flagged passages (CON-018) and the
scene checks, one call per pass and in groups (CON-008, CON-013), record what the call
used inside the lease, before ``LeaseLost`` ends the run."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from backend.domain.jobs import LeaseLost
from backend.services import scene_checks
from backend.services.job_runner import JobContext
from backend.services.scene_checks import CheckNote, CheckNotes
from tests.contract.test_check_groups import PROSE as LONG_PROSE
from tests.contract.test_check_passes import PROSE, _answer, _check
from tests.contract.test_check_passes import _run as _run_passes
from tests.contract.test_studio import _save, _scene_world
from tests.contract.test_voice_and_audits import _book, _run

pytestmark = pytest.mark.contract


@asynccontextmanager
async def _lost_after(self):  # type: ignore[no-untyped-def]
    """The lease is lost while the call runs: the call finishes, then ``LeaseLost``."""
    yield
    raise LeaseLost(self.job.id)


def _keys(container, job_id: str) -> list[str]:
    return [
        record.idempotency_key
        for record in container.usage.records
        if (record.idempotency_key or "").startswith(job_id)
    ]


def test_the_voice_check_counts_its_call_when_the_lease_is_lost(
    auth_client, fake_container, monkeypatch
):
    w, _beat, scenes = _book(auth_client, fake_container)
    fake_container.ai.queue(CheckNotes(notes=[]))
    started = auth_client.post(f"{w['base']}/plan/{scenes[3]['id']}/voice/escalation")
    assert started.status_code == 202, started.text
    monkeypatch.setattr(JobContext, "holding", _lost_after)
    _run(fake_container)
    assert len(fake_container.ai.calls) == 1
    assert _keys(fake_container, started.json()["id"]) == [
        f"{started.json()['id']}:1:voice-passages"
    ]


def test_a_scene_pass_counts_its_call_when_the_lease_is_lost(
    auth_client, fake_container, monkeypatch
):
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    fake_container.ai.queue(_answer())
    started = _check(auth_client, w, scene, passes=["viewpoint"])
    assert started.status_code == 202, started.text
    monkeypatch.setattr(JobContext, "holding", _lost_after)
    _run_passes(fake_container)
    assert len(_keys(fake_container, started.json()["id"])) == 1


def test_a_grouped_check_counts_its_call_when_the_lease_is_lost(
    auth_client, fake_container, monkeypatch
):
    monkeypatch.setattr(scene_checks, "GROUP_ABOVE_TOKENS", 200)
    monkeypatch.setattr(scene_checks, "GROUP_BUDGET", 2_500)
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, LONG_PROSE, None)
    for _ in range(scene_checks.MAX_GROUP_CALLS):
        fake_container.ai.queue(
            CheckNotes(notes=[CheckNote(quote="Mara came along the quay", problem="At sea.")])
        )
    started = _check(auth_client, w, scene, passes=["continuity"])
    assert started.status_code == 202, started.text
    monkeypatch.setattr(JobContext, "holding", _lost_after)
    _run_passes(fake_container)
    # The first group's call finished, then the lease was lost: that call is counted.
    assert len(fake_container.ai.calls) >= 1
    assert len(_keys(fake_container, started.json()["id"])) == len(
        [
            c
            for c in fake_container.ai.calls
            if "Passages from the scene:" in c["messages"][1]["content"]
        ]
    )
