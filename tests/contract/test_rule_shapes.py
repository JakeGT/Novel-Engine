"""Authoring and checking the richer story rules (CON-005, CAN-013) over the API.

Implications, conflicts, exceptions, ranges, rules in story time and rules about
belief: kept, read back in words, their examples run, and checked against changes
in review, warning by default and blocking only when the author chose it and the
answer is sure. No model is asked except where the assistant drafts one.
"""

import pytest

from backend.domain.rules import (
    Check,
    ExampleEntry,
    ExampleFact,
    Exceptions,
    Pattern,
    RuleExample,
    Selector,
)
from backend.services.rule_workflow import RuleDraft
from tests.contract.test_blocking_rules import _set
from tests.contract.test_god_hand import _world
from tests.contract.test_rules import _rules

pytestmark = pytest.mark.contract

# A kind of fact the story bible lets hold many at once, so only the rule speaks.
ONE_FACTION = {
    "applies_to": {"type": "Character"},
    "checks": [{"kind": "at_most", "count": 1, "pattern": {"predicate": "SERVES"}}],
    "time": "every_moment",
}
SERVES = {
    "label": "Service",
    "entity_extensions": [],
    "predicate_extensions": [
        {
            "predicate": "SERVES",
            "subject_types": ["Character"],
            "object_kind": "entity_ref",
            "object_types": ["Faction"],
            "cardinality": "many",
            "temporal_allowed": True,
        }
    ],
}


def _project_id(w) -> str:
    return w["base"].rsplit("/", 1)[1]


def _propose(client, w, operations: list[dict]) -> str:
    proposed = client.post(
        f"{w['base']}/mutations",
        json={"origin": "model", "expected_revision": int(w["head"]), "operations": operations},
    )
    assert proposed.status_code == 201, proposed.text
    mutation_id = proposed.json()["id"]
    client.post(f"{w['base']}/mutations/{mutation_id}/submit", json={"note": "test"})
    return str(mutation_id)


def _serves(w, faction: str) -> dict:
    return {
        "subject_id": w["mara"],
        "predicate": "SERVES",
        "object": {"kind": "entity_ref", "entity_id": w[faction], "project_id": _project_id(w)},
    }


def _joins_watch(w, starts: float) -> dict:
    return {
        "op": "assert_fact",
        "id": "tmp:joins-watch",
        "proposition": {
            "subject_id": w["mara"],
            "predicate": "SERVES",
            "object": {"kind": "entity_ref", "entity_id": w["watch"], "project_id": _project_id(w)},
        },
        "starts": {"kind": "at", "at": starts},
    }


