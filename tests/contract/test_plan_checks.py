"""The plan's checks and promises over HTTP, with no model (STY-005, STY-007)."""

import pytest

from tests.contract.test_context import _head
from tests.contract.test_god_hand import _world

pytestmark = pytest.mark.contract


def _add(client, w, kind: str, title: str, **fields) -> dict:
    made = client.post(
        f"{w['base']}/plan",
        json={"kind": kind, "title": title, "expected_revision": _head(client, w), **fields},
    )
    assert made.status_code == 201, made.text
    return made.json()


def _put(client, w, plan_id: str, what: str, body: dict) -> dict:
    saved = client.put(
        f"{w['base']}/plan/{plan_id}/{what}",
        json={**body, "expected_revision": _head(client, w)},
    )
    assert saved.status_code == 200, saved.text
    return saved.json()


def _outline(client, w) -> dict:
    got = client.get(f"{w['base']}/plan")
    assert got.status_code == 200, got.text
    return got.json()


def test_a_reveal_given_away_early_and_a_thread_resolved_too_soon_are_flagged(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    thread = _add(auth_client, w, "thread", "Is Mara in the guild?")
    docks = _add(auth_client, w, "scene", "The Docks", pays_off=[thread["id"]])
    market = _add(auth_client, w, "scene", "The Market", sets_up=[thread["id"]])
    reckoning = _add(auth_client, w, "scene", "The Reckoning")
    _put(auth_client, w, docks["id"], "reveals", {"reveals": [{"fact_id": w["in_guild"]}]})
    _put(
        auth_client,
        w,
        reckoning["id"],
        "reveals",
        {"reveals": [{"fact_id": w["in_guild"], "main_reveal": True}]},
    )
    hinted = auth_client.put(
        f"{w['base']}/plan/{market['id']}/reveals",
        json={
            "reveals": [{"fact_id": w["in_guild"], "how": "hinted", "main_reveal": True}],
            "expected_revision": _head(auth_client, w),
        },
    )
    assert hinted.status_code in (400, 422)

    outline = _outline(auth_client, w)
    flagged = [(o["plan_id"], o["code"], o["kind"]) for o in outline["open"]]
    assert (docks["id"], "reveals_too_early", "problem") in flagged
    assert (docks["id"], "resolves_before_opening", "problem") in flagged
    assert (reckoning["id"], "reveal_without_clue", "gap") in flagged
    early = next(o for o in outline["open"] if o["code"] == "reveals_too_early")
    assert early["message"] == (
        "This shows the reader “Mara is a member of Harbor Guild” plainly, before "
        "“The Reckoning”, where it is meant to come out."
    )
    # The list reads in outline order: the docks come before the reckoning.
    places = [o["plan_id"] for o in outline["open"]]
    assert places.index(docks["id"]) < places.index(reckoning["id"])
    # No model was asked.
    assert fake_container.ai.calls == []


def test_promises_list_what_the_reader_is_owed_with_the_distance(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    kept = _add(auth_client, w, "thread", "Who sank the ferry?")
    owed = _add(auth_client, w, "thread", "Will Tobin forgive her?")
    first = _add(auth_client, w, "scene", "One", sets_up=[kept["id"]])
    _add(auth_client, w, "scene", "Two", sets_up=[owed["id"]])
    third = _add(auth_client, w, "scene", "Three", pays_off=[kept["id"]])
    _put(
        auth_client,
        w,
        first["id"],
        "reveals",
        {"reveals": [{"fact_id": w["knows"], "how": "hinted"}]},
    )

    promises = _outline(auth_client, w)["promises"]
    assert [
        (p["kind"], p["subject_id"], p["made_at"], p["kept_in"], p["distance"]) for p in promises
    ] == [
        ("thread", kept["id"], 1, third["id"], 2),
        ("clue", w["knows"], 1, None, 2),
        ("thread", owed["id"], 2, None, 1),
    ]


def test_scene_conditions_are_required_or_preferred_checked_in_time_and_given_to_drafting(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    at_saltmere = {
        "kind": "has",
        "pattern": {"predicate": "LOCATED_AT", "other_id": w["saltmere"]},
    }
    at_harbor = {"kind": "has", "pattern": {"predicate": "LOCATED_AT", "other_id": w["harbor"]}}
    scene = _add(
        auth_client,
        w,
        "scene",
        "Saltmere",
        refs=[{"entity_id": w["mara"], "role": "point_of_view"}],
    )
    saved = _put(
        auth_client,
        w,
        scene["id"],
        "conditions",
        {
            "conditions": [
                {"when": "before", "entity_id": w["mara"], "check": at_saltmere},
                {
                    "when": "before",
                    "strength": "soft",
                    "entity_id": w["mara"],
                    "check": at_harbor,
                },
                {"when": "before", "strength": "soft", "words": "The storm has broken."},
            ]
        },
    )
    assert [c.get("strength") for c in saved["conditions"]] == ["hard", "soft", "soft"]
    assert saved["conditions"][2]["words"] == "The storm has broken."

    outline = _outline(auth_client, w)
    states = [(s["strength"], s["status"]) for s in outline["conditions"]]
    assert states == [("hard", "not_met"), ("soft", "met"), ("soft", "unchecked")]
    # Only the required one that is not true is listed as something to look at.
    flagged = [o for o in outline["open"] if o["code"] == "condition_not_met"]
    assert [o["plan_id"] for o in flagged] == [scene["id"]]
    assert "Mara must have this: it is located at Saltmere" in flagged[0]["message"]

    # Placing the scene in story time judges it against the facts true then.
    _put(auth_client, w, scene["id"], "when", {"when": {"kind": "at", "at": 5}})
    ends = auth_client.post(
        f"{w['base']}/facts/{w['at_harbor']}/amend",
        json={"expected_revision": _head(auth_client, w), "ends": {"kind": "at", "at": 3}},
    )
    assert ends.status_code == 200, ends.text
    states = [(s["strength"], s["status"]) for s in _outline(auth_client, w)["conditions"]]
    assert states[1] == ("soft", "not_met")

    # Drafting is given every condition, as the plan's own words.
    context = auth_client.get(f"{w['base']}/plan/{scene['id']}/context").json()
    given = {i["key"]: i for i in context["items"] if i["key"].startswith("condition:")}
    assert given[f"condition:{scene['id']}:0"]["text"] == (
        "Must be true before this scene: Mara must have this: it is located at Saltmere."
    )
    assert given[f"condition:{scene['id']}:0"]["hard"] is True
    assert given[f"condition:{scene['id']}:2"]["text"] == (
        "Better true before this scene, though not needed: The storm has broken."
    )
    assert given[f"condition:{scene['id']}:2"]["authority"] == "plan"

    # A condition is one thing or the other, never both or neither.
    both = auth_client.put(
        f"{w['base']}/plan/{scene['id']}/conditions",
        json={
            "conditions": [
                {"when": "before", "words": "Rain", "entity_id": w["mara"], "check": at_harbor}
            ],
            "expected_revision": _head(auth_client, w),
        },
    )
    assert both.status_code in (400, 422)
