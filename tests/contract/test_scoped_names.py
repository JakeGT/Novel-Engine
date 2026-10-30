"""Name answers that hold only in part of the story (CTX-016), through the HTTP surface.

"From this scene on, 'the captain' means Roe; before that, Mara." "In scenes seen by
Tobin, 'the old man' is his father." "In this what-if, 'the stranger' is Roe." Each is
read the same way by a text, the scene studio's checks and the scene's context.
"""

import pytest

from tests.contract.test_context import _head, _scene
from tests.contract.test_god_hand import _world

pytestmark = pytest.mark.contract


def _in(branch: str | None) -> dict:
    return {"X-Story-Branch": branch} if branch else {}


def _entry(client, w, key: str, name: str) -> None:
    made = client.post(
        f"{w['base']}/entities",
        json={"type": "Character", "name": name, "expected_revision": _head(client, w)},
    )
    assert made.status_code == 201, made.text
    w[key] = made.json()["id"]


def _story(client, container) -> tuple[dict, list[dict]]:
    """Three scenes in reading order: the first and second seen by Tobin, the third by Mara."""
    w = _world(client, container)
    _entry(client, w, "roe", "Roe")
    _entry(client, w, "father", "Old Ashe")
    scenes = [
        _scene(client, w, [("tobin", "point_of_view"), ("harbor", "place")], title="Dawn"),
        _scene(client, w, [("tobin", "point_of_view"), ("roe", "participant")], title="Noon"),
        _scene(client, w, [("mara", "point_of_view"), ("roe", "participant")], title="Dusk"),
    ]
    return w, scenes


def _write(client, w, scene: dict, content: str, branch: str | None = None) -> dict:
    state = client.get(f"{w['base']}/plan/{scene['id']}/studio", headers=_in(branch)).json()
    saved = client.put(
        f"{w['base']}/plan/{scene['id']}/studio/text",
        json={"content": content, "base_version_id": state.get("version_id")},
        headers=_in(branch),
    )
    assert saved.status_code == 200, saved.text
    return saved.json()


def _decide(client, w, branch: str | None = None, **body):
    return client.post(f"{w['base']}/names", json=body, headers=_in(branch))


def _named(client, w, text_id: str, phrase: str, branch: str | None = None) -> list[list[str]]:
    got = client.get(f"{w['base']}/texts/{text_id}", headers=_in(branch))
    assert got.status_code == 200, got.text
    return [
        m["entity_ids"] for m in got.json()["mentions"] if m["phrase"].lower() == phrase.lower()
    ]


def _checks(client, w, scene: dict, branch: str | None = None) -> list[str]:
    state = client.get(f"{w['base']}/plan/{scene['id']}/studio", headers=_in(branch)).json()
    return [check["code"] for check in state["checks"]]


def _writing(client, w, scene: dict, branch: str | None = None) -> list[str]:
    got = client.get(
        f"{w['base']}/plan/{scene['id']}/context",
        params={"budget": 12000},
        headers=_in(branch),
    )
    assert got.status_code == 200, got.text
    return [
        item["text"]
        for item in got.json()["items"]
        if item["section"] == "writing" and not item["reason"].startswith("A sample")
    ]


# ------------------------------------------------------------------ reading a text