def test_implications_conflicts_and_exceptions_are_kept_and_read_back(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    body = {
        "applies_to": {"type": "Character"},
        "checks": [
            {
                "kind": "implies",
                "if_has": {"predicate": "MEMBER_OF", "other_id": w["guild"]},
                "pattern": {"predicate": "KNOWS", "other_type": "Character"},
            },
            {
                "kind": "not_both",
                "pattern": {"predicate": "LOCATED_AT", "other_id": w["harbor"]},
                "not_with": {"predicate": "LOCATED_AT", "other_id": w["saltmere"]},
            },
        ],
        "except_for": {"entity_ids": [w["tobin"]], "tag": "ghost"},
    }
    checked = auth_client.post(f"{w['base']}/rules/check", json={"body": body}).json()
    assert checked["problems"] == []
    assert checked["reading"] == (
        "Every character, except for Tobin Ashe and anything tagged “ghost”, if it has this: "
        "it is a member of Harbor Guild, must also have this: it knows a character; and "
        "cannot have both this: it is located at Lantern Harbor and this: it is located at "
        "Saltmere."
    )
    # Mara is in the guild and knows Tobin, and is only at the harbour: it holds.
    assert checked["broken_by"] == []

    made = auth_client.post(
        f"{w['base']}/rules", json={"title": "Members know someone", "body": body}
    )
    assert made.status_code == 201, made.text
    rule = made.json()
    assert rule["body"]["form"] == 2 and rule["body"]["except_for"]["tag"] == "ghost"
    assert rule["reading"] == checked["reading"]

    # A missing entry among the exceptions is said plainly; a malformed check is refused.
    gone = {**body, "except_for": {"entity_ids": ["ent_nobody"]}}
    problems = auth_client.post(f"{w['base']}/rules/check", json={"body": gone}).json()
    assert problems["problems"] == ["An entry this rule leaves out is not in the story bible."]
    malformed = {**body, "checks": [{"kind": "implies", "pattern": {"predicate": "KNOWS"}}]}
    assert auth_client.post(f"{w['base']}/rules/check", json={"body": malformed}).status_code == 422
    newer = {**body, "form": 99}
    assert auth_client.post(f"{w['base']}/rules/check", json={"body": newer}).status_code == 422
    assert fake_container.ai.calls == []


def test_a_range_is_checked_and_its_examples_must_agree(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    body = {
        "applies_to": {"type": "Character"},
        "checks": [
            {"kind": "between", "pattern": {"predicate": "HAS_STATUS"}, "low": 0, "high": 120}
        ],
    }
    entries = [{"ref": "a", "type": "Character", "name": "Ana"}]
    examples = [
        {
            "description": "Ana is 200",
            "entries": entries,
            "facts": [{"subject_ref": "a", "predicate": "HAS_STATUS", "value": "200"}],
            "expect": "caught",
        },
        {
            "description": "Ana is 40",
            "entries": entries,
            "facts": [{"subject_ref": "a", "predicate": "HAS_STATUS", "value": "40"}],
            "expect": "allowed",
        },
    ]
    checked = auth_client.post(
        f"{w['base']}/rules/check", json={"body": body, "examples": examples}
    ).json()
    assert [item["agrees"] for item in checked["example_results"]] == [True, True]
    assert "must have a number from 0 to 120 for “has status”" in checked["reading"]

    wrong = [{**examples[1], "expect": "caught"}]
    refused = auth_client.post(
        f"{w['base']}/rules", json={"title": "Ages", "body": body, "examples": wrong}
    )
    assert refused.status_code == 400
    assert "should be caught, but the rule would leave it allowed" in refused.json()["detail"]


def test_a_rule_in_story_time_allows_a_move_and_blocks_two_at_once(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    watch = auth_client.post(
        f"{w['base']}/entities",
        json={"type": "Faction", "name": "Tide Watch", "expected_revision": int(w["head"])},
    )
    assert watch.status_code == 201, watch.text
    w["watch"], w["head"] = watch.json()["id"], str(int(w["head"]) + 1)
    # A kind of fact of the story's own that can hold many at once and take a time.
    draft = auth_client.post(
        f"/api/v1/projects/{_project_id(w)}/schemas/drafts", json={"definition": SERVES}
    )
    assert draft.status_code == 201, draft.text
    version = draft.json()["version"]["id"]
    published = auth_client.post(f"/api/v1/projects/{_project_id(w)}/schemas/{version}/publish")
    assert published.status_code == 200, published.text
    served = auth_client.post(
        f"{w['base']}/facts",
        json={"proposition": _serves(w, "guild"), "expected_revision": int(w["head"])},
    )
    assert served.status_code == 201, served.text
    w["serves"], w["head"] = served.json()["id"], str(int(w["head"]) + 1)
    examples = [
        {
            "description": "Ana leaves one guild for another",
            "entries": [
                {"ref": "a", "type": "Character", "name": "Ana"},
                {"ref": "g", "type": "Faction", "name": "Guild"},
                {"ref": "h", "type": "Faction", "name": "Watch"},
            ],
            "facts": [
                {"subject_ref": "a", "predicate": "SERVES", "object_ref": "g", "ends": 3},
                {"subject_ref": "a", "predicate": "SERVES", "object_ref": "h", "starts": 3},
            ],
            "expect": "allowed",
        },
        {
            "description": "Ana in both in year 4",
            "entries": [
                {"ref": "a", "type": "Character", "name": "Ana"},
                {"ref": "g", "type": "Faction", "name": "Guild"},
                {"ref": "h", "type": "Faction", "name": "Watch"},
            ],
            "facts": [
                {"subject_ref": "a", "predicate": "SERVES", "object_ref": "g"},
                {"subject_ref": "a", "predicate": "SERVES", "object_ref": "h", "starts": 3},
            ],
            "expect": "caught",
        },
    ]
    made = auth_client.post(
        f"{w['base']}/rules",
        json={"title": "Serves one at a time", "body": ONE_FACTION, "examples": examples},
    )
    assert made.status_code == 201, made.text
    rule = made.json()
    assert rule["reading"] == (
        "At every moment of the story: Every character can have at most 1 of this: it serves "
        "something."
    )
    assert [item["agrees"] for item in rule["example_results"]] == [True, True]
    assert rule["broken_by"] == []
    assert _set(auth_client, w, rule["id"], "blocking").status_code == 200

    # Leaving the guild in year 5 and joining the watch then: one at a time.
    moved = _propose(
        auth_client,
        w,
        [
            {"op": "amend_fact", "id": w["serves"], "ends": {"kind": "at", "at": 5}},
            _joins_watch(w, 5),
        ],
    )
    report = auth_client.post(f"{w['base']}/mutations/{moved}/validate").json()
    assert report["ok"] is True and report["issues"] == [], report

    # Joining the watch while still in the guild: sure, so it blocks.
    both = _propose(auth_client, w, [_joins_watch(w, 5)])
    report = auth_client.post(f"{w['base']}/mutations/{both}/validate").json()
    assert report["ok"] is False
    (issue,) = report["issues"]
    assert issue["severity"] == "blocking"
    assert "Mara at 5 in story time" in issue["message"]
    refused = auth_client.post(f"{w['base']}/mutations/{both}/approve", json={})
    assert refused.status_code in (400, 422), refused.text
    assert "Serves one at a time" in refused.json()["detail"]

    # Times too loose to tell (she leaves some time in years 4 to 8): it only warns.
    loose = _propose(
        auth_client,
        w,
        [
            {
                "op": "amend_fact",
                "id": w["serves"],
                "ends": {"kind": "between", "earliest": 4, "latest": 8},
            },
            _joins_watch(w, 6),
        ],
    )
    report = auth_client.post(f"{w['base']}/mutations/{loose}/validate").json()
    assert report["ok"] is True
    (issue,) = report["issues"]
    assert issue["severity"] == "advisory" and "too loose to be sure" in issue["message"]
    assert auth_client.post(f"{w['base']}/mutations/{loose}/approve", json={}).status_code == 200
    assert auth_client.post(f"{w['base']}/mutations/{loose}/commit").status_code == 200

    (listed,) = _rules(auth_client, w)
    assert listed["broken_by"] == [
        {"entity_id": w["mara"], "name": "Mara", "certain": False, "when": "around 6 in story time"}
    ]


def test_every_member_of_the_guild_must_believe_something(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    project_id = _project_id(w)
    body = {
        "applies_to": {
            "type": "Character",
            "having": [{"predicate": "MEMBER_OF", "other_id": w["guild"]}],
        },
        "checks": [
            {
                "kind": "believes",
                "about_id": w["tobin"],
                "pattern": {"predicate": "LOCATED_AT", "other_id": w["harbor"]},
            }
        ],
    }
    made = auth_client.post(
        f"{w['base']}/rules", json={"title": "The guild trusts Tobin", "body": body}
    )
    assert made.status_code == 201, made.text
    rule = made.json()
    assert rule["reading"] == (
        "Every character where it is a member of Harbor Guild, must believe this: Tobin Ashe "
        "is located at Lantern Harbor."
    )
    assert [item["name"] for item in rule["broken_by"]] == ["Mara"]

    believed = auth_client.post(
        f"{w['base']}/beliefs",
        json={
            "holder_entity_id": w["mara"],
            "proposition": {
                "subject_id": w["tobin"],
                "predicate": "LOCATED_AT",
                "object": {
                    "kind": "entity_ref",
                    "entity_id": w["harbor"],
                    "project_id": project_id,
                },
            },
            "source_ref": "test",
            "expected_revision": int(w["head"]),
        },
    )
    assert believed.status_code == 201, believed.text
    assert _rules(auth_client, w)[0]["broken_by"] == []

    # Set to block, ending that belief is refused: the answer is sure and complete.
    assert _set(auth_client, w, rule["id"], "blocking").status_code == 200
    w["head"] = str(int(w["head"]) + 1)
    doubt = _propose(auth_client, w, [{"op": "close_belief", "id": believed.json()["id"]}])
    report = auth_client.post(f"{w['base']}/mutations/{doubt}/validate").json()
    assert report["ok"] is False
    assert "The guild trusts Tobin" in report["issues"][0]["message"]


def test_the_assistant_drafts_a_rule_in_story_time_with_exceptions(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    entries = [
        ExampleEntry(ref="a", type="Character", name="Ana"),
        ExampleEntry(ref="h", type="Location", name="Home"),
        ExampleEntry(ref="t", type="Location", name="Town"),
    ]
    fake_container.ai.queue(
        RuleDraft(
            title="One place at a time",
            understood="Nobody but Tobin is in two places at the same moment.",
            applies_to=Selector(type="Character"),
            checks=[Check(kind="at_most", count=1, pattern=Pattern(predicate="LOCATED_AT"))],
            except_for=Exceptions(entity_ids=[w["tobin"]]),
            time="every_moment",
            examples=[
                RuleExample(
                    description="Ana moves",
                    entries=entries,
                    facts=[
                        ExampleFact(
                            subject_ref="a", predicate="LOCATED_AT", object_ref="h", ends=2
                        ),
                        ExampleFact(
                            subject_ref="a", predicate="LOCATED_AT", object_ref="t", starts=2
                        ),
                    ],
                    expect="allowed",
                ),
                RuleExample(
                    description="Ana in both at once",
                    entries=entries,
                    facts=[
                        ExampleFact(subject_ref="a", predicate="LOCATED_AT", object_ref="h"),
                        ExampleFact(subject_ref="a", predicate="LOCATED_AT", object_ref="t"),
                    ],
                    expect="caught",
                ),
            ],
        )
    )
    drafted = auth_client.post(
        f"{w['base']}/assist/rules/draft",
        json={"sentence": "Nobody except Tobin is ever in two places at once."},
    )
    assert drafted.status_code == 200, drafted.text
    found = drafted.json()
    assert found["body"]["time"] == "every_moment" and found["body"]["form"] == 2
    assert found["check"]["reading"] == (
        "At every moment of the story: Every character, except for Tobin Ashe, can have at "
        "most 1 of this: it is located at something."
    )
    assert [item["agrees"] for item in found["check"]["example_results"]] == [True, True]
    system, _user = fake_container.ai.calls[-1]["messages"]
    assert "every_moment" in system["content"] and "believes" in system["content"]
    assert _rules(auth_client, w) == []


def test_a_badly_shaped_drafted_check_is_set_aside_not_the_whole_draft(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    # The model's answer as it comes back: one good check, one range with no ends,
    # and one of a kind a rule does not have.
    answer = RuleDraft.model_validate_json(
        """{
            "title": "Ages make sense",
            "applies_to": {"type": "Character"},
            "checks": [
                {"kind": "has", "pattern": {"predicate": "MEMBER_OF"}},
                {"kind": "between", "pattern": {"predicate": "AGE"}},
                {"kind": "sometimes", "pattern": {"predicate": "AGE"}}
            ]
        }"""
    )
    fake_container.ai.queue(answer)
    drafted = auth_client.post(
        f"{w['base']}/assist/rules/draft", json={"sentence": "Every character is in a guild."}
    )
    assert drafted.status_code == 200, drafted.text
    found = drafted.json()
    assert [check["kind"] for check in found["body"]["checks"]] == ["has"]
    assert found["cannot_express"] == (
        "2 checks the assistant drafted were left out: a range needs a smallest value, a "
        "largest value, or both; part of it is missing or is not something a rule can say."
    )

    # Nothing that makes sense: no rule, and why.
    fake_container.ai.queue(
        RuleDraft.model_validate(
            {"title": "Ages", "checks": [{"kind": "between", "pattern": {"predicate": "AGE"}}]}
        )
    )
    alone = auth_client.post(
        f"{w['base']}/assist/rules/draft", json={"sentence": "Ages are sensible."}
    ).json()
    assert alone["body"] is None
    assert alone["cannot_express"] == (
        "One check the assistant drafted was left out: a range needs a smallest value, a "
        "largest value, or both."
    )
