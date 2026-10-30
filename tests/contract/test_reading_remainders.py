"""Reading remainders (EXT-001, EXT-007): a pasted text read as a scene's prose suggests the
steps of the plan's threads; a status whose values the author picks by hand is suggested
from the author's own list when the passage uses the word; and thread steps a stopped
reading found are suggested by the next one instead of being lost."""

import pytest

from backend.domain.errors import ProviderFailureError
from backend.services.intake_workflow import (
    PassageDraft,
    ReadFact,
    ReadStateChange,
    ReadThreadStep,
)
from tests.contract.test_context import _head
from tests.contract.test_reading_events import TWO_PARTS, _threads_world
from tests.contract.test_reading_state import _key_world
from tests.contract.test_scene_reading import _operations, _read, _run
from tests.contract.test_studio import _save
from tests.contract.test_texts_assist import _read as _read_text
from tests.contract.test_texts_assist import _text

pytestmark = pytest.mark.contract

PASTED = "He wondered again what the guild was hiding. Tobin paid the last of the debt."


def test_a_pasted_text_read_as_a_scenes_prose_suggests_thread_steps(auth_client, fake_container):
    w, scene, plan = _threads_world(auth_client, fake_container)
    text = _text(auth_client, w, PASTED)
    guild = plan["guild"]["id"]
    step = ReadThreadStep(thread=guild, step="advances", quote="He wondered again")

    # Without a scene, a step is set aside with the reason.
    fake_container.ai.queue(PassageDraft(thread_steps=[step]))
    plain = _read_text(auth_client, w, text["id"], expected_revision=_head(auth_client, w))
    assert plain.status_code == 200, plain.text
    assert plain.json()["thread_steps"] == 0

    fake_container.ai.queue(PassageDraft(thread_steps=[step]))
    read = _read_text(
        auth_client, w, text["id"], expected_revision=_head(auth_client, w), scene_id=scene["id"]
    )
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["thread_steps"] == 1
    # The plan's threads were listed for the model, as for a scene's own reading.
    assert guild in fake_container.ai.calls[-1]["messages"][1]["content"]
    (change,) = [
        op
        for op in _operations(auth_client, w, body["mutation_id"])
        if op["op"] == "update_plan_item"
    ]
    assert change["id"] == scene["id"] and change["only_fills"] is True
    assert change["details"]["_advances"] == f'["{guild}"]'

    missing = _read_text(
        auth_client, w, text["id"], expected_revision=_head(auth_client, w), scene_id="pln_nope"
    )
    assert missing.status_code == 404


def _status_fact(client, w, who: str, value: str) -> None:
    made = client.post(
        f"{w['base']}/facts",
        json={
            "proposition": {
                "subject_id": w[who],
                "predicate": "HAS_STATUS",
                "object": {
                    "kind": "enum",
                    "value": value,
                    "allowed": ["free", "captive", "wounded"],
                },
            },
            "expected_revision": _head(client, w),
        },
    )
    assert made.status_code == 201, made.text


STATUS_TEXT = "A knife caught Tobin's arm and he was Wounded. Mara was taken and held."


def test_a_hand_picked_status_is_suggested_from_the_authors_list(auth_client, fake_container):
    w, _scene = _key_world(auth_client, fake_container)
    _status_fact(auth_client, w, "mara", "free")
    text = _text(auth_client, w, STATUS_TEXT)
    fake_container.ai.queue(
        PassageDraft(
            state_changes=[
                ReadStateChange(
                    kind="status",
                    subject=w["tobin"],
                    predicate="HAS_STATUS",
                    value="wounded",
                    name="Tobin is wounded",
                    quote="he was Wounded",
                ),
                # Not one of the author's values.
                ReadStateChange(
                    kind="status",
                    subject=w["tobin"],
                    predicate="HAS_STATUS",
                    value="injured",
                    quote="he was Wounded",
                ),
                # One of them, but the passage never uses the word.
                ReadStateChange(
                    kind="status",
                    subject=w["mara"],
                    predicate="HAS_STATUS",
                    value="captive",
                    quote="Mara was taken and held",
                ),
            ],
            facts=[
                ReadFact(
                    subject_ref=w["mara"],
                    predicate="HAS_STATUS",
                    object_value="captive",
                    quote="Mara was taken and held",
                )
            ],
        )
    )
    read = _read_text(auth_client, w, text["id"], expected_revision=_head(auth_client, w))
    assert read.status_code == 200, read.text
    body = read.json()
    reasons = {item["reason"] for item in body["discarded"]}
    assert "HAS_STATUS: 'injured' is not one of your values (free, captive, wounded)" in reasons
    assert "HAS_STATUS: the passage does not use the word “captive”" in reasons
    facts = [
        op for op in _operations(auth_client, w, body["mutation_id"]) if op["op"] == "assert_fact"
    ]
    (status,) = [op for op in facts if op["proposition"]["predicate"] == "HAS_STATUS"]
    assert status["proposition"]["subject_id"] == w["tobin"]
    # The author's own spelling and list.
    assert status["proposition"]["object"] == {
        "kind": "enum",
        "value": "wounded",
        "allowed": ["free", "captive", "wounded"],
    }
    # The model was shown the author's values.
    assert "['free', 'captive', 'wounded']" in fake_container.ai.calls[-1]["messages"][0]["content"]


