"""Reading a written scene back into the story, for review (Phase 6)."""

import asyncio

import pytest

from backend.services.intake_workflow import PassageDraft, ReadFact
from tests.contract.test_studio import _save, _scene_world, _studio

pytestmark = pytest.mark.contract

FIRST = "Tobin waited at Lantern Harbor.\n\nMara tied up at Saltmere before dawn."
SECOND = FIRST + "\n\nTobin Ashe walked to Saltmere to find her."


def _run(container) -> list:
    return asyncio.run(container.job_runner.run_pending())


def _read(client, w, scene, branch: str | None = None):
    headers = {"X-Story-Branch": branch} if branch else {}
    return client.post(f"{w['base']}/plan/{scene['id']}/studio/readings", json={}, headers=headers)


def _moves(w, who: str, quote: str) -> PassageDraft:
    return PassageDraft(
        facts=[
            ReadFact(
                subject_ref=w[who], predicate="LOCATED_AT", object_ref=w["saltmere"], quote=quote
            )
        ]
    )


def _shown_moving(w, who: str, quote: str, place: str = "saltmere"):
    """A move a timed scene shows happening, so it takes the scene's time."""
    from backend.services.intake_workflow import SceneDraft, SceneFact

    return SceneDraft(
        facts=[
            SceneFact(
                subject_ref=w[who],
                predicate="LOCATED_AT",
                object_ref=w[place],
                quote=quote,
                changes_here=True,
            )
        ]
    )


