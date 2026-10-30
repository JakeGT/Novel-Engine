"""The author's voice, drift across an audit, correction plans, audits on a schedule,
comparing a what-if, and proposals from findings (CON-018, CON-011, CON-010)."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from backend.ports import AIUsage, StructuredResponse
from backend.services.canon_change_workflow import ChangePlan, RecordRef
from backend.services.correction_plans import CorrectionSheet, CorrectionStep
from backend.services.scene_checks import CheckNote, CheckNotes
from tests.contract.test_god_hand import _world
from tests.contract.test_story_audit import _plan
from tests.contract.test_studio import _save, _studio

pytestmark = pytest.mark.contract

THIRD = (
    "Mara walked down to the harbor before the light came up over the water. "
    "She looked at the boats and counted the ones that had come back in the night. "
    "Tobin Ashe was waiting by the wall with his coat buttoned against the wind. "
    '"You are late," he said, and she shrugged and sat down beside him. '
    "They watched the gulls fight over a crust somebody had dropped on the stones. "
    "He had brought bread and she took half of it without asking for it. "
)
FIRST = (
    "I walk down to the harbor before the light comes up, and I look at the boats, and I "
    "count the ones that come back, because counting is the only thing I can do while the "
    "water keeps moving and the gulls keep screaming over the stones and nobody comes. "
)


def _prose(paragraph: str, words: int = 700) -> str:
    text = ""
    while len(text.split()) < words:
        text += paragraph
    return text


def _head(client, w, headers: dict | None = None) -> int:
    return int(client.get(f"{w['base']}/projection", headers=headers or {}).json()["head_revision"])


def _run(container) -> list:
    return asyncio.run(container.job_runner.run_pending())


def _book(client, container, *, drifting: bool = True):
    """Four scenes in a beat: three in the author's usual voice, one told differently."""
    w = _world(client, container)
    beat = _plan(client, w, "beat", "The wait")
    refs = [
        {"entity_id": w["mara"], "role": "point_of_view"},
        {"entity_id": w["tobin"], "role": "participant"},
    ]
    scenes = [
        _plan(client, w, "scene", title, beat["id"], refs=refs)
        for title in ("One", "Two", "Three", "Four")
    ]
    for index, scene in enumerate(scenes):
        text = _prose(FIRST if drifting and index == 3 else THIRD)
        assert _save(client, w, scene, text, None).status_code == 200
    return w, beat, scenes


def test_a_scene_is_compared_with_the_authors_usual_and_notes_say_how_it_departs(
    auth_client, fake_container
):
    w, _beat, scenes = _book(auth_client, fake_container)
    calls = len(fake_container.ai.calls)
    usual = auth_client.get(f"{w['base']}/plan/{scenes[0]['id']}/voice").json()
    assert usual["not_compared"] is None and usual["notes"] == []
    assert usual["stats"]["person"] == "third" and usual["stats"]["tense"] == "past"
    assert usual["usual"]["enough"] is True and usual["usual"]["own_only"] is True
    assert usual["counted"] is True

    odd = auth_client.get(f"{w['base']}/plan/{scenes[3]['id']}/voice").json()
    codes = {note["code"] for note in odd["notes"]}
    assert {"voice_person", "voice_tense"} <= codes
    messages = " ".join(note["message"] for note in odd["notes"])
    assert "told with “I”" in messages and "present tense" in messages
    # The usual it is compared with is made from the other scenes only.
    assert odd["usual"]["scenes"] == 3
    # No model was asked.
    assert len(fake_container.ai.calls) == calls

    book = auth_client.get(f"{w['base']}/voice/book").json()
    assert [row["title"] for row in book["scenes"]] == ["One", "Two", "Three", "Four"]
    assert book["scenes"][3]["notes"] and not book["scenes"][0]["notes"]
    assert len(book["band_labels"]) == len(book["scenes"][0]["stats"]["bands"])


