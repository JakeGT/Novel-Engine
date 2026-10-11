"""The story plan by hand: an outline of intent, built with no provider, kept apart from fact."""

import pytest

from tests.contract.test_canon_entities import _project_id, _publish_schema

pytestmark = pytest.mark.contract


class Plan:
    """A small driver over the HTTP surface that keeps track of the current version."""

    def __init__(self, client, container) -> None:
        self.client = client
        self.project_id = _project_id(container)
        _publish_schema(client, self.project_id)
        self.base = f"/api/v1/projects/{self.project_id}"
        self.head = 0

    def entry(self, entity_type: str, name: str) -> str:
        made = self.client.post(
            f"{self.base}/entities",
            json={"type": entity_type, "name": name, "expected_revision": self.head},
        )
        assert made.status_code == 201, made.text
        self.head += 1
        return made.json()["id"]

    def add(self, kind: str, title: str, **fields) -> dict:
        made = self.client.post(
            f"{self.base}/plan",
            json={"kind": kind, "title": title, "expected_revision": self.head, **fields},
        )
        assert made.status_code == 201, made.text
        self.head += 1
        return made.json()

    def outline(self) -> dict:
        return self.client.get(f"{self.base}/plan").json()

    def open_for(self, plan_id: str) -> list[str]:
        return [item["code"] for item in self.outline()["open"] if item["plan_id"] == plan_id]


def test_a_plan_is_built_by_hand_from_story_down_to_scene(auth_client, fake_container) -> None:
    plan = Plan(auth_client, fake_container)
    ines = plan.entry("Character", "Ines Varga")
    point = plan.entry("Location", "Kestrel Point")

    story = plan.add("story", "The Kestrel Light", summary="A mapmaker charts the shoals.")
    arc = plan.add(
        "arc",
        "Ines learns the truth",
        parent_id=story["id"],
        refs=[{"entity_id": ines, "role": "about"}],
    )
    beat = plan.add("beat", "She finds the false chart", parent_id=arc["id"])
    second = plan.add("beat", "She confronts her father", parent_id=arc["id"])
    thread = plan.add("thread", "Who drew the false chart?")
    scene = plan.add(
        "scene",
        "The lamp room at night",
        parent_id=beat["id"],
        summary="Ines compares the two charts and sees the difference.",
        refs=[
            {"entity_id": ines, "role": "point_of_view"},
            {"entity_id": point, "role": "place"},
        ],
        sets_up=[thread["id"]],
    )

    outline = plan.outline()
    by_id = {item["id"]: item for item in outline["items"]}
    assert [by_id[i]["kind"] for i in (story["id"], arc["id"], beat["id"], scene["id"])] == [
        "story",
        "arc",
        "beat",
        "scene",
    ]
    # New items go last among what they sit beside.
    assert (by_id[beat["id"]]["position"], by_id[second["id"]]["position"]) == (0, 1)
    assert by_id[scene["id"]]["sets_up"] == [thread["id"]]
    # No id of the engine's is needed to read the outline: every item carries its title.
    assert all(item["title"] for item in outline["items"])


def test_what_a_plan_still_needs_is_said_in_plain_words_and_is_not_an_error(
    auth_client, fake_container
) -> None:
    plan = Plan(auth_client, fake_container)
    ines = plan.entry("Character", "Ines Varga")
    point = plan.entry("Location", "Kestrel Point")
    arc = plan.add("arc", "Ines learns the truth")
    beat = plan.add("beat", "She finds the false chart", parent_id=arc["id"])
    scene = plan.add("scene", "The lamp room", parent_id=beat["id"])
    thread = plan.add("thread", "Who drew the false chart?")

    messages = {item["code"]: item["message"] for item in plan.outline()["open"]}
    assert messages["needs_place"] == "This scene needs a place."
    assert messages["needs_point_of_view"] == "This scene needs a point of view."
    assert messages["needs_intent"] == "This scene does not say what happens yet."
    assert messages["needs_owner"] == "This arc does not say whose it is."
    assert messages["needs_setup"] == "Nothing sets this thread up yet."
    # A beat with a scene, and an arc with a beat, need nothing more of that kind.
    assert plan.open_for(beat["id"]) == []

    # Filling the gaps closes them; opening a thread makes its payoff the next thing to do.
    changed = auth_client.patch(
        f"{plan.base}/plan/{scene['id']}",
        json={
            "summary": "Ines sees the two charts differ.",
            "refs": [
                {"entity_id": ines, "role": "point_of_view"},
                {"entity_id": point, "role": "place"},
            ],
            "sets_up": [thread["id"]],
            "expected_revision": plan.head,
        },
    )
    assert changed.status_code == 200, changed.text
    plan.head += 1
    assert plan.open_for(scene["id"]) == []
    assert plan.open_for(thread["id"]) == ["needs_payoff"]

    # An entry the plan names being archived is reported, not hidden and not blocked.
    retired = auth_client.post(
        f"{plan.base}/entities/{point}/retire",
        json={"expected_revision": plan.head, "acknowledge_high_impact": True},
    )
    assert retired.status_code == 200, retired.text
    plan.head += 1
    assert "names_archived_entry" in plan.open_for(scene["id"])


