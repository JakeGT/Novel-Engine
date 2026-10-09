"""Guided character workflow: model output stays a proposal until reviewed (2F)."""

import pytest

from backend.services.canon_character_workflow import (
    CharacterProposal,
    CharacterQuestion,
    CharacterQuestionSheet,
    ProposedBelief,
    ProposedEntity,
    ProposedFact,
)
from tests.contract.test_canon_entities import _project_id, _publish_schema

pytestmark = pytest.mark.contract


def _setup(client, container) -> tuple[str, str, str]:
    project_id = _project_id(container)
    _publish_schema(client, project_id)
    base = f"/api/v1/projects/{project_id}"
    character = client.post(
        f"{base}/entities", json={"type": "Character", "name": "Mara", "expected_revision": 0}
    ).json()["id"]
    harbor = client.post(
        f"{base}/entities", json={"type": "Location", "name": "Harbor", "expected_revision": 1}
    ).json()["id"]
    return base, character, harbor


def test_questions_are_generated_from_canon_context(auth_client, fake_container) -> None:
    base, character, harbor = _setup(auth_client, fake_container)
    fake_container.ai.queue(
        CharacterQuestionSheet(
            questions=[
                CharacterQuestion(id="where_1", question="Where does she live?", category="place"),
                CharacterQuestion(id="not a valid id!", question="Who?", category="relationships"),
            ]
        )
    )

    response = auth_client.post(f"{base}/characters/{character}/questions")

    assert response.status_code == 200, response.text
    assert [q["id"] for q in response.json()["questions"]] == ["where_1", "q2"]
    prompt = fake_container.ai.calls[-1]["messages"][1]["content"]
    assert "Mara" in prompt and harbor in prompt


