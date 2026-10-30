"""Dismissing one kind of finding wider than one scene (CON-016): wherever it is about a
character, or in every scene of a stretch of the story; each says what it applies to,
holds for findings found later, and is undone everywhere with the record kept."""

import pytest

from tests.contract.test_context import _head, _scene, _setup
from tests.contract.test_god_hand import _world
from tests.contract.test_studio import _save, _studio

pytestmark = pytest.mark.contract

PROSE = "Mara rubbed her blue eyes. The salt air was palpable. Tobin waited."


def _looks(state: dict) -> dict:
    return next(c for c in state["checks"] if c["code"] == "appearance_differs")


def _stock(state: dict) -> dict:
    return next(c for c in state["checks"] if c["code"] == "stock_phrase")


def _two_scenes(client, container) -> tuple[dict, dict, dict]:
    w = _world(client, container)
    made = client.post(
        f"{w['base']}/facts",
        json={
            "proposition": {
                "subject_id": w["mara"],
                "predicate": "HAS_DETAIL",
                "object": {"kind": "structured", "value": {"appearance": "Green eyes."}},
            },
            "expected_revision": _head(client, w),
        },
    )
    assert made.status_code == 201, made.text
    first = _setup(client, w)
    second = _scene(
        client, w, [("tobin", "point_of_view"), ("mara", "participant")], title="The Wait"
    )
    return w, first, second


def _dismiss(client, w, scene, key: str, **body):
    return client.post(
        f"{w['base']}/plan/{scene['id']}/studio/dismissals",
        json={"key": key, "reason": "not_a_problem", **body},
    )


def test_a_finding_dismissed_for_a_character_stays_dismissed_in_every_scene(
    auth_client, fake_container
):
    w, first, second = _two_scenes(auth_client, fake_container)
    state = _save(auth_client, w, first, PROSE, None).json()
    looks = _looks(state)
    # Whom it is about, so it can be dismissed for them.
    assert looks["about"] == [{"entity_id": w["mara"], "name": "Mara"}]
    assert _stock(state)["about"] is None

    # Only someone the finding names.
    refused = _dismiss(
        auth_client, w, first, looks["key"], applies="character", entity_id=w["tobin"]
    )
    assert refused.status_code == 400, refused.text
    done = _dismiss(auth_client, w, first, looks["key"], applies="character", entity_id=w["mara"])
    assert done.status_code == 200, done.text
    dismissal = _looks(done.json())["dismissal"]
    assert dismissal["applies_to"] == (
        "Eyes, hair or a beard unlike the accepted details, wherever it is about Mara"
    )
    assert dismissal["scope_id"] == dismissal["id"]
    # Other findings in the scene are not touched.
    assert _stock(done.json())["dismissal"] is None

    # Another scene, written later, with the same kind of finding about Mara.
    other = _save(auth_client, w, second, "Mara's blue eyes narrowed at the gulls.", None).json()
    assert _looks(other)["dismissal"]["applies_to"] == dismissal["applies_to"]

    # Check history shows it as dismissed, by the same wider dismissal.
    runs = auth_client.get(f"{w['base']}/plan/{second['id']}/checks/runs").json()["items"]
    found = next(f for f in runs[0]["findings"] if f["code"] == "appearance_differs")
    assert found["status"] == "dismissed"

    # Listed with what it applies to; undoing it from any scene brings it back everywhere.
    listed = auth_client.get(f"{w['base']}/dismissals/scoped").json()["items"]
    assert [item["applies_to"] for item in listed] == [dismissal["applies_to"]]
    assert listed[0]["standing"] is True and listed[0]["applies"] == "character"
    undone = auth_client.delete(
        f"{w['base']}/plan/{second['id']}/studio/dismissals/{dismissal['id']}"
    )
    assert undone.status_code == 200, undone.text
    assert _looks(undone.json())["dismissal"] is None
    assert _looks(_studio(auth_client, w, first))["dismissal"] is None
    kept = auth_client.get(f"{w['base']}/dismissals/scoped").json()["items"]
    assert kept[0]["standing"] is False and kept[0]["withdrawn_at"] is not None
    again = auth_client.delete(f"{w['base']}/dismissals/scoped/{dismissal['id']}")
    assert again.status_code == 404, again.text


