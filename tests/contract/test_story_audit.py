"""Deep audits of a part of the story (CON-011).

The chosen passes run over every written scene in a part of the plan, in reading order,
as one cancellable job with an estimate first; notes are stored against the exact
versions read, and the results come back scene by scene with the checks that need no
model.
"""

import asyncio

import pytest

from backend.ports import AIUsage, StructuredResponse
from backend.services.scene_checks import CheckNote, CheckNotes
from tests.contract.test_context import _head, _setup
from tests.contract.test_god_hand import _world
from tests.contract.test_studio import _ids, _save, _scope_of, _studio

pytestmark = pytest.mark.contract

FIRST = "Tobin waited at Lantern Harbor while the tide came in."
SECOND = "Mara did not come. Tobin walked home past the guild hall, a testament to patience."


def _run(container) -> list:
    return asyncio.run(container.job_runner.run_pending())


def _plan(client, w, kind: str, title: str, parent: str | None = None, **extra) -> dict:
    made = client.post(
        f"{w['base']}/plan",
        json={
            "kind": kind,
            "title": title,
            "parent_id": parent,
            "expected_revision": _head(client, w),
            **extra,
        },
    )
    assert made.status_code == 201, made.text
    return made.json()


def _story(client, container) -> tuple[dict, dict, list[dict]]:
    """A beat with three scenes under it: two written, one not."""
    w = _world(client, container)
    _setup(client, w)  # a scene outside the beat, never written
    beat = _plan(client, w, "beat", "The wait")
    refs = [{"entity_id": w["tobin"], "role": "point_of_view"}]
    scenes = [
        _plan(client, w, "scene", title, beat["id"], refs=refs)
        for title in ("At the harbour", "Walking home", "Not yet written")
    ]
    _save(client, w, scenes[0], FIRST, None)
    _save(client, w, scenes[1], SECOND, None)
    return w, beat, scenes


def _answer(*notes: CheckNote, tokens: int = 100) -> StructuredResponse:
    return StructuredResponse(
        parsed=CheckNotes(notes=list(notes)),
        model="fake",
        usage=AIUsage(prompt_tokens=tokens, completion_tokens=10),
    )


def test_the_estimate_lists_the_part_scene_by_scene_and_asks_no_model(auth_client, fake_container):
    w, beat, scenes = _story(auth_client, fake_container)
    url = f"{w['base']}/audits/estimate"
    every = auth_client.get(url, params={"plan_id": beat["id"]})
    assert every.status_code == 200, every.text
    found = every.json()
    assert found["title"] == "The wait"
    assert [scene["title"] for scene in found["scenes"]] == [s["title"] for s in scenes]
    assert [scene["written"] for scene in found["scenes"]] == [True, True, False]
    assert found["written"] == 2 and found["scenes"][2]["total_tokens"] == 0
    assert [item["check_pass"] for item in found["passes"]] == [
        "viewpoint",
        "continuity",
        "plan",
        "voice",
        "clarity",
    ]
    assert found["total_tokens"] == sum(item["total_tokens"] for item in found["passes"])
    assert found["total_tokens"] == sum(scene["total_tokens"] for scene in found["scenes"])

    one = auth_client.get(url, params={"plan_id": beat["id"], "passes": ["clarity"]}).json()
    assert [item["check_pass"] for item in one["passes"]] == ["clarity"]
    assert one["total_tokens"] < found["total_tokens"]
    # The whole plan has the scene outside the beat too.
    whole = auth_client.get(url).json()
    assert whole["title"] == "The whole story" and len(whole["scenes"]) == 4
    assert fake_container.ai.calls == [] and fake_container.jobs.jobs == {}