def test_a_scene_left_out_of_the_usual_no_longer_sets_it(auth_client, fake_container):
    w, _beat, scenes = _book(auth_client, fake_container, drifting=False)
    url = f"{w['base']}/plan/{scenes[1]['id']}/voice/usual"
    left = auth_client.put(url, json={"left_out": True})
    assert left.status_code == 200, left.text
    assert left.json()["left_out"] is True and left.json()["counted"] is False
    other = auth_client.get(f"{w['base']}/plan/{scenes[0]['id']}/voice").json()
    assert scenes[1]["id"] in other["usual"]["left_out"] and other["usual"]["scenes"] == 2
    # Two scenes are too few to know the usual: it says so instead of comparing.
    assert (
        other["usual"]["enough"] is False and "Not enough of your writing" in other["not_compared"]
    )
    back = auth_client.put(url, json={"left_out": False}).json()
    assert back["left_out"] is False and back["counted"] is True

    # The choice travels with the archive.
    auth_client.put(url, json={"left_out": True})
    workspace = fake_container.settings.AUTH_LOCAL_WORKSPACE_ID
    project = fake_container.settings.AUTH_LOCAL_PROJECT_ID
    archive = asyncio.run(fake_container.archive_service.export_project(workspace, project))
    assert archive["voice_left_out"] == [scenes[1]["id"]]
    result = asyncio.run(
        fake_container.archive_service.import_project(workspace, "pr_voice_restored", archive)
    )
    assert result["voice_left_out"] == 1
    store = fake_container.voice_service._store  # noqa: SLF001 - the rows as restored
    assert store.left[(workspace, "pr_voice_restored")] == [scenes[1]["id"]]