def test_answers_become_a_reviewable_proposal_not_canon(auth_client, fake_container) -> None:
    base, character, harbor = _setup(auth_client, fake_container)
    fake_container.ai.queue(
        CharacterProposal(
            attributes={"hair": "black"},
            aliases=["The Fox", "mara"],
            new_entities=[
                ProposedEntity(ref="guild", type="Faction", name="Salt Guild"),
                ProposedEntity(ref="ufo", type="Spaceship", name="Nope"),
            ],
            facts=[
                ProposedFact(predicate="LOCATED_AT", object_ref=harbor, question_id="where_1"),
                ProposedFact(predicate="MEMBER_OF", object_ref="guild", question_id="group_1"),
                ProposedFact(predicate="HAS_NAME", object_value="Mara Venn"),
                ProposedFact(predicate="INVENTED_PREDICATE", object_value="x"),
                ProposedFact(predicate="OWNS", object_ref="ent_does_not_exist"),
            ],
            beliefs=[
                ProposedBelief(
                    predicate="HAS_NAME",
                    about_ref=harbor,
                    object_value="The Safe Harbor",
                    confidence=3.0,
                    question_id="where_1",
                )
            ],
        )
    )

    response = auth_client.post(
        f"{base}/characters/{character}/proposals",
        json={
            "answers": [
                {
                    "question_id": "where_1",
                    "question": "Where does she live?",
                    "answer": "By the harbor, which she thinks is safe.",
                },
                {
                    "question_id": "group_1",
                    "question": "Any allegiances?",
                    "answer": "The Salt Guild.",
                },
            ],
            "expected_revision": 2,
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "needs_review"
    assert body["validation_ok"] is True
    assert sorted(item["kind"] for item in body["discarded"]) == ["entity", "fact", "fact"]
    # 1 new entity + 1 update + 3 facts + 1 belief.
    assert body["operation_count"] == 6

    # Nothing reached canon: the head is unchanged and the character is untouched.
    entity = auth_client.get(f"{base}/entities/{character}").json()
    assert entity["attributes"] == {} and entity["aliases"] == []
    assert auth_client.get(f"{base}/facts").json()["items"] == []

    review = auth_client.get(f"{base}/mutations/{body['mutation_id']}").json()
    assert review["origin"] == "model" and review["requires_review"] is True
    types = [op["operation_type"] for op in review["operations"]]
    assert types == [
        "create_entity",
        "update_entity",
        "assert_fact",
        "assert_fact",
        "assert_fact",
        "assert_belief",
    ]
    update = review["operations"][1]
    assert update["payload"]["attributes"] == {"hair": "black"}
    assert update["payload"]["aliases"] == ["The Fox"]
    assert update["target_before"]["name"] == "Mara"
    located = review["operations"][2]["payload"]
    assert located["evidence"] == [
        {"source_artefact_id": body["session_id"], "location": "question:where_1"}
    ]
    belief = review["operations"][5]["payload"]
    assert belief["confidence"] == 1.0 and belief["source_ref"] == body["session_id"]
    assert "is_true" not in belief

    # It cannot be committed without an explicit human approval.
    blocked = auth_client.post(f"{base}/mutations/{body['mutation_id']}/commit")
    assert blocked.status_code == 409 and blocked.json()["code"] == "review_required"

    # The reviewer rejects one operation and accepts the rest.
    auth_client.post(
        f"{base}/mutations/{body['mutation_id']}/operations/4/decision",
        json={"decision": "rejected", "note": "name is already known"},
    )
    assert (
        auth_client.post(f"{base}/mutations/{body['mutation_id']}/approve", json={}).status_code
        == 200
    )
    committed = auth_client.post(f"{base}/mutations/{body['mutation_id']}/commit")
    assert committed.status_code == 200, committed.text

    entity = auth_client.get(f"{base}/entities/{character}").json()
    assert entity["attributes"] == {"hair": "black"} and entity["aliases"] == ["The Fox"]
    facts = auth_client.get(f"{base}/facts").json()["items"]
    assert sorted(fact["predicate"] for fact in facts) == ["LOCATED_AT", "MEMBER_OF"]
    provenance = auth_client.get(f"{base}/provenance/entity/{character}").json()["items"]
    assert provenance[-1]["origin"] == "model"
    assert provenance[-1]["evidence"][0]["source_artefact_id"] == body["session_id"]


def test_workflow_rejects_non_characters_and_empty_output(auth_client, fake_container) -> None:
    base, character, harbor = _setup(auth_client, fake_container)

    assert auth_client.post(f"{base}/characters/{harbor}/questions").status_code == 404
    assert auth_client.post(f"{base}/characters/ent_missing/questions").status_code == 404

    fake_container.ai.queue(CharacterProposal())
    empty = auth_client.post(
        f"{base}/characters/{character}/proposals",
        json={
            "answers": [{"question_id": "q1", "question": "Q?", "answer": "Nothing to add."}],
            "expected_revision": 2,
        },
    )
    assert empty.status_code == 400
    assert auth_client.get(f"{base}/mutations").json()["items"][0]["status"] == "committed"

    invalid = auth_client.post(
        f"{base}/characters/{character}/proposals",
        json={"answers": [], "expected_revision": 2},
    )
    assert invalid.status_code == 422


def test_legacy_graph_character_writes_are_local_only(hosted_client, hosted_container) -> None:
    from tests.contract.test_workspace_scope import (
        _create_project,
        _create_workspace,
        _hosted_login,
    )

    csrf = _hosted_login(hosted_client, hosted_container)
    workspace_id = _create_workspace(hosted_client, csrf, "legacy-char")
    project_id = _create_project(hosted_client, csrf, workspace_id, "proj")
    headers = {"X-CSRF-Token": csrf, "X-Workspace-Id": workspace_id, "X-Project-Id": project_id}

    init = hosted_client.post(
        "/api/v1/characters/init", json={"name": "Arin", "bio": "A scout."}, headers=headers
    )
    process = hosted_client.post(
        "/api/v1/characters/process-answers",
        json={"id": "char_arin", "chapter": 1, "answers": {"Q": "A"}},
        headers=headers,
    )
    assert init.status_code == 403 and process.status_code == 403
    assert (
        hosted_container.graph.get_node(
            "char_arin", workspace_id=workspace_id, project_id=project_id
        )
        is None
    )
