"""Dismissing advisory findings on a proposal in review (CON-016): for that proposal only,
back by itself when the change is edited or what the finding says changes; a finding that
blocks is never offered."""

import pytest

from tests.contract.test_canon_entities import _project_id, _publish_schema

pytestmark = pytest.mark.contract


def _proposal(client, container) -> tuple[str, str]:
    project_id = _project_id(container)
    _publish_schema(client, project_id)
    client.post(
        f"/api/v1/projects/{project_id}/entities",
        json={"type": "Location", "name": "Lantern Harbor", "expected_revision": 0},
    )
    base = f"/api/v1/projects/{project_id}/mutations"
    made = client.post(
        base,
        json={
            "operations": [
                {"op": "create_entity", "id": "tmp:a", "type": "Faction", "name": "lantern harbor"},
            ],
            "expected_revision": 1,
            "origin": "model",
        },
    )
    assert made.status_code == 201, made.text
    return base, made.json()["id"]


def test_an_advisory_finding_is_dismissed_for_its_proposal_and_comes_back_on_an_edit(
    auth_client, fake_container
):
    base, mutation_id = _proposal(auth_client, fake_container)
    listed = auth_client.get(f"{base}/{mutation_id}/findings")
    assert listed.status_code == 200, listed.text
    (finding,) = listed.json()["items"]
    assert finding["code"] == "possible_duplicate" and finding["ordinal"] == 0
    assert finding["dismissal"] is None and finding["severity"] == "advisory"

    # The author's own words are required for "other".
    refused = auth_client.post(
        f"{base}/{mutation_id}/findings/dismissals",
        json={"key": finding["key"], "reason": "other", "note": " "},
    )
    assert refused.status_code == 400, refused.text
    done = auth_client.post(
        f"{base}/{mutation_id}/findings/dismissals",
        json={"key": finding["key"], "reason": "not_a_problem"},
    )
    assert done.status_code == 200, done.text
    (dismissed,) = done.json()["items"]
    assert dismissed["dismissal"]["reason"] == "not_a_problem"
    # Still shown by the review screen's own check: dismissing hides nothing there.
    report = auth_client.post(f"{base}/{mutation_id}/validate").json()
    assert any(i["code"] == "possible_duplicate" for i in report["issues"])

    # Undo brings it back; dismissing again works.
    undone = auth_client.delete(
        f"{base}/{mutation_id}/findings/dismissals/{dismissed['dismissal']['id']}"
    )
    assert undone.status_code == 200 and undone.json()["items"][0]["dismissal"] is None
    again = auth_client.post(
        f"{base}/{mutation_id}/findings/dismissals",
        json={"key": finding["key"], "reason": "intended"},
    ).json()["items"][0]
    assert again["dismissal"]["reason"] == "intended"

    # The change is edited (still a duplicate): it is a new revision, so the finding is back.
    edited = auth_client.put(
        f"{base}/{mutation_id}/operations/0",
        json={
            "operation": {
                "op": "create_entity",
                "id": "tmp:a",
                "type": "Faction",
                "name": "Lantern Harbor",
            }
        },
    )
    assert edited.status_code == 200, edited.text
    (back,) = auth_client.get(f"{base}/{mutation_id}/findings").json()["items"]
    assert back["dismissal"] is None and back["key"] != finding["key"]


def test_a_finding_that_is_gone_cannot_be_dismissed(auth_client, fake_container):
    base, mutation_id = _proposal(auth_client, fake_container)
    missing = auth_client.post(
        f"{base}/{mutation_id}/findings/dismissals",
        json={"key": "0" * 64, "reason": "intended"},
    )
    assert missing.status_code == 404, missing.text
    unknown = auth_client.get(f"{base}/nope/findings")
    assert unknown.status_code == 404