def test_planned_intent_never_appears_as_fact_in_any_reading_of_the_story(
    auth_client, fake_container
) -> None:
    plan = Plan(auth_client, fake_container)
    ines = plan.entry("Character", "Ines Varga")
    story = plan.add("story", "Ines Varga dies at Kestrel Point", summary="She drowns.")
    plan.add(
        "scene",
        "The drowning",
        parent_id=story["id"],
        summary="Ines Varga drowns off the Point.",
        refs=[{"entity_id": ines, "role": "point_of_view"}],
    )

    def reads(path: str, **params) -> str:
        response = auth_client.get(f"{plan.base}{path}", params=params)
        assert response.status_code == 200, (path, response.text)
        return response.text

    story_bible = "".join(
        (
            reads("/entities", limit=200),
            reads("/entities", limit=200, include_retired="true"),
            reads("/entities/search", q="drown"),
            reads("/entities/search", q="Kestrel"),
            reads("/entities/needs-detail"),
            reads("/facts", limit=200),
            reads("/beliefs", limit=200),
            reads("/events", limit=200),
            reads(f"/entities/{ines}"),
        )
    )
    for intent in ("drown", "The drowning", "dies at Kestrel Point"):
        assert intent not in story_bible
    # The graph the story is read from holds nothing of the plan either.
    writer = fake_container.canon_writer
    graph = str(getattr(writer, "entities", "")) + str(getattr(writer, "events", ""))
    assert "drown" not in graph
    # The plan is where it belongs, and only there.
    assert "The drowning" in auth_client.get(f"{plan.base}/plan").text


def test_the_outline_refuses_what_makes_no_sense_and_says_why(auth_client, fake_container) -> None:
    plan = Plan(auth_client, fake_container)
    ines = plan.entry("Character", "Ines Varga")
    point = plan.entry("Location", "Kestrel Point")
    story = plan.add("story", "The Kestrel Light")
    arc = plan.add("arc", "An arc", parent_id=story["id"])
    scene = plan.add("scene", "A scene", parent_id=arc["id"])

    def refused(**fields) -> str:
        response = auth_client.post(
            f"{plan.base}/plan",
            json={"kind": "scene", "title": "x", "expected_revision": plan.head, **fields},
        )
        assert response.status_code == 422, response.text
        return response.json()["detail"]

    assert "cannot sit under a scene" in refused(kind="arc", parent_id=scene["id"])
    assert "does not exist" in refused(parent_id="pln_nowhere")
    assert "must be a Character" in refused(refs=[{"entity_id": point, "role": "point_of_view"}])
    assert "must be a Location" in refused(refs=[{"entity_id": ines, "role": "place"}])
    assert "unknown plan role" in refused(refs=[{"entity_id": ines, "role": "owner"}])
    assert "only one point_of_view" in refused(
        refs=[
            {"entity_id": ines, "role": "point_of_view"},
            {"entity_id": ines, "role": "point_of_view"},
        ]
    )
    assert "is not a thread" in refused(sets_up=[arc["id"]])
    # Nothing was half-saved by any of those.
    assert len(plan.outline()["items"]) == 3