def test_a_scene_is_read_into_proposals_that_point_at_its_words(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    never = _studio(auth_client, w, scene)["reading"]
    assert never["version_id"] is None and never["changed_since"] is False  # nothing written

    saved = _save(auth_client, w, scene, FIRST, None).json()
    assert saved["reading"]["changed_since"] is True
    fake_container.ai.queue(_moves(w, "mara", "Mara tied up at Saltmere"))
    started = _read(auth_client, w, scene)
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    (proposal,) = done.result["proposals"]

    # The proposal waits for review and points at the scene's version and words.
    detail = auth_client.get(f"{w['base']}/mutations/{proposal}").json()
    assert detail["requires_review"] is True
    for operation in detail["operations"]:
        (ref,) = operation["payload"]["evidence"]
        assert ref["source_artefact_id"] == saved["version_id"]

    after = _studio(auth_client, w, scene)["reading"]
    assert after["version_id"] == saved["version_id"] and after["version_number"] == 1
    assert after["proposals"] == [proposal] and after["changed_since"] is False
    # Reading again with nothing changed is refused, and costs nothing.
    again = _read(auth_client, w, scene)
    assert again.status_code == 400 and "Nothing has changed" in again.json()["detail"]


def test_the_second_reading_reads_only_what_changed(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    first = _save(auth_client, w, scene, FIRST, None).json()
    fake_container.ai.queue(PassageDraft())
    _read(auth_client, w, scene)
    _run(fake_container)

    second = _save(auth_client, w, scene, SECOND, first["version_id"]).json()
    assert second["reading"]["changed_since"] is True
    fake_container.ai.queue(_moves(w, "tobin", "Tobin Ashe walked to Saltmere"))
    _read(auth_client, w, scene)
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    assert done.result["passages"] == 1
    sent = fake_container.ai.calls[-1]["messages"][-1]["content"]
    assert "Tobin Ashe walked to Saltmere" in sent
    passage = sent.split("<<<PASSAGE-1>>>")[-1].split("<<<END PASSAGE-1>>>")[0]
    assert "Tobin Ashe walked to Saltmere" in passage and "Mara tied up" not in passage


def test_reading_without_a_provider_fails_in_plain_words_and_records_nothing(
    auth_client, fake_container
):
    from backend.domain.errors import CapabilityUnavailableError

    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, FIRST, None)
    fake_container.ai.queue(CapabilityUnavailableError("no key"))
    _read(auth_client, w, scene)
    (done,) = _run(fake_container)
    assert done.status.value == "failed" and done.error_code == "no_provider"
    assert _studio(auth_client, w, scene)["reading"]["changed_since"] is True


def test_a_what_if_reads_its_own_scene_into_its_own_story(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    main = _save(auth_client, w, scene, FIRST, None).json()
    fake_container.ai.queue(PassageDraft())
    _read(auth_client, w, scene)
    _run(fake_container)
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Storm"}).json()["id"]

    # The what-if starts knowing the scene was read, like the main story.
    assert _studio(auth_client, w, scene, branch)["reading"]["changed_since"] is False
    changed = _save(auth_client, w, scene, SECOND, main["version_id"], branch)
    assert changed.status_code == 200, changed.text
    assert changed.json()["reading"]["changed_since"] is True
    assert _studio(auth_client, w, scene)["reading"]["changed_since"] is False

    fake_container.ai.queue(_moves(w, "tobin", "Tobin Ashe walked to Saltmere"))
    assert _read(auth_client, w, scene, branch).status_code == 202
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    (proposal,) = done.result["proposals"]
    # The proposal belongs to the what-if, and would change only the what-if's story.
    stored = asyncio.run(
        fake_container.journal.get_mutation(
            workspace_id=fake_container.settings.AUTH_LOCAL_WORKSPACE_ID,
            project_id=w["base"].rsplit("/", 1)[1],
            mutation_id=proposal,
        )
    )
    assert stored is not None and stored.branch_id == branch


THREE = (
    "Tobin waited at Lantern Harbor.\n\nGulls cried over the water.\n\nMara tied up at Saltmere."
)


def test_a_reading_stopped_partway_is_resumed_and_proposes_nothing_twice(
    auth_client, fake_container
):
    from backend.ports import AIProviderError

    w, scene = _scene_world(auth_client, fake_container)
    first = _save(auth_client, w, scene, "Gulls cried over the water.", None).json()
    fake_container.ai.queue(PassageDraft())
    _read(auth_client, w, scene)
    _run(fake_container)
    # Two separate new lines: two passages. The first is read, the second fails.
    _save(auth_client, w, scene, THREE, first["version_id"])
    fake_container.ai.queue(_moves(w, "tobin", "Tobin waited at Lantern Harbor"))
    fake_container.ai.queue(AIProviderError("down"))
    _read(auth_client, w, scene)
    (failed,) = _run(fake_container)
    assert failed.status.value == "failed" and failed.error_code == "provider"
    assert _studio(auth_client, w, scene)["reading"]["changed_since"] is True

    # The next reading reads only the part not yet read.
    fake_container.ai.queue(_moves(w, "mara", "Mara tied up at Saltmere"))
    assert _read(auth_client, w, scene).status_code == 202
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    assert done.result["passages"] == 1
    passage = (
        fake_container.ai.calls[-1]["messages"][-1]["content"]
        .split("<<<PASSAGE-1>>>")[-1]
        .split("<<<END PASSAGE-1>>>")[0]
    )
    assert "Mara tied up at Saltmere" in passage and "Tobin waited" not in passage
    assert _studio(auth_client, w, scene)["reading"]["changed_since"] is False


def test_lines_with_single_breaks_are_read_one_by_one(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    first = _save(auth_client, w, scene, "Tobin waited.\nGulls cried.", None).json()
    fake_container.ai.queue(PassageDraft())
    _read(auth_client, w, scene)
    _run(fake_container)
    _save(
        auth_client,
        w,
        scene,
        "Tobin waited.\nGulls cried.\nMara tied up at Saltmere.",
        first["version_id"],
    )
    fake_container.ai.queue(PassageDraft())
    _read(auth_client, w, scene)
    _run(fake_container)
    passage = (
        fake_container.ai.calls[-1]["messages"][-1]["content"]
        .split("<<<PASSAGE-1>>>")[-1]
        .split("<<<END PASSAGE-1>>>")[0]
    )
    assert passage.strip() == "Mara tied up at Saltmere."


def test_one_reading_at_a_time_and_an_out_of_date_one_reads_nothing(auth_client, fake_container):
    from backend.domain.studio import SceneReading
    from tests.doubles import utcnow

    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, FIRST, None)
    assert _read(auth_client, w, scene).status_code == 202
    again = _read(auth_client, w, scene)
    assert again.status_code == 400 and "already being read" in again.json()["detail"]

    # Another reading finished first (as if from another worker): this one reads nothing.
    workspace_id = fake_container.settings.AUTH_LOCAL_WORKSPACE_ID
    project_id = w["base"].rsplit("/", 1)[1]
    branch = asyncio.run(
        fake_container.journal.working_branch(workspace_id=workspace_id, project_id=project_id)
    )
    studio = _studio(auth_client, w, scene)
    asyncio.run(
        fake_container.studio_store.save_reading(
            SceneReading(
                workspace_id=workspace_id,
                project_id=project_id,
                branch_id=branch.id,
                id="rdg_other",
                plan_id=scene["id"],
                source_id=studio["text_id"],
                version_id=studio["version_id"],
                job_id="job_other",
                passages=1,
                proposals=(),
                discarded=0,
                created_by=None,
                created_at=utcnow(),
            )
        )
    )
    (done,) = _run(fake_container)
    assert done.status.value == "failed" and "read into the story again" in done.error_message
    assert fake_container.ai.calls == []


def test_a_reader_cannot_start_a_reading(hosted_client, hosted_container):
    from tests.contract.test_studio import _demote, _hosted_scene

    base, scene, headers, workspace_id, user_id = _hosted_scene(hosted_client, hosted_container)
    saved = hosted_client.put(
        f"{base}/plan/{scene['id']}/studio/text", json={"content": FIRST}, headers=headers
    )
    assert saved.status_code == 200, saved.text
    _demote(hosted_container, workspace_id, user_id)
    started = hosted_client.post(
        f"{base}/plan/{scene['id']}/studio/readings", json={}, headers=headers
    )
    assert started.status_code == 403, started.text


# ------------------------------------------------- facts take the scene's story time (EXT-007)


def _at(client, w, scene, at: float) -> None:
    from tests.contract.test_context import _head

    timed = client.put(
        f"{w['base']}/plan/{scene['id']}/when",
        json={"when": {"kind": "at", "at": at}, "expected_revision": _head(client, w)},
    )
    assert timed.status_code == 200, timed.text


def _operations(client, w, mutation_id: str) -> list[dict]:
    detail = client.get(f"{w['base']}/mutations/{mutation_id}").json()
    return [operation["payload"] for operation in detail["operations"]]


def test_facts_read_from_a_timed_scene_start_at_its_time(auth_client, fake_container):
    from backend.services.intake_workflow import SceneDraft, SceneFact

    w, scene = _scene_world(auth_client, fake_container)
    _at(auth_client, w, scene, 12)
    text = "Tobin walked to Saltmere. He had served the Harbor Guild for years."
    _save(auth_client, w, scene, text, None)
    fake_container.ai.queue(
        SceneDraft(
            facts=[
                SceneFact(
                    subject_ref=w["tobin"],
                    predicate="LOCATED_AT",
                    object_ref=w["saltmere"],
                    quote="Tobin walked to Saltmere",
                    changes_here=True,
                ),
                # Membership cannot take a time: the schema refuses a span on it.
                SceneFact(
                    subject_ref=w["tobin"],
                    predicate="MEMBER_OF",
                    object_ref=w["guild"],
                    quote="He had served the Harbor Guild for years",
                    changes_here=True,
                ),
            ]
        )
    )
    _read(auth_client, w, scene)
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    assert done.result["timed"] == 1
    call = fake_container.ai.calls[-1]
    assert call["response_format"] is SceneDraft
    assert "Set changes_here to true only" in call["messages"][0]["content"]

    (proposal,) = done.result["proposals"]
    operations = _operations(auth_client, w, proposal)
    facts = {op["proposition"]["predicate"]: op for op in operations if op["op"] == "assert_fact"}
    assert facts["LOCATED_AT"]["starts"] == {"kind": "at", "at": 12.0, "label": None}
    assert facts["MEMBER_OF"].get("starts") is None
    validation = auth_client.get(f"{w['base']}/mutations/{proposal}").json()
    assert not [i for i in validation.get("validation", {}).get("issues", []) if i.get("blocking")]


def test_what_the_scene_does_not_show_starting_takes_no_start(auth_client, fake_container):
    from backend.services.intake_workflow import SceneDraft, SceneFact

    w, scene = _scene_world(auth_client, fake_container)
    _at(auth_client, w, scene, 12)
    _save(auth_client, w, scene, "Tobin had lived at Saltmere all his life.", None)
    fake_container.ai.queue(
        SceneDraft(
            facts=[
                SceneFact(
                    subject_ref=w["tobin"],
                    predicate="LOCATED_AT",
                    object_ref=w["saltmere"],
                    quote="Tobin had lived at Saltmere all his life",
                )
            ]
        )
    )
    _read(auth_client, w, scene)
    (done,) = _run(fake_container)
    assert done.result["timed"] == 0
    (proposal,) = done.result["proposals"]
    (fact,) = [op for op in _operations(auth_client, w, proposal) if op["op"] == "assert_fact"]
    assert fact.get("starts") is None


def test_a_new_place_ends_the_old_one_at_the_scenes_time(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _at(auth_client, w, scene, 12)
    _save(auth_client, w, scene, FIRST, None)
    # Mara is at Lantern Harbor (no time given); the scene moves her to Saltmere.
    fake_container.ai.queue(_shown_moving(w, "mara", "Mara tied up at Saltmere"))
    _read(auth_client, w, scene)
    (done,) = _run(fake_container)
    (proposal,) = done.result["proposals"]
    operations = _operations(auth_client, w, proposal)
    assert [op["op"] for op in operations] == ["amend_fact", "assert_fact"]
    assert operations[0]["id"] == w["at_harbor"]
    assert operations[0]["ends"] == {"kind": "at", "at": 12.0, "label": None}
    assert operations[1]["starts"] == operations[0]["ends"]

    assert auth_client.post(f"{w['base']}/mutations/{proposal}/approve", json={}).status_code == 200
    committed = auth_client.post(f"{w['base']}/mutations/{proposal}/commit")
    assert committed.status_code == 200, committed.text
    # Both places are kept: the harbor until the scene, Saltmere from it.
    then = auth_client.get(f"{w['base']}/timeline/moment", params={"at": 5}).json()
    now = auth_client.get(f"{w['base']}/timeline/moment", params={"at": 20}).json()

    def places(moment):
        return {
            item["fact"]["object"]["entity_id"]
            for item in moment["facts"]
            if item["fact"]["predicate"] == "LOCATED_AT" and item["fact"]["subject_id"] == w["mara"]
        }

    assert places(then) == {w["harbor"]} and places(now) == {w["saltmere"]}


def test_an_untimed_scene_reads_as_before(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, FIRST, None)
    fake_container.ai.queue(_moves(w, "mara", "Mara tied up at Saltmere"))
    _read(auth_client, w, scene)
    (done,) = _run(fake_container)
    assert fake_container.ai.calls[-1]["response_format"] is PassageDraft
    assert "changes_here" not in fake_container.ai.calls[-1]["messages"][0]["content"]
    (proposal,) = done.result["proposals"]
    operations = _operations(auth_client, w, proposal)
    assert [op["op"] for op in operations] == ["close_fact", "assert_fact"]
    assert operations[1].get("starts") is None


def _commit(client, w, proposal: str) -> None:
    assert client.post(f"{w['base']}/mutations/{proposal}/approve", json={}).status_code == 200
    committed = client.post(f"{w['base']}/mutations/{proposal}/commit")
    assert committed.status_code == 200, committed.text


def _mara_at(client, w, at: float) -> set[str]:
    moment = client.get(f"{w['base']}/timeline/moment", params={"at": at}).json()
    return {
        item["fact"]["object"]["entity_id"]
        for item in moment["facts"]
        if item["fact"]["predicate"] == "LOCATED_AT" and item["fact"]["subject_id"] == w["mara"]
    }


def _read_timed(client, container, w, scene, at: float, text: str, draft) -> list[dict]:
    """Read a scene set at ``at`` and return the operations of its one proposal."""
    _at(client, w, scene, at)
    assert _save(client, w, scene, text, None).status_code == 200
    container.ai.queue(draft)
    _read(client, w, scene)
    (done,) = _run(container)
    assert done.status.value == "succeeded", done
    (proposal,) = done.result["proposals"]
    return [{**op, "_proposal": proposal} for op in _operations(client, w, proposal)]


def test_going_back_to_an_earlier_place_ends_the_one_in_between(auth_client, fake_container):
    from tests.contract.test_context import _scene

    w, first = _scene_world(auth_client, fake_container)
    moved = _read_timed(
        auth_client,
        fake_container,
        w,
        first,
        5,
        FIRST,
        _shown_moving(w, "mara", "Mara tied up at Saltmere"),
    )
    _commit(auth_client, w, moved[0]["_proposal"])
    at_saltmere = next(op for op in moved if op["op"] == "assert_fact")

    # A later scene takes her back to the harbor, which she left at scene 5.
    second = _scene(auth_client, w, [("mara", "participant")], title="Z Home")
    back = _read_timed(
        auth_client,
        fake_container,
        w,
        second,
        9,
        "Mara sailed home to Lantern Harbor.",
        _shown_moving(w, "mara", "Mara sailed home to Lantern Harbor", place="harbor"),
    )
    assert [op["op"] for op in back] == ["amend_fact", "assert_fact"]
    assert back[0]["ends"] == {"kind": "at", "at": 9.0, "label": None}
    assert back[0]["id"] != w["at_harbor"]
    assert back[1]["proposition"]["object"]["entity_id"] == w["harbor"]
    assert back[1]["starts"] == back[0]["ends"]
    assert at_saltmere["proposition"]["object"]["entity_id"] == w["saltmere"]
    _commit(auth_client, w, back[0]["_proposal"])
    assert _mara_at(auth_client, w, 3) == {w["harbor"]}
    assert _mara_at(auth_client, w, 7) == {w["saltmere"]}
    assert _mara_at(auth_client, w, 12) == {w["harbor"]}


def test_a_scene_read_out_of_story_order_keeps_the_later_place(auth_client, fake_container):
    from tests.contract.test_context import _head, _scene

    w, later = _scene_world(auth_client, fake_container)
    made = auth_client.post(
        f"{w['base']}/entities",
        json={"type": "Location", "name": "Gull Rock", "expected_revision": _head(auth_client, w)},
    )
    assert made.status_code == 201, made.text
    w["rock"] = made.json()["id"]
    moved = _read_timed(
        auth_client,
        fake_container,
        w,
        later,
        20,
        FIRST,
        _shown_moving(w, "mara", "Mara tied up at Saltmere"),
    )
    _commit(auth_client, w, moved[0]["_proposal"])

    # An earlier scene, read afterwards, puts her on Gull Rock before she reaches Saltmere.
    earlier = _scene(auth_client, w, [("mara", "participant")], title="A Rock")
    ops = _read_timed(
        auth_client,
        fake_container,
        w,
        earlier,
        10,
        "Mara climbed onto Gull Rock.",
        _shown_moving(w, "mara", "Mara climbed onto Gull Rock", place="rock"),
    )
    assert [op["op"] for op in ops] == ["amend_fact", "assert_fact"]
    assert ops[0]["id"] == w["at_harbor"]
    assert ops[1]["starts"] == {"kind": "at", "at": 10.0, "label": None}
    assert ops[1]["ends"] == {"kind": "at", "at": 20.0, "label": None}
    _commit(auth_client, w, ops[0]["_proposal"])
    assert _mara_at(auth_client, w, 5) == {w["harbor"]}
    assert _mara_at(auth_client, w, 15) == {w["rock"]}
    assert _mara_at(auth_client, w, 25) == {w["saltmere"]}
