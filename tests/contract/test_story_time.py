"""Story time: dated events, facts true for a span, and the timeline (ADR-020)."""

import pytest

from tests.contract.test_god_hand import _world

pytestmark = pytest.mark.contract


def _event(client, w, head: int, name: str, when: dict | None = None) -> dict:
    body = {"kind": "happening", "name": name, "expected_revision": head}
    if when is not None:
        body["when"] = when
    made = client.post(f"{w['base']}/events", json=body)
    assert made.status_code == 201, made.text
    return made.json()


def _located(w, target: str) -> dict:
    project_id = w["base"].rsplit("/", 1)[1]
    return {
        "subject_id": w["mara"],
        "predicate": "LOCATED_AT",
        "object": {"kind": "entity_ref", "entity_id": w[target], "project_id": project_id},
    }


def test_events_are_ordered_by_story_time_and_described_in_words(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    war = _event(
        auth_client, w, head, "The War", {"kind": "between", "earliest": 100, "latest": 102}
    )
    _event(
        auth_client,
        w,
        head + 1,
        "The Exile",
        {"kind": "relative", "anchor_event_id": war["id"], "offset": 3, "direction": "before"},
    )
    _event(
        auth_client,
        w,
        head + 2,
        "The Founding",
        {"kind": "at", "at": 1, "label": "the first spring"},
    )
    _event(auth_client, w, head + 3, "A Dream")

    timeline = auth_client.get(f"{w['base']}/timeline").json()
    assert timeline["unit"] == "year"
    assert [(p["event"]["name"], p["when"]) for p in timeline["placed"]] == [
        ("The Founding", "the first spring"),
        ("The Exile", "3 years before The War"),
        ("The War", "between year 100 and 102"),
    ]
    assert timeline["placed"][1]["earliest"] == 97 and timeline["placed"][1]["latest"] == 99
    assert [p["event"]["name"] for p in timeline["unplaced"]] == ["A Dream"]
    assert timeline["issues"] == []


def test_the_story_names_its_own_unit_of_time(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    named = auth_client.put(f"{w['base']}/timeline/unit", json={"unit": "Seasons"})
    assert named.json() == {"unit": "season"}
    _event(auth_client, w, int(w["head"]), "The Thaw", {"kind": "at", "at": 4})
    timeline = auth_client.get(f"{w['base']}/timeline").json()
    assert timeline["placed"][0]["when"] == "season 4"
    bad = auth_client.put(f"{w['base']}/timeline/unit", json={"unit": "year 12!"})
    assert bad.status_code == 400


def test_a_time_set_relative_to_itself_is_refused(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    first = _event(auth_client, w, head, "First")
    second = _event(
        auth_client,
        w,
        head + 1,
        "Second",
        {"kind": "relative", "anchor_event_id": first["id"], "offset": 1},
    )
    looped = auth_client.patch(
        f"{w['base']}/events/{first['id']}",
        json={
            "expected_revision": head + 2,
            "when": {"kind": "relative", "anchor_event_id": second["id"], "offset": 1},
        },
    )
    assert looped.status_code == 422, looped.text
    unknown = auth_client.post(
        f"{w['base']}/events",
        json={
            "kind": "happening",
            "name": "Lost",
            "expected_revision": head + 2,
            "when": {"kind": "relative", "anchor_event_id": "event_gone", "offset": 1},
        },
    )
    assert unknown.status_code == 422


def test_one_at_a_time_facts_may_follow_each_other_in_story_time(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    # Mara is at the harbor (no span). A second place with no span still clashes.
    clash = auth_client.post(
        f"{w['base']}/facts",
        json={"proposition": _located(w, "saltmere"), "expected_revision": head},
    )
    assert clash.status_code == 409
    # Say the harbor was only until year 10, and Saltmere from year 10: both may stand.
    bounded = auth_client.post(
        f"{w['base']}/facts/{w['at_harbor']}/amend",
        json={"expected_revision": head, "ends": {"kind": "at", "at": 10}},
    )
    assert bounded.status_code == 200, bounded.text
    assert bounded.json()["ends"] == {"kind": "at", "at": 10.0, "label": None}
    later = auth_client.post(
        f"{w['base']}/facts",
        json={
            "proposition": _located(w, "saltmere"),
            "expected_revision": head + 1,
            "starts": {"kind": "at", "at": 10},
        },
    )
    assert later.status_code == 201, later.text
    # Overlapping spans still clash.
    overlapping = auth_client.post(
        f"{w['base']}/facts/{later.json()['id']}/amend",
        json={"expected_revision": head + 2, "starts": {"kind": "at", "at": 5}},
    )
    assert overlapping.status_code == 422

    at_five = auth_client.get(f"{w['base']}/timeline/moment", params={"at": 5}).json()
    places = {
        f["fact"]["object"]["entity_id"]
        for f in at_five["facts"]
        if f["fact"]["predicate"] == "LOCATED_AT"
    }
    assert places == {w["harbor"]}
    at_twelve = auth_client.get(f"{w['base']}/timeline/moment", params={"at": 12}).json()
    places = {
        f["fact"]["object"]["entity_id"]
        for f in at_twelve["facts"]
        if f["fact"]["predicate"] == "LOCATED_AT"
    }
    assert places == {w["saltmere"]}

    spans = auth_client.get(f"{w['base']}/timeline").json()["spans"]
    assert {(s["starts"], s["ends"]) for s in spans} == {(None, "year 10"), ("year 10", None)}


def test_a_fact_span_cannot_end_before_it_starts(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    backwards = auth_client.post(
        f"{w['base']}/facts/{w['knows']}/amend",
        json={
            "expected_revision": head,
            "starts": {"kind": "at", "at": 10},
            "ends": {"kind": "at", "at": 3},
        },
    )
    assert backwards.status_code == 422


def test_dating_an_event_can_be_undone_from_history(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    war = _event(auth_client, w, head, "The War")
    dated = auth_client.patch(
        f"{w['base']}/events/{war['id']}",
        json={"expected_revision": head + 1, "when": {"kind": "at", "at": 100}},
    )
    assert dated.json()["when"]["at"] == 100
    preview = auth_client.post(
        f"{w['base']}/reverts/preview", json={"from_revision": head + 1, "to_revision": None}
    ).json()
    assert preview["operations"] == [
        {"op": "amend_event", "id": war["id"], "when": {"kind": "unknown", "label": None}}
    ]
