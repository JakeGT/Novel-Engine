"""Reading finds events and thread steps, each quoting the words, for review (EXT-001).

An event names what happens, who takes part (known entries, or ones the same reading
adds), and a story time only when the passage says it; an event a timed scene shows
happening takes the scene's time. A scene's reading may also say the scene moves a
thread of the plan along or settles it; that is one change to the scene's plan that only
adds, and is refused at approval if the author has since changed the scene's plan.
"""

import pytest

from backend.services.assist_time import StoryTime
from backend.services.intake_workflow import (
    PassageDraft,
    ReadEntity,
    ReadEvent,
    ReadThreadStep,
    SceneDraft,
    SceneEvent,
)
from tests.contract.test_context import _head
from tests.contract.test_plan_checks import _add, _outline
from tests.contract.test_scene_reading import _at, _operations, _read, _run
from tests.contract.test_studio import _save, _scene_world
from tests.contract.test_texts_assist import _read as _read_text
from tests.contract.test_texts_assist import _text

pytestmark = pytest.mark.contract

PASSAGE = (
    "In the seventh year Mara fought Captain Roe on the sea wall at Saltmere. "
    "Nobody won. Later they drank together.\n"
)


def _approve_and_commit(client, w, mutation_id: str):
    assert client.post(f"{w['base']}/mutations/{mutation_id}/approve", json={}).status_code == 200
    return client.post(f"{w['base']}/mutations/{mutation_id}/commit")


def test_a_text_is_read_into_events_with_who_took_part_and_when_it_says(
    auth_client, fake_container
):
    from tests.contract.test_god_hand import _world

    w = _world(auth_client, fake_container)
    text = _text(auth_client, w, PASSAGE)
    fake_container.ai.queue(
        PassageDraft(
            entities=[
                ReadEntity(ref="roe", type="Character", name="Captain Roe", quote="Captain Roe")
            ],
            events=[
                ReadEvent(
                    name="The fight on the sea wall",
                    kind="duel",
                    participants=[w["mara"], "roe", w["saltmere"], "nobody"],
                    when=StoryTime(kind="at", at=7, words="in the seventh year"),
                    quote="Mara fought Captain Roe on the sea wall at Saltmere",
                ),
                # Implied, untimed, and the same people.
                ReadEvent(
                    name="A drink after the fight",
                    participants=[w["mara"], "roe"],
                    quote="Later they drank together",
                    stated=False,
                ),
                # Twice in one answer: kept once.
                ReadEvent(name="The fight on the sea wall", quote="Nobody won"),
                # Not in the passage.
                ReadEvent(name="A wedding", quote="They were married at noon"),
                # Relative to an event the story does not have: kept, without the time.
                ReadEvent(
                    name="Nobody won",
                    quote="Nobody won",
                    when=StoryTime(kind="relative", anchor="evt_missing", offset=1, words="Later"),
                ),
            ],
            # Not a scene: nothing may move a thread along.
            thread_steps=[ReadThreadStep(thread="pln_x", step="advances", quote="Nobody won")],
        )
    )
    read = _read_text(auth_client, w, text["id"])
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["events"] == 3 and body["thread_steps"] == 0
    reasons = [(item["kind"], item["reason"]) for item in body["discarded"]]
    assert ("event", "The fight on the sea wall: already in the story bible") in reasons
    assert ("event", "A wedding: not in the passage") in reasons
    assert any(kind == "time" and "evt_missing" in reason for kind, reason in reasons)
    assert any(kind == "event" and "'nobody' is not known" in reason for kind, reason in reasons)
    assert ("thread", "only reading a scene can move a thread of the plan along") in reasons
    # The plain read asks for events but lists no threads.
    system = fake_container.ai.calls[-1]["messages"][0]["content"]
    assert "add an event for something that happens" in system
    assert "thread step" not in system

    operations = _operations(auth_client, w, body["mutation_id"])
    roe = next(op for op in operations if op["op"] == "create_entity")
    fight, drink, untimed = [op for op in operations if op["op"] == "create_event"]
    assert fight["name"] == "The fight on the sea wall" and fight["kind"] == "duel"
    assert [p["entity_id"] for p in fight["participants"]] == [w["mara"], roe["id"]]
    assert fight["location_ids"] == [w["saltmere"]]
    assert fight["when"] == {"kind": "at", "at": 7.0, "label": "in the seventh year"}
    (evidence,) = fight["evidence"]
    assert evidence["source_artefact_id"] == text["latest_version_id"]
    assert evidence["location"] and not evidence["location"].endswith(",implied")
    assert drink["evidence"][0]["location"].endswith(",implied") and drink.get("when") is None
    assert untimed.get("when") is None
    assert body["implied"] >= 1

    # Approved, the events join the story with the people the reading added.
    assert _approve_and_commit(auth_client, w, body["mutation_id"]).status_code == 200
    events = auth_client.get(f"{w['base']}/events").json()["items"]
    names = {event["name"] for event in events}
    assert {"The fight on the sea wall", "A drink after the fight", "Nobody won"} <= names


