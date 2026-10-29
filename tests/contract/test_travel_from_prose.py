"""Travel read from a scene's words in the studio (SCN-007, CON-004): the words taking a
person of the scene away from the plan's place, or out of a status that holds them, are
notes quoting the words; continuity across story time notes a lasting status a scene
does not fit."""

import pytest

from tests.contract.test_context import _head, _scene
from tests.contract.test_god_hand import _world
from tests.contract.test_studio import _save

pytestmark = pytest.mark.contract


# Kinds of fact of the story's own that take a time: where someone is held, and a condition.
KINDS = {
    "label": "Lasting statuses",
    "entity_extensions": [],
    "predicate_extensions": [
        {
            "predicate": "IMPRISONED_AT",
            "subject_types": ["Character"],
            "object_kind": "entity_ref",
            "object_types": ["Location"],
            "cardinality": "one",
            "temporal_allowed": True,
        },
        {
            "predicate": "CONDITION",
            "subject_types": ["Character"],
            "object_kind": "string",
            "cardinality": "one",
            "temporal_allowed": True,
        },
    ],
}


def _kinds(client, w) -> None:
    project_id = w["base"].rsplit("/", 1)[1]
    draft = client.post(f"/api/v1/projects/{project_id}/schemas/drafts", json={"definition": KINDS})
    assert draft.status_code == 201, draft.text
    version = draft.json()["version"]["id"]
    published = client.post(f"/api/v1/projects/{project_id}/schemas/{version}/publish")
    assert published.status_code == 200, published.text


def _fact(client, w, predicate: str, obj: dict, *, starts: int) -> str:
    made = client.post(
        f"{w['base']}/facts",
        json={
            "proposition": {"subject_id": w["mara"], "predicate": predicate, "object": obj},
            "starts": {"kind": "at", "at": starts},
            "expected_revision": _head(client, w),
        },
    )
    assert made.status_code == 201, made.text
    return str(made.json()["id"])


def _held(client, w, *, starts: int) -> str:
    project_id = w["base"].rsplit("/", 1)[1]
    place = {"kind": "entity_ref", "entity_id": w["harbor"], "project_id": project_id}
    return _fact(client, w, "IMPRISONED_AT", place, starts=starts)


def _at(client, w, scene, when: int) -> None:
    timed = client.put(
        f"{w['base']}/plan/{scene['id']}/when",
        json={"when": {"kind": "at", "at": when}, "expected_revision": _head(client, w)},
    )
    assert timed.status_code == 200, timed.text


def _checks(state: dict, *codes: str) -> list[dict]:
    return [check for check in state["checks"] if check["code"] in codes]


def test_the_words_taking_someone_elsewhere_or_out_of_prison_are_notes(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _scene(
        auth_client, w, [("tobin", "point_of_view"), ("harbor", "place"), ("mara", "participant")]
    )
    _at(auth_client, w, scene, 5)
    _kinds(auth_client, w)
    status = _held(auth_client, w, starts=1)
    prose = "Tobin watched the water. Mara rode to Saltmere. “Mara walked to Saltmere,” he said."
    state = _save(auth_client, w, scene, prose, None).json()
    elsewhere = _checks(state, "moves_elsewhere")
    assert len(elsewhere) == 1 and elsewhere[0]["severity"] == "note"
    assert "the plan places this scene at Lantern Harbor" in elsewhere[0]["message"]
    assert prose[elsewhere[0]["start"] : elsewhere[0]["end"]] == "Mara rode to Saltmere"
    (held,) = _checks(state, "moves_while_held")
    assert "Lantern Harbor" in held["message"] and "nothing has freed them" in held["message"]
    # She is held where the scene is: the records alone say nothing about it.
    continuity = auth_client.get(f"{w['base']}/timeline/checks").json()["findings"]
    assert not [item for item in continuity if item["code"] == "held_elsewhere"]

    # Ended before the scene: nothing holds her.
    amended = auth_client.post(
        f"{w['base']}/facts/{status}/amend",
        json={"expected_revision": _head(auth_client, w), "ends": {"kind": "at", "at": 3}},
    )
    assert amended.status_code == 200, amended.text
    again = _save(auth_client, w, scene, prose + " Then quiet.", state["version_id"]).json()
    assert not _checks(again, "moves_while_held")
    assert _checks(again, "moves_elsewhere")


def test_continuity_notes_a_scene_that_does_not_fit_a_lasting_status(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _scene(
        auth_client, w, [("tobin", "point_of_view"), ("harbor", "place"), ("mara", "participant")]
    )
    _at(auth_client, w, scene, 5)
    _kinds(auth_client, w)
    _fact(auth_client, w, "CONDITION", {"kind": "string", "value": "missing"}, starts=2)
    found = auth_client.get(f"{w['base']}/timeline/checks").json()
    (note,) = [item for item in found["findings"] if item["code"] == "missing_seen"]
    assert note["severity"] == "note" and "with Tobin Ashe" in note["message"]
    # Said once, exactly: the plain "should show it" note is not said for this scene too.
    assert not [item for item in found["findings"] if item["code"] == "condition_then"]
    assert any(item["check"] == "lifecycle" for item in found["coverage"])
