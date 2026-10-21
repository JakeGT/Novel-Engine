"""Reading finds changes of state and new threads, each quoting the words (EXT-001).

A thing held or lost, a place reached or left, a status or a relationship beginning or
ending: a beginning is a fact (from the scene's time where its kind can take a time), an
ending ends the fact the story records (at the scene's time, or by closing it), and in a
timed scene a change whose kind of fact cannot take a time is also an event at the
scene's time. A scene's reading may open a thread the plan lacks, in the same one change
to the scene's plan. Everything waits for review.
"""

import pytest

from backend.services.intake_workflow import (
    PassageDraft,
    ReadEntity,
    ReadNewThread,
    ReadStateChange,
    SceneDraft,
)
from tests.contract.test_context import _head
from tests.contract.test_plan_checks import _outline
from tests.contract.test_scene_reading import _at, _operations, _read, _run
from tests.contract.test_studio import _save, _scene_world
from tests.contract.test_texts_assist import _read as _read_text
from tests.contract.test_texts_assist import _text

pytestmark = pytest.mark.contract

SCENE = (
    "Mara handed the brass key to Tobin. "
    "She left Lantern Harbor for Saltmere before dawn. "
    "A knife caught Tobin's arm and he was wounded. "
    "That night Mara quit the Harbor Guild. "
    "The ferryman lay dead on the steps. "
    "Who had sent the knife, nobody could say."
)


def _key_world(client, container):
    w, scene = _scene_world(client, container)
    key = client.post(
        f"{w['base']}/entities",
        json={"type": "Object", "name": "The brass key", "expected_revision": _head(client, w)},
    )
    assert key.status_code == 201, key.text
    w["key"] = key.json()["id"]
    owns = client.post(
        f"{w['base']}/facts",
        json={
            "proposition": {
                "subject_id": w["mara"],
                "predicate": "OWNS",
                "object": {
                    "kind": "entity_ref",
                    "entity_id": w["key"],
                    "project_id": w["base"].rsplit("/", 1)[-1],
                },
            },
            "expected_revision": _head(client, w),
        },
    )
    assert owns.status_code == 201, owns.text
    w["owns_key"] = owns.json()["id"]
    return w, scene


def _changes(w) -> list[ReadStateChange]:
    return [
        ReadStateChange(
            kind="possession",
            change="ends",
            subject=w["mara"],
            predicate="OWNS",
            object=w["key"],
            name="Mara gives up the key",
            quote="Mara handed the brass key to Tobin",
        ),
        ReadStateChange(
            kind="possession",
            change="starts",
            subject=w["tobin"],
            predicate="OWNS",
            object=w["key"],
            name="Tobin takes the key",
            quote="Mara handed the brass key to Tobin",
        ),
        ReadStateChange(
            kind="location",
            subject=w["mara"],
            predicate="LOCATED_AT",
            object=w["saltmere"],
            name="Mara goes to Saltmere",
            quote="She left Lantern Harbor for Saltmere",
        ),
        ReadStateChange(
            kind="status",
            subject=w["tobin"],
            predicate="HAS_STATUS",
            value="wounded",
            name="Tobin is wounded",
            quote="he was wounded",
        ),
        ReadStateChange(
            kind="relationship",
            change="ends",
            subject=w["mara"],
            predicate="MEMBER_OF",
            object=w["guild"],
            name="Mara leaves the guild",
            quote="Mara quit the Harbor Guild",
        ),
        ReadStateChange(
            kind="status",
            subject="ferryman",
            predicate="HAS_STATUS",
            value="dead",
            quote="The ferryman lay dead",
        ),
        # Not a kind of change; not in the passage; nothing recorded to end; the wrong
        # kind of fact for a place; a status with no word.
        ReadStateChange(
            kind="mood", subject=w["mara"], predicate="KNOWS", object=w["tobin"], quote="Mara"
        ),
        ReadStateChange(
            kind="location",
            subject=w["mara"],
            predicate="LOCATED_AT",
            object=w["harbor"],
            quote="She sailed home at once",
        ),
        ReadStateChange(
            kind="relationship",
            change="ends",
            subject=w["tobin"],
            predicate="KNOWS",
            object=w["mara"],
            name="Tobin forgets Mara",
            quote="nobody could say",
        ),
        ReadStateChange(
            kind="location", subject=w["mara"], predicate="OWNS", object=w["key"], quote="Mara"
        ),
        ReadStateChange(kind="status", subject=w["tobin"], predicate="HAS_STATUS", quote="Tobin"),
    ]


def _approve_and_commit(client, w, mutation_id: str):
    assert client.post(f"{w['base']}/mutations/{mutation_id}/approve", json={}).status_code == 200
    return client.post(f"{w['base']}/mutations/{mutation_id}/commit")