def test_an_audit_checks_each_written_scene_and_reports_by_scene(auth_client, fake_container):
    w, beat, scenes = _story(auth_client, fake_container)
    first_version = _studio(auth_client, w, scenes[0])["version_id"]
    # Two passes over two written scenes: four calls, in reading order.
    fake_container.ai.queue(_answer(CheckNote(quote="Lantern Harbor", problem="Wrong harbour.")))
    fake_container.ai.queue(_answer())
    fake_container.ai.queue(_answer(CheckNote(quote="guild hall", problem="No guild hall here.")))
    fake_container.ai.queue(
        _answer(CheckNote(quote="not in the scene at all", problem="Thrown away."))
    )
    started = auth_client.post(
        f"{w['base']}/audits", json={"plan_id": beat["id"], "passes": ["continuity", "clarity"]}
    )
    assert started.status_code == 202, started.text
    job = started.json()
    assert job["kind"] == "story_audit" and job["status"] == "queued"
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done.error_message
    assert len(fake_container.ai.calls) == 4
    assert [entry["status"] for entry in done.result["scenes"]] == ["checked", "checked"]
    assert done.result["scenes"][1]["dropped"] == 1

    report = auth_client.get(f"{w['base']}/audits/{job['id']}")
    assert report.status_code == 200, report.text
    found = report.json()
    assert found["title"] == "The wait" and found["passes"] == ["continuity", "clarity"]
    assert [scene["title"] for scene in found["scenes"]] == ["At the harbour", "Walking home"]
    assert [item["title"] for item in found["unwritten"]] == ["Not yet written"]
    first, second = found["scenes"]
    assert first["status"] == "checked" and first["current"] is True
    assert first["version_id"] == first_version and first["version_number"] == 1
    (note,) = first["notes"]
    assert note["check_pass"] == "continuity" and note["quoted"] == "Lantern Harbor"
    assert second["notes"][0]["quoted"] == "guild hall"
    # The checks that need no model come with it, quoting the words they are about.
    assert isinstance(first["checks"], list) and isinstance(first["story_checks"], list)
    assert all(check["quoted"] or check["start"] is None for check in first["checks"])
    stock = next(check for check in second["checks"] if check["code"] == "stock_phrase")
    assert "testament to" in stock["quoted"].lower()

    # The notes also sit beside that version in the studio, with the pass that found them.
    studio_notes = _studio(auth_client, w, scenes[0])["assistant"]
    assert [item["check_pass"] for item in studio_notes] == ["continuity"]

    # Edited since: the report still shows the version read, and says it is not the newest.
    _save(auth_client, w, scenes[0], FIRST + " Gulls cried.", first_version)
    later = auth_client.get(f"{w['base']}/audits/{job['id']}").json()
    assert later["scenes"][0]["current"] is False
    assert later["scenes"][0]["notes"][0]["quoted"] == "Lantern Harbor"

    listed = auth_client.get(f"{w['base']}/audits", params={"plan_id": beat["id"]}).json()
    assert [item["id"] for item in listed["items"]] == [job["id"]]


def test_an_audit_asks_first_above_the_threshold(auth_client, fake_container):
    w, beat, _ = _story(auth_client, fake_container)
    auth_client.put(f"{w['base']}/assistant-settings", json={"confirm_above_tokens": 1_000})
    asked = auth_client.post(f"{w['base']}/audits", json={"plan_id": beat["id"]})
    assert asked.status_code == 409 and asked.json()["code"] == "needs_confirmation"
    assert asked.json()["detail"].startswith("Auditing this part of the story")
    estimate = auth_client.get(
        f"{w['base']}/audits/estimate", params={"plan_id": beat["id"]}
    ).json()
    tokens = asked.json()["field_errors"]["estimated_tokens"]
    assert int(tokens) == estimate["total_tokens"]
    assert fake_container.jobs.jobs == {}
    sent = auth_client.post(
        f"{w['base']}/audits",
        json={"plan_id": beat["id"]},
        headers={"X-Confirm-Tokens": tokens, "Idempotency-Key": "audit-1"},
    )
    assert sent.status_code == 202, sent.text
    again = auth_client.post(
        f"{w['base']}/audits",
        json={"plan_id": beat["id"]},
        headers={"X-Confirm-Tokens": tokens, "Idempotency-Key": "audit-1"},
    )
    assert again.json()["id"] == sent.json()["id"]


