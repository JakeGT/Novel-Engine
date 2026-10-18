"""Checks kept as runs (CON-001): what each run read and ran, and what its findings come to
now (open, dismissed or fixed), with what it is out of date for. No model is needed to
read a run."""

import asyncio

import pytest

from backend.services.scene_checks import CheckNote, CheckNotes
from tests.contract.test_context import _head
from tests.contract.test_story_audit import _answer, _story
from tests.contract.test_studio import _save, _scene_world

pytestmark = pytest.mark.contract

PROSE = "Tobin waited at Lantern Harbor. The salt air was palpable. Mara did not come."


def _runs(client, w, scene) -> list[dict]:
    got = client.get(f"{w['base']}/plan/{scene['id']}/checks/runs")
    assert got.status_code == 200, got.text
    return got.json()["items"]


def _stock(run: dict) -> dict:
    return next(f for f in run["findings"] if f["code"] == "stock_phrase")


def test_saving_keeps_a_run_of_the_checks_and_reads_it_back_judged(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    assert _runs(auth_client, w, scene) == []
    first = _save(auth_client, w, scene, PROSE, None).json()

    (run,) = _runs(auth_client, w, scene)
    assert run["kind"] == "checks" and run["scope"] == "scene" and run["status"] == "done"
    assert run["passes"] == ["model_free"]
    assert run["story_revision"] == _head(auth_client, w)
    assert run["scenes"][0]["version_id"] == first["version_id"]
    assert run["stale"] is False and run["scenes"][0]["reasons"] == []
    stock = _stock(run)
    assert stock["status"] == "open" and stock["quoted"] == "palpable"

    # Asking again with nothing changed gives the same record, not a second one.
    again = auth_client.post(f"{w['base']}/plan/{scene['id']}/checks/runs")
    assert again.status_code == 201, again.text
    assert again.json()["id"] == run["id"]

    # Dismissed in the studio: dismissed in the run, with its reason.
    key = next(c for c in first["checks"] if c["code"] == "stock_phrase")["key"]
    auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/dismissals",
        json={"key": key, "reason": "intended"},
    )
    judged = _stock(_runs(auth_client, w, scene)[0])
    assert judged["status"] == "dismissed" and judged["dismissal"]["reason"] == "intended"

    # The words change: a second run, and in the first the finding is fixed by the words.
    second = _save(
        auth_client,
        w,
        scene,
        PROSE.replace("The salt air was palpable.", "Cold."),
        first["version_id"],
    ).json()
    newest, oldest = _runs(auth_client, w, scene)
    assert newest["scenes"][0]["version_id"] == second["version_id"]
    assert oldest["id"] == run["id"]
    assert oldest["stale"] is True
    assert oldest["scenes"][0]["reasons"] == ["The words have changed since."]
    fixed = _stock(oldest)
    assert fixed["status"] == "fixed" and fixed["fixed_by"] == "words"
    assert oldest["scenes"][0]["fixed"] >= 1
    assert not any(f["code"] == "stock_phrase" for f in newest["findings"])


