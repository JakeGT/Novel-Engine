"""The studio's checks against the prose, gathered from the story (STY-005, SCN-007,
CON-004, CON-014), with no model."""

import pytest

from tests.contract.test_context import _head
from tests.contract.test_god_hand import _world
from tests.contract.test_plan_checks import _add, _put
from tests.contract.test_studio import _save

pytestmark = pytest.mark.contract


def _fact(client, w, subject: str, predicate: str, obj: dict) -> str:
    made = client.post(
        f"{w['base']}/facts",
        json={
            "proposition": {"subject_id": subject, "predicate": predicate, "object": obj},
            "expected_revision": _head(client, w),
        },
    )
    assert made.status_code == 201, made.text
    return made.json()["id"]


def _codes(state: dict) -> dict[str, str]:
    return {check["code"]: check["message"] for check in state["checks"]}


def test_the_studio_reads_the_prose_against_the_story(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    _fact(
        auth_client,
        w,
        w["mara"],
        "HAS_DETAIL",
        {"kind": "structured", "value": {"appearance": "Green eyes and cropped black hair."}},
    )
    first = _add(auth_client, w, "scene", "Arrival")
    scene = _add(
        auth_client,
        w,
        "scene",
        "The strongroom",
        refs=[{"entity_id": w["mara"], "role": "point_of_view"}],
    )
    _put(auth_client, w, first["id"], "when", {"when": {"kind": "at", "at": 5}})
    _put(auth_client, w, scene["id"], "when", {"when": {"kind": "at", "at": 5}})
    _put(
        auth_client,
        w,
        scene["id"],
        "conditions",
        {
            "conditions": [
                {
                    "when": "after",
                    "entity_id": w["mara"],
                    "check": {
                        "kind": "has",
                        "pattern": {"predicate": "LOCATED_AT", "other_id": w["saltmere"]},
                    },
                }
            ]
        },
    )
    unit = auth_client.put(f"{w['base']}/timeline/unit", json={"unit": "day"})
    assert unit.status_code in (200, 204), unit.text
    saved = _save(
        auth_client,
        w,
        scene,
        "The next morning, Mara rubbed her blue eyes and looked at the strongroom door.",
        None,
    )
    assert saved.status_code == 200, saved.text
    codes = _codes(saved.json())
    assert "appearance_differs" in codes
    assert (
        "the story bible says: “Green eyes and cropped black hair.”" in codes["appearance_differs"]
    )
    assert "time_words_next_day" in codes
    assert "same day as “Arrival”" in codes["time_words_next_day"]
    assert "after_not_shown" in codes
    assert fake_container.ai.calls == []


def test_the_studio_reads_height_marks_and_age_against_the_story(
    auth_client, fake_container
) -> None:
    """CON-004: a birth placed in story time gives an age; accepted details give height
    and a scar; the prose that says otherwise is a note quoting both."""
    w = _world(auth_client, fake_container)
    _fact(
        auth_client,
        w,
        w["mara"],
        "HAS_DETAIL",
        {"kind": "structured", "value": {"appearance": "Tall, with a scar across her left cheek."}},
    )
    born = auth_client.post(
        f"{w['base']}/events",
        json={
            "kind": "birth",
            "name": "Mara is born",
            "when": {"kind": "at", "at": 5},
            "participants": [{"entity_id": w["mara"], "role": "born"}],
            "expected_revision": _head(auth_client, w),
        },
    )
    assert born.status_code == 201, born.text
    scene = _add(
        auth_client,
        w,
        "scene",
        "The strongroom",
        refs=[{"entity_id": w["mara"], "role": "point_of_view"}],
    )
    _put(auth_client, w, scene["id"], "when", {"when": {"kind": "at", "at": 21}})
    unit = auth_client.put(f"{w['base']}/timeline/unit", json={"unit": "year"})
    assert unit.status_code in (200, 204), unit.text
    saved = _save(
        auth_client,
        w,
        scene,
        "Mara pulled up her hood. Mara was a small woman, and quick. "
        "Mara, nineteen and tired, touched the scar on her right cheek.",
        None,
    )
    assert saved.status_code == 200, saved.text
    codes = _codes(saved.json())
    assert (
        "the story bible says: “Tall, with a scar across her left cheek.”"
        in codes["height_differs"]
    )
    assert "“scar on her right cheek”" in codes["mark_differs"]
    assert codes["age_differs"].startswith("Here Mara is 19 (“Mara, nineteen”), but “Mara is born”")
    assert "so Mara is 15 or 16 here." in codes["age_differs"]
    assert fake_container.ai.calls == []