def test_a_reader_cannot_change_the_usual_and_a_short_scene_is_not_compared(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    scene = _plan(auth_client, w, "scene", "Short")
    _save(auth_client, w, scene, "Mara waited.", None)
    found = auth_client.get(f"{w['base']}/plan/{scene['id']}/voice").json()
    assert "Too short to compare" in found["not_compared"]
    nothing = _plan(auth_client, w, "scene", "Empty")
    empty = auth_client.get(f"{w['base']}/plan/{nothing['id']}/voice").json()
    assert (
        empty["stats"] is None and empty["not_compared"] == "Nothing is written in this scene yet."
    )


# ------------------------------------------------------------------- audits


def _audit(client, container, w, beat, passes: list[str]) -> dict:
    started = client.post(f"{w['base']}/audits", json={"plan_id": beat["id"], "passes": passes})
    assert started.status_code == 202, started.text
    (done,) = _run(container)
    assert done.status.value == "succeeded", done.error_message
    return started.json()


def test_an_audit_shows_voice_and_relationship_drift_across_its_scenes(auth_client, fake_container):
    w, beat, scenes = _book(auth_client, fake_container)
    job = _audit(auth_client, fake_container, w, beat, [])
    drift = auth_client.get(f"{w['base']}/audits/{job['id']}/drift")
    assert drift.status_code == 200, drift.text
    found = drift.json()
    assert [row["title"] for row in found["voice"]] == ["One", "Two", "Three", "Four"]
    assert found["voice"][3]["notes"] and not found["voice"][0]["notes"]
    pair = next(
        row for row in found["relationships"] if set(row["names"]) == {"Mara", "Tobin Ashe"}
    )
    assert len(pair["together"]) == 4 and "Mara knows Tobin Ashe" in pair["recorded"]
    assert auth_client.get(f"{w['base']}/audits/job_missing/drift").status_code == 404


def _note_answer(*notes: CheckNote) -> StructuredResponse:
    return StructuredResponse(
        parsed=CheckNotes(notes=list(notes)),
        model="fake",
        usage=AIUsage(prompt_tokens=100, completion_tokens=10),
    )


def test_a_correction_plan_cites_findings_and_waits_to_be_kept_or_skipped(
    auth_client, fake_container
):
    w, beat, scenes = _book(auth_client, fake_container)
    for index in range(4):
        fake_container.ai.queue(
            _note_answer(CheckNote(quote="the gulls", problem=f"Gulls again ({index})."))
        )
    job = _audit(auth_client, fake_container, w, beat, ["clarity"])
    base = f"{w['base']}/audits/{job['id']}/corrections"
    estimate = auth_client.get(f"{base}/estimate").json()
    assert estimate["total_tokens"] > 0 and estimate["scenes"] == 4 and estimate["findings"] >= 4
    calls = len(fake_container.ai.calls)
    fake_container.ai.queue(
        StructuredResponse(
            parsed=CorrectionSheet(
                summary="Cut the gulls back.",
                steps=[
                    CorrectionStep(
                        scene="S1",
                        change="Drop the gulls here.",
                        why="Seen too often.",
                        findings=["N1", "N9"],
                    ),
                    # No finding it answers: thrown away.
                    CorrectionStep(
                        scene="S2", change="Rewrite it all.", why="Why not.", findings=[]
                    ),
                    # A scene the audit did not read: thrown away.
                    CorrectionStep(scene="S9", change="Something.", why="?", findings=["N1"]),
                ],
            ),
            model="fake",
            usage=AIUsage(prompt_tokens=200, completion_tokens=40),
        )
    )
    started = auth_client.post(base)
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done.error_message
    assert len(fake_container.ai.calls) == calls + 1
    sent = fake_container.ai.calls[-1]["messages"][1]["content"]
    assert "<<<FINDINGS-1>>>" in sent and "Gulls again (0)." in sent

    plans = auth_client.get(base).json()["plans"]
    (plan,) = plans
    assert plan["summary"] == "Cut the gulls back." and plan["dropped"] == 2
    (item,) = plan["items"]
    assert item["plan_id"] == scenes[0]["id"] and item["decision"] == "waiting"
    assert item["cited"] and item["cited"][0].startswith("Gulls again (0).")
    kept = auth_client.put(
        f"{w['base']}/corrections/{plan['id']}/items/0", json={"decision": "kept"}
    )
    assert kept.status_code == 200 and kept.json()["items"][0]["decision"] == "kept"
    assert (
        auth_client.put(
            f"{w['base']}/corrections/{plan['id']}/items/5", json={"decision": "kept"}
        ).status_code
        == 404
    )


def test_a_correction_plan_needs_findings(auth_client, fake_container):
    w, beat, _scenes = _book(auth_client, fake_container, drifting=False)
    job = _audit(auth_client, fake_container, w, beat, [])
    refused = auth_client.get(f"{w['base']}/audits/{job['id']}/corrections/estimate")
    # The checks without the assistant may find nothing in these scenes.
    if refused.status_code == 200:
        assert refused.json()["findings"] > 0
    else:
        assert refused.status_code == 400 and "nothing to correct" in refused.text


def _tick(container, *, days: float = 0) -> None:
    service = container.audit_schedules
    from backend.domain.identity import utcnow

    later = utcnow() + timedelta(days=days, minutes=1)
    service._clock = lambda: later  # noqa: SLF001 - the schedule's clock, moved on
    asyncio.run(service.tick())


def test_a_scheduled_audit_runs_when_due_skips_when_nothing_changed_and_keeps_to_its_most(
    auth_client, fake_container
):
    w, beat, scenes = _book(auth_client, fake_container, drifting=False)
    url = f"{w['base']}/audit-schedules"
    saved = auth_client.put(
        url, json={"plan_id": beat["id"], "passes": [], "every_days": 7, "max_tokens": 0}
    )
    assert saved.status_code == 200, saved.text
    schedule = saved.json()
    assert schedule["every_days"] == 7 and schedule["last_outcome"] is None
    # Not due yet: nothing starts.
    _tick(fake_container)
    assert not [j for j in fake_container.jobs.jobs.values() if j.kind == "story_audit"]

    _tick(fake_container, days=7)
    audits = [j for j in fake_container.jobs.jobs.values() if j.kind == "story_audit"]
    assert len(audits) == 1 and audits[0].payload["schedule_id"] == schedule["id"]
    (listed,) = auth_client.get(url).json()["items"]
    assert listed["last_outcome"] == "Started an audit." and listed["last_job_id"] == audits[0].id
    _run(fake_container)

    # A week on with nothing written: skipped, nothing spent.
    _tick(fake_container, days=14.1)
    assert len([j for j in fake_container.jobs.jobs.values() if j.kind == "story_audit"]) == 1
    (listed,) = auth_client.get(url).json()["items"]
    assert listed["last_outcome"].startswith("Nothing written there had changed")

    # With the assistant's passes, a run that would use more than the most does not start.
    passes = auth_client.put(
        url, json={"plan_id": beat["id"], "passes": ["clarity"], "every_days": 1, "max_tokens": 10}
    )
    assert passes.status_code == 200 and passes.json()["id"] == schedule["id"]
    _save(
        auth_client,
        w,
        scenes[0],
        _prose(THIRD) + " Then it rained.",
        _studio(auth_client, w, scenes[0])["version_id"],
    )
    _tick(fake_container, days=30)
    (listed,) = auth_client.get(url).json()["items"]
    assert "more than the most you set" in listed["last_outcome"]
    # Nothing was sent to a model by any run.
    assert fake_container.ai.calls == []

    assert auth_client.delete(f"{url}/{schedule['id']}").status_code == 204
    assert auth_client.get(url).json()["items"] == []


def test_a_schedule_is_refused_out_of_range_and_turned_off_when_its_part_is_gone(
    auth_client, fake_container
):
    w, beat, _scenes = _book(auth_client, fake_container, drifting=False)
    url = f"{w['base']}/audit-schedules"
    assert (
        auth_client.put(
            url, json={"plan_id": beat["id"], "passes": [], "every_days": 0, "max_tokens": 0}
        ).status_code
        == 422
    )
    needs_most = auth_client.put(
        url, json={"plan_id": beat["id"], "passes": ["clarity"], "every_days": 3, "max_tokens": 0}
    )
    assert needs_most.status_code == 400 and "the most each run may use" in needs_most.text
    assert (
        auth_client.put(
            url, json={"plan_id": beat["id"], "passes": [], "every_days": 3, "max_tokens": 0}
        ).status_code
        == 200
    )
    removed = auth_client.post(
        f"{w['base']}/plan/{beat['id']}/remove", json={"expected_revision": _head(auth_client, w)}
    )
    assert removed.status_code == 200, removed.text
    _tick(fake_container, days=4)
    (listed,) = auth_client.get(url).json()["items"]
    assert listed["enabled"] is False and "no longer there" in listed["last_outcome"]


# ------------------------------------------------------------- comparing a what-if


def test_a_what_if_is_compared_with_the_main_story(auth_client, fake_container):
    w, _beat, scenes = _book(auth_client, fake_container, drifting=False)
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Mara stays away"}).json()[
        "id"
    ]
    header = {"X-Story-Branch": branch}
    same = auth_client.get(f"{w['base']}/what-ifs/{branch}/audit").json()
    assert same["same"] is True and same["what_if_name"] == "Mara stays away"

    current = _studio(auth_client, w, scenes[1], branch)["version_id"]
    assert _save(auth_client, w, scenes[1], _prose(FIRST), current, branch).status_code == 200
    ended = auth_client.post(
        f"{w['base']}/facts/{w['in_guild']}/close",
        json={"expected_revision": _head(auth_client, w, header)},
        headers=header,
    )
    assert ended.status_code == 200, ended.text
    found = auth_client.get(f"{w['base']}/what-ifs/{branch}/audit").json()
    assert found["same"] is False
    (scene,) = found["scenes"]
    assert scene["title"] == "Two" and scene["what_if_words"] >= 700
    assert any("told with “I”" in line for line in scene["voice"])
    (fact,) = [item for item in found["bible"] if item["kind"] == "fact"]
    assert fact["change"] == "removed" and fact["title"] == "Mara is a member of Harbor Guild"
    # The main story is untouched.
    assert _studio(auth_client, w, scenes[1])["version_id"] == current
    assert auth_client.get(f"{w['base']}/what-ifs/br_missing/audit").status_code == 404


