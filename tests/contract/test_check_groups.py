"""A long scene's continuity check sent in groups (CON-013): passages with only the parts
of the story they bear on, one call each, estimated first, every call checked and kept,
and how it was sent shown with the run. Below the size, or when grouping cannot cover
every passage, it is sent whole as before."""

import asyncio

import pytest

from backend.services import scene_checks
from backend.services.scene_checks import CheckNote, CheckNotes
from tests.contract.test_studio import _save, _scene_world

pytestmark = pytest.mark.contract

PARAGRAPHS = [
    "Tobin waited at Lantern Harbor while the tide came in and the boats knocked together.",
    "The gulls went over twice, and the lamps along the quay were lit one after another.",
    "Mara came along the quay at last, her coat dark with rain, and did not look at him.",
    "Nobody else was out. The water slapped the steps and went back, and slapped again.",
    "Tobin counted the boats a third time. Mara stood at the far end of the harbour wall.",
    "The rain eased off and then came back harder, and the lamps hissed in their glass.",
]
PROSE = "\n\n".join(PARAGRAPHS * 3)


def _groups(container) -> list[dict]:
    return [
        call
        for call in container.ai.calls
        if "Passages from the scene:" in call["messages"][1]["content"]
    ]


def test_a_long_scene_is_checked_for_continuity_in_groups(auth_client, fake_container, monkeypatch):
    monkeypatch.setattr(scene_checks, "GROUP_ABOVE_TOKENS", 200)
    monkeypatch.setattr(scene_checks, "GROUP_BUDGET", 2_500)
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    estimate = auth_client.get(
        f"{w['base']}/plan/{scene['id']}/studio/check-estimate", params={"passes": "continuity"}
    )
    assert estimate.status_code == 200, estimate.text
    for _ in range(scene_checks.MAX_GROUP_CALLS):
        fake_container.ai.queue(
            CheckNotes(
                notes=[CheckNote(quote="Mara came along the quay", problem="She is at sea.")]
            )
        )
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews", json={"passes": ["continuity"]}
    )
    assert started.status_code == 202, started.text
    asyncio.run(fake_container.job_runner.run_pending())
    job = auth_client.get(f"{w['base']}/jobs/{started.json()['id']}").json()
    assert job["status"] == "succeeded", job

    calls = _groups(fake_container)
    assert len(calls) > 1, job["result"]
    # Groups about someone read only the passages that name them, with their records.
    assert any(call["messages"][1]["content"].count("Tobin waited") == 0 for call in calls)
    # Every call is recorded once, under its own key.
    keys = [
        record.idempotency_key
        for record in fake_container.usage.records
        if (record.idempotency_key or "").startswith(started.json()["id"])
    ]
    assert len(keys) == len(set(keys)) == len(calls)
    # What the estimate said is what the groups were estimated to use.
    (asked,) = estimate.json()["passes"]
    assert asked["prompt_tokens"] > 0

    run = next(
        r
        for r in auth_client.get(f"{w['base']}/plan/{scene['id']}/checks/runs").json()["items"]
        if r["kind"] == "assistant"
    )
    sent = run["coverage"]["continuity"]
    assert sent["sent"] == "groups" and sent["groups"] == len(calls) and sent["complete"]
    # The author was told the cost of the groups, not of one whole call.
    assert asked["prompt_tokens"] + asked["completion_tokens"] == sent["tokens"]
    assert sent["passages"] and all(item["checks"] for item in sent["passages"])
    assert any("group" in line for line in sent["lines"])
    # The same note from two groups is kept once.
    notes = [f for f in run["findings"] if f["source"] == "note"]
    assert len(notes) == len({(n["quoted"], n["message"]) for n in notes}) >= 1


def test_a_short_scene_is_sent_whole_as_before(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PARAGRAPHS[0], None)
    fake_container.ai.queue(CheckNotes(notes=[]))
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews", json={"passes": ["continuity"]}
    )
    assert started.status_code == 202, started.text
    asyncio.run(fake_container.job_runner.run_pending())
    assert _groups(fake_container) == []
    run = next(
        r
        for r in auth_client.get(f"{w['base']}/plan/{scene['id']}/checks/runs").json()["items"]
        if r["kind"] == "assistant"
    )
    assert run["coverage"] == {}


def test_grouping_that_would_take_too_many_calls_sends_it_whole_and_says_why(
    auth_client, fake_container, monkeypatch
):
    monkeypatch.setattr(scene_checks, "GROUP_ABOVE_TOKENS", 200)
    monkeypatch.setattr(scene_checks, "GROUP_BUDGET", 2_500)
    monkeypatch.setattr(scene_checks, "MAX_GROUP_CALLS", 1)
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    fake_container.ai.queue(CheckNotes(notes=[]))
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews", json={"passes": ["continuity"]}
    )
    asyncio.run(fake_container.job_runner.run_pending())
    job = auth_client.get(f"{w['base']}/jobs/{started.json()['id']}").json()
    assert job["status"] == "succeeded", job
    assert _groups(fake_container) == []
    sent = job["result"]["passes"]["continuity"]["bundles"]
    assert sent["sent"] == "whole" and "groups" in sent["why"]