def test_a_finding_dismissed_for_a_stretch_of_scenes_holds_only_there(auth_client, fake_container):
    w, first, second = _two_scenes(auth_client, fake_container)
    state = _save(auth_client, w, first, PROSE, None).json()
    _save(auth_client, w, second, "The salt air was palpable again.", None)
    done = _dismiss(
        auth_client,
        w,
        first,
        _stock(state)["key"],
        applies="range",
        from_plan_id=first["id"],
        to_plan_id=first["id"],
        reason="intended",
    )
    assert done.status_code == 200, done.text
    assert _stock(done.json())["dismissal"]["applies_to"] == "Stock phrasing, in “The Docks”"
    # The scene outside the stretch still shows it.
    assert _stock(_studio(auth_client, w, second))["dismissal"] is None

    wider = auth_client.post(
        f"{w['base']}/dismissals/scoped",
        json={
            "kind": "check",
            "code": "stock_phrase",
            "applies": "range",
            "from_plan_id": first["id"],
            "to_plan_id": second["id"],
            "reason": "other",
            "note": "This narrator says palpable.",
            "days": 30,
        },
    )
    assert wider.status_code == 201, wider.text
    assert wider.json()["applies_to"] == (
        "Stock phrasing, in every scene from “The Docks” to “The Wait”"
    )
    assert wider.json()["expires_at"] is not None
    dismissed = _stock(_studio(auth_client, w, second))["dismissal"]
    assert dismissed["note"] == "This narrator says palpable."

    # A stretch must name scenes in the plan; the author's own reason needs words.
    bad = auth_client.post(
        f"{w['base']}/dismissals/scoped",
        json={
            "kind": "check",
            "code": "stock_phrase",
            "applies": "range",
            "from_plan_id": first["id"],
            "to_plan_id": "pln_missing",
            "reason": "intended",
        },
    )
    assert bad.status_code == 400, bad.text
    wordless = auth_client.post(
        f"{w['base']}/dismissals/scoped",
        json={
            "kind": "note",
            "code": "voice",
            "applies": "character",
            "entity_id": w["mara"],
            "reason": "other",
        },
    )
    assert wordless.status_code == 400, wordless.text


def test_how_often_review_findings_are_dismissed_is_told_per_kind(auth_client, fake_container):
    from tests.contract.test_review_dismissals import _proposal

    base, mutation_id = _proposal(auth_client, fake_container)
    project = base.removesuffix("/mutations")
    empty = auth_client.get(f"{project}/review/calibration")
    assert empty.status_code == 200, empty.text
    assert empty.json()["items"] == []

    (finding,) = auth_client.get(f"{base}/{mutation_id}/findings").json()["items"]
    # Shown twice, counted once.
    auth_client.get(f"{base}/{mutation_id}/findings")
    done = auth_client.post(
        f"{base}/{mutation_id}/findings/dismissals",
        json={"key": finding["key"], "reason": "not_a_problem"},
    )
    assert done.status_code == 200, done.text
    (line,) = auth_client.get(f"{project}/review/calibration").json()["items"]
    assert line["code"] == "possible_duplicate" and line["words"] == "A possible duplicate"
    assert (line["found"], line["dismissed"], line["accepted"]) == (1, 1, 0)
    assert line["rate"] is None
    assert line["note"].startswith("Found something once. Too few to say how often")


