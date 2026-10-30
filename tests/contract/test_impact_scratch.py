"""How the plan's conditions and the reader's guide would read after a change, worked out
on a scratch copy of the story with the change applied, before it is approved (CON-017).
Nothing is saved: the story, its version and its guide are as they were."""

import pytest

from tests.contract.test_context import _head
from tests.contract.test_god_hand import _world
from tests.contract.test_impact import _impact, _leave_guild
from tests.contract.test_plan_checks import _add, _put

pytestmark = pytest.mark.contract


def _story(client, w) -> tuple[dict, dict]:
    market = _add(client, w, "scene", "The Market")
    _put(client, w, market["id"], "reveals", {"reveals": [{"fact_id": w["in_guild"]}]})
    quay = _add(client, w, "scene", "The Quay")
    _put(
        client,
        w,
        quay["id"],
        "conditions",
        {
            "conditions": [
                {
                    "when": "before",
                    "entity_id": w["mara"],
                    "check": {
                        "kind": "has",
                        "pattern": {"predicate": "MEMBER_OF", "other_id": w["guild"]},
                    },
                }
            ]
        },
    )
    return market, quay


def test_a_change_shows_how_conditions_and_the_guide_would_read_without_saving(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    market, quay = _story(auth_client, w)
    head = _head(auth_client, w)
    journey = auth_client.get(f"{w['base']}/reader").json()
    mutation_id = _leave_guild(auth_client, w)

    impact = _impact(auth_client, w, f"mutations/{mutation_id}")
    assert impact["scratch"] is True
    (condition,) = [item for item in impact["after_change"] if item["kind"] == "condition"]
    assert condition["plan_id"] == quay["id"] and condition["title"] == "The Quay"
    assert (condition["now"], condition["after"]) == ("met", "not met")
    assert "would no longer be met" in condition["words"]
    reader = [item for item in impact["after_change"] if item["kind"] == "reader"]
    assert any(
        item["plan_id"] == market["id"]
        and item["words"] == "The reader would no longer learn: Mara is a member of Harbor Guild"
        for item in reader
    ), reader

    # Nothing was saved: the story's version, its facts and the guide are as they were.
    assert _head(auth_client, w) == head
    facts = auth_client.get(f"{w['base']}/facts").json()
    assert any(fact["id"] == w["in_guild"] for fact in facts["items"])
    assert auth_client.get(f"{w['base']}/reader").json() == journey


def test_a_new_scene_and_a_new_fact_read_on_the_copy(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    _market, quay = _story(auth_client, w)
    proposed = auth_client.post(
        f"{w['base']}/mutations",
        json={
            "origin": "model",
            "expected_revision": _head(auth_client, w),
            "operations": [
                {
                    "op": "assert_fact",
                    "id": "tmp:f",
                    "proposition": {
                        "subject_id": w["tobin"],
                        "predicate": "MEMBER_OF",
                        "object": {
                            "kind": "entity_ref",
                            "entity_id": w["guild"],
                            "project_id": w["base"].rsplit("/", 1)[-1],
                        },
                    },
                },
                {
                    "op": "create_plan_item",
                    "id": "tmp:s",
                    "kind": "scene",
                    "title": "The Oath",
                    "position": 9,
                    "details": {"_reveals": '[{"fact_id": "tmp:f", "how": "hinted"}]'},
                },
            ],
        },
    )
    assert proposed.status_code == 201, proposed.text
    impact = _impact(auth_client, w, f"mutations/{proposed.json()['id']}")
    reader = {item["words"] for item in impact["after_change"] if item["kind"] == "reader"}
    assert "It would come into the reading order" in reader
    assert "The reader would be given a hint of: Tobin Ashe is a member of Harbor Guild" in reader
    # The quay's condition is about Mara: it reads the same.
    assert not [item for item in impact["after_change"] if item["plan_id"] == quay["id"]]


def test_an_entry_page_preview_has_no_scratch_copy(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    impact = _impact(auth_client, w, f"entities/{w['mara']}")
    assert impact["scratch"] is False and impact["after_change"] == []
