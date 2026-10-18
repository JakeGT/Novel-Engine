"""Filling in a scene's plan card from its prose, as a reviewed plan change (Phase 6)."""

import asyncio

import pytest

from backend.services.scene_plan_card import CardChoice, PlanCardDraft
from tests.contract.test_context import _head
from tests.contract.test_god_hand import _world
from tests.contract.test_plan_assist import _accept
from tests.contract.test_studio import _demote, _hosted_scene, _save

pytestmark = pytest.mark.contract

PROSE = (
    "Tobin waited at Lantern Harbor while the tide came in.\n\n"
    "Mara tied up her boat and did not look at him."
)


def _run(container) -> list:
    return asyncio.run(container.job_runner.run_pending())


def _titled_scene(client, w, **fields) -> dict:
    """A scene with only a title, as an author who writes first would start one."""
    made = client.post(
        f"{w['base']}/plan",
        json={
            "kind": "scene",
            "title": "The Docks",
            "summary": "",
            "expected_revision": _head(client, w),
            **fields,
        },
    )
    assert made.status_code == 201, made.text
    return made.json()


def _item(client, w, plan_id: str) -> dict:
    (item,) = [i for i in client.get(f"{w['base']}/plan").json()["items"] if i["id"] == plan_id]
    return item


def _card(client, w, scene):
    return client.post(f"{w['base']}/plan/{scene['id']}/studio/plan-card", json={})


def _pick(w, key: str, name: str, quote: str) -> CardChoice:
    return CardChoice(id=w[key], name=name, quote=quote)


