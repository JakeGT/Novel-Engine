"""Who knows what, telling, and what the reader is shown (ADR-020)."""

import pytest

from tests.contract.test_god_hand import _world

pytestmark = pytest.mark.contract


def _prop(w, subject: str, predicate: str, target: str) -> dict:
    project_id = w["base"].rsplit("/", 1)[1]
    return {
        "subject_id": w[subject],
        "predicate": predicate,
        "object": {"kind": "entity_ref", "entity_id": w[target], "project_id": project_id},
    }


def _believe(client, w, holder: str, prop: dict, head: int) -> dict:
    made = client.post(
        f"{w['base']}/beliefs",
        json={
            "holder_entity_id": w[holder],
            "proposition": prop,
            "source_ref": "author",
            "expected_revision": head,
        },
    )
    assert made.status_code == 201, made.text
    return made.json()


def _secret(client, w, fact: str, head: int, visibility: str = "secret") -> None:
    made = client.post(
        f"{w['base']}/facts/{w[fact]}/amend",
        json={"expected_revision": head, "visibility": visibility},
    )
    assert made.status_code == 200, made.text


def _knows(client, w, who: str) -> dict:
    return client.get(f"{w['base']}/knowledge/characters/{w[who]}").json()


def test_knowledge_is_what_a_character_believes_or_everyone_knows(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    _secret(auth_client, w, "in_guild", head)  # Mara's guild membership is a secret
    _secret(auth_client, w, "at_harbor", head + 1, "common")  # everyone knows where she is
    _believe(auth_client, w, "mara", _prop(w, "mara", "MEMBER_OF", "guild"), head + 2)
    # Tobin believes Mara is in Saltmere: wrong.
    _believe(auth_client, w, "tobin", _prop(w, "mara", "LOCATED_AT", "saltmere"), head + 3)

    mara = _knows(auth_client, w, "mara")
    assert {(k["fact"]["id"], k["how"]) for k in mara["knows"]} == {
        (w["in_guild"], "believes"),
        (w["at_harbor"], "everyone"),
    }
    assert mara["unaware"] == []

    tobin = _knows(auth_client, w, "tobin")
    assert [k["fact"]["id"] for k in tobin["knows"]] == [w["at_harbor"]]
    assert [f["id"] for f in tobin["unaware"]] == [w["in_guild"]]
    assert [(b["belief"]["object"]["entity_id"], b["fact"]["id"]) for b in tobin["wrong"]] == [
        (w["saltmere"], w["at_harbor"])
    ]
    # Nothing says Tobin knows Mara knows Tobin: ordinary, not recorded either way.
    assert tobin["unrecorded"] == 1

    who = auth_client.get(f"{w['base']}/knowledge/facts/{w['at_harbor']}").json()
    assert who["everyone"] is True and who["mistaken"] == [w["tobin"]]
    secret = auth_client.get(f"{w['base']}/knowledge/facts/{w['in_guild']}").json()
    assert secret["fact"]["visibility"] == "secret" and secret["holders"] == [w["mara"]]


def test_only_characters_know_things(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    refused = auth_client.get(f"{w['base']}/knowledge/characters/{w['harbor']}")
    assert refused.status_code == 400


def test_telling_gives_each_listener_a_belief_and_a_lie_is_simply_false(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    told = auth_client.post(
        f"{w['base']}/knowledge/tell",
        json={
            "speaker_id": w["mara"],
            "listener_ids": [w["tobin"]],
            "proposition": _prop(w, "mara", "LOCATED_AT", "saltmere"),
            "expected_revision": head,
        },
    )
    assert told.status_code == 201, told.text
    assert told.json()["beliefs"] == 1 and told.json()["revision"] == head + 1
    tobin = _knows(auth_client, w, "tobin")
    assert [b["belief"]["source_ref"] for b in tobin["wrong"]] == [told.json()["event_id"]]

    event = auth_client.get(f"{w['base']}/events/{told.json()['event_id']}").json()
    assert event["kind"] == "telling" and event["name"] == "Mara tells Tobin Ashe"
    assert {(p["entity_id"], p["role"]) for p in event["participants"]} == {
        (w["mara"], "speaker"),
        (w["tobin"], "listener"),
    }

    # Telling the truth to someone who already believes it adds nothing for them.
    again = auth_client.post(
        f"{w['base']}/knowledge/tell",
        json={
            "speaker_id": w["mara"],
            "listener_ids": [w["tobin"]],
            "proposition": _prop(w, "mara", "LOCATED_AT", "saltmere"),
            "expected_revision": head + 1,
        },
    )
    assert again.json()["already_knew"] == [w["tobin"]] and again.json()["beliefs"] == 0


def test_overhearing_is_believed_less_firmly(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    ines = auth_client.post(
        f"{w['base']}/entities",
        json={"type": "Character", "name": "Ines", "expected_revision": head},
    ).json()
    told = auth_client.post(
        f"{w['base']}/knowledge/tell",
        json={
            "speaker_id": w["mara"],
            "listener_ids": [w["tobin"]],
            "overheard_by": [ines["id"]],
            "fact_id": w["in_guild"],
            "expected_revision": head + 1,
        },
    )
    assert told.status_code == 201, told.text
    beliefs = auth_client.get(
        f"{w['base']}/beliefs", params={"holder_entity_id": ines["id"]}
    ).json()["items"]
    assert [b["confidence"] for b in beliefs] == [0.6]
    same_person = auth_client.post(
        f"{w['base']}/knowledge/tell",
        json={
            "speaker_id": w["mara"],
            "listener_ids": [w["mara"]],
            "fact_id": w["in_guild"],
            "expected_revision": head + 2,
        },
    )
    assert same_person.status_code == 400


def _scene(client, w, head: int, title: str, pov: str | None = None, parent=None) -> dict:
    refs = [{"entity_id": w[pov], "role": "point_of_view"}] if pov else []
    body = {"kind": "scene", "title": title, "refs": refs, "expected_revision": head}
    if parent:
        body["parent_id"] = parent
    made = client.post(f"{w['base']}/plan", json=body)
    assert made.status_code == 201, made.text
    return made.json()


def test_the_reader_is_shown_facts_scene_by_scene_and_irony_is_derived(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    _secret(auth_client, w, "in_guild", head)
    _believe(auth_client, w, "mara", _prop(w, "mara", "MEMBER_OF", "guild"), head + 1)
    head += 2
    first = _scene(auth_client, w, head, "The Docks", pov="tobin")
    second = _scene(auth_client, w, head + 1, "The Guildhall", pov="mara")
    third = _scene(auth_client, w, head + 2, "The Reckoning", pov="tobin")
    head += 3
    shown = auth_client.put(
        f"{w['base']}/plan/{first['id']}/reveals",
        json={"reveals": [{"fact_id": w["in_guild"], "how": "shown"}], "expected_revision": head},
    )
    assert shown.status_code == 200, shown.text
    assert shown.json()["reveals"] == [
        {"fact_id": w["in_guild"], "how": "shown", "main_reveal": False}
    ]
    # Plan notes stay out of sight of the reserved keys.
    assert "_reveals" not in shown.json()["details"]
    auth_client.put(
        f"{w['base']}/plan/{third['id']}/reveals",
        json={
            "reveals": [{"fact_id": w["in_guild"], "how": "shown"}],
            "expected_revision": head + 1,
        },
    )

    journey = auth_client.get(f"{w['base']}/reader").json()
    steps = {step["title"]: step for step in journey["steps"]}
    assert [s["title"] for s in journey["steps"]] == ["The Docks", "The Guildhall", "The Reckoning"]
    # The reader learns Mara's secret through Tobin's eyes; Tobin does not know it.
    assert steps["The Docks"]["irony"] == [w["in_guild"]]
    assert steps["The Guildhall"]["withheld"] == []
    assert steps["The Reckoning"]["repeated"] == [w["in_guild"]]
    assert w["in_guild"] in journey["facts"]

    bad = auth_client.put(
        f"{w['base']}/plan/{second['id']}/reveals",
        json={"reveals": [{"fact_id": "fact_gone"}], "expected_revision": head + 2},
    )
    assert bad.status_code == 400


def test_a_viewpoint_character_can_know_what_the_reader_has_not_been_shown(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    _secret(auth_client, w, "in_guild", head)
    _believe(auth_client, w, "mara", _prop(w, "mara", "MEMBER_OF", "guild"), head + 1)
    _scene(auth_client, w, head + 2, "The Guildhall", pov="mara")
    (step,) = auth_client.get(f"{w['base']}/reader").json()["steps"]
    assert step["withheld"] == [w["in_guild"]] and step["irony"] == []


def test_a_scene_set_earlier_than_the_one_before_it_is_a_flashback(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    first = _scene(auth_client, w, head, "Now")
    second = _scene(auth_client, w, head + 1, "Long ago")
    for scene, at, version in ((first, 20, head + 2), (second, 5, head + 3)):
        timed = auth_client.put(
            f"{w['base']}/plan/{scene['id']}/when",
            json={"when": {"kind": "at", "at": at}, "expected_revision": version},
        )
        assert timed.status_code == 200, timed.text
    steps = auth_client.get(f"{w['base']}/reader").json()["steps"]
    assert [s["flashback"] for s in steps] == [False, True]


def test_the_readers_guide_leaves_out_unshown_secrets_and_the_unmet(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    _secret(auth_client, w, "in_guild", head)
    first = _scene(auth_client, w, head + 1, "The Docks", pov="mara")
    # Mara and the harbor appear; Tobin and the guild do not.
    auth_client.patch(
        f"{w['base']}/plan/{first['id']}",
        json={
            "refs": [
                {"entity_id": w["mara"], "role": "point_of_view"},
                {"entity_id": w["harbor"], "role": "place"},
            ],
            "expected_revision": head + 2,
        },
    )
    second = _scene(auth_client, w, head + 3, "The Guildhall", pov="mara")
    auth_client.put(
        f"{w['base']}/plan/{second['id']}/reveals",
        json={"reveals": [{"fact_id": w["in_guild"]}], "expected_revision": head + 4},
    )

    early = auth_client.get(f"{w['base']}/reader/guide", params={"scene": first["id"]}).json()
    assert "Mara is located at Lantern Harbor." in early["markdown"]
    assert "Harbor Guild" not in early["markdown"] and "Tobin" not in early["markdown"]
    later = auth_client.get(f"{w['base']}/reader/guide", params={"scene": second["id"]}).json()
    assert "Mara is a member of Harbor Guild." in later["markdown"]

    as_tobin = auth_client.get(
        f"{w['base']}/reader/guide", params={"scene": second["id"], "viewpoint": w["tobin"]}
    ).json()
    assert as_tobin["title"].startswith("As Tobin Ashe knows it")
    assert "Harbor Guild." not in as_tobin["markdown"]


def _guide(client, w, scene: dict, **params):
    return client.get(f"{w['base']}/reader/guide", params={"scene": scene["id"], **params})


def test_a_guide_as_the_story_stood_at_an_earlier_version(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    made = auth_client.post(
        f"{w['base']}/plan",
        json={
            "kind": "scene",
            "title": "The Docks",
            "refs": [
                {"entity_id": w["mara"], "role": "point_of_view"},
                {"entity_id": w["harbor"], "role": "place"},
                {"entity_id": w["saltmere"], "role": "participant"},
            ],
            "expected_revision": head,
        },
    )
    assert made.status_code == 201, made.text
    scene = made.json()
    then = head + 1
    # Afterwards: Mara is renamed, leaves the harbor and goes to Saltmere.
    renamed = auth_client.patch(
        f"{w['base']}/entities/{w['mara']}", json={"name": "Mara Venn", "expected_revision": then}
    )
    assert renamed.status_code == 200, renamed.text
    closed = auth_client.post(
        f"{w['base']}/facts/{w['at_harbor']}/close", json={"expected_revision": then + 1}
    )
    assert closed.status_code == 200, closed.text
    moved = auth_client.post(
        f"{w['base']}/facts",
        json={
            "proposition": _prop(w, "mara", "LOCATED_AT", "saltmere"),
            "expected_revision": then + 2,
        },
    )
    assert moved.status_code == 201, moved.text
    latest = then + 3

    now = _guide(auth_client, w, scene).json()
    assert now["version"] == latest and now["title"].endswith(f"version {latest}")
    assert "Mara Venn is located at Saltmere." in now["markdown"]
    assert "Lantern Harbor." not in now["markdown"].split("## ")[-1]

    old = _guide(auth_client, w, scene, version=then)
    assert old.status_code == 200, old.text
    guide = old.json()
    # Read as it stood: the old name, where she was then, nothing added since.
    assert guide["version"] == then and guide["title"].endswith(f"version {then}")
    assert "Mara is located at Lantern Harbor." in guide["markdown"]
    assert "Mara Venn" not in guide["markdown"] and "located at Saltmere" not in guide["markdown"]
    people = next(s for s in guide["sections"] if s["heading"] == "People")
    assert people["entries"][0]["name"] == "Mara"
    assert guide["bound_to"] == (
        f"The story itself, as it stood at version {then}; up to scene 1 of 1, “The Docks”; "
        "as the reader knows it."
    )
    # The export's header says exactly what it shows.
    header = guide["markdown"].split("---\n")[1]
    assert f"version: {then}\n" in header
    assert 'story_line: "the story itself"\n' in header
    assert "up_to_scene: 1\nof_scenes: 1\n" in header and 'scene: "The Docks"\n' in header
    assert 'seen_as: "the reader"\n' in header and "guide_rules: 1\n" in header


def test_a_guide_at_a_version_refuses_what_did_not_exist_then(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    scene = _scene(auth_client, w, head, "The Docks", pov="mara")
    later = _guide(auth_client, w, scene, version=head + 2)
    assert later.status_code == 400 and "no version" in later.json()["detail"]
    before = _guide(auth_client, w, scene, version=head)
    assert before.status_code == 404
    assert f"not in the plan at version {head}" in before.json()["detail"]
    # Tobin did not exist at version 1, so no guide can be seen through his eyes then.
    early = auth_client.post(
        f"{w['base']}/plan", json={"kind": "scene", "title": "Early", "expected_revision": head + 1}
    )
    assert early.status_code == 201
    as_tobin = _guide(auth_client, w, scene, viewpoint=w["tobin"], version=1)
    assert as_tobin.status_code == 404


def test_a_guide_in_a_what_if_names_the_what_if(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    scene = _scene(auth_client, w, head, "The Docks", pov="mara")
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Storm"}).json()["id"]
    guide = auth_client.get(
        f"{w['base']}/reader/guide",
        params={"scene": scene["id"], "version": head + 1, "viewpoint": w["mara"]},
        headers={"X-Story-Branch": branch},
    ).json()
    assert guide["story_line"] == "the what-if “Storm”"
    assert guide["viewpoint_name"] == "Mara" and 'seen_as: "Mara"' in guide["markdown"]
    assert guide["bound_to"].startswith("The what-if “Storm”, as it stood at version")


@pytest.mark.parametrize("page", [2, 3, 2000])
def test_the_story_rebuilt_at_the_latest_version_is_the_story_now(
    auth_client, fake_container, monkeypatch, page
):
    import asyncio

    from backend.services import story_at_version
    from backend.services.story_time_service import read_story

    w = _world(auth_client, fake_container)
    head = int(w["head"])
    _secret(auth_client, w, "in_guild", head)
    _believe(auth_client, w, "tobin", _prop(w, "mara", "LOCATED_AT", "saltmere"), head + 1)
    _scene(auth_client, w, head + 2, "The Docks", pov="mara")
    auth_client.post(f"{w['base']}/facts/{w['knows']}/close", json={"expected_revision": head + 3})
    # Small pages make the rebuild cross page boundaries mid-way through the history.
    monkeypatch.setattr(story_at_version, "_PAGE", page)
    workspace, project = (
        fake_container.settings.AUTH_LOCAL_WORKSPACE_ID,
        w["base"].rsplit("/", 1)[1],
    )
    canon = fake_container.canon_repository

    rebuilt = asyncio.run(story_at_version.story_at(canon, workspace, project, head + 4))
    live = asyncio.run(read_story(canon, workspace, project))

    # What a guide reads; which schema and provenance a record came with is not replayed.
    def entity(e):
        return (e.id, e.type, e.name, e.aliases, e.attributes, e.lifecycle)

    def fact(f):
        return (f.id, f.proposition, f.starts, f.ends, f.visibility, f.created_revision)

    assert {k: entity(v) for k, v in rebuilt.story.entities.items()} == {
        k: entity(v) for k, v in live.entities.items()
    }
    assert [fact(f) for f in rebuilt.story.facts] == [fact(f) for f in live.facts]
    assert [b.id for b in rebuilt.beliefs] == [
        b.id for b in asyncio.run(canon.list_beliefs(workspace_id=workspace, project_id=project))
    ]
    assert [p.title for p in rebuilt.plan_items] == ["The Docks"]