def test_from_a_scene_on_the_captain_is_roe_and_before_it_mara(auth_client, fake_container):
    w, (dawn, noon, dusk) = _story(auth_client, fake_container)
    texts = {
        key: _write(auth_client, w, scene, "The captain waved from the quay.")["text_id"]
        for key, scene in (("dawn", dawn), ("noon", noon), ("dusk", dusk))
    }
    notes = auth_client.post(
        f"{w['base']}/texts", json={"title": "Notes", "content": "The captain owes money."}
    ).json()["id"]

    until = _decide(
        auth_client,
        w,
        phrase="the captain",
        verdict="is",
        entity_id=w["mara"],
        until_scene_id=dawn["id"],
    )
    assert until.status_code == 201, until.text
    assert until.json()["until_scene_id"] == dawn["id"] and until.json()["branch_id"] is None
    since = _decide(
        auth_client,
        w,
        phrase="the captain",
        verdict="is",
        entity_id=w["roe"],
        from_scene_id=noon["id"],
    )
    assert since.status_code == 201, since.text

    assert _named(auth_client, w, texts["dawn"], "the captain") == [[w["mara"]]]
    assert _named(auth_client, w, texts["noon"], "the captain") == [[w["roe"]]]
    assert _named(auth_client, w, texts["dusk"], "the captain") == [[w["roe"]]]
    # A text that is no scene has no place in reading order: neither answer holds there.
    assert _named(auth_client, w, notes, "the captain") == []

    memory = auth_client.get(f"{w['base']}/names").json()
    assert {item["entity_id"] for item in memory["items"]} == {w["mara"], w["roe"]}
    assert memory["scenes"] == {dawn["id"]: "Dawn", noon["id"]: "Noon"}

    # Moving the last scene to the start changes what the phrase means there.
    moved = auth_client.post(
        f"{w['base']}/plan/{dusk['id']}/move",
        json={"before_id": dawn["id"], "expected_revision": _head(auth_client, w)},
    )
    assert moved.status_code == 200, moved.text
    # Dusk now comes before Dawn, where "the captain" is still Mara.
    assert _named(auth_client, w, texts["dusk"], "the captain") == [[w["mara"]]]


def test_in_scenes_seen_by_tobin_the_old_man_is_his_father(auth_client, fake_container):
    w, (dawn, noon, dusk) = _story(auth_client, fake_container)
    seen_by_tobin = _write(auth_client, w, noon, "The old man mended nets.")["text_id"]
    seen_by_mara = _write(auth_client, w, dusk, "The old man mended nets.")["text_id"]
    said = _decide(
        auth_client,
        w,
        phrase="the old man",
        verdict="is",
        entity_id=w["father"],
        viewpoint_id=w["tobin"],
    )
    assert said.status_code == 201, said.text
    assert _named(auth_client, w, seen_by_tobin, "the old man") == [[w["father"]]]
    assert _named(auth_client, w, seen_by_mara, "the old man") == []


def test_limits_that_make_no_sense_are_refused_in_plain_words(auth_client, fake_container):
    w, (dawn, noon, _dusk) = _story(auth_client, fake_container)
    backwards = _decide(
        auth_client,
        w,
        phrase="the captain",
        verdict="is",
        entity_id=w["roe"],
        from_scene_id=noon["id"],
        until_scene_id=dawn["id"],
    )
    assert backwards.status_code == 400 and "after" in backwards.json()["detail"]
    nowhere = _decide(
        auth_client, w, phrase="the captain", verdict="is", entity_id=w["roe"], from_scene_id="x"
    )
    assert nowhere.status_code == 404
    nobody = _decide(
        auth_client, w, phrase="the captain", verdict="is", entity_id=w["roe"], viewpoint_id="x"
    )
    assert nobody.status_code == 404
    main = _decide(
        auth_client, w, phrase="the captain", verdict="is", entity_id=w["roe"], what_if_only=True
    )
    assert main.status_code == 400 and "main story" in main.json()["detail"]


# ------------------------------------------------------------------- what-ifs


def test_an_answer_given_in_a_what_if_stays_in_it_unless_asked(auth_client, fake_container):
    w, (dawn, _noon, _dusk) = _story(auth_client, fake_container)
    text = _write(auth_client, w, dawn, "The stranger mended nets.")["text_id"]
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Roe stays"}).json()["id"]

    inside = _decide(
        auth_client, w, branch, phrase="the stranger", verdict="is", entity_id=w["roe"]
    )
    assert inside.status_code == 201, inside.text
    assert inside.json()["branch_id"] == branch
    assert _named(auth_client, w, text, "the stranger", branch) == [[w["roe"]]]
    assert _named(auth_client, w, text, "the stranger") == []

    everywhere = _decide(
        auth_client,
        w,
        branch,
        phrase="the lantern man",
        verdict="is",
        entity_id=w["roe"],
        what_if_only=False,
    )
    assert everywhere.json()["branch_id"] is None
    _write(auth_client, w, dawn, "The lantern man mended nets.")
    assert _named(auth_client, w, text, "the lantern man") == [[w["roe"]]]


