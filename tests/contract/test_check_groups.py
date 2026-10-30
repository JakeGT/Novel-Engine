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


def _parts(container) -> list[dict]:
    return [
        call
        for call in container.ai.calls
        if "it is checked in parts" in call["messages"][0]["content"]
    ]


def test_a_long_scene_is_checked_for_viewpoint_and_clarity_in_parts(
    auth_client, fake_container, monkeypatch
):
    """CON-013: the passes that read the same part of the story for every passage are
    sent in parts, each with all of it; every passage is in one part, and the run's
    coverage lists each passage with its part and its first words. The plan check is
    always sent whole, and says why."""
    monkeypatch.setattr(scene_checks, "GROUP_ABOVE_TOKENS", 200)
    # Room for a part of the scene with what each pass reads of the story.
    monkeypatch.setattr(scene_checks, "GROUP_BUDGET", 1_100)
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, "\n\n".join(PARAGRAPHS * 6), None)
    for _ in range(2 * scene_checks.MAX_GROUP_CALLS + 1):
        fake_container.ai.queue(
            CheckNotes(notes=[CheckNote(quote="Mara came along the quay", problem="Unclear.")])
        )
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews",
        json={"passes": ["viewpoint", "clarity", "plan"]},
    )
    assert started.status_code == 202, started.text
    asyncio.run(fake_container.job_runner.run_pending())
    job = auth_client.get(f"{w['base']}/jobs/{started.json()['id']}").json()
    assert job["status"] == "succeeded", job
    calls = _parts(fake_container)
    viewpoint = job["result"]["passes"]["viewpoint"]["bundles"]
    clarity = job["result"]["passes"]["clarity"]["bundles"]
    assert viewpoint["sent"] == clarity["sent"] == "groups", (viewpoint, clarity)
    assert len(calls) == viewpoint["groups"] + clarity["groups"] and clarity["groups"] > 1
    # Each part is sent with everything the pass reads, and different passages.
    mine = [c for c in calls if "secrets kept from them" in c["messages"][0]["content"]]
    assert all("For the author only" in c["messages"][1]["content"] for c in mine)
    assert len({c["messages"][1]["content"] for c in mine}) == len(mine) > 1
    assert job["result"]["passes"]["plan"]["bundles"] == {
        "sent": "whole",
        "why": "The plan check reads the whole scene to tell what it leaves out, so it is "
        "always sent whole.",
    }
    run = next(
        r
        for r in auth_client.get(f"{w['base']}/plan/{scene['id']}/checks/runs").json()["items"]
        if r["kind"] == "assistant"
    )
    passages = run["coverage"]["clarity"]["passages"]
    assert [p["part"] for p in passages] == sorted(p["part"] for p in passages)
    assert {p["part"] for p in passages} == {1, 2}
    assert all(p["checks"] == ["Clarity"] for p in passages)
    # The first words of each passage, read from the version checked (never stored).
    assert passages[0]["words"].startswith("Tobin waited at Lantern Harbor")
    assert all(p["words"] for p in passages)
    again = auth_client.get(f"{w['base']}/jobs/{started.json()['id']}").json()
    assert "words" not in again["result"]["passes"]["clarity"]["bundles"]["passages"][0]


def test_a_pass_whose_story_is_too_large_for_parts_is_sent_whole_and_says_why(
    auth_client, fake_container, monkeypatch
):
    monkeypatch.setattr(scene_checks, "GROUP_ABOVE_TOKENS", 200)
    monkeypatch.setattr(scene_checks, "GROUP_BUDGET", 500)
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    fake_container.ai.queue(CheckNotes(notes=[]))
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews", json={"passes": ["voice"]}
    )
    asyncio.run(fake_container.job_runner.run_pending())
    job = auth_client.get(f"{w['base']}/jobs/{started.json()['id']}").json()
    assert job["status"] == "succeeded", job
    assert _parts(fake_container) == []
    sent = job["result"]["passes"]["voice"]["bundles"]
    assert sent["sent"] == "whole" and "too large" in sent["why"]


def test_each_check_and_pass_records_how_long_it_took(auth_client, fake_container):
    """CON-001: a run keeps how long each check that needs no model took, and each stage
    of them, and how long each pass of a check with the assistant took, and its calls."""
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PARAGRAPHS[0], None)
    fake_container.ai.queue(CheckNotes(notes=[]))
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews", json={"passes": ["continuity"]}
    )
    assert started.status_code == 202, started.text
    asyncio.run(fake_container.job_runner.run_pending())
    runs = auth_client.get(f"{w['base']}/plan/{scene['id']}/checks/runs").json()["items"]
    run = next(r for r in runs if r["kind"] == "assistant")
    [part] = run["scenes"]
    assert part["checks"] and all(c["ms"] is not None and c["ms"] >= 0 for c in part["checks"])
    assert [s["stage"] for s in part["stages"]] == ["gather", "checks", "keep"]
    assert part["stages"][0]["words"] == "Reading what the checks need from the story"
    [timing] = run["timings"]
    assert timing["check_pass"] == "continuity" and timing["words"].startswith("Continuity")
    assert timing["calls"] == 1 and timing["ms"] >= 0 and not timing["earlier"]
