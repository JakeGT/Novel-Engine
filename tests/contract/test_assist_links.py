"""Suggested connections between existing entries, confirmed by the author."""

import pytest

from backend.services.canon_revise_workflow import LinkPlan, SuggestedLink
from tests.contract.test_god_hand import _world

pytestmark = pytest.mark.contract


def test_missing_connections_become_facts_to_review_and_nothing_else(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    fake_container.ai.queue(
        LinkPlan(
            links=[
                SuggestedLink(
                    subject_ref=w["tobin"],
                    predicate="MEMBER_OF",
                    object_ref=w["guild"],
                    why="His details say he keeps the guild's books.",
                ),
                # Already recorded; a correction to a one-at-a-time fact; nonsense.
                SuggestedLink(
                    subject_ref=w["mara"], predicate="KNOWS", object_ref=w["tobin"], why=""
                ),
                SuggestedLink(
                    subject_ref=w["mara"], predicate="LOCATED_AT", object_ref=w["saltmere"], why="x"
                ),
                SuggestedLink(
                    subject_ref=w["mara"], predicate="KNOWS", object_ref=w["mara"], why="x"
                ),
                SuggestedLink(
                    subject_ref=w["harbor"], predicate="KNOWS", object_ref=w["mara"], why="x"
                ),
                SuggestedLink(
                    subject_ref=w["mara"], predicate="ADORES", object_ref=w["tobin"], why="x"
                ),
                SuggestedLink(
                    subject_ref="ent_elsewhere", predicate="KNOWS", object_ref=w["mara"], why="x"
                ),
                # The same suggestion twice is one change.
                SuggestedLink(
                    subject_ref=w["tobin"], predicate="MEMBER_OF", object_ref=w["guild"], why="x"
                ),
            ]
        )
    )

    response = auth_client.post(
        f"{w['base']}/assist/links", json={"expected_revision": int(w["head"])}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    detail = auth_client.get(f"{w['base']}/mutations/{body['mutation_id']}").json()
    assert [op["operation_type"] for op in detail["operations"]] == ["assert_fact"]
    proposition = detail["operations"][0]["payload"]["proposition"]
    assert (proposition["subject_id"], proposition["predicate"]) == (w["tobin"], "MEMBER_OF")
    assert proposition["object"]["entity_id"] == w["guild"]
    # Why it was suggested is shown with names, and it waits for a person.
    assert body["notes"] == [
        "Tobin Ashe and Harbor Guild: His details say he keeps the guild's books."
    ]
    assert detail["requires_review"] is True
    assert len(body["discarded"]) == 5
    # The model was shown the story and told not to guess.
    system, user = fake_container.ai.calls[-1]["messages"]
    assert "Never guess" in system["content"] and w["tobin"] in user["content"]


def test_no_missing_connections_is_said_plainly(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    fake_container.ai.queue(LinkPlan())

    response = auth_client.post(
        f"{w['base']}/assist/links", json={"expected_revision": int(w["head"])}
    )

    assert response.status_code == 400
    assert "No missing connections were found" in response.json()["detail"]


def test_link_suggestions_use_a_provider_safe_format() -> None:
    from openai.lib._pydantic import to_strict_json_schema

    def free_form(node: object) -> bool:
        if isinstance(node, dict):
            extra = node.get("additionalProperties")
            return (node.get("type") == "object" and extra is not False) or any(
                free_form(value) for value in node.values()
            )
        return isinstance(node, list) and any(free_form(value) for value in node)

    assert not free_form(to_strict_json_schema(LinkPlan))
