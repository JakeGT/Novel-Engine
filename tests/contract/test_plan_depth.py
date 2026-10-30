"""Threads that move along between opening and resolving, and what a scene's viewpoint wants.

STY-002 (thread progress) and STY-003 (scene intent): kept in plan details, edited by
hand, shown on the outline, and given to the assistant's context. No model is involved.
"""

import pytest

from tests.contract.test_plan import Plan

pytestmark = pytest.mark.contract


def _change(plan: Plan, plan_id: str, **fields):
    got = plan.client.patch(
        f"{plan.base}/plan/{plan_id}", json={"expected_revision": plan.head, **fields}
    )
    if got.status_code == 200:
        plan.head += 1
    return got


def _steps(plan: Plan, thread_id: str) -> list[tuple[str, str]]:
    (found,) = [t for t in plan.outline()["threads"] if t["thread_id"] == thread_id]
    return [(step["plan_id"], step["step"]) for step in found["steps"]]


def test_a_thread_shows_where_it_opens_moves_along_and_resolves_in_outline_order(
    auth_client, fake_container
) -> None:
    plan = Plan(auth_client, fake_container)
    thread = plan.add("thread", "Who drew the false chart?")
    arc = plan.add("arc", "Ines learns the truth")
    first = plan.add("scene", "The lamp room", parent_id=arc["id"], sets_up=[thread["id"]])
    middle = plan.add("scene", "The harbour office", parent_id=arc["id"])
    last = plan.add("scene", "The father's study", parent_id=arc["id"], pays_off=[thread["id"]])

    # Opened and resolved with a scene between and nothing moving it: a gap, not an error.
    assert plan.open_for(thread["id"]) == ["needs_progress"]
    message = next(i for i in plan.outline()["open"] if i["code"] == "needs_progress")["message"]
    assert message == "This thread opens and resolves, but no scene in between moves it along."

    moved = _change(plan, middle["id"], advances=[thread["id"]])
    assert moved.status_code == 200, moved.text
    assert moved.json()["advances"] == [thread["id"]]
    # Kept as a reserved part of the scene, never shown among its own notes.
    assert moved.json()["details"] == {}
    assert plan.open_for(thread["id"]) == []
    assert _steps(plan, thread["id"]) == [
        (first["id"], "opens"),
        (middle["id"], "advances"),
        (last["id"], "resolves"),
    ]

    # Reordering the outline reorders the steps.
    reorder = auth_client.post(
        f"{plan.base}/plan/{last['id']}/move",
        json={"parent_id": arc["id"], "before_id": middle["id"], "expected_revision": plan.head},
    )
    assert reorder.status_code == 200, reorder.text
    plan.head += 1
    assert [step for _, step in _steps(plan, thread["id"])] == ["opens", "resolves", "advances"]

    # Editing the scene's notes keeps what it moves along.
    noted = _change(plan, middle["id"], details={"stakes": "the licence"})
    assert noted.status_code == 200, noted.text
    assert noted.json()["advances"] == [thread["id"]]


def test_scenes_next_to_each_other_need_nothing_between(auth_client, fake_container) -> None:
    plan = Plan(auth_client, fake_container)
    thread = plan.add("thread", "Will the lamp be lit?")
    plan.add("scene", "Dusk", sets_up=[thread["id"]])
    plan.add("scene", "Night", pays_off=[thread["id"]])
    assert plan.open_for(thread["id"]) == []


def test_moving_a_thread_along_is_checked_and_said_plainly(auth_client, fake_container) -> None:
    plan = Plan(auth_client, fake_container)
    thread = plan.add("thread", "Who drew the false chart?")
    other = plan.add("thread", "Is the father lying?")
    scene = plan.add("scene", "The lamp room", sets_up=[thread["id"]])

    for fields, words in [
        ({"advances": [thread["id"]]}, "list it in one place"),
        ({"advances": ["plan_item_missing"]}, "Only a thread in the plan"),
        ({"advances": [other["id"], other["id"]]}, "listed once"),
    ]:
        refused = _change(plan, scene["id"], **fields)
        assert refused.status_code == 400, refused.text
        assert words in refused.text

    as_thread = _change(plan, other["id"], advances=[thread["id"]])
    assert as_thread.status_code == 400
    assert "A thread cannot move another thread along" in as_thread.text

    # A new beat or scene can move a thread along from the start.
    beat = plan.add("beat", "The doubt grows", advances=[other["id"]])
    assert beat["advances"] == [other["id"]]


def test_opening_or_resolving_a_thread_already_moved_along_is_refused(
    auth_client, fake_container
) -> None:
    plan = Plan(auth_client, fake_container)
    thread = plan.add("thread", "Who drew the false chart?")
    scene = plan.add("scene", "The lamp room", advances=[thread["id"]])

    for fields in ({"sets_up": [thread["id"]]}, {"pays_off": [thread["id"]]}):
        refused = _change(plan, scene["id"], **fields)
        assert refused.status_code == 400, refused.text
        assert "list it in one place" in refused.text
    # Moving it along no longer, and opening it instead, is one edit and fine.
    moved = _change(plan, scene["id"], sets_up=[thread["id"]], advances=[])
    assert moved.status_code == 200, moved.text
    assert moved.json()["sets_up"] == [thread["id"]] and moved.json()["advances"] == []


def test_removing_a_thread_takes_it_off_whatever_moved_it_along(auth_client, fake_container):
    plan = Plan(auth_client, fake_container)
    thread = plan.add("thread", "Who drew the false chart?")
    scene = plan.add("scene", "The lamp room", advances=[thread["id"]], details={"mood": "cold"})
    removed = auth_client.post(
        f"{plan.base}/plan/{thread['id']}/remove", json={"expected_revision": plan.head}
    )
    assert removed.status_code == 200, removed.text
    plan.head += 1
    (after,) = [item for item in plan.outline()["items"] if item["id"] == scene["id"]]
    assert after["advances"] == [] and after["details"] == {"mood": "cold"}


def test_a_scene_says_what_its_viewpoint_wants_what_is_in_the_way_and_how_it_ends(
    auth_client, fake_container
) -> None:
    plan = Plan(auth_client, fake_container)
    scene = plan.add(
        "scene",
        "The lamp room",
        intent={"goal": "  find the  true chart ", "outcome": "yes_but"},
    )
    assert scene["intent"] == {"goal": "find the true chart", "obstacle": "", "outcome": "yes_but"}
    assert scene["details"] == {}

    changed = _change(
        plan,
        scene["id"],
        intent={
            "goal": "find the true chart",
            "obstacle": "her father keeps the key",
            "outcome": "no_and",
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["intent"]["obstacle"] == "her father keeps the key"
    # Other edits leave it alone; an empty one clears it.
    kept = _change(plan, scene["id"], summary="Ines searches the lamp room.")
    assert kept.json()["intent"]["outcome"] == "no_and"
    cleared = _change(plan, scene["id"], intent={"goal": "", "obstacle": "", "outcome": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["intent"] is None

    # Only a scene has one, and the outcome is one of the four.
    arc = plan.add("arc", "Ines learns the truth")
    refused = _change(plan, arc["id"], intent={"goal": "win"})
    assert refused.status_code == 400 and "Only a scene" in refused.text
    wrong = _change(plan, scene["id"], intent={"outcome": "maybe"})
    assert wrong.status_code == 422