def test_a_stopped_audit_keeps_the_scenes_it_finished(auth_client, fake_container, monkeypatch):
    w, beat, _ = _story(auth_client, fake_container)
    fake_container.ai.queue(_answer(CheckNote(quote="Lantern Harbor", problem="Wrong harbour.")))
    fake_container.ai.queue(_answer(CheckNote(quote="guild hall", problem="Never kept.")))
    started = auth_client.post(
        f"{w['base']}/audits", json={"plan_id": beat["id"], "passes": ["continuity"]}
    ).json()
    ai = fake_container.ai
    original = ai.generate_structured
    calls = {"n": 0}

    async def stop_during_the_second_scene(**kwargs):
        calls["n"] += 1
        answer = await original(**kwargs)
        if calls["n"] == 2:
            await fake_container.studio_service.cancel(
                _scope_of(fake_container), *_ids(fake_container), started["id"]
            )
        return answer

    monkeypatch.setattr(ai, "generate_structured", stop_during_the_second_scene)
    (done,) = _run(fake_container)
    assert done.status.value == "cancelled"
    found = auth_client.get(f"{w['base']}/audits/{started['id']}").json()
    assert found["job"]["status"] == "cancelled"
    assert [scene["status"] for scene in found["scenes"]] == ["checked", "not_checked"]
    assert [len(scene["notes"]) for scene in found["scenes"]] == [1, 0]