def _threads_world(client, container):
    w, scene = _scene_world(client, container)
    debt = _add(client, w, "thread", "The debt", summary="What Tobin owes Roe")
    guild = _add(client, w, "thread", "The guild's secret")
    opened = _add(client, w, "thread", "The letter")
    client.patch(
        f"{w['base']}/plan/{scene['id']}",
        json={"expected_revision": _head(client, w), "sets_up": [opened["id"]]},
    )
    storm = client.post(
        f"{w['base']}/events",
        json={
            "kind": "storm",
            "name": "The great storm",
            "when": {"kind": "at", "at": 5},
            "expected_revision": _head(client, w),
        },
    )
    assert storm.status_code == 201, storm.text
    return w, scene, {"debt": debt, "guild": guild, "opened": opened, "storm": storm.json()}


SCENE_TEXT = (
    "Tobin paid the last of the debt at Lantern Harbor. "
    "He wondered again what the guild was hiding. "
    "The mill had burned three years before the great storm."
)


def test_a_timed_scene_reads_events_at_its_time_and_steps_of_the_plans_threads(
    auth_client, fake_container
):
    w, scene, plan = _threads_world(auth_client, fake_container)
    _at(auth_client, w, scene, 12)
    assert _save(auth_client, w, scene, SCENE_TEXT, None).status_code == 200
    storm = plan["storm"]["id"]
    fake_container.ai.queue(
        SceneDraft(
            events=[
                SceneEvent(
                    name="Tobin pays the debt",
                    kind="payment",
                    participants=[w["tobin"], w["harbor"]],
                    quote="Tobin paid the last of the debt",
                    in_scene=True,
                ),
                # Remembered, with a time the passage gives against a known event.
                SceneEvent(
                    name="The mill burns",
                    quote="The mill had burned three years before the great storm",
                    when=StoryTime(
                        kind="relative",
                        anchor=storm,
                        offset=3,
                        direction="before",
                        words="three years before the great storm",
                    ),
                ),
                # Already in the story bible.
                SceneEvent(name="The great storm", quote="the great storm"),
            ],
            thread_steps=[
                ReadThreadStep(
                    thread=plan["debt"]["id"],
                    step="resolves",
                    quote="Tobin paid the last of the debt",
                ),
                # Settled here, so not also moved along here.
                ReadThreadStep(
                    thread=plan["debt"]["id"], step="advances", quote="paid the last of the debt"
                ),
                ReadThreadStep(
                    thread=plan["guild"]["id"],
                    step="advances",
                    quote="He wondered again what the guild was hiding",
                ),
                # Opened by this scene already: part of it.
                ReadThreadStep(
                    thread=plan["opened"]["id"], step="advances", quote="He wondered again"
                ),
                ReadThreadStep(thread="pln_missing", step="advances", quote="He wondered again"),
                ReadThreadStep(thread=plan["guild"]["id"], step="opens", quote="He wondered"),
            ],
        )
    )
    _read(auth_client, w, scene)
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    call = fake_container.ai.calls[-1]
    user = call["messages"][1]["content"]
    assert "Threads in the plan:" in user and plan["debt"]["id"] in user
    assert f"- {storm} | The great storm" in user
    assert "set in_scene to true" in call["messages"][0]["content"]

    # What the scene shows, then the threads it moves as one change at the end.
    proposal, steps = done.result["proposals"]
    operations = _operations(auth_client, w, proposal)
    pays, mill = [op for op in operations if op["op"] == "create_event"]
    assert pays["when"] == {"kind": "at", "at": 12.0, "label": None}
    # Happening in the scene, it is in the scene; the remembered one is not.
    assert pays["scene_id"] == scene["id"] and mill.get("scene_id") is None
    assert [p["entity_id"] for p in pays["participants"]] == [w["tobin"]]
    assert pays["location_ids"] == [w["harbor"]]
    assert mill["when"]["kind"] == "relative" and mill["when"]["anchor_event_id"] == storm
    assert not [op for op in operations if op["op"] == "update_plan_item"]
    (step,) = _operations(auth_client, w, steps)
    assert step["op"] == "update_plan_item"
    assert step["id"] == scene["id"] and step["only_fills"] is True
    assert step["pays_off"] == [plan["debt"]["id"]]
    assert step["details"]["_advances"] == f'["{plan["guild"]["id"]}"]'
    assert step["base"]["sets_up"] == [plan["opened"]["id"]] and step["base"]["advances"] == []
    assert len(step["evidence"]) == 2

    # Approved, the plan shows the scene settling one thread and moving another along.
    assert _approve_and_commit(auth_client, w, proposal).status_code == 200
    moved = auth_client.post(
        f"{w['base']}/mutations/{steps}/retarget",
        json={"expected_revision": _head(auth_client, w)},
    )
    assert moved.status_code == 200, moved.text
    assert _approve_and_commit(auth_client, w, steps).status_code == 200
    threads = {t["thread_id"]: t for t in _outline(auth_client, w)["threads"]}
    assert "resolves" in [s["step"] for s in threads[plan["debt"]["id"]]["steps"]]
    assert [s["step"] for s in threads[plan["guild"]["id"]]["steps"]] == ["advances"]


