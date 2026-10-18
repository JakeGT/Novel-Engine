"""Keep the safe ones for me (AST-EXP-015, OSS-007).

Off by default; a kind can be switched on only after it was approved by hand three
times; only purely additive suggestions from the assistant in the app are kept; a kept
suggestion is committed through the ordinary path with a decision that names the rule,
listed with an Undo that suggests the revert; switching off stops it at once.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from backend.domain.branching import working_in
from backend.domain.delegation import (
    DAILY_LIMIT,
    VIA_CLIENT,
    VIA_TEXT,
    DelegationSettings,
    RuleState,
)
from backend.domain.identity import AccessScope, AuthMode, utcnow
from backend.services.canon_change_workflow import ChangePlan, EntityChange, RecordRef
from backend.services.canon_creation_workflow import DraftAttribute, DraftEntity, DraftFact
from tests.contract.test_god_hand import _world
from tests.contract.test_rules import _add

pytestmark = pytest.mark.contract


# ----- helpers ---------------------------------------------------------------------------


def _head(client, w) -> int:
    items = client.get(f"{w['base']}/revisions", params={"limit": 1}).json()["items"]
    return max((item["revision"] for item in items), default=0)


def _ask(client, container, w, plan: ChangePlan) -> dict:
    """The author asks the assistant for a change in the app."""
    container.ai.queue(plan)
    response = client.post(
        f"{w['base']}/assist/changes",
        json={"instruction": "Please do this.", "expected_revision": _head(client, w)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _new_entry(name: str, **extra: Any) -> ChangePlan:
    return ChangePlan(
        understood=f"Add {name}.",
        new_entities=[DraftEntity(ref="n", type="Character", name=name, **extra)],
    )


def _keep_by_hand(client, w, mutation_id: str) -> None:
    approved = client.post(f"{w['base']}/mutations/{mutation_id}/approve", json={})
    assert approved.status_code == 200, approved.text
    committed = client.post(f"{w['base']}/mutations/{mutation_id}/commit", json={})
    assert committed.status_code == 200, committed.text


def _earn(client, container, w, plans: list[ChangePlan]) -> None:
    for plan in plans:
        body = _ask(client, container, w, plan)
        assert body["status"] == "needs_review", body
        _keep_by_hand(client, w, body["mutation_id"])


def _settings(client, w) -> dict:
    return client.get(f"{w['base']}/kept-for-you/settings").json()


def _switch(client, w, kinds: list[str], **body: Any):
    return client.put(f"{w['base']}/kept-for-you/settings", json={"kinds": kinds, **body})


def _decision(client, w, mutation_id: str) -> dict:
    return client.get(f"{w['base']}/kept-for-you/decisions/{mutation_id}").json()


def _with_new_entries_on(client, container) -> dict:
    w = _world(client, container)
    _earn(client, container, w, [_new_entry(name) for name in ("Ines", "Oskar", "Pell")])
    switched = _switch(client, w, ["new_entry"])
    assert switched.status_code == 200, switched.text
    return w


def _scope(container) -> AccessScope:
    settings = container.settings
    return AccessScope(
        actor_user_id=settings.AUTH_LOCAL_USER_ID,
        auth_mode=AuthMode.LOCAL,
        workspace_id=settings.AUTH_LOCAL_WORKSPACE_ID,
        project_id=settings.AUTH_LOCAL_PROJECT_ID,
    )


def _suggest(client, w, operations: list[dict], *, origin: str = "model", head: int | None = None):
    """A suggestion made through the general API (as another program would make it)."""
    proposed = client.post(
        f"{w['base']}/mutations",
        json={
            "origin": origin,
            "expected_revision": _head(client, w) if head is None else head,
            "operations": operations,
        },
    )
    assert proposed.status_code == 201, proposed.text
    mutation_id = proposed.json()["id"]
    client.post(f"{w['base']}/mutations/{mutation_id}/submit", json={"note": "test"})
    return mutation_id


def _consider(container, mutation_id: str, **kwargs: Any):
    return asyncio.run(
        container.delegation_service.consider(_scope(container), mutation_id, **kwargs)
    )


def _status(client, w, mutation_id: str) -> str:
    return client.get(f"{w['base']}/mutations/{mutation_id}").json()["status"]


# ----- off by default, and earned -----------------------------------------------------------


def test_off_by_default_and_nothing_is_kept(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    settings = _settings(auth_client, w)
    assert [rule["on"] for rule in settings["rules"]] == [False, False, False]
    assert settings["hand_approvals_needed"] == 3 and settings["allow_clients"] is False
    assert settings["workspace_forbidden"] is False
    assert any("Undoing" in line for line in settings["always_waits"])

    body = _ask(auth_client, fake_container, w, _new_entry("Ines"))
    assert body["status"] == "needs_review"
    # Nothing was switched on, so nothing looked at it.
    assert _decision(auth_client, w, body["mutation_id"])["how"] is None


def test_a_kind_is_switched_on_only_after_three_approvals_by_hand(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    refused = _switch(auth_client, w, ["new_entry"])
    assert refused.status_code == 400
    assert "by hand 3 times first" in refused.json()["detail"]
    assert "0 times" in refused.json()["detail"]

    _earn(auth_client, fake_container, w, [_new_entry("Ines"), _new_entry("Oskar")])
    rule = next(r for r in _settings(auth_client, w)["rules"] if r["kind"] == "new_entry")
    assert rule["hand_approvals"] == 2
    assert _switch(auth_client, w, ["new_entry"]).status_code == 400

    # A discarded suggestion is not an approval.
    discarded = _ask(auth_client, fake_container, w, _new_entry("Quill"))
    auth_client.post(f"{w['base']}/mutations/{discarded['mutation_id']}/reject", json={})
    assert _switch(auth_client, w, ["new_entry"]).status_code == 400

    _earn(auth_client, fake_container, w, [_new_entry("Pell")])
    switched = _switch(auth_client, w, ["new_entry"])
    assert switched.status_code == 200, switched.text
    rule = next(r for r in switched.json()["rules"] if r["kind"] == "new_entry")
    assert rule["on"] is True and rule["hand_approvals"] == 3 and rule["since"]
    # The author's own changes never count: facts were never approved from the assistant.
    assert _switch(auth_client, w, ["new_entry", "new_fact"]).status_code == 400
    assert _switch(auth_client, w, ["nonsense"]).status_code == 400


# ----- kept, recorded, listed, undone ---------------------------------------------------------


def test_a_kept_suggestion_is_committed_with_a_decision_naming_the_rule(
    auth_client, fake_container
) -> None:
    w = _with_new_entries_on(auth_client, fake_container)
    assert _settings(auth_client, w)["kept_since_seen"] == 0

    body = _ask(auth_client, fake_container, w, _new_entry("Ruth"))
    assert body["status"] == "committed", body
    mutation_id = body["mutation_id"]

    # In the story, through the ordinary commit.
    names = [item["name"] for item in auth_client.get(f"{w['base']}/entities").json()["items"]]
    assert "Ruth" in names
    review = auth_client.get(f"{w['base']}/mutations/{mutation_id}").json()
    assert review["origin"] == "model" and review["status"] == "committed"
    notes = [item["note"] for item in review["decisions"]]
    assert "Kept for you under your rule “New entries”." in notes
    revisions = auth_client.get(f"{w['base']}/revisions").json()["items"]
    assert any(item["mutation_id"] == mutation_id for item in revisions)

    decision = _decision(auth_client, w, mutation_id)
    assert decision["how"] == "kept" and decision["kinds"] == ["new_entry"]
    assert decision["note"] == "Kept for you under your rule “New entries”."

    # Listed, and counted for the review queue until the author looks.
    kept = auth_client.get(f"{w['base']}/kept-for-you").json()["items"]
    assert [item["mutation_id"] for item in kept] == [mutation_id]
    assert kept[0]["operations"][0]["operation_type"] == "create_entity"
    assert kept[0]["revision"] == _head(auth_client, w)
    assert _settings(auth_client, w)["kept_since_seen"] == 1
    assert auth_client.post(f"{w['base']}/kept-for-you/seen").status_code == 204
    assert _settings(auth_client, w)["kept_since_seen"] == 0
    # Kept suggestions do not count as approvals by hand.
    rule = next(r for r in _settings(auth_client, w)["rules"] if r["kind"] == "new_entry")
    assert rule["hand_approvals"] == 3


def test_undo_suggests_the_revert_which_always_waits(auth_client, fake_container) -> None:
    w = _with_new_entries_on(auth_client, fake_container)
    mutation_id = _ask(auth_client, fake_container, w, _new_entry("Ruth"))["mutation_id"]

    undo = auth_client.post(f"{w['base']}/kept-for-you/{mutation_id}/undo")
    assert undo.status_code == 201, undo.text
    undo_id = undo.json()["undo_mutation_id"]
    review = auth_client.get(f"{w['base']}/mutations/{undo_id}").json()
    assert review["intent"] == "revert" and review["status"] == "needs_review"
    assert [op["operation_type"] for op in review["operations"]] == ["retire_entity"]
    # Asking again gives the same waiting undo, not a second one.
    again = auth_client.post(f"{w['base']}/kept-for-you/{mutation_id}/undo")
    assert again.json()["undo_mutation_id"] == undo_id
    kept = auth_client.get(f"{w['base']}/kept-for-you").json()["items"][0]
    assert kept["undo_mutation_id"] == undo_id and kept["undo_status"] == "needs_review"

    # Nothing is undone until the author says so.
    names = [item["name"] for item in auth_client.get(f"{w['base']}/entities").json()["items"]]
    assert "Ruth" in names
    _keep_by_hand(auth_client, w, undo_id)
    names = [item["name"] for item in auth_client.get(f"{w['base']}/entities").json()["items"]]
    assert "Ruth" not in names

    # Only what was kept can be undone from here.
    unknown = auth_client.post(f"{w['base']}/kept-for-you/mut_nothing/undo")
    assert unknown.status_code == 404


def test_first_details_and_new_facts_can_be_kept(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    tobin = w["tobin"]
    # Details for entries that have none, approved by hand three times.
    for name in ("Ines", "Oskar", "Pell"):
        made = _ask(auth_client, fake_container, w, _new_entry(name))
        _keep_by_hand(auth_client, w, made["mutation_id"])
        entity_id = next(
            item["id"]
            for item in auth_client.get(f"{w['base']}/entities").json()["items"]
            if item["name"] == name
        )
        plan = ChangePlan(
            understood="Give them a role.",
            entity_changes=[
                EntityChange(
                    ref=entity_id, set_attributes=[DraftAttribute(key="role", value="cook")]
                )
            ],
        )
        detail = _ask(auth_client, fake_container, w, plan)
        _keep_by_hand(auth_client, w, detail["mutation_id"])
    assert _switch(auth_client, w, ["first_details"]).status_code == 200

    kept = _ask(
        auth_client,
        fake_container,
        w,
        ChangePlan(
            understood="Tobin is a smith.",
            entity_changes=[
                EntityChange(ref=tobin, set_attributes=[DraftAttribute(key="role", value="smith")])
            ],
        ),
    )
    assert kept["status"] == "committed", kept
    assert auth_client.get(f"{w['base']}/entities/{tobin}").json()["attributes"]["role"] == "smith"

    # Tobin has details now, so changing them waits.
    again = _ask(
        auth_client,
        fake_container,
        w,
        ChangePlan(
            understood="Tobin is a baker.",
            entity_changes=[
                EntityChange(ref=tobin, set_attributes=[DraftAttribute(key="role", value="baker")])
            ],
        ),
    )
    assert again["status"] == "needs_review"
    assert _decision(auth_client, w, again["mutation_id"])["reason_code"] == "not_additive"


# ----- every reason it waits ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("plan_for", "code"),
    [
        # Removing, archiving or ending anything.
        (
            lambda w: ChangePlan(
                understood="Cut Tobin.", archive_entities=[RecordRef(id=w["tobin"])]
            ),
            "not_adding",
        ),
        (
            lambda w: ChangePlan(
                understood="Mara leaves.", end_facts=[RecordRef(id=w["in_guild"])]
            ),
            "not_adding",
        ),
        # Replacing what is there: a new name, or details an entry already has.
        (
            lambda w: ChangePlan(
                understood="Rename.",
                entity_changes=[EntityChange(ref=w["tobin"], new_name="Tobias")],
            ),
            "not_adding",
        ),
        (
            lambda w: ChangePlan(
                understood="Eyes.",
                entity_changes=[
                    EntityChange(
                        ref=w["mara"], set_attributes=[DraftAttribute(key="eyes", value="grey")]
                    )
                ],
            ),
            "not_adding",
        ),
        # A kind not switched on.
        (
            lambda w: ChangePlan(
                understood="Tobin is in the guild.",
                add_facts=[
                    DraftFact(subject_ref=w["tobin"], predicate="MEMBER_OF", object_ref=w["guild"])
                ],
            ),
            "kind_off",
        ),
    ],
)
def test_suggestions_that_are_not_purely_new_wait(auth_client, fake_container, plan_for, code):
    w = _with_new_entries_on(auth_client, fake_container)
    body = _ask(auth_client, fake_container, w, plan_for(w))
    assert body["status"] == "needs_review", body
    decision = _decision(auth_client, w, body["mutation_id"])
    assert decision["how"] == "waited"
    if code == "not_adding":
        assert decision["reason_code"] in {"not_additive", "kind_off"}, decision
        assert decision["reason_code"] == "not_additive" or "fact" not in decision["reason"]
    else:
        assert decision["reason_code"] == code, decision
    assert decision["reason"] and "[" not in decision["reason"]


def test_a_whole_suggestion_waits_when_one_change_does_not_qualify(auth_client, fake_container):
    w = _with_new_entries_on(auth_client, fake_container)
    plan = ChangePlan(
        understood="Add Ruth and cut Tobin.",
        new_entities=[DraftEntity(ref="n", type="Character", name="Ruth")],
        archive_entities=[RecordRef(id=w["tobin"])],
    )
    body = _ask(auth_client, fake_container, w, plan)
    assert body["status"] == "needs_review"
    assert "archiving" in _decision(auth_client, w, body["mutation_id"])["reason"]


def test_a_story_rule_that_warns_or_blocks_makes_it_wait(auth_client, fake_container) -> None:
    w = _with_new_entries_on(auth_client, fake_container)
    rule = _add(auth_client, w).json()
    guild = ChangePlan(
        understood="A new guild.",
        new_entities=[DraftEntity(ref="g", type="Faction", name="The Empty Guild")],
    )
    warned = _ask(auth_client, fake_container, w, guild)
    assert _decision(auth_client, w, warned["mutation_id"])["reason_code"] == "warning"

    auth_client.patch(f"{w['base']}/rules/{rule['id']}", json={"enforcement": "blocking"})
    guild.new_entities[0].name = "The Hollow Guild"
    blocked = _ask(auth_client, fake_container, w, guild)
    assert blocked["status"] == "needs_review"
    assert _decision(auth_client, w, blocked["mutation_id"])["reason_code"] == "blocked"


def test_a_secret_is_never_kept(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    secret = auth_client.post(
        f"{w['base']}/facts",
        json={
            "proposition": {
                "subject_id": w["tobin"],
                "predicate": "LOCATED_AT",
                "object": {
                    "kind": "entity_ref",
                    "entity_id": w["harbor"],
                    "project_id": w["base"].rsplit("/", 1)[1],
                },
            },
            "visibility": "secret",
            "expected_revision": _head(auth_client, w),
        },
    )
    assert secret.status_code == 201, secret.text
    asyncio.run(
        fake_container.delegation_store.set(
            workspace_id=fake_container.settings.AUTH_LOCAL_WORKSPACE_ID,
            project_id=fake_container.settings.AUTH_LOCAL_PROJECT_ID,
            settings=DelegationSettings(rules={"new_fact": RuleState(since=utcnow())}),
            actor_user_id=None,
        )
    )
    for _ in range(3):
        asyncio.run(
            fake_container.delegation_store.put_record(
                workspace_id=fake_container.settings.AUTH_LOCAL_WORKSPACE_ID,
                project_id=fake_container.settings.AUTH_LOCAL_PROJECT_ID,
                mutation_id=f"mut_hand_{_}",
                how="by_hand",
                state="counted",
                kinds=["new_fact"],
                branch_id="",
                actor_user_id=None,
            )
        )
    mutation_id = _suggest(
        auth_client,
        w,
        [
            {
                "op": "assert_fact",
                "id": "tmp:f",
                "proposition": {
                    "subject_id": w["tobin"],
                    "predicate": "LOCATED_AT",
                    "object": {
                        "kind": "entity_ref",
                        "entity_id": w["saltmere"],
                        "project_id": w["base"].rsplit("/", 1)[1],
                    },
                },
            }
        ],
    )
    considered = _consider(fake_container, mutation_id)
    assert considered.kept is False and considered.refusal.code == "secret"
    # And one that is itself a secret.
    marked = _suggest(
        auth_client,
        w,
        [
            {
                "op": "assert_fact",
                "id": "tmp:g",
                "visibility": "secret",
                "proposition": {
                    "subject_id": w["mara"],
                    "predicate": "OWNS",
                    "object": {"kind": "string", "value": "a key"},
                },
            }
        ],
    )
    assert _consider(fake_container, marked).refusal.code == "secret"


def test_reasons_beyond_the_changes_themselves(auth_client, fake_container) -> None:
    """Imports, undoing, other assistants, texts, what-ifs, size, stale and the daily cap."""
    w = _with_new_entries_on(auth_client, fake_container)
    entry = [{"op": "create_entity", "id": "tmp:s", "type": "Character", "name": "Sela"}]

    imported = _suggest(auth_client, w, entry, origin="import")
    assert _consider(fake_container, imported).refusal.code == "import"

    # A suggestion made through the general API is never looked at by the rules...
    from_api = _suggest(auth_client, w, entry)
    assert _status(auth_client, w, from_api) == "needs_review"
    assert _decision(auth_client, w, from_api)["how"] is None
    # ...and one from another assistant waits unless the story allows it.
    assert _consider(fake_container, from_api, via=VIA_CLIENT).refusal.code == "client"
    assert _decision(auth_client, w, from_api)["reason_code"] == "client"
    assert _consider(fake_container, from_api, via=VIA_TEXT).refusal.code == "text"

    with working_in("brn_somewhere"):
        assert _consider(fake_container, from_api).refusal.code == "what_if"

    many = _suggest(
        auth_client,
        w,
        [
            {"op": "create_entity", "id": f"tmp:c{n}", "type": "Character", "name": f"Crew {n}"}
            for n in range(13)
        ],
    )
    assert _consider(fake_container, many).refusal.code == "too_many"

    stale = _suggest(auth_client, w, entry, head=_head(auth_client, w))
    created = auth_client.post(
        f"{w['base']}/entities",
        json={"type": "Character", "name": "Vale", "expected_revision": _head(auth_client, w)},
    )
    assert created.status_code == 201
    assert _consider(fake_container, stale).refusal.code == "stale"

    # Another assistant's suggestion is never kept, and allowing it is refused.
    refused = _switch(auth_client, w, ["new_entry"], allow_clients=True)
    assert refused.status_code == 400 and "only suggest" in refused.json()["detail"]
    fresh = _suggest(
        auth_client, w, [{"op": "create_entity", "id": "tmp:t", "type": "Character", "name": "Tam"}]
    )
    assert _consider(fake_container, fresh, via=VIA_CLIENT).refusal.code == "client"

    # At most so many a day.
    for n in range(DAILY_LIMIT):
        asyncio.run(
            fake_container.delegation_store.put_record(
                workspace_id=fake_container.settings.AUTH_LOCAL_WORKSPACE_ID,
                project_id=fake_container.settings.AUTH_LOCAL_PROJECT_ID,
                mutation_id=f"mut_day_{n}",
                how="kept",
                state="kept",
                kinds=["new_entry"],
                branch_id="",
                actor_user_id=None,
            )
        )
    capped = _ask(auth_client, fake_container, w, _new_entry("Una"))
    assert capped["status"] == "needs_review"
    assert _decision(auth_client, w, capped["mutation_id"])["reason_code"] == "daily_limit"


def test_an_undo_is_never_kept_even_when_rules_are_on(auth_client, fake_container) -> None:
    w = _with_new_entries_on(auth_client, fake_container)
    mutation_id = _ask(auth_client, fake_container, w, _new_entry("Ruth"))["mutation_id"]
    undo_id = auth_client.post(f"{w['base']}/kept-for-you/{mutation_id}/undo").json()[
        "undo_mutation_id"
    ]
    assert _consider(fake_container, undo_id).refusal.code in {"not_assistant", "revert"}
    assert _status(auth_client, w, undo_id) == "needs_review"


def test_a_kind_without_enough_approvals_waits_even_if_switched_on(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    asyncio.run(
        fake_container.delegation_store.set(
            workspace_id=fake_container.settings.AUTH_LOCAL_WORKSPACE_ID,
            project_id=fake_container.settings.AUTH_LOCAL_PROJECT_ID,
            settings=DelegationSettings(rules={"new_entry": RuleState(since=utcnow())}),
            actor_user_id=None,
        )
    )
    body = _ask(auth_client, fake_container, w, _new_entry("Ines"))
    assert body["status"] == "needs_review"
    decision = _decision(auth_client, w, body["mutation_id"])
    assert decision["reason_code"] == "needs_hand_approvals"
    assert "0 times; it takes 3" in decision["reason"]


# ----- switching off -----------------------------------------------------------------------


def test_switching_off_stops_it_at_once(auth_client, fake_container) -> None:
    w = _with_new_entries_on(auth_client, fake_container)
    assert _ask(auth_client, fake_container, w, _new_entry("Ruth"))["status"] == "committed"
    off = _switch(auth_client, w, [])
    assert off.status_code == 200 and not any(rule["on"] for rule in off.json()["rules"])
    body = _ask(auth_client, fake_container, w, _new_entry("Sela"))
    assert body["status"] == "needs_review"
    # Switching back on needs no new approvals: they were earned.
    assert _switch(auth_client, w, ["new_entry"]).status_code == 200


def test_switching_off_while_one_is_being_kept_sends_it_back_to_you(
    auth_client, fake_container
) -> None:
    w = _with_new_entries_on(auth_client, fake_container)
    service = fake_container.mutation_service
    approve = service.approve

    async def approve_then_switch_off(scope, mutation_id, note=None):
        await approve(scope, mutation_id, note)
        # The author switches it off in another tab, after approval and before the commit.
        await fake_container.delegation_service.set_story(
            scope, scope.workspace_id, scope.project_id, kinds=[], allow_clients=False
        )

    service.approve = approve_then_switch_off
    try:
        body = _ask(auth_client, fake_container, w, _new_entry("Ruth"))
    finally:
        service.approve = approve
    assert body["status"] == "needs_review", body
    decision = _decision(auth_client, w, body["mutation_id"])
    assert decision["how"] == "waited" and decision["reason_code"] == "kept_rule_off"
    assert "switched off" in decision["reason"]
    names = [item["name"] for item in auth_client.get(f"{w['base']}/entities").json()["items"]]
    assert "Ruth" not in names
    assert auth_client.get(f"{w['base']}/kept-for-you").json()["items"] == []


def test_switching_off_returns_one_left_approved_to_review(auth_client, fake_container) -> None:
    """A request that stopped after approving under a rule but before committing."""
    w = _with_new_entries_on(auth_client, fake_container)
    mutation_id = _suggest(
        auth_client,
        w,
        [{"op": "create_entity", "id": "tmp:r", "type": "Character", "name": "Ruth"}],
    )
    asyncio.run(
        fake_container.delegation_store.put_record(
            workspace_id=fake_container.settings.AUTH_LOCAL_WORKSPACE_ID,
            project_id=fake_container.settings.AUTH_LOCAL_PROJECT_ID,
            mutation_id=mutation_id,
            how="kept",
            state="pending",
            kinds=["new_entry"],
            branch_id="",
            actor_user_id=None,
        )
    )
    auth_client.post(f"{w['base']}/mutations/{mutation_id}/approve", json={})
    assert _status(auth_client, w, mutation_id) == "approved"

    _switch(auth_client, w, [])
    assert _status(auth_client, w, mutation_id) == "needs_review"
    assert _decision(auth_client, w, mutation_id)["reason_code"] == "kept_rule_off"


def test_the_commit_refuses_a_kept_suggestion_once_its_rule_is_off(auth_client, fake_container):
    w = _with_new_entries_on(auth_client, fake_container)
    mutation_id = _suggest(
        auth_client,
        w,
        [{"op": "create_entity", "id": "tmp:r", "type": "Character", "name": "Ruth"}],
    )
    auth_client.post(f"{w['base']}/mutations/{mutation_id}/approve", json={})
    asyncio.run(
        fake_container.delegation_store.put_record(
            workspace_id=fake_container.settings.AUTH_LOCAL_WORKSPACE_ID,
            project_id=fake_container.settings.AUTH_LOCAL_PROJECT_ID,
            mutation_id=mutation_id,
            how="kept",
            state="pending",
            kinds=["new_entry"],
            branch_id="",
            actor_user_id=None,
        )
    )
    # Switched off directly in storage, so only the commit's own check stands in the way.
    asyncio.run(
        fake_container.delegation_store.set(
            workspace_id=fake_container.settings.AUTH_LOCAL_WORKSPACE_ID,
            project_id=fake_container.settings.AUTH_LOCAL_PROJECT_ID,
            settings=DelegationSettings(),
            actor_user_id=None,
        )
    )
    refused = auth_client.post(f"{w['base']}/mutations/{mutation_id}/commit", json={})
    assert refused.status_code == 422, refused.text
    assert "switched off" in refused.text


def test_a_workspace_owner_can_turn_it_off_for_every_story(auth_client, fake_container) -> None:
    w = _with_new_entries_on(auth_client, fake_container)
    assert auth_client.get("/api/v1/ai/kept-for-you").json() == {"forbidden": False}
    off = auth_client.put("/api/v1/ai/kept-for-you", json={"forbidden": True})
    assert off.status_code == 200 and off.json() == {"forbidden": True}

    settings = _settings(auth_client, w)
    assert settings["workspace_forbidden"] is True
    body = _ask(auth_client, fake_container, w, _new_entry("Ruth"))
    assert body["status"] == "needs_review"
    assert _decision(auth_client, w, body["mutation_id"])["reason_code"] == "workspace_forbids"
    # The story's choice is kept, unused; nothing new can be switched on meanwhile.
    _earn(
        auth_client,
        fake_container,
        w,
        [
            ChangePlan(
                understood="Fact.",
                add_facts=[
                    DraftFact(subject_ref=w["tobin"], predicate="MEMBER_OF", object_ref=w["guild"])
                ],
            )
        ],
    )
    assert _switch(auth_client, w, ["new_entry", "new_fact"]).status_code == 400
    # Switching a kind off is never refused.
    assert _switch(auth_client, w, []).status_code == 200

    auth_client.put("/api/v1/ai/kept-for-you", json={"forbidden": False})
    assert _switch(auth_client, w, ["new_entry"]).status_code == 200
    assert _ask(auth_client, fake_container, w, _new_entry("Sela"))["status"] == "committed"


# ----- review findings (2026-10-18) ------------------------------------------------------------


def _ids(container) -> tuple[str, str]:
    return container.settings.AUTH_LOCAL_WORKSPACE_ID, container.settings.AUTH_LOCAL_PROJECT_ID


def _switch_on_directly(container, *kinds: str) -> None:
    """A rule on, with its approvals by hand, written straight to storage."""
    ws, pr = _ids(container)
    store = container.delegation_store
    asyncio.run(
        store.set(
            workspace_id=ws,
            project_id=pr,
            settings=DelegationSettings(rules={kind: RuleState(since=utcnow()) for kind in kinds}),
            actor_user_id=None,
        )
    )
    for n in range(3):
        asyncio.run(
            store.put_record(
                workspace_id=ws,
                project_id=pr,
                mutation_id=f"mut_hand_{n}",
                how="by_hand",
                state="counted",
                kinds=list(kinds),
                branch_id="",
                actor_user_id=None,
            )
        )


def _record(container, mutation_id: str):
    ws, pr = _ids(container)
    return asyncio.run(
        container.delegation_store.get_record(
            workspace_id=ws, project_id=pr, mutation_id=mutation_id
        )
    )


def test_rule_changes_are_written_to_the_workspace_audit(auth_client, fake_container) -> None:
    w = _with_new_entries_on(auth_client, fake_container)
    ws, pr = _ids(fake_container)
    changed = [e for e in fake_container.tenancy.audit if e.action == "delegation.rules_changed"]
    assert len(changed) == 1
    assert changed[0].workspace_id == ws and changed[0].target_id == pr
    assert changed[0].detail == {"before": [], "after": ["new_entry"]}
    author = changed[0].actor_user_id
    assert author

    _switch(auth_client, w, [])
    changed = [e for e in fake_container.tenancy.audit if e.action == "delegation.rules_changed"]
    assert changed[-1].detail == {"before": ["new_entry"], "after": []}

    auth_client.put("/api/v1/ai/kept-for-you", json={"forbidden": True})
    forbidden = [
        e for e in fake_container.tenancy.audit if e.action == "delegation.workspace_forbidden"
    ]
    assert len(forbidden) == 1 and forbidden[0].target_id == ws
    assert forbidden[0].detail == {"before": False, "after": True}
    assert forbidden[0].actor_user_id == author


def _fact_op(subject: str, predicate: str, obj: dict) -> dict:
    return {
        "op": "assert_fact",
        "id": "tmp:f",
        "proposition": {"subject_id": subject, "predicate": predicate, "object": obj},
    }


def test_a_fact_naming_anyone_a_secret_is_about_waits(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    project_id = w["base"].rsplit("/", 1)[1]

    def ref(key: str) -> dict:
        return {"kind": "entity_ref", "entity_id": w[key], "project_id": project_id}

    secret = auth_client.post(
        f"{w['base']}/facts",
        json={
            "proposition": {
                "subject_id": w["tobin"],
                "predicate": "LOCATED_AT",
                "object": ref("harbor"),
            },
            "visibility": "secret",
            "expected_revision": _head(auth_client, w),
        },
    )
    assert secret.status_code == 201, secret.text
    _switch_on_directly(fake_container, "new_fact")

    # About the same person, but a different kind of fact.
    owns = _suggest(
        auth_client, w, [_fact_op(w["tobin"], "OWNS", {"kind": "string", "value": "a boat"})]
    )
    assert _consider(fake_container, owns).refusal.code == "secret"
    # About someone else, but pointing at the place the secret names.
    there = _suggest(auth_client, w, [_fact_op(w["guild"], "LOCATED_AT", ref("harbor"))])
    assert _consider(fake_container, there).refusal.code == "secret"
    # Pointing at the person the secret is about.
    knows = _suggest(auth_client, w, [_fact_op(w["guild"], "KNOWS", ref("tobin"))])
    assert _consider(fake_container, knows).refusal.code == "secret"
    # Naming no one a secret is held about: not refused for a secret.
    plain = _suggest(
        auth_client, w, [_fact_op(w["saltmere"], "OWNS", {"kind": "string", "value": "a bell"})]
    )
    considered = _consider(fake_container, plain)
    assert considered.refusal is None or considered.refusal.code != "secret"


def test_marks_are_not_details_and_a_kept_change_never_touches_them(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    # Tobin has only a mark: he was made from a passing mention.
    marked = _suggest(
        auth_client,
        w,
        [{"op": "update_entity", "id": w["tobin"], "attributes": {"_needs_detail": True}}],
        origin="author",
    )
    _keep_by_hand(auth_client, w, marked)
    _switch_on_directly(fake_container, "first_details")

    # His first details, with the mark left as it is, are kept.
    details = _suggest(
        auth_client,
        w,
        [
            {
                "op": "update_entity",
                "id": w["tobin"],
                "attributes": {"_needs_detail": True, "role": "smith"},
            }
        ],
    )
    considered = _consider(fake_container, details)
    assert considered.kept, considered.refusal
    attributes = auth_client.get(f"{w['base']}/entities/{w['tobin']}").json()["attributes"]
    assert attributes == {"_needs_detail": True, "role": "smith"}

    # Details that also set a mark wait.
    ranked = _suggest(
        auth_client,
        w,
        [
            {
                "op": "update_entity",
                "id": w["saltmere"],
                "attributes": {"climate": "wet", "_importance": "main"},
            }
        ],
    )
    refused = _consider(fake_container, ranked)
    assert not refused.kept and refused.refusal.code == "not_additive"
    assert "marks" in refused.refusal.message


def test_a_new_entry_sharing_a_name_or_nickname_waits(auth_client, fake_container) -> None:
    w = _with_new_entries_on(auth_client, fake_container)

    def entry(name: str, *aliases: str) -> str:
        return _suggest(
            auth_client,
            w,
            [
                {
                    "op": "create_entity",
                    "id": "tmp:n",
                    "type": "Character",
                    "name": name,
                    "aliases": list(aliases),
                }
            ],
        )

    # Mara's nickname, in other case and spacing.
    assert _consider(fake_container, entry("the  FOX")).refusal.code == "looks_like_existing"
    # A nickname that is an entry's name.
    assert _consider(fake_container, entry("Wren", "mara")).refusal.code == "looks_like_existing"
    # The name of an entry of another kind.
    assert _consider(fake_container, entry("Saltmere")).refusal.code == "looks_like_existing"
    # A name that only contains another is fine.
    assert _consider(fake_container, entry("Marabel")).kept


def test_a_commit_that_won_the_race_stays_kept(auth_client, fake_container) -> None:
    """Switching off read a suggestion as pending, then its commit landed first."""
    w = _with_new_entries_on(auth_client, fake_container)
    ws, pr = _ids(fake_container)
    mutation_id = _suggest(
        auth_client,
        w,
        [{"op": "create_entity", "id": "tmp:r", "type": "Character", "name": "Ruth"}],
    )
    asyncio.run(
        fake_container.delegation_store.put_record(
            workspace_id=ws,
            project_id=pr,
            mutation_id=mutation_id,
            how="kept",
            state="pending",
            kinds=["new_entry"],
            branch_id="",
            actor_user_id=None,
        )
    )
    stale = _record(fake_container, mutation_id)
    _keep_by_hand(auth_client, w, mutation_id)
    assert _status(auth_client, w, mutation_id) == "committed"

    from backend.domain.delegation import Refusal

    service = fake_container.delegation_service
    asyncio.run(
        service._back_to_review(
            _scope(fake_container), stale, Refusal("kept_rule_off", "switched off")
        )
    )
    record = _record(fake_container, mutation_id)
    assert (record.how, record.state) == ("kept", "kept") and record.revision is not None
    assert _status(auth_client, w, mutation_id) == "committed"


def test_a_kept_record_is_never_counted_as_approved_by_hand(auth_client, fake_container):
    """The commit transaction marks it kept before the service hears of the commit."""
    w = _with_new_entries_on(auth_client, fake_container)
    ws, pr = _ids(fake_container)
    store = fake_container.delegation_store
    mutation_id = _ask(auth_client, fake_container, w, _new_entry("Ruth"))["mutation_id"]
    before = asyncio.run(store.hand_counts(workspace_id=ws, project_id=pr))

    review = asyncio.run(
        fake_container.mutation_service.review(_scope(fake_container), mutation_id)
    )
    asyncio.run(
        fake_container.delegation_service.committed(
            _scope(fake_container), review.mutation, list(review.operations), 99
        )
    )
    record = _record(fake_container, mutation_id)
    assert (record.how, record.state) == ("kept", "kept") and record.revision != 99
    assert asyncio.run(store.hand_counts(workspace_id=ws, project_id=pr)) == before


def test_the_daily_cap_counts_those_on_their_way_in(auth_client, fake_container) -> None:
    w = _with_new_entries_on(auth_client, fake_container)
    ws, pr = _ids(fake_container)
    store = fake_container.delegation_store
    for n in range(DAILY_LIMIT - 1):
        asyncio.run(
            store.put_record(
                workspace_id=ws,
                project_id=pr,
                mutation_id=f"mut_day_{n}",
                how="kept",
                state="kept",
                kinds=["new_entry"],
                branch_id="",
                actor_user_id=None,
            )
        )
    # Another suggestion is marked as being kept at the same moment as this one.
    put = store.put_record

    async def put_and_race(**kwargs):
        await put(**kwargs)
        if kwargs["how"] == "kept" and kwargs["state"] == "pending":
            await put(**{**kwargs, "mutation_id": "mut_racing"})

    store.put_record = put_and_race
    try:
        body = _ask(auth_client, fake_container, w, _new_entry("Una"))
    finally:
        store.put_record = put
    assert body["status"] == "needs_review", body
    assert _decision(auth_client, w, body["mutation_id"])["reason_code"] == "daily_limit"

    # With one already on its way in, the cap is reached before deciding.
    asyncio.run(store.put_record(**{**_pending_args(ws, pr), "mutation_id": "mut_racing_2"}))
    body = _ask(auth_client, fake_container, w, _new_entry("Vera"))
    assert _decision(auth_client, w, body["mutation_id"])["reason_code"] == "daily_limit"


def _pending_args(ws: str, pr: str) -> dict:
    return {
        "workspace_id": ws,
        "project_id": pr,
        "how": "kept",
        "state": "pending",
        "kinds": ["new_entry"],
        "branch_id": "",
        "actor_user_id": None,
    }


def test_switching_off_sends_back_every_pending_one_not_just_the_first_page(
    auth_client, fake_container
) -> None:
    _with_new_entries_on(auth_client, fake_container)
    ws, pr = _ids(fake_container)
    store = fake_container.delegation_store
    for n in range(130):
        asyncio.run(store.put_record(**{**_pending_args(ws, pr), "mutation_id": f"mut_p_{n}"}))
    sent_back: list[str] = []
    service = fake_container.delegation_service
    back = service._back_to_review

    async def count(scope, record, refusal):
        sent_back.append(record.mutation_id)

    service._back_to_review = count
    try:
        asyncio.run(
            service.set_story(_scope(fake_container), ws, pr, kinds=[], allow_clients=False)
        )
    finally:
        service._back_to_review = back
    assert len(set(sent_back)) == 130


def test_approvals_in_a_what_if_do_not_count_for_the_story(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    made = auth_client.post(f"{w['base']}/what-ifs", json={"name": "What if?"})
    assert made.status_code == 201, made.text
    headers = {"X-Story-Branch": made.json()["id"]}
    ws, pr = _ids(fake_container)
    for name in ("Ines", "Oskar", "Pell"):
        fake_container.ai.queue(_new_entry(name))
        head = auth_client.get(f"{w['base']}/projection", headers=headers).json()
        asked = auth_client.post(
            f"{w['base']}/assist/changes",
            json={"instruction": "Please.", "expected_revision": int(head["head_revision"])},
            headers=headers,
        )
        assert asked.status_code == 200, asked.text
        mutation_id = asked.json()["mutation_id"]
        for step in ("approve", "commit"):
            done = auth_client.post(
                f"{w['base']}/mutations/{mutation_id}/{step}", json={}, headers=headers
            )
            assert done.status_code == 200, done.text
    counts = asyncio.run(
        fake_container.delegation_store.hand_counts(workspace_id=ws, project_id=pr)
    )
    assert counts.get("new_entry", 0) == 0
    assert _switch(auth_client, w, ["new_entry"]).status_code == 400
