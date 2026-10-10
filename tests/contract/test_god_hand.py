"""The God Hand: any instruction about the story becomes one reviewable proposal."""

import pytest

from backend.services.canon_change_workflow import (
    ChangePlan,
    Elsewhere,
    EntityChange,
    Invite,
    RecordRef,
)
from backend.services.canon_creation_workflow import DraftAttribute, DraftEntity, DraftFact
from tests.contract.test_canon_entities import _project_id, _publish_schema

pytestmark = pytest.mark.contract


def _world(client, container) -> dict[str, str]:
    """Mara (a pilot) is at the harbor and in the guild; Tobin exists; a duplicate of Mara exists."""
    project_id = _project_id(container)
    _publish_schema(client, project_id)
    base = f"/api/v1/projects/{project_id}"
    ids = {"base": base}
    head = 0
    for key, body in (
        (
            "mara",
            {
                "type": "Character",
                "name": "Mara",
                "aliases": ["The Fox"],
                "attributes": {"role": "pilot", "eyes": "green"},
            },
        ),
        ("tobin", {"type": "Character", "name": "Tobin Ashe"}),
        ("harbor", {"type": "Location", "name": "Lantern Harbor"}),
        ("saltmere", {"type": "Location", "name": "Saltmere"}),
        ("guild", {"type": "Faction", "name": "Harbor Guild"}),
    ):
        created = client.post(f"{base}/entities", json={**body, "expected_revision": head})
        assert created.status_code == 201, created.text
        ids[key] = created.json()["id"]
        head += 1
    for key, predicate, target in (
        ("at_harbor", "LOCATED_AT", "harbor"),
        ("in_guild", "MEMBER_OF", "guild"),
        ("knows", "KNOWS", "tobin"),
    ):
        fact = client.post(
            f"{base}/facts",
            json={
                "proposition": {
                    "subject_id": ids["mara"],
                    "predicate": predicate,
                    "object": {
                        "kind": "entity_ref",
                        "entity_id": ids[target],
                        "project_id": project_id,
                    },
                },
                "expected_revision": head,
            },
        )
        assert fact.status_code == 201, fact.text
        ids[key] = fact.json()["id"]
        head += 1
    ids["head"] = str(head)
    return ids