def test_a_thread_step_is_refused_if_the_author_changed_the_scenes_plan_since(
    auth_client, fake_container
):
    w, scene, plan = _threads_world(auth_client, fake_container)
    assert _save(auth_client, w, scene, SCENE_TEXT, None).status_code == 200
    fake_container.ai.queue(
        PassageDraft(
            thread_steps=[
                ReadThreadStep(
                    thread=plan["guild"]["id"],
                    step="advances",
                    quote="He wondered again what the guild was hiding",
                )
            ]
        )
    )
    _read(auth_client, w, scene)
    (done,) = _run(fake_container)
    (proposal,) = done.result["proposals"]
    # An untimed scene still lists the plan's threads.
    assert "Threads in the plan:" in fake_container.ai.calls[-1]["messages"][1]["content"]

    # The author writes a note on the scene meanwhile.
    noted = auth_client.patch(
        f"{w['base']}/plan/{scene['id']}",
        json={"expected_revision": _head(auth_client, w), "details": {"stakes": "his name"}},
    )
    assert noted.status_code == 200, noted.text
    check = auth_client.post(f"{w['base']}/mutations/{proposal}/validate").json()
    assert "plan_changed_since" in {issue["code"] for issue in check["issues"]}


def test_approving_a_thread_step_cannot_bring_back_a_step_the_author_removed(
    auth_client, fake_container
):
    w, scene, plan = _threads_world(auth_client, fake_container)
    debt, guild = plan["debt"]["id"], plan["guild"]["id"]
    marked = auth_client.patch(
        f"{w['base']}/plan/{scene['id']}",
        json={"expected_revision": _head(auth_client, w), "advances": [debt]},
    )
    assert marked.status_code == 200, marked.text
    assert _save(auth_client, w, scene, SCENE_TEXT, None).status_code == 200
    fake_container.ai.queue(
        PassageDraft(
            thread_steps=[ReadThreadStep(thread=guild, step="advances", quote="He wondered again")]
        )
    )
    _read(auth_client, w, scene)
    (done,) = _run(fake_container)
    (proposal,) = done.result["proposals"]

    # The author decides the scene does not move the debt along after all.
    removed = auth_client.patch(
        f"{w['base']}/plan/{scene['id']}",
        json={"expected_revision": _head(auth_client, w), "advances": []},
    )
    assert removed.status_code == 200, removed.text
    check = auth_client.post(f"{w['base']}/mutations/{proposal}/validate").json()
    (issue,) = [i for i in check["issues"] if i["code"] == "plan_changed_since"]
    assert "Ask for a new suggestion instead." in issue["message"]


