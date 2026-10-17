"""Story rules the author sets to block (CON-015, ADR-013).

A rule warns until the author chooses to make it block. A blocking rule stops a change
that would newly break it, at approval and again inside the commit, and only when the
rule was worked out over the whole story: anything less only warns.
"""

import asyncio

import pytest

from backend.domain.validation import (
    RuleEnforcement,
    ValidationContext,
    ValidationSeverity,
    classify_rule_evaluation,
)
from tests.contract.test_god_hand import _world
from tests.contract.test_rules import _add, _rules

pytestmark = pytest.mark.contract


def _set(client, w, rule_id: str, enforcement: str):
    return client.patch(f"{w['base']}/rules/{rule_id}", json={"enforcement": enforcement})


def _leave_guild(client, w, head: int) -> str:
    """A suggested change (from the assistant) that leaves the guild with nobody in it."""
    proposed = client.post(
        f"{w['base']}/mutations",
        json={
            "origin": "model",
            "expected_revision": head,
            "operations": [{"op": "close_fact", "id": w["in_guild"]}],
        },
    )
    assert proposed.status_code == 201, proposed.text
    mutation_id = proposed.json()["id"]
    client.post(f"{w['base']}/mutations/{mutation_id}/submit", json={"note": "test"})
    return str(mutation_id)


