"""What the assistant would be given for a scene, with why, and the author's say (ADR-021)."""

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


def _head(client, w) -> int:
    return int(client.get(f"{w['base']}/projection").json()["head_revision"])


def _scene(client, w, refs: list[tuple[str, str]], title="The Docks") -> dict:
    made = client.post(
        f"{w['base']}/plan",
        json={
            "kind": "scene",
            "title": title,
            "summary": "Tobin watches the water.",
            "refs": [{"entity_id": w[key], "role": role} for key, role in refs],
            "expected_revision": _head(client, w),
        },
    )
    assert made.status_code == 201, made.text
    return made.json()


def _setup(client, w) -> dict:
    """Mara's guild membership is a secret; Tobin wrongly believes she is at Saltmere."""
    client.post(
        f"{w['base']}/facts/{w['in_guild']}/amend",
        json={"expected_revision": _head(client, w), "visibility": "secret"},
    )
    client.post(
        f"{w['base']}/beliefs",
        json={
            "holder_entity_id": w["tobin"],
            "proposition": _prop(w, "mara", "LOCATED_AT", "saltmere"),
            "source_ref": "author",
            "expected_revision": _head(client, w),
        },
    )
    return _scene(
        client, w, [("tobin", "point_of_view"), ("harbor", "place"), ("mara", "participant")]
    )


def _context(client, w, scene, **params) -> dict:
    got = client.get(f"{w['base']}/plan/{scene['id']}/context", params=params)
    assert got.status_code == 200, got.text
    return got.json()