# ----------------------------------------------------------- the studio's checks


def test_the_studios_checks_read_names_as_the_scene_does(auth_client, fake_container):
    w, (dawn, noon, _dusk) = _story(auth_client, fake_container)
    _write(auth_client, w, dawn, 'Then @"the captain" spoke. The boy listened at the harbour.')
    _write(auth_client, w, noon, 'Then @"the captain" spoke. The boy listened.')
    assert "new_name" in _checks(auth_client, w, dawn)
    assert "new_name" in _checks(auth_client, w, noon)
    assert "viewpoint_missing" in _checks(auth_client, w, noon)

    for phrase, entity in (("the captain", "roe"), ("the boy", "tobin")):
        said = _decide(
            auth_client,
            w,
            phrase=phrase,
            verdict="is",
            entity_id=w[entity],
            from_scene_id=noon["id"],
        )
        assert said.status_code == 201, said.text

    # From Noon on, the marked name is known and "the boy" is the viewpoint.
    assert "new_name" not in _checks(auth_client, w, noon)
    assert "viewpoint_missing" not in _checks(auth_client, w, noon)
    # Before it, nothing changed.
    assert "new_name" in _checks(auth_client, w, dawn)
    assert "viewpoint_missing" in _checks(auth_client, w, dawn)


# -------------------------------------------------------------- the scene's context


def test_the_scenes_context_takes_passages_as_this_line_reads_them(auth_client, fake_container):
    w, (dawn, _noon, dusk) = _story(auth_client, fake_container)
    _write(auth_client, w, dawn, "The stranger mended nets by the harbour wall.")
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Roe stays"}).json()["id"]
    said = _decide(auth_client, w, branch, phrase="the stranger", verdict="is", entity_id=w["roe"])
    assert said.status_code == 201, said.text

    # Dusk has Roe in it, and not Old Ashe; it is two scenes on from Dawn, because the end of
    # the scene just before is always given as written (CTX-006A). In the what-if, Dawn's
    # passage names Roe; in the main story it names nobody.
    assert any("stranger mended nets" in text for text in _writing(auth_client, w, dusk, branch))
    assert not any("stranger mended nets" in text for text in _writing(auth_client, w, dusk))

    # And the other way: the main story says "the stranger" is Roe, the what-if says Old Ashe.
    _decide(
        auth_client, w, phrase="the stranger", verdict="is", entity_id=w["roe"], what_if_only=False
    )
    assert any("stranger mended nets" in text for text in _writing(auth_client, w, dusk))
    _decide(auth_client, w, branch, phrase="the stranger", verdict="is", entity_id=w["father"])
    assert not any(
        "stranger mended nets" in text for text in _writing(auth_client, w, dusk, branch)
    )


# ------------------------------------------------------------------- examples