def test_a_timed_scene_reads_changes_of_state_into_time_bounded_facts_and_events(
    auth_client, fake_container
):
    w, scene = _key_world(auth_client, fake_container)
    _at(auth_client, w, scene, 12)
    assert _save(auth_client, w, scene, SCENE, None).status_code == 200
    fake_container.ai.queue(
        SceneDraft(
            entities=[
                ReadEntity(ref="ferryman", type="Character", name="The ferryman", quote="ferryman")
            ],
            state_changes=_changes(w),
            new_threads=[
                ReadNewThread(
                    title="Who sent the knife",
                    summary="Someone wants Tobin hurt, and nobody knows who.",
                    quote="Who had sent the knife, nobody could say",
                ),
                # Not in the passage.
                ReadNewThread(title="The lost ship", quote="The ship was never found"),
            ],
        )
    )
    _read(auth_client, w, scene)
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    system = fake_container.ai.calls[-1]["messages"][0]["content"]
    assert "add a state change when the passage shows a state beginning or ending" in system
    assert "add it to new_threads" in system
    assert done.result["new_threads"] == 1
    # Two endings, two beginnings (the status's fact is left for the author: its values
    # are picked by hand), and events for the four changes whose facts take no time.
    assert done.result["state_changes"] == 9

    proposal, steps = done.result["proposals"]
    operations = _operations(auth_client, w, proposal)
    by_op: dict[str, list[dict]] = {}
    for op in operations:
        by_op.setdefault(op["op"], []).append(op)

    # Mara no longer has the key: closed (it cannot take a time), said why.
    closed = {op["id"]: op for op in by_op["close_fact"]}
    assert closed[w["owns_key"]]["why"] == "The passage shows this coming to an end."
    assert closed[w["in_guild"]]["why"] == "The passage shows this coming to an end."
    # Tobin has it now; Mara is at Saltmere from the scene's time, and the harbour ends then.
    asserted = {
        (op["proposition"]["subject_id"], op["proposition"]["predicate"]): op
        for op in by_op["assert_fact"]
    }
    tobin_owns = asserted[(w["tobin"], "OWNS")]
    assert tobin_owns["why"] == "The passage shows this beginning."
    assert tobin_owns.get("starts") is None
    moved = asserted[(w["mara"], "LOCATED_AT")]
    assert moved["starts"] == {"kind": "at", "at": 12.0, "label": None}
    (ended,) = by_op["amend_fact"]
    assert ended["id"] == w["at_harbor"] and ended["ends"]["at"] == 12.0
    assert (w["tobin"], "HAS_STATUS") not in asserted

    events = {op["name"]: op for op in by_op["create_event"]}
    assert set(events) == {
        "Mara gives up the key",
        "Tobin takes the key",
        "Tobin is wounded",
        "Mara leaves the guild",
        "The ferryman: death (starts)",
    }
    for event in events.values():
        assert event["when"]["at"] == 12.0 and event["scene_id"] == scene["id"]
        (evidence,) = event["evidence"]
        assert evidence["location"]
    assert events["Tobin is wounded"]["kind"] == "condition"
    death = events["The ferryman: death (starts)"]
    assert death["kind"] == "death"
    (died,) = death["participants"]
    assert died["role"] == "died" and died["entity_id"].startswith("tmp:")
    assert [p["entity_id"] for p in events["Tobin takes the key"]["participants"]] == [
        w["tobin"],
        w["key"],
    ]

    assert done.result["discarded"] >= 6

    # The new thread is one change with the scene that opens it.
    thread, change = _operations(auth_client, w, steps)
    assert thread["op"] == "create_plan_item" and thread["kind"] == "thread"
    assert thread["title"] == "Who sent the knife"
    assert thread["summary"] == "Someone wants Tobin hurt, and nobody knows who."
    assert change["op"] == "update_plan_item" and change["only_fills"] is True
    assert change["sets_up"] == [thread["id"]]

    # Approved, the story and the plan show it.
    committed = _approve_and_commit(auth_client, w, proposal)
    assert committed.status_code == 200, committed.text
    moved_on = auth_client.post(
        f"{w['base']}/mutations/{steps}/retarget",
        json={"expected_revision": _head(auth_client, w)},
    )
    assert moved_on.status_code == 200, moved_on.text
    assert _approve_and_commit(auth_client, w, steps).status_code == 200
    outline = _outline(auth_client, w)
    (made,) = [
        item["id"]
        for item in auth_client.get(f"{w['base']}/plan").json()["items"]
        if item["title"] == "Who sent the knife"
    ]
    threads = {t["thread_id"]: t for t in outline["threads"]}
    assert [(s["plan_id"], s["step"]) for s in threads[made]["steps"]] == [(scene["id"], "opens")]
    facts = auth_client.get(
        f"{w['base']}/facts", params={"subject_id": w["mara"], "predicate": "LOCATED_AT"}
    ).json()["items"]
    at = {fact["object"]["entity_id"]: fact for fact in facts}
    assert at[w["saltmere"]]["starts"]["at"] == 12.0
    assert at[w["harbor"]]["ends"]["at"] == 12.0


