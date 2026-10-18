"""Continuity across story time and place over HTTP, with no model (CON-003, CON-004)."""

import pytest

from tests.contract.test_context import _head
from tests.contract.test_god_hand import _world

pytestmark = pytest.mark.contract


def _scene(client, w, title: str, at: float, place: str, who: list[str]) -> dict:
    made = client.post(
        f"{w['base']}/plan",
        json={
            "kind": "scene",
            "title": title,
            "refs": [
                {"entity_id": w[place], "role": "place"},
                *[{"entity_id": w[item], "role": "participant"} for item in who],
            ],
            "expected_revision": _head(client, w),
        },
    )
    assert made.status_code == 201, made.text
    timed = client.put(
        f"{w['base']}/plan/{made.json()['id']}/when",
        json={"when": {"kind": "at", "at": at}, "expected_revision": _head(client, w)},
    )
    assert timed.status_code == 200, timed.text
    return made.json()


def _checks(client, w) -> dict:
    got = client.get(f"{w['base']}/timeline/checks")
    assert got.status_code == 200, got.text
    return got.json()


def _codes(client, w) -> list[str]:
    return [item["code"] for item in _checks(client, w)["findings"]]


def test_two_places_at_once_shows_on_the_timeline_the_plan_and_the_studio(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    # Counted in hours, hour 3 is one moment (in years, year 3 is the whole year).
    assert auth_client.put(f"{w['base']}/timeline/unit", json={"unit": "hour"}).status_code == 200
    first = _scene(auth_client, w, "Low tide", 3, "harbor", ["tobin"])
    second = _scene(auth_client, w, "Salt roads", 3, "saltmere", ["tobin"])

    checks = _checks(auth_client, w)
    (finding,) = checks["findings"]
    assert checks["not_shown"] == 0
    assert finding["code"] == "two_places_at_once" and finding["severity"] == "warning"
    assert set(finding["scene_ids"]) == {first["id"], second["id"]}
    kinds = {(record["kind"], record["label"]) for record in finding["records"]}
    assert ("entry", "Tobin Ashe") in kinds and ("scene", "Low tide") in kinds
    # Every check says what it covers, and none of them blocks.
    assert len(checks["coverage"]) == 9 and "nothing here stops you" in checks["always"]

    outline = auth_client.get(f"{w['base']}/plan").json()
    flagged = [item for item in outline["open"] if item["code"] == "two_places_at_once"]
    assert {item["plan_id"] for item in flagged} == {first["id"], second["id"]}
    assert all(item["kind"] == "problem" and item["records"] for item in flagged)

    studio = auth_client.get(f"{w['base']}/plan/{first['id']}/studio").json()
    (check,) = [c for c in studio["checks"] if c["code"] == "continuity_two_places_at_once"]
    assert check["severity"] == "warning" and check["key"]
    saved = auth_client.put(
        f"{w['base']}/plan/{first['id']}/studio/text",
        json={"content": "Tobin waited at Lantern Harbor.", "base_version_id": None},
    )
    assert saved.status_code == 200, saved.text
    assert any(c["code"] == "continuity_two_places_at_once" for c in saved.json()["checks"])


def test_travel_times_are_the_authors_own_and_saved_with_a_version(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    _scene(auth_client, w, "Leaving", 1, "harbor", ["tobin"])
    _scene(auth_client, w, "Arriving", 2, "saltmere", ["tobin"])
    assert _codes(auth_client, w) == []

    empty = auth_client.get(f"{w['base']}/timeline/how-long").json()
    assert empty == {"travel": [], "lasting": [], "version": 0}
    table = {
        "travel": [{"from_id": w["harbor"], "to_id": w["saltmere"], "amount": 3}],
        "lasting": [],
        "expected_version": 0,
    }
    saved = auth_client.put(f"{w['base']}/timeline/how-long", json=table)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    assert _codes(auth_client, w) == ["too_little_travel_time"]

    # Saved by someone else first: refused, not overwritten.
    stale = auth_client.put(f"{w['base']}/timeline/how-long", json=table)
    assert stale.status_code == 409
    # Only places in the story bible, each pair once.
    wrong = {**table, "travel": [{"from_id": w["mara"], "to_id": w["harbor"], "amount": 1}]}
    assert (
        auth_client.put(
            f"{w['base']}/timeline/how-long", json={**wrong, "expected_version": 1}
        ).status_code
        == 400
    )
    twice = {
        "travel": [
            {"from_id": w["harbor"], "to_id": w["saltmere"], "amount": 3},
            {"from_id": w["saltmere"], "to_id": w["harbor"], "amount": 2},
        ],
        "expected_version": 1,
    }
    refused = auth_client.put(f"{w['base']}/timeline/how-long", json=twice)
    assert refused.status_code == 400
    assert "Each pair of places has one travel time." in refused.text
    assert "different places" not in refused.text

    # A place inside the other: never two places at once, never a journey.
    inside = {
        "travel": [{"from_id": w["harbor"], "to_id": w["saltmere"], "amount": 0}],
        "expected_version": 1,
    }
    assert auth_client.put(f"{w['base']}/timeline/how-long", json=inside).status_code == 200
    assert _codes(auth_client, w) == []


def test_a_participant_after_their_recorded_death(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    died = auth_client.post(
        f"{w['base']}/events",
        json={
            "kind": "death",
            "name": "Tobin drowns",
            "participants": [{"entity_id": w["tobin"], "role": "participant"}],
            "when": {"kind": "at", "at": 5},
            "expected_revision": _head(auth_client, w),
        },
    )
    assert died.status_code == 201, died.text
    late = _scene(auth_client, w, "The wake", 9, "harbor", ["tobin"])
    (finding,) = _checks(auth_client, w)["findings"]
    assert finding["code"] == "after_death" and finding["scene_ids"] == [late["id"]]
    assert "Tobin drowns" in finding["message"]
    assert any(record["kind"] == "event" for record in finding["records"])


def test_a_length_the_author_gives_an_event_is_kept_not_checked(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    siege = auth_client.post(
        f"{w['base']}/events",
        json={
            "kind": "happening",
            "name": "The siege",
            "when": {"kind": "between", "earliest": 10, "latest": 12},
            "expected_revision": _head(auth_client, w),
        },
    ).json()
    saved = auth_client.put(
        f"{w['base']}/timeline/how-long",
        json={
            "lasting": [{"subject_kind": "event", "subject_id": siege["id"], "amount": 5}],
            "expected_version": 0,
        },
    )
    assert saved.status_code == 200, saved.text
    # Between year 10 and 12 says when it happens, not that it lasts two years.
    assert _codes(auth_client, w) == []
    unknown = auth_client.put(
        f"{w['base']}/timeline/how-long",
        json={
            "lasting": [{"subject_kind": "fact", "subject_id": "nope", "amount": 5}],
            "expected_version": 1,
        },
    )
    assert unknown.status_code == 400


def test_rows_another_line_added_are_kept_when_this_line_saves(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    first = auth_client.put(
        f"{w['base']}/timeline/how-long", json={"travel": [], "expected_version": 0}
    )
    assert first.status_code == 200, first.text
    (stored_key,) = fake_container.story_clocks.how_long
    # A what-if added a journey to a place this line does not have.
    what_if_row = {"from_id": w["harbor"], "to_id": "ent_only_in_the_what_if", "amount": 4}
    fake_container.story_clocks.how_long[stored_key] = ({"travel": [what_if_row]}, 1)
    shown = auth_client.get(f"{w['base']}/timeline/how-long").json()
    assert shown["travel"] == [what_if_row]

    new_row = {"from_id": w["harbor"], "to_id": w["saltmere"], "amount": 2}
    saved = auth_client.put(
        f"{w['base']}/timeline/how-long",
        json={"travel": [{**what_if_row, "amount": 5}, new_row], "expected_version": 1},
    )
    assert saved.status_code == 200, saved.text
    assert len(saved.json()["travel"]) == 2
    # Something new this line does not have is still refused, and said plainly.
    unknown = {"from_id": w["saltmere"], "to_id": "ent_nowhere", "amount": 1}
    refused = auth_client.put(
        f"{w['base']}/timeline/how-long",
        json={"travel": [what_if_row, new_row, unknown], "expected_version": 2},
    )
    assert refused.status_code == 400
    assert "A journey from Saltmere names a place that is not in the story bible." in refused.text
    not_a_place = {"from_id": w["saltmere"], "to_id": w["mara"], "amount": 1}
    refused = auth_client.put(
        f"{w['base']}/timeline/how-long",
        json={"travel": [not_a_place], "expected_version": 2},
    )
    assert "names Mara, which is not a place." in refused.text


@pytest.mark.asyncio
async def test_the_table_travels_in_an_archive_and_never_overwrites_one(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    table = {"travel": [{"from_id": w["harbor"], "to_id": w["saltmere"], "amount": 2}]}
    saved = auth_client.put(f"{w['base']}/timeline/how-long", json={**table, "expected_version": 0})
    assert saved.status_code == 200, saved.text
    archive = auth_client.post(f"{w['base']}/archive/export").json()
    assert archive["story_time"]["how_long"]["travel"][0]["amount"] == 2

    workspace_id = archive["project"]["workspace_id"]
    await fake_container.archive_service.import_project(workspace_id, "restored", archive)
    restored, version = await fake_container.story_clocks.get_how_long(
        workspace_id=workspace_id, project_id="restored"
    )
    assert version == 1 and restored["travel"][0]["to_id"] == w["saltmere"]


def test_findings_past_the_cap_are_counted_on_the_timeline_and_the_plan(
    auth_client, fake_container, monkeypatch
):
    import backend.domain.continuity as continuity

    w = _world(auth_client, fake_container)
    assert auth_client.put(f"{w['base']}/timeline/unit", json={"unit": "hour"}).status_code == 200
    first = _scene(auth_client, w, "Low tide", 3, "harbor", ["tobin"])
    _scene(auth_client, w, "Salt roads", 3, "saltmere", ["tobin"])
    monkeypatch.setattr(continuity, "MAX_WARNINGS", 0)

    checks = _checks(auth_client, w)
    assert checks["findings"] == [] and checks["not_shown"] == 1
    outline = auth_client.get(f"{w['base']}/plan").json()
    (more,) = [item for item in outline["open"] if item["code"] == "continuity_not_shown"]
    assert more["plan_id"] == first["id"] and "1 more is not shown" in more["message"]