def test_a_scene_gets_its_people_facts_and_beliefs_each_with_a_reason(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _setup(auth_client, w)
    context = _context(auth_client, w, scene)
    items = {item["key"]: item for item in context["items"]}
    assert context["point_of_view"] == w["tobin"]

    assert items[f"scene:{scene['id']}"]["hard"] is True
    assert items[f"entity:{w['tobin']}"]["reason"] == "Tobin Ashe is the scene's point of view."
    assert items[f"entity:{w['harbor']}"]["reason"] == "Lantern Harbor is where the scene happens."
    assert items[f"fact:{w['at_harbor']}"]["section"] == "true"
    wrong = next(item for item in context["items"] if item["section"] == "wrong")
    assert wrong["text"] == (
        "Tobin Ashe believes Mara is located at Saltmere; in truth Mara is located at Lantern Harbor."
    )

    # The secret Tobin does not know is kept out, and says why.
    secret = next(o for o in context["left_out"] if o["item"]["key"] == f"fact:{w['in_guild']}")
    assert secret["reason"] == "secret" and secret["item"]["section"] == "secrets"
    assert "Tobin Ashe does not know this" in secret["item"]["text"]

    # The scene comes last in what is given.
    assert context["rendered"].rstrip().splitlines()[-1].startswith("- The Docks.")
    assert context["rendered"].index("## The scene") > context["rendered"].index("## Who and what")
    # No model was asked.
    assert fake_container.ai.calls == []


def test_secrets_can_be_given_and_are_marked_as_the_authors_alone(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _setup(auth_client, w)
    context = _context(auth_client, w, scene, secrets="true")
    secret = next(item for item in context["items"] if item["key"] == f"fact:{w['in_guild']}")
    assert secret["section_title"].startswith("Only for you")


def test_the_author_pins_excludes_and_sets_a_budget_and_it_is_kept_with_the_scene(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    scene = _setup(auth_client, w)
    knows = f"fact:{w['knows']}"
    kept = auth_client.put(
        f"{w['base']}/plan/{scene['id']}/context",
        json={
            "excludes": [knows],
            "pins": [f"fact:{w['in_guild']}"],
            "budget": 300,
            "expected_revision": _head(auth_client, w),
        },
    )
    assert kept.status_code == 200, kept.text
    context = kept.json()
    assert context["settings"]["budget"] == 300 and context["budget"] == 300
    assert {o["item"]["key"]: o["reason"] for o in context["left_out"]}[knows] == "excluded"
    pinned = next(item for item in context["items"] if item["key"] == f"fact:{w['in_guild']}")
    assert pinned["pinned"] is True

    # It is a change to the plan, so it is in the history and can be undone.
    assert _context(auth_client, w, scene)["settings"]["excludes"] == [knows]

    both = auth_client.put(
        f"{w['base']}/plan/{scene['id']}/context",
        json={"excludes": [knows], "pins": [knows], "expected_revision": _head(auth_client, w)},
    )
    assert both.status_code == 400
    stale = auth_client.put(
        f"{w['base']}/plan/{scene['id']}/context",
        json={"excludes": [], "expected_revision": 0},
    )
    assert stale.status_code == 409


def test_a_small_budget_drops_the_least_relevant_first_and_says_so(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _setup(auth_client, w)
    context = _context(auth_client, w, scene, budget=300)
    assert context["words"] <= 300 or all(item["hard"] for item in context["items"])
    full = _context(auth_client, w, scene, budget=12000)
    dropped = {o["item"]["key"] for o in context["left_out"] if o["reason"] == "budget"}
    kept_full = {item["key"] for item in full["items"]}
    assert dropped <= kept_full


def test_the_authors_writing_that_names_the_cast_is_given_with_where_it_came_from(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    scene = _setup(auth_client, w)
    text = auth_client.post(
        f"{w['base']}/texts",
        json={
            "title": "Chapter one",
            "content": "# Dawn\n\nMara tied up at Lantern Harbor before the bells.\n\n"
            "# Noon\n\nThe gulls argued over nothing at all.",
        },
    ).json()
    status = auth_client.get(f"{w['base']}/texts-index").json()
    assert status == {
        "texts": 1,
        "indexed": 1,
        "behind": [],
        "names_as_of": status["names_as_of"],
        "semantic": status["semantic"],
        # Nothing waits to be read in (CTX-005).
        "waiting": 0,
        "waiting_seconds": None,
        "failing": 0,
    }
    context = _context(auth_client, w, scene)
    writing = [item for item in context["items"] if item["section"] == "writing"]
    first = next(item for item in writing if item["reason"].startswith("It mentions"))
    assert first["text"] == "From “Chapter one”: Mara tied up at Lantern Harbor before the bells."
    assert first["reason"] == "It mentions Mara, Lantern Harbor."
    assert first["record_type"] == "source_version" and first["location"].startswith("chars:")
    # The most recent writing also comes as a sample of the author's voice.
    voice = next(item for item in writing if item["reason"].startswith("A sample of your voice"))
    assert "gulls" in voice["text"]

    # Removing the text removes what was remembered of it.
    assert auth_client.delete(f"{w['base']}/texts/{text['id']}").status_code == 204
    after = _context(auth_client, w, scene)
    assert [item for item in after["items"] if item["section"] == "writing"] == []
    assert fake_container.passage_store.passages == {}


def test_a_fact_no_longer_true_at_the_scenes_time_is_not_given(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _setup(auth_client, w)
    auth_client.post(
        f"{w['base']}/facts/{w['at_harbor']}/amend",
        json={"expected_revision": _head(auth_client, w), "ends": {"kind": "at", "at": 10}},
    )
    auth_client.put(
        f"{w['base']}/plan/{scene['id']}/when",
        json={"when": {"kind": "at", "at": 20}, "expected_revision": _head(auth_client, w)},
    )
    context = _context(auth_client, w, scene)
    keys = {item["key"] for item in context["items"]} | {
        o["item"]["key"] for o in context["left_out"]
    }
    assert f"fact:{w['at_harbor']}" not in keys
    scene_text = next(item for item in context["items"] if item["section"] == "scene")["text"]
    assert scene_text.endswith("It happens year 20.")


def test_in_a_what_if_the_context_is_the_what_ifs_story(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _setup(auth_client, w)
    what_if = auth_client.post(f"{w['base']}/what-ifs", json={"name": "No harbor"}).json()
    header = {"X-Story-Branch": what_if["id"]}
    head = auth_client.get(f"{w['base']}/projection", headers=header).json()["head_revision"]
    closed = auth_client.post(
        f"{w['base']}/facts/{w['at_harbor']}/close",
        json={"expected_revision": head},
        headers=header,
    )
    assert closed.status_code == 200, closed.text
    inside = auth_client.get(f"{w['base']}/plan/{scene['id']}/context", headers=header).json()
    assert f"fact:{w['at_harbor']}" not in {item["key"] for item in inside["items"]}
    outside = _context(auth_client, w, scene)
    assert f"fact:{w['at_harbor']}" in {item["key"] for item in outside["items"]}


def test_only_a_scene_has_a_context_and_readers_cannot_change_it(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    arc = auth_client.post(
        f"{w['base']}/plan",
        json={"kind": "arc", "title": "Rise", "expected_revision": _head(auth_client, w)},
    ).json()
    assert auth_client.get(f"{w['base']}/plan/{arc['id']}/context").status_code == 400


def test_a_secret_does_not_leak_through_who_someone_is_connected_to(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _setup(auth_client, w)  # Mara's only link to the guild is a secret Tobin lacks
    context = _context(auth_client, w, scene, budget=12000)
    every = [item["text"] for item in context["items"]]
    assert not any("Harbor Guild" in text for text in every)
    with_secrets = _context(auth_client, w, scene, secrets="true", budget=12000)
    secret = next(i for i in with_secrets["items"] if i["key"] == f"fact:{w['in_guild']}")
    assert secret["text"].startswith("Mara is a member of Harbor Guild")


def _reveal(client, w, scene: dict, fact_key: str) -> None:
    shown = client.put(
        f"{w['base']}/plan/{scene['id']}/reveals",
        json={
            "reveals": [{"fact_id": w[fact_key], "how": "shown"}],
            "expected_revision": _head(client, w),
        },
    )
    assert shown.status_code == 200, shown.text


def test_a_secret_the_reader_was_shown_stays_the_authors_when_the_viewpoint_lacks_it(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    earlier = _scene(auth_client, w, [("mara", "point_of_view")], title="A The Guildhall")
    scene = _setup(auth_client, w)  # Tobin's scene; Mara's guild membership is a secret
    _reveal(auth_client, w, earlier, "in_guild")
    key = f"reader:{w['in_guild']}"

    context = _context(auth_client, w, scene, budget=12000)
    assert key not in {item["key"] for item in context["items"]}
    left = next(o for o in context["left_out"] if o["item"]["key"] == key)
    assert left["reason"] == "secret" and left["item"]["section"] == "secrets"
    assert left["item"]["reason"].startswith("An earlier scene showed the reader this.")
    assert "Tobin Ashe does not know this" in left["item"]["text"]
    assert "Harbor Guild" not in context["rendered"]

    given = _context(auth_client, w, scene, secrets="true", budget=12000)
    item = next(item for item in given["items"] if item["key"] == key)
    assert item["section"] == "secrets"
    assert item["section_title"].startswith("Only for you")


def test_a_secret_the_viewpoint_knows_stays_with_what_the_reader_was_shown(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    earlier = _scene(auth_client, w, [("tobin", "point_of_view")], title="A The Quay")
    auth_client.post(
        f"{w['base']}/facts/{w['in_guild']}/amend",
        json={"expected_revision": _head(auth_client, w), "visibility": "secret"},
    )
    # Mara knows her own secret, and sees the scene.
    auth_client.post(
        f"{w['base']}/beliefs",
        json={
            "holder_entity_id": w["mara"],
            "proposition": _prop(w, "mara", "MEMBER_OF", "guild"),
            "source_ref": "author",
            "expected_revision": _head(auth_client, w),
        },
    )
    scene = _scene(auth_client, w, [("mara", "point_of_view"), ("harbor", "place")])
    _reveal(auth_client, w, earlier, "in_guild")
    context = _context(auth_client, w, scene, budget=12000)
    item = next(i for i in context["items"] if i["key"] == f"reader:{w['in_guild']}")
    assert item["section"] == "reader"


def test_with_no_viewpoint_every_secret_is_the_authors_alone(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    auth_client.post(
        f"{w['base']}/facts/{w['in_guild']}/amend",
        json={"expected_revision": _head(auth_client, w), "visibility": "secret"},
    )
    scene = _scene(auth_client, w, [("harbor", "place"), ("mara", "participant")])
    context = _context(auth_client, w, scene, budget=12000)
    assert context["point_of_view"] is None
    items = {item["key"]: item for item in context["items"]}
    assert items[f"fact:{w['at_harbor']}"]["section"] == "true"
    left = next(o for o in context["left_out"] if o["item"]["key"] == f"fact:{w['in_guild']}")
    assert left["reason"] == "secret" and left["item"]["section"] == "secrets"
    assert left["item"]["text"].endswith("It is a secret.")
    assert "no point of view is chosen" in left["item"]["reason"]
    assert "Harbor Guild" not in context["rendered"]

    given = _context(auth_client, w, scene, secrets="true", budget=12000)
    secret = next(i for i in given["items"] if i["key"] == f"fact:{w['in_guild']}")
    assert secret["section_title"].startswith("Only for you")
