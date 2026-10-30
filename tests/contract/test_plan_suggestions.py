"""What a plan change may say about the threads an item moves along, and what a suggested
plan change (``only_fills``) may touch (EXT-001, ADR-023)."""

import json

import pytest

from backend.domain.canon.plans import MAX_THREAD_LINKS
from tests.contract.test_context import _head
from tests.contract.test_plan_checks import _add
from tests.contract.test_reading_events import _threads_world

pytestmark = pytest.mark.contract


def _issues(client, w, operations: list[dict]) -> set[str]:
    proposed = client.post(
        f"{w['base']}/mutations",
        json={"origin": "model", "expected_revision": _head(client, w), "operations": operations},
    )
    assert proposed.status_code == 201, proposed.text
    checked = client.post(f"{w['base']}/mutations/{proposed.json()['id']}/validate")
    assert checked.status_code == 200, checked.text
    return {issue["code"] for issue in checked.json()["issues"]}


def _moves(scene_id: str, thread_ids: list[str]) -> dict:
    return {
        "op": "update_plan_item",
        "id": scene_id,
        "details": {"_advances": json.dumps(thread_ids)},
    }


def test_the_threads_an_item_moves_along_must_be_threads_of_the_plan(auth_client, fake_container):
    w, scene, plan = _threads_world(auth_client, fake_container)
    assert "unknown_plan_thread" in _issues(auth_client, w, [_moves(scene["id"], ["pln_nope"])])
    # A thread cannot move another thread along.
    issues = _issues(auth_client, w, [_moves(plan["debt"]["id"], [plan["guild"]["id"]])])
    assert "invalid_plan_thread_link" in issues
    malformed = {"op": "update_plan_item", "id": scene["id"], "details": {"_advances": "{"}}
    assert "invalid_plan_thread_link" in _issues(auth_client, w, [malformed])
    assert not _issues(auth_client, w, [_moves(scene["id"], [plan["guild"]["id"]])])


def test_an_item_cannot_move_more_threads_along_than_the_limit(auth_client, fake_container):
    w, scene, _plan = _threads_world(auth_client, fake_container)
    many = [
        _add(auth_client, w, "thread", f"Thread {n}")["id"] for n in range(MAX_THREAD_LINKS + 1)
    ]
    assert "invalid_plan_thread_link" in _issues(auth_client, w, [_moves(scene["id"], many)])


@pytest.mark.parametrize(
    "move", [{"position": 3}, {"detach": True}, {"parent_id": "pln_elsewhere"}]
)
def test_a_suggested_plan_change_never_moves_the_item(auth_client, fake_container, move):
    w, scene, _plan = _threads_world(auth_client, fake_container)
    change = {"op": "update_plan_item", "id": scene["id"], "only_fills": True, **move}
    assert "plan_changed_since" in _issues(auth_client, w, [change])
