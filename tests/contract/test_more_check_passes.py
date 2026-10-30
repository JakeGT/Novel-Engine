"""The optional passes of a check (CON-008, batch 10): why characters act, bodies and
cause, and the story's own rules. Each runs only when named, one call each with its own
prompt and part of the story; a long scene sends the physical and world-rule passes in
groups by what passages name and the motivation pass in parts, as continuity and the
viewpoint pass are sent."""

import asyncio

import pytest

from backend.services import scene_checks
from backend.services.scene_checks import CheckNote, CheckNotes
from tests.contract.test_check_groups import PARAGRAPHS
from tests.contract.test_check_groups import PROSE as LONG
from tests.contract.test_studio import PROSE, _save, _scene_world, _studio

pytestmark = pytest.mark.contract

OPTIONAL = ["motivation", "physical", "world"]


def _run(container) -> list:
    return asyncio.run(container.job_runner.run_pending())


def test_the_optional_passes_run_only_when_named_each_its_own_call(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    url = f"{w['base']}/plan/{scene['id']}/studio/check-estimate"
    costs = auth_client.get(url, params={"passes": OPTIONAL}).json()["passes"]
    assert [item["check_pass"] for item in costs] == OPTIONAL
    assert all(item["total_tokens"] > 0 and item["description"] for item in costs)
    assert [item["label"] for item in costs] == [
        "Why characters act",
        "Bodies, objects and cause",
        "Your story’s own rules",
    ]
    for quote in ("Tobin waited", "Lantern Harbor", "the tide came in"):
        fake_container.ai.queue(CheckNotes(notes=[CheckNote(quote=quote, problem="A slip.")]))
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews", json={"passes": OPTIONAL}
    )
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done.error_message
    assert done.payload["passes"] == OPTIONAL and done.result["findings"] == 3
    systems = [call["messages"][0]["content"] for call in fake_container.ai.calls]
    assert "why characters act" in systems[0]
    assert "bodies, objects, places and cause" in systems[1]
    assert "the story's own rules" in systems[2]
    # No optional pass is given the secret the viewpoint character does not know.
    assert all(
        "Harbor Guild" not in call["messages"][1]["content"] for call in fake_container.ai.calls
    )
    notes = {note["check_pass"] for note in _studio(auth_client, w, scene)["assistant"]}
    assert notes == set(OPTIONAL)


def test_a_check_with_no_passes_named_never_runs_an_optional_one(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    for _ in range(5):
        fake_container.ai.queue(CheckNotes(notes=[]))
    started = auth_client.post(f"{w['base']}/plan/{scene['id']}/studio/reviews", json={})
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert not set(done.payload["passes"]) & set(OPTIONAL)
    systems = " ".join(call["messages"][0]["content"] for call in fake_container.ai.calls)
    assert "why characters act" not in systems and "the story's own rules" not in systems


def _long_check(auth_client, fake_container, monkeypatch, prose, budget, passes):
    monkeypatch.setattr(scene_checks, "GROUP_ABOVE_TOKENS", 200)
    monkeypatch.setattr(scene_checks, "GROUP_BUDGET", budget)
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, prose, None)
    for _ in range(scene_checks.MAX_GROUP_CALLS + 1):
        fake_container.ai.queue(CheckNotes(notes=[]))
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews", json={"passes": passes}
    )
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done.error_message
    systems = [call["messages"][0]["content"] for call in fake_container.ai.calls]
    return done.result["passes"], systems


def test_a_long_scene_sends_the_physical_pass_in_groups_by_what_passages_name(
    auth_client, fake_container, monkeypatch
):
    passes, systems = _long_check(
        auth_client, fake_container, monkeypatch, LONG, 2_500, ["physical"]
    )
    sent = passes["physical"]
    assert sent["bundles"]["sent"] == "groups" and sent["calls"] == sent["bundles"]["groups"] > 1
    assert all("checked in groups" in text and "bodies, objects" in text for text in systems)


def test_a_long_scene_sends_the_motivation_pass_in_parts(auth_client, fake_container, monkeypatch):
    passes, systems = _long_check(
        auth_client,
        fake_container,
        monkeypatch,
        "\n\n".join(PARAGRAPHS * 6),
        1_100,
        ["motivation"],
    )
    sent = passes["motivation"]["bundles"]
    assert sent["sent"] == "groups" and sent["policy"] == scene_checks.PARTS_POLICY
    assert all("checked in parts" in text and "why characters act" in text for text in systems)