def test_an_empty_card_is_filled_from_the_prose_and_waits_for_review(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _titled_scene(auth_client, w)
    saved = _save(auth_client, w, scene, PROSE, None).json()
    fake_container.ai.queue(
        PlanCardDraft(
            summary="  Tobin waits at the harbor;\n Mara arrives and ignores him. ",
            point_of_view=_pick(w, "tobin", "Tobin Ashe", "Tobin waited"),
            place=_pick(w, "harbor", "Lantern Harbor", "at Lantern Harbor"),
            participants=[
                _pick(w, "mara", "Mara", "Mara tied up her boat"),
                # Already the viewpoint: not named twice, and not counted as a mistake.
                _pick(w, "tobin", "Tobin Ashe", "did not look at him"),
            ],
        )
    )
    started = _card(auth_client, w, scene)
    assert started.status_code == 202, started.text
    assert started.json()["kind"] == "scene_plan"
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    result = done.result
    assert result["summary_filled"] is True and result["refs_added"] == 3
    assert result["dropped"] == 0 and result["mutation_id"]

    # The scene's words and the story bible's ids went to the model, fenced as material.
    sent = fake_container.ai.calls[-1]["messages"][-1]["content"]
    assert "Mara tied up her boat" in sent and w["harbor"] in sent and "<<<SCENE-1>>>" in sent

    detail = auth_client.get(f"{w['base']}/mutations/{result['mutation_id']}").json()
    assert detail["requires_review"] is True and detail["origin"] == "model"
    (operation,) = detail["operations"]
    payload = operation["payload"]
    assert operation["operation_type"] == "update_plan_item" and payload["id"] == scene["id"]
    assert payload["summary"] == "Tobin waits at the harbor; Mara arrives and ignores him."
    assert payload["title"] is None
    assert {(r["entity_id"], r["role"]) for r in payload["refs"]} == {
        (w["tobin"], "point_of_view"),
        (w["harbor"], "place"),
        (w["mara"], "participant"),
    }
    # Each choice points at the scene's version and the words that show it.
    quotes = []
    for ref in payload["evidence"]:
        assert ref["source_artefact_id"] == saved["version_id"]
        passage = auth_client.get(
            f"{w['base']}/texts/passage",
            params={"version_id": saved["version_id"], "location": ref["location"]},
        ).json()
        quotes.append(passage["quote"])
    assert quotes == ["Tobin waited", "at Lantern Harbor", "Mara tied up her boat"]

    # Nothing changes until the author keeps it.
    before = _item(auth_client, w, scene["id"])
    assert before["summary"] == "" and before["refs"] == [] and before["title"] == "The Docks"
    _accept(auth_client, w, result["mutation_id"])
    after = _item(auth_client, w, scene["id"])
    assert after["summary"] == payload["summary"] and after["title"] == "The Docks"
    assert len(after["refs"]) == 3


def test_made_up_mixed_up_and_unquoted_choices_are_dropped(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _titled_scene(auth_client, w)
    _save(auth_client, w, scene, PROSE, None)
    fake_container.ai.queue(
        PlanCardDraft(
            summary="",
            # Not in the story bible.
            point_of_view=CardChoice(id="ent_nobody", name="Captain Roe", quote="Tobin waited"),
            # The scene does not say this.
            place=_pick(w, "harbor", "Lantern Harbor", "at the old lighthouse"),
            participants=[
                # Another entry's name on this id: a swapped id, so it is not trusted.
                _pick(w, "guild", "Tobin Ashe", "Tobin waited"),
                # No words at all.
                _pick(w, "saltmere", "Saltmere", "  "),
                # Known by an alias, and quoted: kept.
                _pick(w, "mara", "the fox", "Mara tied up her boat"),
            ],
        )
    )
    _card(auth_client, w, scene)
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    assert done.result["dropped"] == 4 and done.result["refs_added"] == 1
    assert done.result["summary_filled"] is False
    detail = auth_client.get(f"{w['base']}/mutations/{done.result['mutation_id']}").json()
    (operation,) = detail["operations"]
    assert operation["payload"]["summary"] is None
    assert operation["payload"]["refs"] == [{"entity_id": w["mara"], "role": "participant"}]


def test_a_viewpoint_that_is_not_a_character_is_dropped(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _titled_scene(auth_client, w)
    _save(auth_client, w, scene, PROSE, None)
    fake_container.ai.queue(
        PlanCardDraft(
            point_of_view=_pick(w, "harbor", "Lantern Harbor", "Lantern Harbor"),
            place=_pick(w, "mara", "Mara", "Mara tied up"),
        )
    )
    _card(auth_client, w, scene)
    (done,) = _run(fake_container)
    # Nothing survived, so nothing is proposed.
    assert done.result == {
        "mutation_id": None,
        "summary_filled": False,
        "refs_added": 0,
        "dropped": 2,
    }


def test_the_authors_own_summary_viewpoint_and_place_are_kept(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _titled_scene(
        auth_client,
        w,
        summary="Mara comes home and says nothing.",
        refs=[
            {"entity_id": w["mara"], "role": "point_of_view"},
            {"entity_id": w["saltmere"], "role": "participant"},
        ],
    )
    _save(auth_client, w, scene, PROSE, None)
    fake_container.ai.queue(
        PlanCardDraft(
            summary="Tobin waits at the harbor.",
            point_of_view=_pick(w, "tobin", "Tobin Ashe", "Tobin waited"),
            place=_pick(w, "harbor", "Lantern Harbor", "Lantern Harbor"),
            participants=[_pick(w, "tobin", "Tobin Ashe", "Tobin waited")],
        )
    )
    _card(auth_client, w, scene)
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    assert done.result["summary_filled"] is False and done.result["refs_added"] == 2
    detail = auth_client.get(f"{w['base']}/mutations/{done.result['mutation_id']}").json()
    (operation,) = detail["operations"]
    assert operation["payload"]["summary"] is None
    # The author's viewpoint and everyone already named stay; the gaps are filled.
    assert operation["payload"]["refs"] == [
        {"entity_id": w["mara"], "role": "point_of_view"},
        {"entity_id": w["saltmere"], "role": "participant"},
        {"entity_id": w["harbor"], "role": "place"},
        {"entity_id": w["tobin"], "role": "participant"},
    ]
    _accept(auth_client, w, done.result["mutation_id"])
    after = _item(auth_client, w, scene["id"])
    assert after["summary"] == "Mara comes home and says nothing."


def test_nothing_written_means_nothing_to_fill_from(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _titled_scene(auth_client, w)
    refused = _card(auth_client, w, scene)
    assert refused.status_code == 400
    assert "nothing written" in refused.json()["detail"]
    assert len(fake_container.jobs.jobs) == 0
    assert fake_container.ai.calls == []


def test_hosted_a_reader_cannot_ask_for_the_plan_card(hosted_client, hosted_container):
    base, scene, headers, workspace_id, user_id = _hosted_scene(hosted_client, hosted_container)
    studio = f"{base}/plan/{scene['id']}/studio"
    saved = hosted_client.put(f"{studio}/text", json={"content": PROSE}, headers=headers)
    assert saved.status_code == 200, saved.text
    _demote(hosted_container, workspace_id, user_id)
    refused = hosted_client.post(f"{studio}/plan-card", json={}, headers=headers)
    assert refused.status_code == 403, refused.text
    assert len(hosted_container.jobs.jobs) == 0
    assert hosted_container.ai.calls == []


def test_the_passage_says_which_scene_its_words_belong_to(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _titled_scene(auth_client, w)
    saved = _save(auth_client, w, scene, PROSE, None).json()
    from_scene = auth_client.get(
        f"{w['base']}/texts/passage",
        params={"version_id": saved["version_id"], "location": "chars:0-12"},
    )
    assert from_scene.status_code == 200, from_scene.text
    assert from_scene.json()["scene_id"] == scene["id"]
    assert from_scene.json()["quote"] == "Tobin waited"

    made = auth_client.post(
        f"{w['base']}/texts", json={"title": "Notes", "content": "Mara at sea."}
    ).json()
    version = auth_client.get(f"{w['base']}/texts/{made['id']}").json()["version"]["id"]
    ordinary = auth_client.get(f"{w['base']}/texts/passage", params={"version_id": version})
    assert ordinary.status_code == 200, ordinary.text
    assert ordinary.json()["scene_id"] is None


def test_approving_a_card_suggestion_cannot_undo_what_the_author_set_since(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    scene = _titled_scene(auth_client, w)
    _save(auth_client, w, scene, PROSE, None)
    fake_container.ai.queue(
        PlanCardDraft(
            summary="Tobin waits; Mara ignores him.",
            point_of_view=_pick(w, "tobin", "Tobin Ashe", "Tobin waited"),
        )
    )
    _card(auth_client, w, scene)
    (done,) = _run(fake_container)
    proposal = done.result["mutation_id"]

    # Meanwhile the author writes their own summary and chooses Mara as the viewpoint.
    changed = auth_client.patch(
        f"{w['base']}/plan/{scene['id']}",
        json={
            "summary": "Mara comes home.",
            "refs": [{"entity_id": w["mara"], "role": "point_of_view"}],
            "expected_revision": _head(auth_client, w),
        },
    )
    assert changed.status_code == 200, changed.text

    auth_client.post(f"{w['base']}/mutations/{proposal}/approve", json={})
    committed = auth_client.post(f"{w['base']}/mutations/{proposal}/commit")
    assert committed.status_code >= 400
    item = _item(auth_client, w, scene["id"])
    assert item["summary"] == "Mara comes home."
    assert [(r["entity_id"], r["role"]) for r in item["refs"]] == [(w["mara"], "point_of_view")]


def test_approving_a_card_suggestion_cannot_bring_back_someone_the_author_removed(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    scene = _titled_scene(auth_client, w, refs=[{"entity_id": w["mara"], "role": "participant"}])
    _save(auth_client, w, scene, PROSE, None)
    fake_container.ai.queue(
        PlanCardDraft(point_of_view=_pick(w, "tobin", "Tobin Ashe", "Tobin waited"))
    )
    _card(auth_client, w, scene)
    (done,) = _run(fake_container)
    proposal = done.result["mutation_id"]

    # Meanwhile the author takes Mara out of the scene.
    changed = auth_client.patch(
        f"{w['base']}/plan/{scene['id']}",
        json={"refs": [], "expected_revision": _head(auth_client, w)},
    )
    assert changed.status_code == 200, changed.text

    check = auth_client.post(f"{w['base']}/mutations/{proposal}/validate").json()
    (issue,) = [i for i in check["issues"] if i["code"] == "plan_changed_since"]
    assert "changed since this was suggested" in issue["message"]
    auth_client.post(f"{w['base']}/mutations/{proposal}/approve", json={})
    committed = auth_client.post(f"{w['base']}/mutations/{proposal}/commit")
    assert committed.status_code >= 400
    assert _item(auth_client, w, scene["id"])["refs"] == []
