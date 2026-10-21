"""Character arc paths over HTTP, with no model (STY-006)."""

import pytest

from tests.contract.test_context import _head
from tests.contract.test_god_hand import _world
from tests.contract.test_plan_checks import _add, _outline, _put

pytestmark = pytest.mark.contract


def _arc(client, w) -> dict:
    return _add(
        client,
        w,
        "arc",
        "Learning to trust",
        refs=[{"entity_id": w["mara"], "role": "about"}],
    )


def test_an_arc_path_is_saved_read_back_and_checked_without_a_model(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    one = _add(auth_client, w, "scene", "One")
    two = _add(auth_client, w, "scene", "Two")
    arc = _arc(auth_client, w)
    saved = _put(
        auth_client,
        w,
        arc["id"],
        "path",
        {
            "stages": [
                {"name": "Trusts no one"},
                {"name": "Trusts Tobin", "scene_id": two["id"], "fact_id": w["knows"]},
                {"name": "Doubts", "scene_id": one["id"]},
                {"name": "Forgives"},
            ]
        },
    )
    assert [s["name"] for s in saved["path"]] == [
        "Trusts no one",
        "Trusts Tobin",
        "Doubts",
        "Forgives",
    ]
    # Kept apart from the arc's own notes.
    assert saved["details"] == {}

    outline = _outline(auth_client, w)
    [reading] = outline["arcs"]
    assert reading["arc_id"] == arc["id"]
    assert reading["about"] == [w["mara"]]
    assert [(s["name"], s["scene_number"], s["fact"]) for s in reading["stages"]] == [
        ("Trusts no one", None, None),
        ("Trusts Tobin", 2, "holds"),
        ("Doubts", 1, None),
        ("Forgives", None, None),
    ]
    flagged = [(o["plan_id"], o["code"], o["kind"]) for o in outline["open"]]
    assert (arc["id"], "arc_out_of_order", "problem") in flagged
    assert (arc["id"], "arc_stage_not_placed", "gap") in flagged
    # An arc told as stages is not asked for beats.
    assert (arc["id"], "needs_beats", "gap") not in flagged
    assert fake_container.ai.calls == []


def test_a_stage_whose_fact_is_not_true_when_its_scene_happens_is_flagged(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    scene = _add(auth_client, w, "scene", "The Reckoning")
    arc = _arc(auth_client, w)
    _put(
        auth_client,
        w,
        arc["id"],
        "path",
        {"stages": [{"name": "Settled", "scene_id": scene["id"], "fact_id": w["at_harbor"]}]},
    )
    _put(auth_client, w, scene["id"], "when", {"when": {"kind": "at", "at": 5}})
    ends = auth_client.post(
        f"{w['base']}/facts/{w['at_harbor']}/amend",
        json={"expected_revision": _head(auth_client, w), "ends": {"kind": "at", "at": 3}},
    )
    assert ends.status_code == 200, ends.text
    outline = _outline(auth_client, w)
    assert outline["arcs"][0]["stages"][0]["fact"] == "not_then"
    [flag] = [o for o in outline["open"] if o["code"] == "arc_fact_not_true"]
    assert flag["message"] == (
        "“Settled” should hold from here on, but “Mara is located at Lantern Harbor” is not "
        "true when “The Reckoning” happens."
    )


def test_bad_paths_are_refused_in_plain_words(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    scene = _add(auth_client, w, "scene", "One")
    arc = _arc(auth_client, w)

    def put(plan_id: str, stages: list[dict]):
        return auth_client.put(
            f"{w['base']}/plan/{plan_id}/path",
            json={"stages": stages, "expected_revision": _head(auth_client, w)},
        )

    not_arc = put(scene["id"], [{"name": "A"}])
    assert not_arc.status_code == 400
    assert "Only an arc" in not_arc.text
    assert "own name" in put(arc["id"], [{"name": "A"}, {"name": "a"}]).text
    assert "scene of the plan" in put(arc["id"], [{"name": "A", "scene_id": arc["id"]}]).text
    assert "fact that is in the story" in put(arc["id"], [{"name": "A", "fact_id": "fct_x"}]).text
    assert put(arc["id"], [{"name": str(n)} for n in range(10)]).status_code == 422


def test_removing_a_scene_unplaces_its_stage_and_drafting_is_told_the_change(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    scene = _add(
        auth_client,
        w,
        "scene",
        "The Bridge",
        refs=[{"entity_id": w["mara"], "role": "point_of_view"}],
    )
    other = _add(auth_client, w, "scene", "Elsewhere")
    arc = _arc(auth_client, w)
    _put(
        auth_client,
        w,
        arc["id"],
        "path",
        {
            "stages": [
                {"name": "Trusts no one"},
                {"name": "Trusts Tobin", "scene_id": scene["id"], "fact_id": w["knows"]},
                {"name": "Leaves", "scene_id": other["id"]},
            ]
        },
    )
    context = auth_client.get(f"{w['base']}/plan/{scene['id']}/context").json()
    [given] = [i for i in context["items"] if i["key"] == f"arc:{scene['id']}:{arc['id']}"]
    assert given["text"] == (
        "Mara's arc “Learning to trust”: going in, Mara is at “Trusts no one”; in this "
        "scene, Mara reaches “Trusts Tobin”. From here on: Mara knows Tobin Ashe."
    )
    assert given["hard"] is True
    # A scene with no viewpoint is not told someone else's arc.
    elsewhere = auth_client.get(f"{w['base']}/plan/{other['id']}/context").json()
    assert not [i for i in elsewhere["items"] if i["key"].startswith("arc:")]

    removed = auth_client.post(
        f"{w['base']}/plan/{other['id']}/remove",
        json={"expected_revision": _head(auth_client, w)},
    )
    assert removed.status_code == 200, removed.text
    outline = _outline(auth_client, w)
    stages = next(i for i in outline["items"] if i["id"] == arc["id"])["path"]
    assert stages[2] == {
        "name": "Leaves",
        "scene_id": None,
        "fact_id": None,
        "scale_id": None,
        "level": None,
    }
    assert not [o for o in outline["open"] if o["code"] == "arc_stage_scene_gone"]


def test_an_arc_can_be_started_with_its_stages_in_one_change(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    scene = _add(auth_client, w, "scene", "One")
    head = _head(auth_client, w)
    arc = _add(
        auth_client,
        w,
        "arc",
        "Mara's arc",
        refs=[{"entity_id": w["mara"], "role": "about"}],
        path=[{"name": "Alone"}, {"name": "Together", "scene_id": scene["id"]}],
    )
    assert [s["name"] for s in arc["path"]] == ["Alone", "Together"]
    assert _head(auth_client, w) == head + 1
    # Stages belong to arcs only, even when adding.
    refused = auth_client.post(
        f"{w['base']}/plan",
        json={
            "kind": "scene",
            "title": "Two",
            "path": [{"name": "A"}],
            "expected_revision": _head(auth_client, w),
        },
    )
    assert refused.status_code == 400


def test_a_stage_tied_to_a_secret_the_viewpoint_lacks_stays_the_authors(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    scene = _add(
        auth_client,
        w,
        "scene",
        "The Bridge",
        refs=[{"entity_id": w["mara"], "role": "point_of_view"}],
    )
    arc = _arc(auth_client, w)
    _put(
        auth_client,
        w,
        arc["id"],
        "path",
        {"stages": [{"name": "Trusts Tobin", "scene_id": scene["id"], "fact_id": w["knows"]}]},
    )
    secret = auth_client.post(
        f"{w['base']}/facts/{w['knows']}/amend",
        json={"expected_revision": _head(auth_client, w), "visibility": "secret"},
    )
    assert secret.status_code == 200, secret.text
    key = f"arc:{scene['id']}:{arc['id']}"

    context = auth_client.get(f"{w['base']}/plan/{scene['id']}/context").json()
    [given] = [i for i in context["items"] if i["key"] == key]
    assert "From here on" not in given["text"]
    assert not [i for i in context["items"] if "From here on" in i["text"]]
    [left] = [o for o in context["left_out"] if o["item"]["key"] == f"{key}:fact"]
    assert left["reason"] == "secret" and left["item"]["section"] == "secrets"

    shown = auth_client.get(
        f"{w['base']}/plan/{scene['id']}/context", params={"secrets": "true"}
    ).json()
    [kept] = [i for i in shown["items"] if i["key"] == f"{key}:fact"]
    assert kept["text"].startswith("From here on: Mara knows Tobin Ashe.")
    assert "Mara does not know this" in kept["text"]


def test_a_path_sent_as_a_raw_change_is_checked_like_one_saved_directly(
    auth_client, fake_container
) -> None:
    import json

    w = _world(auth_client, fake_container)
    scene = _add(auth_client, w, "scene", "One")
    arc = _arc(auth_client, w)

    def validate(plan_id: str, path) -> dict:
        proposed = auth_client.post(
            f"{w['base']}/mutations",
            json={
                "origin": "model",
                "expected_revision": _head(auth_client, w),
                "operations": [
                    {
                        "op": "update_plan_item",
                        "id": plan_id,
                        "details": {"_path": path if isinstance(path, str) else json.dumps(path)},
                    }
                ],
            },
        )
        assert proposed.status_code == 201, proposed.text
        report = auth_client.post(f"{w['base']}/mutations/{proposed.json()['id']}/validate")
        return report.json()

    def problems(report: dict) -> list[str]:
        return [i["message"] for i in report["issues"] if i["severity"] == "blocking"]

    assert problems(validate(scene["id"], [{"name": "A"}])) == ["only an arc can have stages"]
    assert problems(validate(arc["id"], "not a list")) == ["an arc's stages could not be read"]
    assert problems(validate(arc["id"], [{"name": "A"}, {"name": "a"}])) == [
        "each stage needs its own name"
    ]
    assert problems(validate(arc["id"], [{"name": str(n)} for n in range(10)])) == [
        "an arc can have at most 9 stages"
    ]
    assert problems(validate(arc["id"], [{"name": "A", "scene_id": arc["id"]}])) == [
        "a stage can only be reached in a scene of the plan"
    ]
    assert problems(validate(arc["id"], [{"name": "A", "fact_id": "fct_x"}])) == [
        "a stage can only be tied to a fact that is in the story"
    ]
    good = validate(arc["id"], [{"name": "A"}, {"name": "B", "scene_id": scene["id"]}])
    assert good["ok"] is True, good