def test_items_are_reordered_moved_and_removed_with_everything_under_them(
    auth_client, fake_container
) -> None:
    plan = Plan(auth_client, fake_container)
    arc = plan.add("arc", "First arc")
    other = plan.add("arc", "Second arc")
    one = plan.add("beat", "One", parent_id=arc["id"])
    two = plan.add("beat", "Two", parent_id=arc["id"])
    three = plan.add("beat", "Three", parent_id=arc["id"])
    thread = plan.add("thread", "A promise")
    scene = plan.add("scene", "A scene", parent_id=two["id"], sets_up=[thread["id"]])

    def order(parent: str) -> list[str]:
        items = [item for item in plan.outline()["items"] if item["parent_id"] == parent]
        return [item["title"] for item in sorted(items, key=lambda item: item["position"])]

    moved = auth_client.post(
        f"{plan.base}/plan/{three['id']}/move",
        json={"parent_id": arc["id"], "before_id": one["id"], "expected_revision": plan.head},
    )
    assert moved.status_code == 200, moved.text
    plan.head += 1
    assert order(arc["id"]) == ["Three", "One", "Two"]

    # To another arc, last. A scene cannot be moved under a thread.
    across = auth_client.post(
        f"{plan.base}/plan/{two['id']}/move",
        json={"parent_id": other["id"], "expected_revision": plan.head},
    )
    assert across.status_code == 200, across.text
    plan.head += 1
    assert order(other["id"]) == ["Two"] and order(arc["id"]) == ["Three", "One"]
    wrong = auth_client.post(
        f"{plan.base}/plan/{scene['id']}/move",
        json={"parent_id": thread["id"], "expected_revision": plan.head},
    )
    assert wrong.status_code == 400 and "cannot go under a thread" in wrong.json()["detail"]

    # Removing the thread takes it off the scene that opened it, so nothing dangles.
    gone = auth_client.post(
        f"{plan.base}/plan/{thread['id']}/remove", json={"expected_revision": plan.head}
    )
    assert gone.status_code == 200 and gone.json()["removed"] == 1
    plan.head += 1
    left = {item["id"]: item for item in plan.outline()["items"]}
    assert thread["id"] not in left and left[scene["id"]]["sets_up"] == []

    # Removing an arc removes what is under it, as one version.
    removed = auth_client.post(
        f"{plan.base}/plan/{other['id']}/remove", json={"expected_revision": plan.head}
    )
    assert removed.status_code == 200, removed.text
    assert removed.json() == {"revision": plan.head + 1, "removed": 3}
    plan.head += 1
    assert {item["title"] for item in plan.outline()["items"]} == {"First arc", "Three", "One"}


def test_a_plan_change_has_history_and_can_be_undone(auth_client, fake_container) -> None:
    plan = Plan(auth_client, fake_container)
    arc = plan.add("arc", "An arc", summary="As first written.")
    changed = auth_client.patch(
        f"{plan.base}/plan/{arc['id']}",
        json={"summary": "As rewritten.", "expected_revision": plan.head},
    )
    assert changed.status_code == 200 and changed.json()["summary"] == "As rewritten."
    plan.head += 1

    same = auth_client.patch(
        f"{plan.base}/plan/{arc['id']}",
        json={"summary": "As rewritten.", "expected_revision": plan.head},
    )
    assert same.status_code == 400 and "Nothing has changed" in same.json()["detail"]

    stale = auth_client.patch(
        f"{plan.base}/plan/{arc['id']}", json={"title": "Late", "expected_revision": 0}
    )
    assert stale.status_code == 409

    # Going back one version restores the earlier words.
    preview = auth_client.post(
        f"{plan.base}/reverts/preview", json={"from_revision": plan.head - 1, "to_revision": None}
    )
    assert preview.status_code == 200, preview.text
    assert [op["op"] for op in preview.json()["operations"]] == ["update_plan_item"]
    assert preview.json()["operations"][0]["summary"] == "As first written."


def test_the_plan_requires_a_session_and_readers_cannot_change_it(api_client) -> None:
    assert api_client.get("/api/v1/projects/local-project/plan").status_code == 401
    refused = api_client.post(
        "/api/v1/projects/local-project/plan",
        json={"kind": "arc", "title": "x", "expected_revision": 0},
    )
    assert refused.status_code == 401


def test_an_archive_carries_the_plan_in_its_own_section_marked_as_intent(
    auth_client, fake_container
) -> None:
    plan = Plan(auth_client, fake_container)
    plan.entry("Character", "Ines Varga")
    plan.add("scene", "The drowning", summary="Ines Varga drowns off the Point.")

    exported = auth_client.post(f"{plan.base}/archive/export")

    assert exported.status_code == 200, exported.text
    archive = exported.json()
    assert archive["plan"]["intent"] is True
    assert [item["title"] for item in archive["plan"]["items"]] == ["The drowning"]
    # Nothing of it is among the story's own records.
    assert "drown" not in str(archive["canon"])