def test_without_a_list_a_hand_picked_status_is_left_to_the_author(auth_client, fake_container):
    w, _scene = _key_world(auth_client, fake_container)
    text = _text(auth_client, w, STATUS_TEXT)
    fake_container.ai.queue(
        PassageDraft(
            state_changes=[
                ReadStateChange(
                    kind="status",
                    subject=w["tobin"],
                    predicate="HAS_STATUS",
                    value="wounded",
                    quote="he was Wounded",
                )
            ]
        )
    )
    read = _read_text(auth_client, w, text["id"], expected_revision=_head(auth_client, w))
    body = read.json()
    assert body["mutation_id"] is None
    assert any("you pick its values by hand" in item["reason"] for item in body["discarded"])


def test_thread_steps_a_stopped_reading_found_are_suggested_by_the_next(
    auth_client, fake_container
):
    w, scene, plan = _threads_world(auth_client, fake_container)
    debt, guild = plan["debt"]["id"], plan["guild"]["id"]
    first = _save(auth_client, w, scene, "Gulls cried over the water.", None).json()
    fake_container.ai.queue(PassageDraft())
    _read(auth_client, w, scene)
    _run(fake_container)
    _save(auth_client, w, scene, TWO_PARTS, first["version_id"])
    # The first part is read and moves a thread along; then the model fails on the second.
    fake_container.ai.queue(
        PassageDraft(
            thread_steps=[ReadThreadStep(thread=guild, step="advances", quote="He wondered again")]
        )
    )
    fake_container.ai.queue(ProviderFailureError("the model stopped answering"))
    _read(auth_client, w, scene)
    (stopped,) = _run(fake_container)
    assert stopped.status.value != "succeeded"
    # Asked again, only the second part is read, and both steps are suggested together.
    fake_container.ai.queue(
        PassageDraft(
            thread_steps=[ReadThreadStep(thread=debt, step="advances", quote="paid the last")]
        )
    )
    _read(auth_client, w, scene)
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    assert done.result["thread_steps"] == 2 and done.result["carried_steps"] == 1
    (step,) = _operations(auth_client, w, done.result["proposals"][-1])
    assert step["details"]["_advances"] == f'["{guild}","{debt}"]'
    # Never suggested a second time.
    readings = fake_container.studio_store.saved_readings
    assert all(reading.steps == () for reading in readings)


def test_a_carried_thread_step_cites_the_version_its_reading_read(auth_client, fake_container):
    w, scene, plan = _threads_world(auth_client, fake_container)
    debt, guild = plan["debt"]["id"], plan["guild"]["id"]
    first = _save(auth_client, w, scene, "Gulls cried over the water.", None).json()
    fake_container.ai.queue(PassageDraft())
    _read(auth_client, w, scene)
    _run(fake_container)
    second = _save(auth_client, w, scene, TWO_PARTS, first["version_id"]).json()
    fake_container.ai.queue(
        PassageDraft(
            thread_steps=[ReadThreadStep(thread=guild, step="advances", quote="He wondered again")]
        )
    )
    fake_container.ai.queue(ProviderFailureError("the model stopped answering"))
    _read(auth_client, w, scene)
    (stopped,) = _run(fake_container)
    assert stopped.status.value != "succeeded"
    # The author writes on before asking again: the next reading reads a newer version.
    third = _save(
        auth_client, w, scene, TWO_PARTS + "\n\nThe tide turned.", second["version_id"]
    ).json()
    assert third["version_id"] != second["version_id"]
    fake_container.ai.queue(
        PassageDraft(
            thread_steps=[ReadThreadStep(thread=debt, step="advances", quote="paid the last")]
        )
    )
    fake_container.ai.queue(PassageDraft())
    _read(auth_client, w, scene)
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    assert done.result["carried_steps"] == 1
    (step,) = _operations(auth_client, w, done.result["proposals"][-1])
    assert step["details"]["_advances"] == f'["{guild}","{debt}"]'
    # Each step points at the words it was found in, in the version that has them.
    cited = [ref["source_artefact_id"] for ref in step["evidence"]]
    assert cited == [second["version_id"], third["version_id"]]


def test_a_scene_text_from_another_line_of_the_story_is_not_read_as_this_lines_scene(
    auth_client, fake_container
):
    w, scene, _plan = _threads_world(auth_client, fake_container)
    main = _save(auth_client, w, scene, "Gulls cried over the water.", None).json()
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Storm"}).json()["id"]
    # Changed in the what-if, the scene's words there become a text of its own.
    changed = _save(
        auth_client, w, scene, "A storm broke over the harbor.", main["version_id"], branch
    ).json()
    assert changed["text_id"] != main["text_id"]

    # In the main story, the what-if's words are not this scene's prose.
    elsewhere = _read_text(
        auth_client,
        w,
        changed["text_id"],
        expected_revision=_head(auth_client, w),
        scene_id=scene["id"],
    )
    assert elsewhere.status_code == 400, elsewhere.text
    assert "another line of the story" in elsewhere.text

    # The scene's own words, and a pasted text no scene holds, still are.
    fake_container.ai.queue(PassageDraft())
    own = _read_text(
        auth_client,
        w,
        main["text_id"],
        expected_revision=_head(auth_client, w),
        scene_id=scene["id"],
    )
    assert own.status_code == 200, own.text
    pasted = _text(auth_client, w, PASTED)
    fake_container.ai.queue(PassageDraft())
    free = _read_text(
        auth_client, w, pasted["id"], expected_revision=_head(auth_client, w), scene_id=scene["id"]
    )
    assert free.status_code == 200, free.text
