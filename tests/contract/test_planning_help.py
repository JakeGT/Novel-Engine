"""The assistant's help with a scene's plan, as reviewed plan changes (STY-003, STY-004)."""

import asyncio

import pytest

from backend.services.planning_help import BridgeChoice, BridgeDraft, BridgeSheet, IntentDraft
from tests.contract.test_context import _head
from tests.contract.test_god_hand import _world
from tests.contract.test_plan_assist import _accept
from tests.contract.test_plan_checks import _add, _outline, _put
from tests.contract.test_studio import _save

pytestmark = pytest.mark.contract

PROSE = (
    "Mara wanted the ledger before the tide turned.\n\n"
    "Tobin stood between her and the strongroom door, and he would not move."
)


def _run(container) -> list:
    return asyncio.run(container.job_runner.run_pending())


def _item(client, w, plan_id: str) -> dict:
    (item,) = [i for i in client.get(f"{w['base']}/plan").json()["items"] if i["id"] == plan_id]
    return item


def _scene(client, w, **fields) -> dict:
    return _add(
        client,
        w,
        "scene",
        "The strongroom",
        refs=[{"entity_id": w["mara"], "role": "point_of_view"}],
        **fields,
    )


def test_intent_is_suggested_from_the_card_and_prose_and_waits_for_review(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    scene = _scene(auth_client, w, summary="Mara goes after the ledger.")
    saved = _save(auth_client, w, scene, PROSE, None).json()
    estimate = auth_client.get(f"{w['base']}/plan/{scene['id']}/intent/estimate")
    assert estimate.status_code == 200, estimate.text
    assert estimate.json()["reads_prose"] is True and estimate.json()["total_tokens"] > 0
    # Nothing was sent to estimate.
    assert fake_container.ai.calls == []

    fake_container.ai.queue(
        IntentDraft(
            goal="the ledger, before the tide turns",
            goal_words="Mara wanted the ledger before the tide turned",
            obstacle="Tobin, blocking the strongroom",
            obstacle_words="Tobin stood between her and the strongroom door",
            # Not shown anywhere: dropped.
            outcome="no_and",
            outcome_words="and then the harbour burned",
        )
    )
    started = auth_client.post(f"{w['base']}/plan/{scene['id']}/intent/suggest")
    assert started.status_code == 202, started.text
    assert started.json()["kind"] == "scene_intent"
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    assert done.result["filled"] == ["goal", "obstacle"] and done.result["dropped"] == 1
    sent = fake_container.ai.calls[-1]["messages"][-1]["content"]
    assert "<<<CARD-1>>>" in sent and "<<<SCENE-1>>>" in sent and "Mara goes after" in sent

    detail = auth_client.get(f"{w['base']}/mutations/{done.result['mutation_id']}").json()
    assert detail["requires_review"] is True and detail["origin"] == "model"
    (operation,) = detail["operations"]
    assert operation["payload"]["only_fills"] is True
    assert all(
        r["source_artefact_id"] == saved["version_id"] for r in operation["payload"]["evidence"]
    )
    # Nothing changes until the author keeps it.
    assert _item(auth_client, w, scene["id"])["intent"] is None
    _accept(auth_client, w, done.result["mutation_id"])
    kept = _item(auth_client, w, scene["id"])["intent"]
    assert kept["goal"] == "the ledger, before the tide turns"
    assert kept["obstacle"] == "Tobin, blocking the strongroom"
    assert kept.get("outcome") is None


def test_the_authors_own_intent_wins(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    scene = _scene(auth_client, w, summary="Mara goes after the ledger.")
    changed = auth_client.patch(
        f"{w['base']}/plan/{scene['id']}",
        json={"intent": {"goal": "To get home"}, "expected_revision": _head(auth_client, w)},
    )
    assert changed.status_code == 200, changed.text
    fake_container.ai.queue(
        IntentDraft(
            goal="the ledger",
            goal_words="Mara goes after the ledger",
            obstacle="the tide",
            obstacle_words="goes after the ledger",
        )
    )
    auth_client.post(f"{w['base']}/plan/{scene['id']}/intent/suggest")
    (done,) = _run(fake_container)
    assert done.result["filled"] == ["obstacle"]
    # The author then fills in the obstacle themselves: approving would undo it, so it is refused.
    again = auth_client.patch(
        f"{w['base']}/plan/{scene['id']}",
        json={
            "intent": {"goal": "To get home", "obstacle": "Her own fear"},
            "expected_revision": _head(auth_client, w),
        },
    )
    assert again.status_code == 200, again.text
    validation = auth_client.post(
        f"{w['base']}/mutations/{done.result['mutation_id']}/validate"
    ).json()
    assert any(issue["code"] == "plan_changed_since" for issue in validation["issues"])


def test_nothing_to_go_on_is_refused_before_anything_is_sent(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    bare = _scene(auth_client, w)
    refused = auth_client.post(f"{w['base']}/plan/{bare['id']}/intent/suggest")
    assert refused.status_code == 400 and "nothing to go on" in refused.text
    assert fake_container.ai.calls == []


def _needs_saltmere(client, w) -> tuple[dict, dict]:
    before = _add(client, w, "scene", "The quarrel")
    scene = _scene(client, w)
    _put(
        client,
        w,
        scene["id"],
        "conditions",
        {
            "conditions": [
                {
                    "when": "before",
                    "entity_id": w["mara"],
                    "check": {
                        "kind": "has",
                        "pattern": {"predicate": "LOCATED_AT", "other_id": w["saltmere"]},
                    },
                }
            ]
        },
    )
    return before, scene


def test_bridge_scenes_are_suggested_for_an_unmet_need_and_placed_before_it(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    before, scene = _needs_saltmere(auth_client, w)
    estimate = auth_client.get(
        f"{w['base']}/plan/{scene['id']}/bridges/estimate", params={"condition": 0}
    )
    assert estimate.status_code == 200, estimate.text
    assert estimate.json()["total_tokens"] > 0
    fake_container.ai.queue(
        BridgeSheet(
            bridges=[
                BridgeDraft(
                    title="The crossing to Saltmere",
                    summary="Mara takes the night ferry to Saltmere.",
                    how_it_helps="It brings her to Saltmere.",
                    point_of_view=BridgeChoice(id=w["mara"], name="Mara"),
                    place=BridgeChoice(id=w["saltmere"], name="Saltmere"),
                    # A swapped id: another entry's name. Dropped.
                    participants=[BridgeChoice(id=w["guild"], name="Tobin Ashe")],
                ),
                BridgeDraft(title="  ", summary="No title."),
                BridgeDraft(title="A third", summary="Never more than two."),
            ]
        )
    )
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/bridges/suggest", params={"condition": 0}
    )
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    assert done.result["bridges"] == 1 and done.result["dropped"] == 2
    sent = fake_container.ai.calls[-1]["messages"][-1]["content"]
    assert "needs this first" in sent and "<<<NEED-1>>>" in sent

    detail = auth_client.get(f"{w['base']}/mutations/{done.result['mutation_id']}").json()
    assert detail["origin"] == "model" and detail["requires_review"] is True
    _accept(auth_client, w, done.result["mutation_id"])
    outline = _outline(auth_client, w)
    titles = [item["title"] for item in outline["items"] if item["kind"] == "scene"]
    order = sorted(
        (item for item in outline["items"] if item["kind"] == "scene"),
        key=lambda item: item["position"],
    )
    assert [item["title"] for item in order] == [
        "The quarrel",
        "The crossing to Saltmere",
        "The strongroom",
    ], titles
    bridge = next(item for item in order if item["title"] == "The crossing to Saltmere")
    assert {(r["entity_id"], r["role"]) for r in bridge["refs"]} == {
        (w["mara"], "point_of_view"),
        (w["saltmere"], "place"),
    }
    # It carries the need as what it should leave true, so the scene now reads as planned.
    assert bridge["conditions"][0]["when"] == "after"
    [state] = [s for s in outline["conditions"] if s["plan_id"] == scene["id"]]
    assert state["status"] == "planned" and state["planned_in"] == bridge["id"]
    assert before["id"] in {item["id"] for item in order}


def test_a_met_need_gets_no_bridge(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    scene = _scene(auth_client, w)
    _put(
        auth_client,
        w,
        scene["id"],
        "conditions",
        {
            "conditions": [
                {
                    "when": "before",
                    "entity_id": w["mara"],
                    "check": {
                        "kind": "has",
                        "pattern": {"predicate": "LOCATED_AT", "other_id": w["harbor"]},
                    },
                }
            ]
        },
    )
    refused = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/bridges/suggest", params={"condition": 0}
    )
    assert refused.status_code == 400 and "needs no bridge" in refused.text
    missing = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/bridges/suggest", params={"condition": 3}
    )
    assert missing.status_code == 404
    assert fake_container.ai.calls == []


def test_a_word_or_two_or_the_authors_own_lines_are_not_shown(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    scene = _scene(auth_client, w, summary="Mara goes after the ledger.")
    auth_client.patch(
        f"{w['base']}/plan/{scene['id']}",
        json={"intent": {"goal": "To get home"}, "expected_revision": _head(auth_client, w)},
    )
    fake_container.ai.queue(
        IntentDraft(
            # One word of the card proves nothing.
            obstacle="the ledger",
            obstacle_words="ledger",
            # The card's own "Already decided" line is the author's, not the scene's.
            outcome="no",
            outcome_words="Already decided — wants: To get home",
        )
    )
    auth_client.post(f"{w['base']}/plan/{scene['id']}/intent/suggest")
    (done,) = _run(fake_container)
    assert done.result["filled"] == [] and done.result["dropped"] == 2
    assert done.result["mutation_id"] is None


def test_a_part_the_author_cleared_since_is_not_put_back(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    scene = _scene(auth_client, w, summary="Mara goes after the ledger.")
    auth_client.patch(
        f"{w['base']}/plan/{scene['id']}",
        json={"intent": {"goal": "To get home"}, "expected_revision": _head(auth_client, w)},
    )
    fake_container.ai.queue(
        IntentDraft(obstacle="the tide", obstacle_words="goes after the ledger")
    )
    auth_client.post(f"{w['base']}/plan/{scene['id']}/intent/suggest")
    (done,) = _run(fake_container)
    assert done.result["filled"] == ["obstacle"]
    # The author clears the goal; approving would put "To get home" back.
    cleared = auth_client.patch(
        f"{w['base']}/plan/{scene['id']}",
        json={"intent": {}, "expected_revision": _head(auth_client, w)},
    )
    assert cleared.status_code == 200, cleared.text
    validation = auth_client.post(
        f"{w['base']}/mutations/{done.result['mutation_id']}/validate"
    ).json()
    assert any(issue["code"] == "plan_changed_since" for issue in validation["issues"])


def _bridge(client, container, w, scene, draft: BridgeDraft):
    container.ai.queue(BridgeSheet(bridges=[draft]))
    started = client.post(
        f"{w['base']}/plan/{scene['id']}/bridges/suggest", params={"condition": 0}
    )
    assert started.status_code == 202, started.text
    (done,) = _run(container)
    assert done.status.value == "succeeded", done
    return done


def test_names_a_bridge_invents_are_listed_for_review(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    _before, scene = _needs_saltmere(auth_client, w)
    done = _bridge(
        auth_client,
        fake_container,
        w,
        scene,
        BridgeDraft(
            title="Old Joss rows to Saltmere",
            summary="Mara pays Old Joss and Tamsin for the crossing to Saltmere.",
        ),
    )
    detail = auth_client.get(f"{w['base']}/mutations/{done.result['mutation_id']}").json()
    created = next(op for op in detail["operations"] if op["payload"]["op"] == "create_plan_item")
    notes = created["payload"]["details"]["Names to check"]
    assert "Old Joss" in notes and "Tamsin" in notes
    assert "Mara" not in notes and "Saltmere" not in notes


def test_a_bridge_is_refused_once_the_order_has_changed(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    before, scene = _needs_saltmere(auth_client, w)
    done = _bridge(
        auth_client,
        fake_container,
        w,
        scene,
        BridgeDraft(title="The crossing", summary="Mara takes the ferry to Saltmere."),
    )
    # The author moves the scene ahead of "The quarrel" before approving.
    moved = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/move",
        json={"before_id": before["id"], "expected_revision": _head(auth_client, w)},
    )
    assert moved.status_code == 200, moved.text
    validation = auth_client.post(
        f"{w['base']}/mutations/{done.result['mutation_id']}/validate"
    ).json()
    assert any(issue["code"] == "plan_changed_since" for issue in validation["issues"])