def test_hosted_without_a_key_only_the_free_checks_can_run(hosted_client, hosted_container):
    """With no AI key, an audit with the assistant fails saying so; one without it works."""
    from tests.contract.test_studio import _hosted_scene

    base, scene, headers, _, _ = _hosted_scene(hosted_client, hosted_container)
    saved = hosted_client.put(
        f"{base}/plan/{scene['id']}/studio/text",
        json={"content": FIRST, "base_version_id": None},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    with_model = hosted_client.post(
        f"{base}/audits", json={"plan_id": scene["id"], "passes": ["clarity"]}, headers=headers
    )
    assert with_model.status_code == 202, with_model.text
    without = hosted_client.post(
        f"{base}/audits", json={"plan_id": scene["id"], "passes": []}, headers=headers
    )
    assert without.status_code == 202, without.text
    first, second = _run(hosted_container)
    assert first.status.value == "failed" and first.error_code == "no_provider"
    assert second.status.value == "succeeded"
    assert hosted_container.ai.calls == []
    found = hosted_client.get(f"{base}/audits/{without.json()['id']}", headers=headers).json()
    assert [item["status"] for item in found["scenes"]] == ["checked"]


def test_with_no_passes_only_the_free_checks_run(auth_client, fake_container):
    w, beat, scenes = _story(auth_client, fake_container)
    started = auth_client.post(f"{w['base']}/audits", json={"plan_id": beat["id"], "passes": []})
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done.error_message
    assert fake_container.ai.calls == [] and fake_container.usage.records == []
    found = auth_client.get(f"{w['base']}/audits/{started.json()['id']}").json()
    assert found["passes"] == []
    assert [scene["status"] for scene in found["scenes"]] == ["checked", "checked"]
    assert all(scene["notes"] == [] for scene in found["scenes"])


def test_what_cannot_be_audited_is_refused_in_plain_words(auth_client, fake_container):
    w, beat, scenes = _story(auth_client, fake_container)
    empty = _plan(auth_client, w, "beat", "Nothing yet")
    refused = auth_client.post(f"{w['base']}/audits", json={"plan_id": empty["id"]})
    assert refused.status_code == 400
    assert "Nothing in this part of the story is written yet" in refused.json()["detail"]
    thread = _plan(auth_client, w, "thread", "Who sold the map?")
    across = auth_client.post(f"{w['base']}/audits", json={"plan_id": thread["id"]})
    assert across.status_code == 400 and "thread" in across.json()["detail"]
    missing = auth_client.post(f"{w['base']}/audits", json={"plan_id": "pln_nothing"})
    assert missing.status_code == 404
    assert auth_client.get(f"{w['base']}/audits/job_nothing").status_code == 404
    bad = auth_client.post(f"{w['base']}/audits", json={"plan_id": beat["id"], "passes": ["x"]})
    assert bad.status_code == 422
    assert fake_container.jobs.jobs == {}
    # A single scene can be audited too.
    one = auth_client.get(f"{w['base']}/audits/estimate", params={"plan_id": scenes[0]["id"]})
    assert [scene["title"] for scene in one.json()["scenes"]] == ["At the harbour"]


def test_a_scene_removed_after_the_audit_started_is_passed_over(auth_client, fake_container):
    w, beat, scenes = _story(auth_client, fake_container)
    fake_container.ai.queue(_answer())
    started = auth_client.post(
        f"{w['base']}/audits", json={"plan_id": beat["id"], "passes": ["clarity"]}
    ).json()
    removed = auth_client.post(
        f"{w['base']}/plan/{scenes[1]['id']}/remove",
        json={"expected_revision": _head(auth_client, w)},
    )
    assert removed.status_code == 200, removed.text
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done.error_message
    assert [entry["status"] for entry in done.result["scenes"]] == ["checked", "gone"]
    found = auth_client.get(f"{w['base']}/audits/{started['id']}").json()
    assert [scene["in_plan"] for scene in found["scenes"]] == [True, False]
    assert [scene["status"] for scene in found["scenes"]] == ["checked", "gone"]
    assert found["scenes"][1]["title"] == "A scene no longer in the plan"


def test_an_audit_taken_over_does_not_ask_or_store_a_finished_pass_again(
    auth_client, fake_container, monkeypatch
):
    """A worker stops mid-audit and another takes it over: it starts again from the top,
    but a pass whose notes were already stored is not asked for again."""
    from dataclasses import replace

    from backend.domain.jobs import JobStatus

    w, beat, scenes = _story(auth_client, fake_container)
    fake_container.ai.queue(_answer(CheckNote(quote="Lantern Harbor", problem="Wrong harbour.")))
    for _ in range(4):
        fake_container.ai.queue(_answer())
    started = auth_client.post(
        f"{w['base']}/audits", json={"plan_id": beat["id"], "passes": ["continuity", "clarity"]}
    ).json()
    store = fake_container.jobs
    key = next(k for k in store.jobs if k[2] == started["id"])
    ai = fake_container.ai
    original = ai.generate_structured
    calls = {"n": 0}

    async def taken_over_during_the_second_call(**kwargs):
        calls["n"] += 1
        answer = await original(**kwargs)
        if calls["n"] == 2:
            store.owners[key] = "worker_elsewhere"
        return answer

    monkeypatch.setattr(ai, "generate_structured", taken_over_during_the_second_call)
    (lost,) = _run(fake_container)
    assert lost.status.value == "running" and calls["n"] == 2
    # Its lease runs out, and the job is claimable again.
    store.jobs[key] = replace(store.jobs[key], status=JobStatus.QUEUED)
    store.owners[key] = None
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done.error_message
    assert done.attempts == 2
    # The first scene's continuity pass is not asked again; its clarity pass, whose answer
    # was never kept, is, and the second scene's two passes are.
    assert calls["n"] == 5
    first = done.result["scenes"][0]
    assert first["status"] == "checked" and first["passes"]["continuity"]["findings"] == 1
    found = auth_client.get(f"{w['base']}/audits/{started['id']}").json()
    assert [len(scene["notes"]) for scene in found["scenes"]] == [1, 0]


def test_a_scene_stopped_part_of_the_way_says_which_passes_finished(
    auth_client, fake_container, monkeypatch
):
    w, beat, _ = _story(auth_client, fake_container)
    fake_container.ai.queue(_answer())
    fake_container.ai.queue(_answer())
    fake_container.ai.queue(_answer(CheckNote(quote="guild hall", problem="No guild hall here.")))
    fake_container.ai.queue(_answer())
    started = auth_client.post(
        f"{w['base']}/audits", json={"plan_id": beat["id"], "passes": ["continuity", "clarity"]}
    ).json()
    ai = fake_container.ai
    original = ai.generate_structured
    calls = {"n": 0}

    async def stop_during_the_last_pass(**kwargs):
        calls["n"] += 1
        answer = await original(**kwargs)
        if calls["n"] == 4:
            await fake_container.studio_service.cancel(
                _scope_of(fake_container), *_ids(fake_container), started["id"]
            )
        return answer

    monkeypatch.setattr(ai, "generate_structured", stop_during_the_last_pass)
    (done,) = _run(fake_container)
    assert done.status.value == "cancelled"
    found = auth_client.get(f"{w['base']}/audits/{started['id']}").json()
    assert [scene["status"] for scene in found["scenes"]] == ["checked", "partial"]
    assert found["scenes"][1]["passes_done"] == ["continuity"]
    assert len(found["scenes"][1]["notes"]) == 1


def test_a_failed_audit_says_what_it_finished_is_kept(auth_client, fake_container):
    w, beat, _ = _story(auth_client, fake_container)
    fake_container.ai.queue(_answer(CheckNote(quote="Lantern Harbor", problem="Wrong harbour.")))
    fake_container.ai.queue(RuntimeError("the provider fell over"))
    started = auth_client.post(
        f"{w['base']}/audits", json={"plan_id": beat["id"], "passes": ["continuity"]}
    ).json()
    (done,) = _run(fake_container)
    assert done.status.value == "failed" and done.error_code == "internal"
    assert done.error_message == "Something went wrong. What it finished is kept."
    found = auth_client.get(f"{w['base']}/audits/{started['id']}").json()
    assert [scene["status"] for scene in found["scenes"]] == ["checked", "not_checked"]


def test_starting_an_audit_just_estimated_does_not_gather_each_scene_again(
    auth_client, fake_container, monkeypatch
):
    w, beat, scenes = _story(auth_client, fake_container)
    context = fake_container.studio_service._context
    original = context.for_scene
    gathered: list[str] = []

    async def counted(scope, workspace_id, project_id, plan_id, *args, **kwargs):
        gathered.append(plan_id)
        return await original(scope, workspace_id, project_id, plan_id, *args, **kwargs)

    monkeypatch.setattr(context, "for_scene", counted)
    estimate = auth_client.get(f"{w['base']}/audits/estimate", params={"plan_id": beat["id"]})
    assert estimate.status_code == 200, estimate.text
    assert sorted(gathered) == sorted([scenes[0]["id"], scenes[1]["id"]])
    started = auth_client.post(f"{w['base']}/audits", json={"plan_id": beat["id"]})
    assert started.status_code == 202, started.text
    assert len(gathered) == 2
    # A scene written since is worked out again; the other is not.
    version = _studio(auth_client, w, scenes[0])["version_id"]
    _save(auth_client, w, scenes[0], FIRST + " Gulls cried.", version)
    again = auth_client.get(f"{w['base']}/audits/estimate", params={"plan_id": beat["id"]})
    assert again.status_code == 200
    assert gathered[2:] == [scenes[0]["id"]]


def test_a_part_of_the_plan_taken_out_cannot_be_audited(auth_client, fake_container):
    w, beat, scenes = _story(auth_client, fake_container)
    removed = auth_client.post(
        f"{w['base']}/plan/{scenes[0]['id']}/remove",
        json={"expected_revision": _head(auth_client, w)},
    )
    assert removed.status_code == 200, removed.text
    refused = auth_client.post(f"{w['base']}/audits", json={"plan_id": scenes[0]["id"]})
    assert refused.status_code == 400, refused.text
    assert "taken out" in refused.json()["detail"]
    estimate = auth_client.get(f"{w['base']}/audits/estimate", params={"plan_id": scenes[0]["id"]})
    assert estimate.status_code == 400
    assert fake_container.jobs.jobs == {}
