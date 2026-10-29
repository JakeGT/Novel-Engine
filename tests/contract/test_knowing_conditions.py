"""A scene's condition about what someone knows, has seen or has heard, over HTTP
(STY-005): saved like any condition, checked against the beliefs recorded and how they
were learned, given to drafting in plain words, and read in plan order. No model."""

import pytest

from tests.contract.test_context import _head
from tests.contract.test_god_hand import _world
from tests.contract.test_knowledge import _approve
from tests.contract.test_plan_checks import _add, _outline, _put

pytestmark = pytest.mark.contract


def _knows(w, holder: str, how: str, *, when: str = "before") -> dict:
    return {
        "when": when,
        "entity_id": w[holder],
        "knows": {
            "how": how,
            "subject_id": w["mara"],
            "predicate": "MEMBER_OF",
            "other_id": w["guild"],
        },
    }


def test_seen_and_knows_are_checked_against_what_was_recorded(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    first = _add(auth_client, w, "scene", "The Hall")
    later = _add(auth_client, w, "scene", "The Reckoning")
    _put(
        auth_client,
        w,
        later["id"],
        "conditions",
        {"conditions": [_knows(w, "tobin", "seen"), _knows(w, "tobin", "heard")]},
    )
    states = _outline(auth_client, w)["conditions"]
    assert [s["status"] for s in states] == ["not_met", "not_met"]
    assert (
        states[0]["reading"] == "Tobin Ashe must have seen this: Mara is a member of Harbor Guild."
    )
    assert states[0]["note"] == "Nothing records that Tobin Ashe knows this."
    flagged = [o for o in _outline(auth_client, w)["open"] if o["code"] == "condition_not_met"]
    assert len(flagged) == 2

    # Tobin sees Mara slip into the guild hall in the first scene: seen, not heard.
    seen = auth_client.post(
        f"{w['base']}/knowledge/witness",
        json={
            "name": "Mara slips into the guild hall",
            "present_ids": [w["tobin"]],
            "fact_id": w["in_guild"],
            "scene_id": first["id"],
            "expected_revision": _head(auth_client, w),
        },
    )
    assert seen.status_code == 201, seen.text
    _approve(auth_client, w, seen.json()["mutation_id"])
    states = _outline(auth_client, w)["conditions"]
    assert [s["status"] for s in states] == ["met", "not_met"]
    assert "saw it, but nothing records hearing it" in states[1]["note"]

    # Drafting is given it in the plan's own words.
    context = auth_client.get(f"{w['base']}/plan/{later['id']}/context").json()
    given = {i["key"]: i["text"] for i in context["items"] if i["key"].startswith("condition:")}
    assert given[f"condition:{later['id']}:0"] == (
        "Must be true before this scene: Tobin Ashe must have seen this: Mara is a member of "
        "Harbor Guild."
    )
    assert fake_container.ai.calls == []


def test_an_earlier_scene_meant_to_teach_it_makes_it_planned(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    first = _add(auth_client, w, "scene", "The Hall")
    later = _add(auth_client, w, "scene", "The Reckoning")
    _put(
        auth_client,
        w,
        first["id"],
        "conditions",
        {"conditions": [_knows(w, "tobin", "knows", when="after")]},
    )
    _put(auth_client, w, later["id"], "conditions", {"conditions": [_knows(w, "tobin", "knows")]})
    states = {s["plan_id"]: s for s in _outline(auth_client, w)["conditions"]}
    assert states[later["id"]]["status"] == "planned"
    assert states[later["id"]]["planned_in"] == first["id"]


def test_is_at_is_undone_by_an_earlier_scene_that_moves_them(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    first = _add(auth_client, w, "scene", "The Road")
    later = _add(auth_client, w, "scene", "The Harbour")
    at = lambda place: {"kind": "has", "pattern": {"predicate": "LOCATED_AT", "other_id": w[place]}}  # noqa: E731
    _put(
        auth_client,
        w,
        first["id"],
        "conditions",
        {"conditions": [{"when": "after", "entity_id": w["mara"], "check": at("saltmere")}]},
    )
    _put(
        auth_client,
        w,
        later["id"],
        "conditions",
        {"conditions": [{"when": "before", "entity_id": w["mara"], "check": at("harbor")}]},
    )
    states = {s["plan_id"]: s for s in _outline(auth_client, w)["conditions"]}
    # Mara is at the harbour in the story bible, but "The Road" is meant to take her away.
    assert states[later["id"]]["status"] == "not_met"
    assert (
        "“The Road”, earlier in the plan, is meant to change this." == states[later["id"]]["note"]
    )


def test_a_knowing_condition_must_name_a_character_and_real_entries(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _add(auth_client, w, "scene", "The Hall")
    url = f"{w['base']}/plan/{scene['id']}/conditions"
    for bad in (
        _knows(w, "harbor", "knows"),
        {
            **_knows(w, "tobin", "knows"),
            "knows": {**_knows(w, "tobin", "knows")["knows"], "other_id": "ent_nope"},
        },
        {
            **_knows(w, "tobin", "knows"),
            "knows": {**_knows(w, "tobin", "knows")["knows"], "predicate": "NOPE"},
        },
    ):
        refused = auth_client.put(
            url, json={"conditions": [bad], "expected_revision": _head(auth_client, w)}
        )
        assert refused.status_code == 400, refused.text
    both = auth_client.put(
        url,
        json={
            "conditions": [{**_knows(w, "tobin", "knows"), "words": "He knows."}],
            "expected_revision": _head(auth_client, w),
        },
    )
    assert both.status_code == 422