def test_a_change_to_what_the_scene_names_marks_its_runs_out_of_date(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    # Something about another entry only: still current.
    other = auth_client.post(
        f"{w['base']}/entities",
        json={"type": "Location", "name": "Far Isle", "expected_revision": _head(auth_client, w)},
    )
    assert other.status_code == 201
    (run,) = _runs(auth_client, w, scene)
    assert run["stale"] is False
    # Something about Tobin, who sees the scene: out of date.
    renamed = auth_client.patch(
        f"{w['base']}/entities/{w['tobin']}",
        json={"attributes": {"role": "keeper"}, "expected_revision": _head(auth_client, w)},
    )
    assert renamed.status_code == 200, renamed.text
    (run,) = _runs(auth_client, w, scene)
    assert run["stale"] is True
    assert run["scenes"][0]["reasons"] == [
        "The story bible has changed since, in what this scene names."
    ]
    # The checks that need no model are worked out again, so what is open still holds.
    assert not any(f["stale"] for f in run["findings"] if f["source"] == "check")


def test_a_check_with_the_assistant_is_a_run_with_its_notes(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    saved = _save(auth_client, w, scene, PROSE, None).json()
    fake_container.ai.queue(
        CheckNotes(notes=[CheckNote(quote="Mara did not come", problem="The plan says she comes.")])
    )
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews", json={"passes": ["plan"]}
    )
    assert started.status_code == 202, started.text
    asyncio.run(fake_container.job_runner.run_pending())

    newest = _runs(auth_client, w, scene)[0]
    assert newest["kind"] == "assistant" and newest["job_id"] == started.json()["id"]
    assert newest["passes"] == ["model_free", "plan"]
    assert newest["status"] == "succeeded"
    note = next(f for f in newest["findings"] if f["source"] == "note")
    assert note["check_pass"] == "plan" and note["quoted"] == "Mara did not come"
    assert note["status"] == "open"
    by_job = auth_client.get(f"{w['base']}/checks/runs/by-job/{started.json()['id']}")
    assert by_job.status_code == 200 and by_job.json()["id"] == newest["id"]
    assert note["stale"] is False

    # What the scene names changes: the note may no longer hold, as only the assistant
    # could say whether it still does.
    changed = auth_client.patch(
        f"{w['base']}/entities/{w['mara']}",
        json={"attributes": {"role": "smuggler"}, "expected_revision": _head(auth_client, w)},
    )
    assert changed.status_code == 200, changed.text
    run = next(r for r in _runs(auth_client, w, scene) if r["kind"] == "assistant")
    note = next(f for f in run["findings"] if f["source"] == "note")
    assert note["status"] == "open" and note["stale"] is True

    # The quoted words go: the note is fixed by the words.
    _save(
        auth_client,
        w,
        scene,
        PROSE.replace("Mara did not come.", "Mara came."),
        saved["version_id"],
    )
    run = next(r for r in _runs(auth_client, w, scene) if r["kind"] == "assistant")
    note = next(f for f in run["findings"] if f["source"] == "note")
    assert note["status"] == "fixed" and note["fixed_by"] == "words"


def test_an_audit_is_one_run_over_its_scenes_listed_in_each_scenes_history(
    auth_client, fake_container
):
    w, beat, scenes = _story(auth_client, fake_container)
    fake_container.ai.queue(_answer(CheckNote(quote="Lantern Harbor", problem="Wrong harbour.")))
    fake_container.ai.queue(_answer())
    started = auth_client.post(
        f"{w['base']}/audits", json={"plan_id": beat["id"], "passes": ["continuity"]}
    )
    assert started.status_code == 202, started.text
    asyncio.run(fake_container.job_runner.run_pending())

    run = auth_client.get(f"{w['base']}/checks/runs/by-job/{started.json()['id']}")
    assert run.status_code == 200, run.text
    audit = run.json()
    assert audit["kind"] == "audit" and audit["scope"] == "part" and audit["title"] == "The wait"
    assert [s["title"] for s in audit["scenes"]] == ["At the harbour", "Walking home"]
    assert audit["stale"] is False
    assert any(f["source"] == "note" and f["plan_id"] == scenes[0]["id"] for f in audit["findings"])
    # It shows in each scene's history, judged for that scene only.
    listed = next(r for r in _runs(auth_client, w, scenes[1]) if r["kind"] == "audit")
    assert {f["plan_id"] for f in listed["findings"]} <= {scenes[1]["id"]}
    assert [s["plan_id"] for s in listed["scenes"]] == [scenes[1]["id"]]
    one = auth_client.get(f"{w['base']}/checks/runs/{audit['id']}")
    assert one.status_code == 200 and one.json()["id"] == audit["id"]
    assert auth_client.get(f"{w['base']}/checks/runs/nope").status_code == 404


def test_runs_belong_to_their_line_of_the_story(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    (run,) = _runs(auth_client, w, scene)
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Mara comes"}).json()["id"]
    inside = auth_client.get(
        f"{w['base']}/plan/{scene['id']}/checks/runs", headers={"X-Story-Branch": branch}
    )
    assert inside.status_code == 200 and inside.json()["items"] == []
    assert (
        auth_client.get(
            f"{w['base']}/checks/runs/{run['id']}", headers={"X-Story-Branch": branch}
        ).status_code
        == 404
    )
    # Nothing written: asking for a record says so plainly.
    empty = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/checks/runs", headers={"X-Story-Branch": branch}
    )
    assert empty.status_code in (201, 400)


def test_an_event_changed_by_id_alone_marks_the_runs_of_its_scenes_out_of_date(
    auth_client, fake_container
):
    w, scene = _scene_world(auth_client, fake_container)
    made = auth_client.post(
        f"{w['base']}/events",
        json={
            "kind": "meeting",
            "name": "Tobin meets the tide",
            "participants": [{"entity_id": w["tobin"], "role": "participant"}],
            "expected_revision": _head(auth_client, w),
        },
    )
    assert made.status_code == 201, made.text
    _save(auth_client, w, scene, PROSE, None)
    (run,) = _runs(auth_client, w, scene)
    assert run["stale"] is False
    # Renaming it names only the event, not who takes part in it.
    renamed = auth_client.patch(
        f"{w['base']}/events/{made.json()['id']}",
        json={"name": "Tobin waits for the tide", "expected_revision": _head(auth_client, w)},
    )
    assert renamed.status_code == 200, renamed.text
    (run,) = _runs(auth_client, w, scene)
    assert run["stale"] is True


def test_a_new_rule_marks_every_run_out_of_date(auth_client, fake_container):
    from tests.contract.test_rules import _add as _add_rule

    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    (run,) = _runs(auth_client, w, scene)
    assert run["stale"] is False
    assert _add_rule(auth_client, w).status_code == 201
    (run,) = _runs(auth_client, w, scene)
    assert run["stale"] is True


def test_another_scene_with_the_same_people_moving_in_time_marks_the_run_out_of_date(
    auth_client, fake_container
):
    from tests.contract.test_plan_checks import _add as _add_plan
    from tests.contract.test_scene_reading import _at

    w, scene = _scene_world(auth_client, fake_container)
    elsewhere = _add_plan(auth_client, w, "scene", "Far away")
    shared = _add_plan(
        auth_client,
        w,
        "scene",
        "Tobin again",
        refs=[{"entity_id": w["tobin"], "role": "participant"}],
    )
    _save(auth_client, w, scene, PROSE, None)
    # A scene no one here is in: still current.
    _at(auth_client, w, elsewhere, 3)
    (run,) = _runs(auth_client, w, scene)
    assert run["stale"] is False
    _at(auth_client, w, shared, 4)
    (run,) = _runs(auth_client, w, scene)
    assert run["stale"] is True