TWO_PARTS = (
    "He wondered again what the guild was hiding.\n\n"
    "Gulls cried over the water.\n\n"
    "Tobin paid the last of the debt at Lantern Harbor."
)


def test_a_scene_read_in_parts_suggests_its_thread_steps_as_one_change(auth_client, fake_container):
    w, scene, plan = _threads_world(auth_client, fake_container)
    debt, guild = plan["debt"]["id"], plan["guild"]["id"]
    first = _save(auth_client, w, scene, "Gulls cried over the water.", None).json()
    fake_container.ai.queue(PassageDraft())
    _read(auth_client, w, scene)
    _run(fake_container)
    # Two new paragraphs apart: two passages, each moving a different thread along.
    _save(auth_client, w, scene, TWO_PARTS, first["version_id"])
    fake_container.ai.queue(
        PassageDraft(
            events=[ReadEvent(name="Tobin broods", participants=[w["tobin"]], quote="He wondered")],
            thread_steps=[ReadThreadStep(thread=guild, step="advances", quote="He wondered again")],
        )
    )
    fake_container.ai.queue(
        PassageDraft(
            # The same event, seen again in the second part: not suggested twice.
            events=[ReadEvent(name="Tobin broods", participants=[w["tobin"]], quote="Tobin paid")],
            thread_steps=[ReadThreadStep(thread=debt, step="advances", quote="paid the last")],
        )
    )
    _read(auth_client, w, scene)
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    assert done.result["passages"] == 2
    *read, steps = done.result["proposals"]
    events = [
        op
        for proposal in read
        for op in _operations(auth_client, w, proposal)
        if op["op"] == "create_event"
    ]
    assert [event["name"] for event in events] == ["Tobin broods"]
    (step,) = _operations(auth_client, w, steps)
    assert step["details"]["_advances"] == f'["{guild}","{debt}"]'
    assert len(step["evidence"]) == 2

    for proposal in [*read, steps]:
        moved = auth_client.post(
            f"{w['base']}/mutations/{proposal}/retarget",
            json={"expected_revision": _head(auth_client, w)},
        )
        assert moved.status_code == 200, moved.text
        assert _approve_and_commit(auth_client, w, proposal).status_code == 200
    threads = {t["thread_id"]: t for t in _outline(auth_client, w)["threads"]}
    assert [s["step"] for s in threads[guild]["steps"]] == ["advances"]
    assert [s["step"] for s in threads[debt]["steps"]] == ["advances"]


def test_an_events_time_is_kept_only_when_the_passage_says_it(auth_client, fake_container):
    from tests.contract.test_god_hand import _world

    w = _world(auth_client, fake_container)
    text = _text(auth_client, w, PASSAGE)
    fake_container.ai.queue(
        PassageDraft(
            events=[
                ReadEvent(
                    name="The fight on the sea wall",
                    participants=[w["mara"]],
                    when=StoryTime(kind="at", at=7, words="in the seventh year"),
                    quote="Mara fought Captain Roe",
                ),
                # A time the passage never gives, however sure the model sounds.
                ReadEvent(
                    name="The drink",
                    participants=[w["mara"]],
                    when=StoryTime(kind="at", at=9, words="in the ninth year"),
                    quote="Later they drank together",
                ),
                ReadEvent(
                    name="Nobody won",
                    when=StoryTime(kind="at", at=8),
                    quote="Nobody won",
                ),
            ]
        )
    )
    body = _read_text(auth_client, w, text["id"]).json()
    assert body["events"] == 3
    reasons = [(item["kind"], item["reason"]) for item in body["discarded"]]
    assert ("time", "The drink: the passage does not say when it happens") in reasons
    assert ("time", "Nobody won: the passage does not say when it happens") in reasons
    fight, drink, nobody = [
        op for op in _operations(auth_client, w, body["mutation_id"]) if op["op"] == "create_event"
    ]
    assert fight["when"]["at"] == 7.0
    assert drink.get("when") is None and nobody.get("when") is None