def test_one_instruction_changes_everything_it_implies_and_waits_for_review(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    base, head = w["base"], int(w["head"])
    fake_container.ai.queue(
        ChangePlan(
            understood="Mara becomes Mara Venn, moves to Saltmere, leaves the guild, and Tobin is removed.",
            new_entities=[DraftEntity(ref="ship", type="Object", name="The Gull")],
            entity_changes=[
                EntityChange(
                    ref=w["mara"],
                    new_name="Mara Venn",
                    add_aliases=["Captain Venn", "the fox"],
                    remove_aliases=["The Fox"],
                    set_attributes=[DraftAttribute(key="eyes", value="grey")],
                    remove_attributes=["role"],
                ),
                EntityChange(ref="ent_missing", new_name="Nobody"),
            ],
            # The model moved her without ending the old location: it must be ended anyway.
            add_facts=[
                DraftFact(subject_ref=w["mara"], predicate="LOCATED_AT", object_ref=w["saltmere"]),
                DraftFact(subject_ref=w["mara"], predicate="OWNS", object_ref="ship"),
            ],
            end_facts=[RecordRef(id=w["in_guild"]), RecordRef(id="fct_missing")],
            archive_entities=[RecordRef(id=w["tobin"], reason="removed from the story")],
            notes=["Anyone who believed Mara was in the guild may now be wrong."],
        )
    )

    response = auth_client.post(
        f"{base}/assist/changes",
        json={
            "instruction": "Mara is really Mara Venn, grey-eyed, in Saltmere now and out of the guild. Cut Tobin.",
            "entity_id": w["mara"],
            "expected_revision": head,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "needs_review" and body["validation_ok"] is True, body
    assert body["understood"].startswith("Mara becomes Mara Venn")
    assert sorted(item["kind"] for item in body["discarded"]) == ["change", "fact"]
    notes = " ".join(body["notes"])
    assert "“located at” only one thing at a time" in notes
    assert "1 current fact will still mention Tobin Ashe" in notes
    assert "may now be wrong" in notes

    # Nothing has changed yet.
    assert auth_client.get(f"{base}/entities/{w['mara']}").json()["name"] == "Mara"
    assert len(auth_client.get(f"{base}/facts").json()["items"]) == 3
    # The model was shown the whole story, by id.
    prompt = fake_container.ai.calls[-1]["messages"][1]["content"]
    assert w["at_harbor"] in prompt and "The author is looking at: Mara" in prompt

    review = auth_client.get(f"{base}/mutations/{body['mutation_id']}").json()
    assert review["origin"] == "model" and review["requires_review"] is True
    assert [op["operation_type"] for op in review["operations"]] == [
        "create_entity",
        "update_entity",
        "close_fact",
        "close_fact",
        "assert_fact",
        "assert_fact",
        "retire_entity",
    ]
    update = review["operations"][1]["payload"]
    assert update["name"] == "Mara Venn"
    assert update["aliases"] == ["Captain Venn", "the fox"]
    assert update["attributes"] == {"eyes": "grey"}
    assert {op["payload"]["id"] for op in review["operations"][2:4]} == {
        w["in_guild"],
        w["at_harbor"],
    }

    # Approval is required, and then all of it lands as one version.
    blocked = auth_client.post(f"{base}/mutations/{body['mutation_id']}/commit")
    assert blocked.status_code == 409 and blocked.json()["code"] == "review_required"
    assert (
        auth_client.post(f"{base}/mutations/{body['mutation_id']}/approve", json={}).status_code
        == 200
    )
    committed = auth_client.post(f"{base}/mutations/{body['mutation_id']}/commit")
    assert committed.status_code == 200, committed.text
    assert committed.json()["revision"] == head + 1

    mara = auth_client.get(f"{base}/entities/{w['mara']}").json()
    assert (mara["name"], mara["attributes"]) == ("Mara Venn", {"eyes": "grey"})
    assert auth_client.get(f"{base}/entities/{w['tobin']}").json()["lifecycle"] == "retired"
    open_facts = auth_client.get(f"{base}/facts", params={"subject_id": w["mara"]}).json()["items"]
    assert sorted(fact["predicate"] for fact in open_facts) == ["KNOWS", "LOCATED_AT", "OWNS"]
    located = next(fact for fact in open_facts if fact["predicate"] == "LOCATED_AT")
    assert located["object"]["entity_id"] == w["saltmere"]


def test_an_ambiguous_instruction_gets_questions_and_proposes_nothing(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    fake_container.ai.queue(
        ChangePlan(understood="Unclear.", questions=["Which harbor do you mean?", "  "])
    )

    response = auth_client.post(
        f"{w['base']}/assist/changes",
        json={"instruction": "Burn the harbor down.", "expected_revision": int(w["head"])},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mutation_id"] is None and body["status"] is None
    assert body["questions"] == ["Which harbor do you mean?"]
    origins = {item["origin"] for item in auth_client.get(f"{w['base']}/mutations").json()["items"]}
    assert origins == {"author"}


def test_an_instruction_that_changes_nothing_is_reported_plainly(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    fake_container.ai.queue(
        ChangePlan(
            understood="Nothing to do.",
            entity_changes=[EntityChange(ref=w["mara"], add_aliases=["The Fox"])],
        )
    )
    response = auth_client.post(
        f"{w['base']}/assist/changes",
        json={"instruction": "Mara is also called the Fox.", "expected_revision": int(w["head"])},
    )
    assert response.status_code == 400
    assert "did not lead to any change" in response.json()["detail"]

    blank = auth_client.post(
        f"{w['base']}/assist/changes", json={"instruction": "   ", "expected_revision": 0}
    )
    assert blank.status_code == 400


def test_a_request_that_is_not_a_story_change_is_pointed_to_where_it_is_done(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    fake_container.ai.queue(
        ChangePlan(
            understood="Go back to yesterday and invite Sam.",
            elsewhere=[
                Elsewhere(place="History", what="Go back to the version from yesterday."),
                Elsewhere(place="people", what="Invite Sam as an editor."),
                Elsewhere(place="history", what="A second mention of the same place."),
                Elsewhere(place="the admin console", what="Not a place that exists."),
            ],
        )
    )

    response = auth_client.post(
        f"{w['base']}/assist/changes",
        json={"instruction": "Undo yesterday and invite Sam.", "expected_revision": int(w["head"])},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mutation_id"] is None and body["questions"] == []
    assert body["elsewhere"] == [
        {"place": "history", "what": "Go back to the version from yesterday."},
        {"place": "people", "what": "Invite Sam as an editor."},
    ]
    # The assistant is told the places, and nothing is left behind to pick up.
    assert "history (undoing changes" in fake_container.ai.calls[-1]["messages"][0]["content"]
    assert auth_client.get(f"{w['base']}/assist/sessions").json()["items"] == []
    waiting = auth_client.get(f"{w['base']}/mutations?status=needs_review").json()["items"]
    assert waiting == []


def test_asking_to_undo_or_invite_returns_what_to_confirm_and_does_neither(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    fake_container.ai.queue(
        ChangePlan(
            understood="Undo the last two changes and invite Sam to edit.",
            back_to_version=head - 2,
            invite=Invite(role="Editor", email="sam@example.com"),
        )
    )

    response = auth_client.post(
        f"{w['base']}/assist/changes",
        json={"instruction": "Undo the last two and invite Sam.", "expected_revision": head},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mutation_id"] is None
    assert body["back_to_version"] == head - 2
    assert body["invite"] == {"role": "editor", "email": "sam@example.com"}
    assert [item["place"] for item in body["elsewhere"]] == ["history", "people"]
    # The model was shown the recent versions to work it out from.
    prompt = fake_container.ai.calls[-1]["messages"][1]["content"]
    assert f"- version {head}: 1 change(s)" in prompt
    # Nothing was undone.
    assert auth_client.get(f"{w['base']}/entities/{w['guild']}").json()["lifecycle"] == "active"

    # A version that does not exist, an unknown role and a bad address are not passed on.
    fake_container.ai.queue(
        ChangePlan(
            understood="x",
            back_to_version=head + 5,
            invite=Invite(role="administrator", email="not an address"),
        )
    )
    odd = auth_client.post(
        f"{w['base']}/assist/changes",
        json={"instruction": "Go forward and make Sam an admin.", "expected_revision": head},
    ).json()
    assert odd["back_to_version"] is None
    assert odd["invite"] == {"role": "viewer", "email": None}


def test_the_god_hand_requires_a_session_and_a_provider_safe_format(api_client) -> None:
    from openai.lib._pydantic import to_strict_json_schema

    response = api_client.post(
        "/api/v1/projects/local-project/assist/changes",
        json={"instruction": "x", "expected_revision": 0},
    )
    assert response.status_code == 401

    def free_form(node: object) -> bool:
        if isinstance(node, dict):
            extra = node.get("additionalProperties")
            return (node.get("type") == "object" and extra is not False) or any(
                free_form(value) for value in node.values()
            )
        return isinstance(node, list) and any(free_form(value) for value in node)

    assert not free_form(to_strict_json_schema(ChangePlan))