def test_reading_pasted_text_ends_and_begins_without_a_time_and_opens_no_thread(
    auth_client, fake_container
):
    w, _scene = _key_world(auth_client, fake_container)
    text = _text(auth_client, w, SCENE)
    fake_container.ai.queue(
        PassageDraft(
            state_changes=_changes(w)[:3],
            new_threads=[ReadNewThread(title="Who sent the knife", quote="Who had sent the knife")],
        )
    )
    read = _read_text(auth_client, w, text["id"], expected_revision=_head(auth_client, w))
    assert read.status_code == 200, read.text
    body = read.json()
    # One ending (closed), two beginnings, the old place closed by the new one; no events.
    assert body["state_changes"] == 3 and body["events"] == 0 and body["new_threads"] == 0
    assert ("thread", "only reading a scene can open a thread") in {
        (item["kind"], item["reason"]) for item in body["discarded"]
    }
    operations = _operations(auth_client, w, body["mutation_id"])
    assert not [op for op in operations if op["op"] in ("create_event", "amend_fact")]
    assert {op["id"] for op in operations if op["op"] == "close_fact"} == {
        w["owns_key"],
        w["at_harbor"],
    }
    assert all(op.get("starts") is None for op in operations if op["op"] == "assert_fact")


def test_changes_that_cannot_be_used_say_why_in_plain_words(auth_client, fake_container):
    w, _scene = _key_world(auth_client, fake_container)
    text = _text(auth_client, w, SCENE)
    fake_container.ai.queue(PassageDraft(state_changes=_changes(w)[6:]))
    read = _read_text(auth_client, w, text["id"], expected_revision=_head(auth_client, w))
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["state_changes"] == 0 and body["mutation_id"] is None
    reasons = {item["reason"] for item in body["discarded"] if item["kind"] == "state"}
    assert "mood: 'mood' is not a kind of change" in reasons
    assert "the passage does not say this in those words" in {
        item["reason"] for item in body["discarded"]
    }
    assert "Tobin forgets Mara: the story records nothing for it to end" in reasons
    assert "location: OWNS does not record a location" in reasons
    assert "status: a status needs a word for it, such as injured" in reasons


def test_an_ending_already_ended_before_the_scene_is_left_alone(auth_client, fake_container):
    w, scene = _key_world(auth_client, fake_container)
    # Mara left the harbour at 5; the scene at 12 says she leaves it.
    amended = auth_client.post(
        f"{w['base']}/facts/{w['at_harbor']}/amend",
        json={"expected_revision": _head(auth_client, w), "ends": {"kind": "at", "at": 5}},
    )
    assert amended.status_code == 200, amended.text
    _at(auth_client, w, scene, 12)
    assert _save(auth_client, w, scene, SCENE, None).status_code == 200
    fake_container.ai.queue(
        SceneDraft(
            state_changes=[
                ReadStateChange(
                    kind="location",
                    change="ends",
                    subject=w["mara"],
                    predicate="LOCATED_AT",
                    object=w["harbor"],
                    name="Mara leaves the harbour",
                    quote="She left Lantern Harbor",
                )
            ]
        )
    )
    _read(auth_client, w, scene)
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    assert done.result["proposals"] == [] and done.result["state_changes"] == 0


def test_an_untimed_reading_leaves_timed_endings_and_hinted_endings_alone(
    auth_client, fake_container
):
    w, _scene = _key_world(auth_client, fake_container)
    text = _text(auth_client, w, SCENE)
    fake_container.ai.queue(
        PassageDraft(
            state_changes=[
                # A place can take a time and the text has none: the harbour fact may be
                # one from later in the story, so it is not ended.
                ReadStateChange(
                    kind="location",
                    change="ends",
                    subject=w["mara"],
                    predicate="LOCATED_AT",
                    object=w["harbor"],
                    name="Mara leaves the harbour",
                    quote="She left Lantern Harbor",
                ),
                # Only hinted at: what the story records is not ended on a hint.
                ReadStateChange(
                    kind="possession",
                    change="ends",
                    subject=w["mara"],
                    predicate="OWNS",
                    object=w["key"],
                    name="Mara gives up the key",
                    quote="Mara handed the brass key to Tobin",
                    stated=False,
                ),
            ]
        )
    )
    read = _read_text(auth_client, w, text["id"], expected_revision=_head(auth_client, w))
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["state_changes"] == 0 and body["mutation_id"] is None
    reasons = {item["reason"] for item in body["discarded"] if item["kind"] == "state"}
    assert (
        "Mara leaves the harbour: the passage has no story time, so when it ends cannot be placed"
        in reasons
    )
    assert "Mara gives up the key: the passage only hints that it ends" in reasons