def test_examples_are_kept_with_an_answer_and_checked_against_todays(auth_client, fake_container):
    w, (dawn, noon, dusk) = _story(auth_client, fake_container)
    content = "At dusk the captain rowed home alone."
    text = _write(auth_client, w, dusk, content)
    start = content.index("the captain")

    said = _decide(
        auth_client,
        w,
        phrase="the captain",
        verdict="is",
        entity_id=w["roe"],
        from_scene_id=noon["id"],
        example={
            "source_id": text["text_id"],
            "start": start,
            "end": start + len("the captain"),
        },
    )
    assert said.status_code == 201, said.text
    (example,) = said.json()["examples"]
    assert example["quote"] == content and example["means"] is True
    assert example["scene_id"] == dusk["id"]
    assert content[example["at"][0] : example["at"][1]] == "the captain"

    typed = auth_client.post(
        f"{w['base']}/names/{said.json()['id']}/examples",
        json={"quote": "The captain of the ferry is someone else.", "means": False},
    )
    assert typed.status_code == 201, typed.text
    assert [e["means"] for e in typed.json()["examples"]] == [True, False]
    wrong = auth_client.post(
        f"{w['base']}/names/{said.json()['id']}/examples", json={"quote": "Nobody here."}
    )
    assert wrong.status_code == 400 and "the captain" in wrong.json()["detail"]

    def checked() -> dict[str, bool | None]:
        (item,) = [
            i
            for i in auth_client.get(f"{w['base']}/names").json()["items"]
            if i["id"] == said.json()["id"]
        ]
        return {e["quote"]: e["holds"] for e in item["examples"]}

    assert checked()[content] is True
    # A narrower answer given later reads Dusk differently: the example no longer holds.
    _decide(
        auth_client,
        w,
        phrase="the captain",
        verdict="is",
        entity_id=w["mara"],
        from_scene_id=dusk["id"],
        until_scene_id=dusk["id"],
    )
    assert checked()[content] is False

    # The same answer given again keeps its examples; one can be removed.
    again = _decide(
        auth_client,
        w,
        phrase="the captain",
        verdict="is",
        entity_id=w["roe"],
        from_scene_id=noon["id"],
    )
    assert len(again.json()["examples"]) == 2
    removed = auth_client.delete(f"{w['base']}/names/{again.json()['id']}/examples/{example['id']}")
    assert removed.status_code == 200 and len(removed.json()["examples"]) == 1


@pytest.mark.asyncio
async def test_scoped_answers_and_examples_travel_in_the_archive(auth_client, fake_container):
    w, (dawn, noon, _dusk) = _story(auth_client, fake_container)
    _decide(
        auth_client,
        w,
        phrase="the captain",
        verdict="is",
        entity_id=w["roe"],
        from_scene_id=noon["id"],
        viewpoint_id=w["tobin"],
        example={"quote": "The captain waved.", "scene_id": noon["id"]},
    )
    archive = auth_client.post(f"{w['base']}/archive/export").json()
    (name,) = archive["writing"]["names"]
    assert name["from_scene_id"] == noon["id"] and name["viewpoint_id"] == w["tobin"]
    assert "branch_id" not in name and "until_scene_id" not in name

    workspace_id = archive["project"]["workspace_id"]
    await fake_container.archive_service.import_project(workspace_id, "restored", archive)
    (back,) = await fake_container.sources.list_bindings(
        workspace_id=workspace_id, project_id="restored"
    )
    assert (back.from_scene_id, back.viewpoint_id) == (noon["id"], w["tobin"])
    assert [e.quote for e in back.examples] == ["The captain waved."]


# ------------------------------------------------------------ reading a scene back


def test_reading_a_scene_back_settles_names_as_that_scene_reads_them(auth_client, fake_container):
    import asyncio

    from backend.services.intake_workflow import PassageDraft

    w, (dawn, noon, _dusk) = _story(auth_client, fake_container)
    for scene in (dawn, noon):
        _write(auth_client, w, scene, "The captain tied up at Saltmere before dawn.")
    _decide(
        auth_client,
        w,
        phrase="the captain",
        verdict="is",
        entity_id=w["roe"],
        from_scene_id=noon["id"],
    )

    def settled(scene: dict) -> bool:
        fake_container.ai.queue(PassageDraft())
        started = auth_client.post(f"{w['base']}/plan/{scene['id']}/studio/readings", json={})
        assert started.status_code == 202, started.text
        asyncio.run(fake_container.job_runner.run_pending())
        sent = " ".join(m["content"] for m in fake_container.ai.calls[-1]["messages"])
        return f'"The captain" means {w["roe"]}' in sent

    assert settled(noon) is True
    assert settled(dawn) is False