def test_a_run_over_several_scenes_works_out_its_wider_dismissals_once(
    auth_client, fake_container, monkeypatch
):
    import asyncio

    from tests.contract.test_story_audit import _answer
    from tests.contract.test_story_audit import _story as _audit_story

    w, beat, _ = _audit_story(auth_client, fake_container)
    fake_container.ai.queue(_answer())
    fake_container.ai.queue(_answer())
    started = auth_client.post(
        f"{w['base']}/audits", json={"plan_id": beat["id"], "passes": ["continuity"]}
    )
    assert started.status_code == 202, started.text
    asyncio.run(fake_container.job_runner.run_pending())
    scoped = fake_container.scoped_dismissals
    real = scoped.scoping
    calls = {"n": 0}

    async def counted(*args, **kwargs):
        calls["n"] += 1
        return await real(*args, **kwargs)

    monkeypatch.setattr(scoped, "scoping", counted)
    run = auth_client.get(f"{w['base']}/checks/runs/by-job/{started.json()['id']}")
    assert run.status_code == 200, run.text
    assert len(run.json()["scenes"]) >= 2
    # The whole plan and every character's names are read once, not once a scene.
    assert calls["n"] == 1


def _retire_scene(container, scene_id: str, head: int) -> None:
    import asyncio

    from backend.domain.identity import AccessScope, AuthMode
    from backend.domain.operations import RetirePlanItemOp

    settings = container.settings
    scope = AccessScope(
        actor_user_id="usr_owner",
        auth_mode=AuthMode.LOCAL,
        workspace_id=settings.AUTH_LOCAL_WORKSPACE_ID,
        project_id=settings.AUTH_LOCAL_PROJECT_ID,
    )
    mutation = container.media_service._mutation
    draft = asyncio.run(
        mutation.create_draft(
            scope,
            workspace_id=settings.AUTH_LOCAL_WORKSPACE_ID,
            project_id=settings.AUTH_LOCAL_PROJECT_ID,
            operations=[RetirePlanItemOp(id=scene_id)],
            expected_head=head,
        )
    )
    asyncio.run(mutation.commit(scope, draft.id))


def test_a_stretch_whose_last_scene_left_the_plan_no_longer_applies(auth_client, fake_container):
    w, first, second = _two_scenes(auth_client, fake_container)
    made = auth_client.post(
        f"{w['base']}/dismissals/scoped",
        json={
            "kind": "check",
            "code": "stock_phrase",
            "applies": "range",
            "from_plan_id": first["id"],
            "to_plan_id": second["id"],
            "reason": "intended",
        },
    )
    assert made.status_code == 201, made.text
    assert made.json()["standing"] is True and made.json()["lapsed"] is False
    _retire_scene(fake_container, second["id"], _head(auth_client, w))
    (listed,) = auth_client.get(f"{w['base']}/dismissals/scoped").json()["items"]
    # Not shown as holding when it covers nothing; the author can still undo it.
    assert listed["standing"] is False and listed["lapsed"] is True
    assert "no longer applies" in listed["applies_to"]
    undone = auth_client.delete(f"{w['base']}/dismissals/scoped/{listed['id']}")
    assert undone.status_code == 200, undone.text
    assert undone.json()["withdrawn_at"] is not None and undone.json()["lapsed"] is False


def test_only_a_kind_of_finding_the_studio_gives_can_be_dismissed_wider(
    auth_client, fake_container
):
    w, first, second = _two_scenes(auth_client, fake_container)
    body = {
        "kind": "check",
        "applies": "range",
        "from_plan_id": first["id"],
        "to_plan_id": second["id"],
        "reason": "intended",
    }
    made_up = auth_client.post(f"{w['base']}/dismissals/scoped", json={**body, "code": "made_up"})
    assert made_up.status_code == 400, made_up.text
    for code in ("supply_overspent", "continuity_two_places_at_once", "height_differs"):
        known = auth_client.post(f"{w['base']}/dismissals/scoped", json={**body, "code": code})
        assert known.status_code == 201, (code, known.text)
