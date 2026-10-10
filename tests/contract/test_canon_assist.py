"""Assisted creation: a description becomes drafted entries and links, held for review."""

import pytest

from backend.services.canon_character_workflow import CharacterQuestion, CharacterQuestionSheet
from backend.services.canon_creation_workflow import (
    CreationDraft,
    DraftAttribute,
    DraftBelief,
    DraftEntity,
    DraftFact,
)
from tests.contract.test_canon_entities import _project_id, _publish_schema

pytestmark = pytest.mark.contract


def _setup(client, container) -> tuple[str, str]:
    project_id = _project_id(container)
    _publish_schema(client, project_id)
    base = f"/api/v1/projects/{project_id}"
    harbor = client.post(
        f"{base}/entities",
        json={
            "type": "Location",
            "name": "Lantern Harbor",
            "aliases": ["the harbor"],
            "expected_revision": 0,
        },
    ).json()["id"]
    return base, harbor


def test_questions_come_from_the_description_and_name_what_already_exists(
    auth_client, fake_container
) -> None:
    base, harbor = _setup(auth_client, fake_container)
    fake_container.ai.queue(
        CharacterQuestionSheet(
            questions=[
                CharacterQuestion(
                    id="home_1", question="Does she live at Lantern Harbor?", category="place"
                ),
                CharacterQuestion(
                    id="bad id!", question="Who does she trust?", category="relationships"
                ),
            ]
        )
    )

    response = auth_client.post(
        f"{base}/assist/questions",
        json={"entity_type": "Character", "name": "Mara Venn", "description": "A harbor pilot."},
    )

    assert response.status_code == 200, response.text
    assert [q["id"] for q in response.json()["questions"]] == ["home_1", "q2"]
    prompt = fake_container.ai.calls[-1]["messages"][1]["content"]
    assert "Mara Venn" in prompt and "A harbor pilot." in prompt
    assert harbor in prompt and "the harbor" in prompt


