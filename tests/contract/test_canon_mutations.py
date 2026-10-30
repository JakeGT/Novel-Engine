"""Mutation proposal/review HTTP contract (2E)."""

import pytest

from tests.contract.test_canon_entities import _project_id, _publish_schema

pytestmark = pytest.mark.contract


def _base(container) -> str:
    return f"/api/v1/projects/{_project_id(container)}/mutations"


def _create(name: str, temporary: str = "tmp:a") -> dict:
    return {"op": "create_entity", "id": temporary, "type": "Character", "name": name}


def test_model_proposal_review_edit_and_commit(auth_client, fake_container) -> None:
    _publish_schema(auth_client, _project_id(fake_container))
    base = _base(fake_container)

    proposed = auth_client.post(
        base,
        json={
            "operations": [_create("Mra"), _create("Unwanted", "tmp:b")],
            "expected_revision": 0,
            "origin": "model",
        },
    )
    assert proposed.status_code == 201, proposed.text
    detail = proposed.json()
    mutation_id = detail["id"]
    assert detail["status"] == "draft"
    assert detail["requires_review"] is True
    assert detail["review_reasons"] == ["protected_origin"]
    assert detail["is_stale"] is False
    assert [op["revision"] for op in detail["operations"]] == [1, 1]

    # A protected-origin proposal cannot commit without an explicit approval.
    blocked = auth_client.post(f"{base}/{mutation_id}/commit")
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["code"] == "review_required"
    assert blocked.json()["field_errors"]["mutation_id"] == mutation_id

    listed = auth_client.get(f"{base}?status=needs_review").json()
    assert [item["id"] for item in listed["items"]] == [mutation_id]

    edited = auth_client.put(
        f"{base}/{mutation_id}/operations/0",
        json={"operation": _create("Mara"), "reason": "fix spelling"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["operations"][0]["revision"] == 2
    assert edited.json()["operations"][0]["payload"]["name"] == "Mara"

    rejected_op = auth_client.post(
        f"{base}/{mutation_id}/operations/1/decision",
        json={"decision": "rejected", "note": "duplicate"},
    )
    assert rejected_op.status_code == 200, rejected_op.text
    assert rejected_op.json()["operations"][1]["decision"]["decision"] == "rejected"

    preview = auth_client.post(f"{base}/{mutation_id}/validate")
    assert preview.status_code == 200 and preview.json() == {"ok": True, "issues": []}

    approved = auth_client.post(f"{base}/{mutation_id}/approve", json={"note": "ok"})
    assert approved.status_code == 200 and approved.json()["status"] == "approved"

    committed = auth_client.post(f"{base}/{mutation_id}/commit")
    assert committed.status_code == 200, committed.text
    assert committed.json()["revision"] == 1
    assert committed.headers["ETag"] == '"1"'

    final = auth_client.get(f"{base}/{mutation_id}").json()
    assert final["status"] == "committed"
    assert final["committed_revision"] == 1
    assert [(t["from_status"], t["to_status"]) for t in final["transitions"]] == [
        ("draft", "needs_review"),
        ("needs_review", "approved"),
        ("approved", "committed"),
    ]
    assert all(t["reason"] and t["actor_user_id"] for t in final["transitions"])

    entities = auth_client.get(f"/api/v1/projects/{_project_id(fake_container)}/entities").json()[
        "items"
    ]
    assert [entity["name"] for entity in entities] == ["Mara"]

    # Terminal: nothing about a committed set can change.
    assert auth_client.post(f"{base}/{mutation_id}/reject", json={}).status_code == 409


def test_rejected_proposal_never_commits(auth_client, fake_container) -> None:
    _publish_schema(auth_client, _project_id(fake_container))
    base = _base(fake_container)
    mutation_id = auth_client.post(
        base, json={"operations": [_create("Ghost")], "expected_revision": 0}
    ).json()["id"]

    rejected = auth_client.post(f"{base}/{mutation_id}/reject", json={"note": "no"})
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"

    commit = auth_client.post(f"{base}/{mutation_id}/commit")
    assert commit.status_code == 422
    assert "review_rejected" in commit.json()["detail"]


def test_proposal_validation_reports_issues_per_operation(auth_client, fake_container) -> None:
    _publish_schema(auth_client, _project_id(fake_container))
    base = _base(fake_container)
    mutation_id = auth_client.post(
        base,
        json={
            "operations": [
                _create("Fine"),
                {"op": "create_entity", "id": "tmp:b", "type": "Alien", "name": "Zorp"},
            ],
            "expected_revision": 0,
        },
    ).json()["id"]

    report = auth_client.post(f"{base}/{mutation_id}/validate").json()
    assert report["ok"] is False
    assert [(issue["ordinal"], issue["code"]) for issue in report["issues"]] == [
        (1, "unknown_type")
    ]


def test_proposal_contract_rejects_bad_input(auth_client, fake_container) -> None:
    _publish_schema(auth_client, _project_id(fake_container))
    base = _base(fake_container)

    assert (
        auth_client.post(base, json={"operations": [], "expected_revision": 0}).status_code == 422
    )
    unknown_op = auth_client.post(
        base, json={"operations": [{"op": "drop_database", "id": "x"}], "expected_revision": 0}
    )
    assert unknown_op.status_code == 422
    trusted = auth_client.post(
        base,
        json={"operations": [_create("A")], "expected_revision": 0, "origin": "migration"},
    )
    assert trusted.status_code == 400
    assert auth_client.get(f"{base}/mut_missing").status_code == 404


def test_mutation_api_requires_authentication(api_client, fake_container) -> None:
    base = _base(fake_container)
    assert api_client.get(base).status_code == 401
    assert (
        api_client.post(base, json={"operations": [_create("A")], "expected_revision": 0})
    ).status_code in {401, 403}


def test_several_changes_are_decided_in_one_request_or_not_at_all(
    auth_client, fake_container
) -> None:
    _publish_schema(auth_client, _project_id(fake_container))
    base = _base(fake_container)
    mutation_id = auth_client.post(
        base,
        json={
            "operations": [_create("Ana"), _create("Bo", "tmp:b"), _create("Cy", "tmp:c")],
            "expected_revision": 0,
            "origin": "model",
        },
    ).json()["id"]

    decided = auth_client.post(
        f"{base}/{mutation_id}/operations/decisions",
        json={
            "decisions": [
                {"ordinal": 0, "decision": "rejected"},
                {"ordinal": 2, "decision": "rejected", "note": "not needed"},
            ]
        },
    )
    assert decided.status_code == 200, decided.text
    kinds = [(op["decision"] or {}).get("decision") for op in decided.json()["operations"]]
    assert kinds == ["rejected", None, "rejected"]

    # One unknown change refuses the whole request: nothing is half-recorded.
    refused = auth_client.post(
        f"{base}/{mutation_id}/operations/decisions",
        json={
            "decisions": [
                {"ordinal": 1, "decision": "rejected"},
                {"ordinal": 9, "decision": "rejected"},
            ]
        },
    )
    assert refused.status_code == 404, refused.text
    after = auth_client.get(f"{base}/{mutation_id}").json()
    assert after["operations"][1]["decision"] is None

    empty = auth_client.post(f"{base}/{mutation_id}/operations/decisions", json={"decisions": []})
    assert empty.status_code == 422


def test_a_proposal_is_checked_against_the_story_in_an_authors_words(
    auth_client, fake_container
) -> None:
    project_id = _project_id(fake_container)
    _publish_schema(auth_client, project_id)
    entities = f"/api/v1/projects/{project_id}/entities"
    harbor = auth_client.post(
        entities, json={"type": "Location", "name": "Lantern Harbor", "expected_revision": 0}
    ).json()["id"]
    mara = auth_client.post(
        entities, json={"type": "Character", "name": "Mara", "expected_revision": 1}
    ).json()["id"]
    placed = {
        "subject_id": mara,
        "predicate": "LOCATED_AT",
        "object": {"kind": "entity_ref", "entity_id": harbor, "project_id": project_id},
    }
    known = {
        "subject_id": mara,
        "predicate": "KNOWS",
        "object": {"kind": "entity_ref", "entity_id": "tmp:b", "project_id": project_id},
    }
    assert (
        auth_client.post(
            f"/api/v1/projects/{project_id}/facts",
            json={"proposition": placed, "expected_revision": 2},
        ).status_code
        == 201
    )

    base = _base(fake_container)
    mutation_id = auth_client.post(
        base,
        json={
            "operations": [
                # The same name on a different kind of entry.
                {"op": "create_entity", "id": "tmp:a", "type": "Faction", "name": "lantern harbor"},
                {"op": "create_entity", "id": "tmp:b", "type": "Character", "name": "Bo"},
                # Already true, and single-valued: the structural check refuses it.
                {"op": "assert_fact", "id": "tmp:f", "proposition": placed},
                {"op": "assert_fact", "id": "tmp:g", "proposition": known},
            ],
            "expected_revision": 3,
            "origin": "model",
        },
    ).json()["id"]

    report = auth_client.post(f"{base}/{mutation_id}/validate").json()
    found = {(i["ordinal"], i["code"]): i for i in report["issues"]}
    assert set(found) == {
        (0, "possible_duplicate"),
        (2, "cardinality_conflict"),
        (2, "already_recorded"),
    }
    assert report["ok"] is False
    assert found[(0, "possible_duplicate")]["severity"] == "advisory"
    assert (
        "You already have a location called “Lantern Harbor”"
        in (found[(0, "possible_duplicate")]["message"])
    )
    # Names, never ids.
    assert found[(2, "cardinality_conflict")]["message"].startswith(
        "Mara can be “located at” only one thing at a time"
    )
    assert all("ent_" not in issue["message"] for issue in report["issues"])