def test_a_new_rule_warns_and_blocking_is_the_authors_explicit_choice(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    rule = _add(auth_client, w).json()
    assert rule["enforcement"] == "warning"

    # Rewording never makes a rule block.
    reworded = auth_client.patch(
        f"{w['base']}/rules/{rule['id']}", json={"title": "Every guild has a member"}
    ).json()
    assert reworded["enforcement"] == "warning"

    blocking = _set(auth_client, w, rule["id"], "blocking")
    assert blocking.status_code == 200, blocking.text
    assert blocking.json()["enforcement"] == "blocking"
    assert _rules(auth_client, w)[0]["enforcement"] == "blocking"
    assert _set(auth_client, w, rule["id"], "loud").status_code == 422


def test_a_blocking_rule_cannot_be_approved_past_until_it_is_fixed(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    rule = _add(auth_client, w).json()
    assert _set(auth_client, w, rule["id"], "blocking").status_code == 200
    mutation_id = _leave_guild(auth_client, w, head)

    report = auth_client.post(f"{w['base']}/mutations/{mutation_id}/validate").json()
    assert report["ok"] is False
    (issue,) = report["issues"]
    assert issue["severity"] == "blocking" and issue["ordinal"] == 0
    assert "which you set to block" in issue["message"]
    assert "Harbor Guild" in issue["message"]

    refused = auth_client.post(f"{w['base']}/mutations/{mutation_id}/approve", json={})
    assert refused.status_code in (400, 422), refused.text
    detail = refused.json()["detail"]
    assert "Guilds have members" in detail and "rule:" not in detail
    assert auth_client.get(f"{w['base']}/mutations/{mutation_id}").json()["status"] != "approved"

    # Set back to warn, the same change can be approved and added.
    assert _set(auth_client, w, rule["id"], "warning").status_code == 200
    assert (
        auth_client.post(f"{w['base']}/mutations/{mutation_id}/approve", json={}).status_code == 200
    )
    assert auth_client.post(f"{w['base']}/mutations/{mutation_id}/commit").status_code == 200


def test_the_commit_refuses_a_change_that_breaks_a_blocking_rule(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    rule = _add(auth_client, w).json()
    assert _set(auth_client, w, rule["id"], "blocking").status_code == 200

    # The author's own change goes straight to the commit, which checks the rule itself.
    refused = auth_client.post(
        f"{w['base']}/entities",
        json={"type": "Faction", "name": "The Empty Guild", "expected_revision": head},
    )
    assert refused.status_code == 422, refused.text
    detail = refused.json()["detail"]
    assert "The Empty Guild" in detail and "set to block" in detail and "[" not in detail
    # Nothing was added: the next change still expects the same version of the story.
    allowed = auth_client.post(
        f"{w['base']}/entities",
        json={"type": "Character", "name": "Ines", "expected_revision": head},
    )
    assert allowed.status_code == 201, allowed.text


def test_a_rule_already_broken_does_not_block_an_unrelated_change(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    rule = _add(auth_client, w).json()
    # Break it while it only warns.
    mutation_id = _leave_guild(auth_client, w, head)
    auth_client.post(f"{w['base']}/mutations/{mutation_id}/approve", json={})
    assert auth_client.post(f"{w['base']}/mutations/{mutation_id}/commit").status_code == 200
    assert _set(auth_client, w, rule["id"], "blocking").status_code == 200
    assert [b["name"] for b in _rules(auth_client, w)[0]["broken_by"]] == ["Harbor Guild"]

    unrelated = auth_client.post(
        f"{w['base']}/entities",
        json={"type": "Character", "name": "Ines", "expected_revision": head + 1},
    )
    assert unrelated.status_code == 201, unrelated.text


def test_a_paused_blocking_rule_blocks_nothing(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    rule = _add(auth_client, w).json()
    auth_client.patch(
        f"{w['base']}/rules/{rule['id']}", json={"enforcement": "blocking", "status": "paused"}
    )
    made = auth_client.post(
        f"{w['base']}/entities",
        json={"type": "Faction", "name": "The Empty Guild", "expected_revision": head},
    )
    assert made.status_code == 201, made.text


# ------------------------------------------------- only a complete evaluation blocks


class _PartialReader:
    """A reader that cannot list the whole story, as an extraction-fed one could not."""


def test_a_rule_that_cannot_see_the_whole_story_only_warns(
    auth_client, fake_container, monkeypatch
):
    from backend.domain.operations import CreateEntityOp
    from backend.services import rule_service

    w = _world(auth_client, fake_container)
    rule = _add(auth_client, w).json()
    assert _set(auth_client, w, rule["id"], "blocking").status_code == 200
    service = fake_container.rule_service
    repository = service._reader  # noqa: SLF001 - the story the service reads
    workspace_id = fake_container.settings.AUTH_LOCAL_WORKSPACE_ID
    project_id = fake_container.settings.AUTH_LOCAL_PROJECT_ID
    operation = CreateEntityOp(id="tmp:empty", type="Faction", name="The Empty Guild")

    class _Whole:
        async def active_entities(self, limit: int):
            return list(
                await repository.list_entities(
                    workspace_id=workspace_id, project_id=project_id, limit=limit
                )
            )

        async def asserted_facts(self, limit: int):
            return list(
                await repository.list_facts(
                    workspace_id=workspace_id, project_id=project_id, limit=limit
                )
            )

    def evaluate(reader):
        context = ValidationContext(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id="",
            schema_version_id=None,
            head_revision=0,
            reader=reader,
        )
        return asyncio.run(service.evaluate([operation], context=context))

    (whole,) = evaluate(_Whole())
    assert whole.complete is True and whole.rule_version == 1
    assert classify_rule_evaluation(whole) is ValidationSeverity.BLOCKING_RULE

    # A reader that cannot give the whole story: the result is incomplete.
    (partial,) = evaluate(_PartialReader())
    assert partial.enforcement is RuleEnforcement.BLOCKING and partial.complete is False
    assert classify_rule_evaluation(partial) is ValidationSeverity.ADVISORY

    # A story larger than a rule may read: incomplete too, and it says so.
    monkeypatch.setattr(rule_service, "MAX_ENTITIES", 2)
    (large,) = evaluate(_Whole())
    assert large.complete is False and "too large" in large.message
    assert classify_rule_evaluation(large) is ValidationSeverity.ADVISORY


def test_a_what_ifs_change_is_checked_against_the_what_if_whatever_line_approves_it(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    rule = _add(auth_client, w).json()
    made = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Mara stays"})
    assert made.status_code == 201, made.text
    what_if = {"X-Story-Branch": made.json()["id"]}

    # In the main story the guild empties while the rule only warns: there it is
    # already broken. In the what-if Mara is still a member.
    left = _leave_guild(auth_client, w, head)
    auth_client.post(f"{w['base']}/mutations/{left}/approve", json={})
    assert auth_client.post(f"{w['base']}/mutations/{left}/commit").status_code == 200
    assert _set(auth_client, w, rule["id"], "blocking").status_code == 200

    proposed = auth_client.post(
        f"{w['base']}/mutations",
        json={
            "origin": "model",
            "expected_revision": head,
            "operations": [{"op": "close_fact", "id": w["in_guild"]}],
        },
        headers=what_if,
    )
    assert proposed.status_code == 201, proposed.text
    mutation_id = proposed.json()["id"]
    auth_client.post(
        f"{w['base']}/mutations/{mutation_id}/submit", json={"note": "test"}, headers=what_if
    )

    # Approved from the main story, it still empties the guild in the what-if.
    refused = auth_client.post(f"{w['base']}/mutations/{mutation_id}/approve", json={})
    assert refused.status_code in (400, 422), refused.text
    assert "Guilds have members" in refused.json()["detail"]