def test_a_description_becomes_entries_and_links_for_review_not_canon(
    auth_client, fake_container
) -> None:
    base, harbor = _setup(auth_client, fake_container)
    fake_container.ai.queue(
        CreationDraft(
            entities=[
                DraftEntity(
                    ref="mara",
                    type="Character",
                    name="Mara Venn",
                    aliases=["The Fox", "mara venn"],
                    attributes=[
                        DraftAttribute(key="role", value="harbor pilot"),
                        DraftAttribute(key="", value="ignored"),
                    ],
                ),
                DraftEntity(ref="guild", type="Faction", name="Salt Guild"),
                # Already exists (by alias): must be linked, never created twice.
                DraftEntity(ref="port", type="Location", name="The Harbor"),
                DraftEntity(ref="ufo", type="Spaceship", name="Nope"),
            ],
            facts=[
                DraftFact(
                    subject_ref="mara",
                    predicate="LOCATED_AT",
                    object_ref="port",
                    question_id="home_1",
                ),
                DraftFact(subject_ref="mara", predicate="MEMBER_OF", object_ref="guild"),
                DraftFact(subject_ref="mara", predicate="MEMBER_OF", object_ref="guild"),
                # Wrong subject type, unknown predicate, unknown target: all discarded.
                DraftFact(subject_ref="guild", predicate="MEMBER_OF", object_ref="mara"),
                DraftFact(subject_ref="mara", predicate="INVENTED", object_value="x"),
                DraftFact(subject_ref="mara", predicate="OWNS", object_ref="ent_missing"),
            ],
            beliefs=[
                DraftBelief(
                    holder_ref="mara",
                    about_ref=harbor,
                    predicate="HAS_NAME",
                    object_value="The Safe Harbor",
                    confidence=4,
                ),
                DraftBelief(holder_ref="guild", predicate="HAS_NAME", object_value="x"),
            ],
        )
    )

    response = auth_client.post(
        f"{base}/assist/proposals",
        json={
            "entity_type": "Character",
            "name": "Mara Venn",
            "description": "A harbor pilot in the Salt Guild who lives at the harbor.",
            "answers": [
                {
                    "question_id": "home_1",
                    "question": "Where does she live?",
                    "answer": "At the harbor.",
                }
            ],
            "expected_revision": 1,
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "needs_review" and body["validation_ok"] is True
    assert sorted(item["kind"] for item in body["discarded"]) == [
        "belief",
        "entity",
        "fact",
        "fact",
        "fact",
    ]

    # Nothing reached canon.
    assert len(auth_client.get(f"{base}/entities").json()["items"]) == 1
    assert auth_client.get(f"{base}/facts").json()["items"] == []

    review = auth_client.get(f"{base}/mutations/{body['mutation_id']}").json()
    assert review["origin"] == "model" and review["requires_review"] is True
    operations = review["operations"]
    assert [op["operation_type"] for op in operations] == [
        "create_entity",
        "create_entity",
        "assert_fact",
        "assert_fact",
        "assert_belief",
    ]
    mara = operations[0]["payload"]
    assert mara["name"] == "Mara Venn" and mara["aliases"] == ["The Fox"]
    assert mara["attributes"] == {"role": "harbor pilot"}
    located = operations[2]["payload"]
    # The drafted "The Harbor" resolved to the existing entry.
    assert located["proposition"]["object"]["entity_id"] == harbor
    assert located["proposition"]["subject_id"] == mara["id"]
    assert located["evidence"] == [
        {"source_artefact_id": body["session_id"], "location": "question:home_1"}
    ]
    belief = operations[4]["payload"]
    assert belief["confidence"] == 1.0 and "is_true" not in belief

    # It needs an explicit approval, and then all of it lands in one revision.
    blocked = auth_client.post(f"{base}/mutations/{body['mutation_id']}/commit")
    assert blocked.status_code == 409 and blocked.json()["code"] == "review_required"
    assert (
        auth_client.post(f"{base}/mutations/{body['mutation_id']}/approve", json={}).status_code
        == 200
    )
    committed = auth_client.post(f"{base}/mutations/{body['mutation_id']}/commit")
    assert committed.status_code == 200, committed.text
    names = sorted(item["name"] for item in auth_client.get(f"{base}/entities").json()["items"])
    assert names == ["Lantern Harbor", "Mara Venn", "Salt Guild"]
    assert len(auth_client.get(f"{base}/facts").json()["items"]) == 2


def test_the_named_subject_is_drafted_even_when_the_model_omits_it(
    auth_client, fake_container
) -> None:
    base, _ = _setup(auth_client, fake_container)
    fake_container.ai.queue(CreationDraft())

    response = auth_client.post(
        f"{base}/assist/proposals",
        json={
            "entity_type": "Faction",
            "name": "Salt Guild",
            "description": "A smugglers' guild.",
            "expected_revision": 1,
        },
    )

    assert response.status_code == 201, response.text
    review = auth_client.get(f"{base}/mutations/{response.json()['mutation_id']}").json()
    assert [(op["payload"]["type"], op["payload"]["name"]) for op in review["operations"]] == [
        ("Faction", "Salt Guild")
    ]


def test_nothing_to_draft_and_bad_input_are_reported_plainly(auth_client, fake_container) -> None:
    base, _ = _setup(auth_client, fake_container)
    fake_container.ai.queue(
        CreationDraft(entities=[DraftEntity(ref="h", type="Location", name="Lantern Harbor")])
    )
    nothing = auth_client.post(
        f"{base}/assist/proposals",
        json={"description": "Lantern Harbor is a port.", "expected_revision": 1},
    )
    assert nothing.status_code == 400
    assert "Nothing new could be drafted" in nothing.json()["detail"]
    # No proposal was left behind (the only set is the committed harbor entry).
    origins = [item["origin"] for item in auth_client.get(f"{base}/mutations").json()["items"]]
    assert origins == ["author"]

    unknown = auth_client.post(
        f"{base}/assist/questions", json={"entity_type": "Spaceship", "description": "A ship."}
    )
    assert unknown.status_code == 400
    blank = auth_client.post(f"{base}/assist/questions", json={"description": "   "})
    assert blank.status_code == 400


def test_assist_requires_a_session(api_client) -> None:
    response = api_client.post(
        "/api/v1/projects/local-project/assist/questions", json={"description": "x"}
    )
    assert response.status_code == 401


def test_mentioned_entries_land_on_the_to_do_list_and_can_be_filled_in_later(
    auth_client, fake_container
) -> None:
    base, harbor = _setup(auth_client, fake_container)
    # Describing Mara mentions a guild that does not exist yet: it is drafted as a bare entry.
    fake_container.ai.queue(
        CreationDraft(
            entities=[
                DraftEntity(
                    ref="mara",
                    type="Character",
                    name="Mara",
                    attributes=[DraftAttribute(key="role", value="pilot")],
                ),
                DraftEntity(ref="guild", type="Faction", name="Salt Guild"),
            ],
            facts=[DraftFact(subject_ref="mara", predicate="MEMBER_OF", object_ref="guild")],
        )
    )
    first = auth_client.post(
        f"{base}/assist/proposals",
        json={"description": "Mara is a pilot in the Salt Guild.", "expected_revision": 1},
    ).json()
    auth_client.post(f"{base}/mutations/{first['mutation_id']}/approve", json={})
    assert auth_client.post(f"{base}/mutations/{first['mutation_id']}/commit").status_code == 200

    # Only entries that are still just a name are listed: not Mara, who has details and a fact.
    todo = auth_client.get(f"{base}/entities/needs-detail").json()["items"]
    assert sorted(item["name"] for item in todo) == ["Lantern Harbor", "Salt Guild"]
    guild = next(item["id"] for item in todo if item["name"] == "Salt Guild")

    # Filling one in asks about that entry, with what is already known as context.
    fake_container.ai.queue(
        CharacterQuestionSheet(
            questions=[
                CharacterQuestion(id="base_1", question="Where is it based?", category="place")
            ]
        )
    )
    questions = auth_client.post(f"{base}/assist/questions", json={"entity_id": guild})
    assert questions.status_code == 200, questions.text
    prompt = fake_container.ai.calls[-1]["messages"][1]["content"]
    assert "filling in an existing entry" in prompt and "Salt Guild" in prompt

    # A fill-in with neither a description nor an answer has nothing to work from.
    empty = auth_client.post(
        f"{base}/assist/proposals", json={"entity_id": guild, "expected_revision": 2}
    )
    assert empty.status_code == 400

    fake_container.ai.queue(
        CreationDraft(
            entities=[
                DraftEntity(
                    ref="g",
                    type="Faction",
                    name="Salt Guild",
                    aliases=["The Salters"],
                    attributes=[DraftAttribute(key="trade", value="smuggling")],
                )
            ],
            facts=[DraftFact(subject_ref=guild, predicate="LOCATED_AT", object_ref=harbor)],
        )
    )
    filled = auth_client.post(
        f"{base}/assist/proposals",
        json={
            "entity_id": guild,
            "answers": [
                {"question_id": "base_1", "question": "Where is it based?", "answer": "The harbor."}
            ],
            "expected_revision": 2,
        },
    )
    assert filled.status_code == 201, filled.text
    review = auth_client.get(f"{base}/mutations/{filled.json()['mutation_id']}").json()
    # The existing entry is updated, never created a second time.
    assert [op["operation_type"] for op in review["operations"]] == ["update_entity", "assert_fact"]
    update = review["operations"][0]["payload"]
    assert update["id"] == guild
    assert update["attributes"] == {"trade": "smuggling"} and update["aliases"] == ["The Salters"]

    auth_client.post(f"{base}/mutations/{filled.json()['mutation_id']}/approve", json={})
    assert (
        auth_client.post(f"{base}/mutations/{filled.json()['mutation_id']}/commit").status_code
        == 200
    )
    # Done: it is off the list.
    remaining = auth_client.get(f"{base}/entities/needs-detail").json()["items"]
    assert [item["name"] for item in remaining] == ["Lantern Harbor"]

    missing = auth_client.post(f"{base}/assist/questions", json={"entity_id": "ent_nope"})
    assert missing.status_code == 404


def test_the_draft_format_is_accepted_by_strict_structured_output() -> None:
    """Providers reject free-form objects; the SDK's strict conversion must succeed."""
    from openai.lib._pydantic import to_strict_json_schema

    def free_form(node: object) -> bool:
        if isinstance(node, dict):
            extra = node.get("additionalProperties")
            return (node.get("type") == "object" and extra is not False) or any(
                free_form(value) for value in node.values()
            )
        return isinstance(node, list) and any(free_form(value) for value in node)

    for model in (CreationDraft, CharacterQuestionSheet):
        assert not free_form(to_strict_json_schema(model)), model.__name__
