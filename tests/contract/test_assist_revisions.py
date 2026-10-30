"""Revising a set of suggested changes by asking, while it waits for review."""

import pytest

from backend.services.canon_creation_workflow import DraftAttribute
from backend.services.canon_revise_workflow import ChangeEdit, RevisionPlan
from tests.contract.test_god_hand import _world

pytestmark = pytest.mark.contract


def _proposal(client, w: dict[str, str]) -> str:
    project_id = w["base"].rsplit("/", 1)[-1]
    proposed = client.post(
        f"{w['base']}/mutations",
        json={
            "origin": "model",
            "expected_revision": int(w["head"]),
            "operations": [
                {
                    "op": "create_entity",
                    "id": "tmp:prof",
                    "type": "Character",
                    "name": "Mara’s professor",
                    "attributes": {"role": "teacher", "_needs_detail": True},
                },
                {"op": "create_entity", "id": "tmp:inn", "type": "Location", "name": "The Gull"},
                {
                    "op": "assert_fact",
                    "id": "tmp:f0",
                    "proposition": {
                        "subject_id": "tmp:prof",
                        "predicate": "LOCATED_AT",
                        "object": {
                            "kind": "entity_ref",
                            "entity_id": w["harbor"],
                            "project_id": project_id,
                        },
                    },
                },
                {"op": "update_entity", "id": w["mara"], "attributes": {"role": "captain"}},
                {"op": "close_fact", "id": w["knows"]},
            ],
        },
    )
    assert proposed.status_code == 201, proposed.text
    return proposed.json()["id"]


def test_asking_edits_and_skips_changes_and_adds_nothing_to_the_story(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    mutation_id = _proposal(auth_client, w)
    fake_container.ai.queue(
        RevisionPlan(
            understood="Name the professor, move him to the inn, keep Mara's eyes, drop the inn.",
            changes=[
                ChangeEdit(
                    number=1,
                    action="edit",
                    new_name="Professor Hale",
                    add_aliases=["Hale"],
                    set_attributes=[DraftAttribute(key="age", value="sixty")],
                    remove_attributes=["role", "_needs_detail"],
                    tags=["Mentor", "mara’s professor"],
                    importance="supporting",
                ),
                ChangeEdit(number=3, action="edit", object_ref="tmp:inn"),
                # An update keeps the details it did not mention.
                ChangeEdit(
                    number=4,
                    action="edit",
                    set_attributes=[DraftAttribute(key="rank", value="first mate")],
                ),
                ChangeEdit(number=5, action="skip"),
                ChangeEdit(number=5, action="edit", new_name="x"),
                ChangeEdit(number=9, action="skip"),
            ],
            cannot=["Adding a ship needs a new change."],
        )
    )

    response = auth_client.post(
        f"{w['base']}/assist/revisions",
        json={"mutation_id": mutation_id, "instruction": "Name him Hale, he is at the inn."},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["edited"], body["skipped"]) == (3, 1)
    assert body["notes"] == ["Adding a ship needs a new change."]
    assert sorted(item["reason"] for item in body["discarded"]) == [
        "change 5: this kind of change can only be kept or skipped",
        "there is no change 9",
    ]
    # The model saw the changes by number and the entries by id.
    prompt = fake_container.ai.calls[-1]["messages"][1]["content"]
    assert "1. {" in prompt and w["harbor"] in prompt and "Name him Hale" in prompt

    detail = auth_client.get(f"{w['base']}/mutations/{mutation_id}").json()
    assert detail["status"] in {"draft", "needs_review"}
    first, _inn, fact, update, ended = detail["operations"]
    assert first["revision"] == 2
    assert first["payload"]["name"] == "Professor Hale"
    assert first["payload"]["aliases"] == ["Hale"]
    # A reserved mark cannot be set or removed by name; tags and importance are kept.
    assert first["payload"]["attributes"] == {
        "age": "sixty",
        "_needs_detail": True,
        "_tags": ["Mentor", "mara’s professor"],
        "_importance": "supporting",
    }
    assert fact["payload"]["proposition"]["object"]["entity_id"] == "tmp:inn"
    assert update["payload"]["attributes"] == {"role": "captain", "rank": "first mate"}
    assert ended["decision"]["decision"] == "rejected"
    assert ended["decision"]["note"].startswith("asked: Name him Hale")
    # Nothing reached the story.
    head = auth_client.get(f"{w['base']}/mutations/{mutation_id}").json()["head_revision"]
    assert head == int(w["head"])


def test_a_request_that_changes_nothing_or_is_unclear_is_reported_plainly(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    mutation_id = _proposal(auth_client, w)

    fake_container.ai.queue(RevisionPlan(understood="Unclear.", questions=["Which professor?"]))
    asked = auth_client.post(
        f"{w['base']}/assist/revisions",
        json={"mutation_id": mutation_id, "instruction": "Change the professor."},
    )
    assert asked.status_code == 200, asked.text
    assert asked.json()["questions"] == ["Which professor?"]
    assert (asked.json()["edited"], asked.json()["skipped"]) == (0, 0)

    fake_container.ai.queue(
        RevisionPlan(
            understood="Nothing.",
            changes=[ChangeEdit(number=3, action="edit", object_ref="ent_nowhere")],
        )
    )
    nothing = auth_client.post(
        f"{w['base']}/assist/revisions",
        json={"mutation_id": mutation_id, "instruction": "Move him somewhere."},
    )
    assert nothing.status_code == 400
    assert "did not lead to any difference" in nothing.json()["detail"]
    detail = auth_client.get(f"{w['base']}/mutations/{mutation_id}").json()
    assert [op["revision"] for op in detail["operations"]] == [1, 1, 1, 1, 1]


def test_revising_needs_a_session_and_a_provider_safe_format(api_client) -> None:
    from openai.lib._pydantic import to_strict_json_schema

    response = api_client.post(
        "/api/v1/projects/local-project/assist/revisions",
        json={"mutation_id": "mut_1", "instruction": "x"},
    )
    assert response.status_code == 401

    def free_form(node: object) -> bool:
        if isinstance(node, dict):
            extra = node.get("additionalProperties")
            return (node.get("type") == "object" and extra is not False) or any(
                free_form(value) for value in node.values()
            )
        return isinstance(node, list) and any(free_form(value) for value in node)

    assert not free_form(to_strict_json_schema(RevisionPlan))