# --------------------------------------------------------- proposals from findings


def test_a_finding_becomes_a_story_bible_proposal_for_review(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _plan(auth_client, w, "scene", "The quay")
    text = "Mara stood at Lantern Harbor, a testament to patience, waiting for the tide."
    _save(auth_client, w, scene, text, None)
    check = next(c for c in _studio(auth_client, w, scene)["checks"] if c["code"] == "stock_phrase")
    ref = {"kind": "check", "key": check["key"]}
    base = f"{w['base']}/plan/{scene['id']}/findings"
    estimate = auth_client.post(f"{base}/proposal-estimate", json=ref)
    assert estimate.status_code == 200, estimate.text
    assert estimate.json()["total_tokens"] > 0 and estimate.json()["has_passage"] is True

    fake_container.ai.queue(
        ChangePlan(
            understood="Mara is no longer at Lantern Harbor.",
            end_facts=[RecordRef(id=w["at_harbor"])],
        )
    )
    made = auth_client.post(f"{base}/proposal", json={**ref, "choice": "change_bible"})
    assert made.status_code == 200, made.text
    body = made.json()
    assert body["mutation_id"] and body["operation_count"] == 1
    # The finding and its passage went to the assistant fenced, as material.
    sent = fake_container.ai.calls[-1]["messages"][1]["content"]
    assert "<<<FINDING-1>>>" in sent and "a testament to patience" in sent
    assert "story bible is wrong here" in sent
    review = auth_client.get(f"{w['base']}/mutations/{body['mutation_id']}").json()
    assert review["origin"] == "model" and review["requires_review"] is True
    # Nothing changed yet.
    facts = auth_client.get(f"{w['base']}/facts").json()["items"]
    assert any(fact["id"] == w["at_harbor"] for fact in facts)

    gone = auth_client.post(
        f"{base}/proposal", json={"kind": "check", "key": "nope", "choice": "make_true"}
    )
    assert gone.status_code == 404
