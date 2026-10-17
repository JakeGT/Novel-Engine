"""What-ifs: try a turn of the story without touching it, compare, and bring it back (ADR-020)."""

import pytest

from tests.contract.test_god_hand import _world

pytestmark = pytest.mark.contract


def _start(client, w, name="What if Mara refused?") -> dict:
    made = client.post(f"{w['base']}/what-ifs", json={"name": name})
    assert made.status_code == 201, made.text
    return made.json()


def _in(branch_id: str) -> dict:
    return {"X-Story-Branch": branch_id}


def _names(client, w, branch_id: str | None = None) -> set[str]:
    headers = _in(branch_id) if branch_id else {}
    items = client.get(f"{w['base']}/entities", headers=headers).json()["items"]
    return {item["name"] for item in items}


def _head(client, w, branch_id: str | None = None) -> int:
    headers = _in(branch_id) if branch_id else {}
    status = client.get(f"{w['base']}/projection", headers=headers).json()
    return int(status["head_revision"])


def test_a_what_if_starts_as_the_story_and_changes_in_it_stay_in_it(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    what_if = _start(auth_client, w)
    assert what_if["started_at_version"] == head and what_if["changes"] == 0
    assert _names(auth_client, w, what_if["id"]) == _names(auth_client, w)

    # A change made in the what-if.
    added = auth_client.post(
        f"{w['base']}/entities",
        json={"type": "Character", "name": "Ines", "expected_revision": head},
        headers=_in(what_if["id"]),
    )
    assert added.status_code == 201, added.text
    assert "Ines" in _names(auth_client, w, what_if["id"])
    assert "Ines" not in _names(auth_client, w)

    # A change made in the main story after the what-if started.
    main_added = auth_client.post(
        f"{w['base']}/entities",
        json={"type": "Character", "name": "Oren", "expected_revision": head},
    )
    assert main_added.status_code == 201, main_added.text
    assert "Oren" in _names(auth_client, w)
    assert "Oren" not in _names(auth_client, w, what_if["id"])

    listed = auth_client.get(f"{w['base']}/what-ifs").json()["items"]
    assert [(item["name"], item["changes"]) for item in listed] == [("What if Mara refused?", 1)]


def test_a_what_if_of_another_story_is_not_found_rather_than_the_main_story(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    missing = auth_client.get(f"{w['base']}/entities", headers=_in("branch_not_here"))
    assert missing.status_code == 404
    assert "what-if" in missing.json()["detail"]


def test_names_must_be_given_and_not_repeat(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    _start(auth_client, w, "Storm")
    again = auth_client.post(f"{w['base']}/what-ifs", json={"name": "storm"})
    assert again.status_code == 409
    blank = auth_client.post(f"{w['base']}/what-ifs", json={"name": "   "})
    assert blank.status_code == 400


def test_comparing_lists_each_side_and_what_both_changed(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    what_if = _start(auth_client, w)
    # Both sides rename Tobin; only the what-if adds Ines; only main ends Mara's guild.
    renamed = auth_client.patch(
        f"{w['base']}/entities/{w['tobin']}",
        json={"name": "Tobin Vale", "expected_revision": head},
        headers=_in(what_if["id"]),
    )
    assert renamed.status_code == 200, renamed.text
    auth_client.post(
        f"{w['base']}/entities",
        json={"type": "Character", "name": "Ines", "expected_revision": head + 1},
        headers=_in(what_if["id"]),
    )
    assert (
        auth_client.patch(
            f"{w['base']}/entities/{w['tobin']}",
            json={"name": "Tobin Marsh", "expected_revision": head},
        ).status_code
        == 200
    )
    assert (
        auth_client.post(
            f"{w['base']}/facts/{w['in_guild']}/close", json={"expected_revision": head + 1}
        ).status_code
        == 200
    )

    compared = auth_client.get(f"{w['base']}/what-ifs/{what_if['id']}/compare").json()
    assert {(c["label"], c["kind"]) for c in compared["in_what_if"]} == {
        ("Ines", "added"),
        ("Tobin Vale", "changed"),
    }
    assert {(c["label"], c["kind"]) for c in compared["in_main"]} == {
        ("Tobin Marsh", "changed"),
        ("Mara member of Harbor Guild", "ended"),
    }
    assert [c["key"] for c in compared["conflicts"]] == [f"entity:{w['tobin']}"]


def test_bringing_back_asks_about_conflicts_then_proposes_for_review(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    what_if = _start(auth_client, w)
    for name, at in (("Tobin Vale", head),):
        auth_client.patch(
            f"{w['base']}/entities/{w['tobin']}",
            json={"name": name, "expected_revision": at},
            headers=_in(what_if["id"]),
        )
    auth_client.post(
        f"{w['base']}/entities",
        json={"type": "Character", "name": "Ines", "expected_revision": head + 1},
        headers=_in(what_if["id"]),
    )
    auth_client.patch(
        f"{w['base']}/entities/{w['tobin']}",
        json={"name": "Tobin Marsh", "expected_revision": head},
    )

    undecided = auth_client.post(f"{w['base']}/what-ifs/{what_if['id']}/bring-back", json={})
    assert undecided.status_code == 400
    assert "Tobin Vale" in undecided.json()["detail"]

    brought = auth_client.post(
        f"{w['base']}/what-ifs/{what_if['id']}/bring-back",
        json={"keep": {f"entity:{w['tobin']}": "main"}},
    )
    assert brought.status_code == 201, brought.text
    mutation_id = brought.json()["mutation_id"]
    # Nothing has changed in the main story yet.
    assert "Ines" not in _names(auth_client, w)

    mutation = auth_client.get(f"{w['base']}/mutations/{mutation_id}").json()
    ops = [op["payload"] if "payload" in op else op for op in mutation["operations"]]
    assert [op["op"] for op in ops] == ["create_entity"]
    assert ops[0]["evidence"][-1] == {"source_artefact_id": what_if["id"], "location": "what_if"}

    auth_client.post(f"{w['base']}/mutations/{mutation_id}/submit", json={"note": "bring back"})
    assert (
        auth_client.post(f"{w['base']}/mutations/{mutation_id}/approve", json={}).status_code == 200
    )
    committed = auth_client.post(f"{w['base']}/mutations/{mutation_id}/commit")
    assert committed.status_code == 200, committed.text
    assert {"Ines", "Tobin Marsh"} <= _names(auth_client, w)


def test_a_what_if_can_be_set_aside_and_taken_out_again(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    what_if = _start(auth_client, w)
    aside = auth_client.post(
        f"{w['base']}/what-ifs/{what_if['id']}/set-aside", json={"aside": True}
    )
    assert aside.json()["status"] == "set_aside"
    back = auth_client.post(
        f"{w['base']}/what-ifs/{what_if['id']}/set-aside", json={"aside": False}
    )
    assert back.json()["status"] == "active"


def test_proposals_made_in_a_what_if_are_listed_only_there(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    what_if = _start(auth_client, w)
    proposed = auth_client.post(
        f"{w['base']}/mutations",
        json={
            "origin": "model",
            "expected_revision": head,
            "operations": [{"op": "close_fact", "id": w["in_guild"]}],
        },
        headers=_in(what_if["id"]),
    )
    assert proposed.status_code == 201, proposed.text
    in_what_if = auth_client.get(f"{w['base']}/mutations", headers=_in(what_if["id"])).json()
    in_main = auth_client.get(f"{w['base']}/mutations").json()
    assert proposed.json()["id"] in [m["id"] for m in in_what_if["items"]]
    assert proposed.json()["id"] not in [m["id"] for m in in_main["items"]]
    assert _head(auth_client, w, what_if["id"]) == head
