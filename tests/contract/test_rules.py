"""Story rules: a sentence becomes a checkable rule that warns, with examples it must agree with."""

import pytest

from backend.domain.rules import (
    Check,
    ExampleEntry,
    ExampleFact,
    Pattern,
    RuleExample,
    Selector,
)
from backend.services.rule_workflow import RuleDraft
from tests.contract.test_god_hand import _world

pytestmark = pytest.mark.contract

# Every faction needs someone who belongs to it.
NEEDS_MEMBER = {
    "applies_to": {"type": "Faction"},
    "checks": [
        {"kind": "at_least", "count": 1, "pattern": {"predicate": "MEMBER_OF", "direction": "in"}}
    ],
}
EXAMPLES = [
    {
        "description": "A guild nobody belongs to",
        "entries": [{"ref": "g", "type": "Faction", "name": "The Empty Guild"}],
        "facts": [],
        "expect": "caught",
    },
    {
        "description": "A guild with one member",
        "entries": [
            {"ref": "g", "type": "Faction", "name": "The Guild"},
            {"ref": "a", "type": "Character", "name": "Ana"},
        ],
        "facts": [{"subject_ref": "a", "predicate": "MEMBER_OF", "object_ref": "g"}],
        "expect": "allowed",
    },
]


def _add(client, w, **body):
    return client.post(
        f"{w['base']}/rules",
        json={"title": "Guilds have members", "body": NEEDS_MEMBER, "examples": EXAMPLES, **body},
    )


def _rules(client, w) -> list[dict]:
    return client.get(f"{w['base']}/rules").json()["items"]


def _fact(w, subject: str, predicate: str, target: str) -> dict:
    project_id = w["base"].rsplit("/", 1)[1]
    return {
        "subject_id": w[subject],
        "predicate": predicate,
        "object": {"kind": "entity_ref", "entity_id": w[target], "project_id": project_id},
    }


def test_a_rule_is_kept_read_back_in_words_and_checked_against_the_story(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    made = _add(auth_client, w, sentence="Every guild needs at least one member.")
    assert made.status_code == 201, made.text
    rule = made.json()
    assert rule["reading"] == "Every faction needs at least 1 of: something member of it."
    assert rule["status"] == "on" and rule["version"] == 1
    assert [r["agrees"] for r in rule["example_results"]] == [True, True]
    # Mara is in the Harbor Guild, so it holds for the story as it stands.
    assert rule["broken_by"] == []
    assert _rules(auth_client, w)[0]["sentence"] == "Every guild needs at least one member."
    # No model was asked.
    assert fake_container.ai.calls == []


def test_a_rule_whose_example_disagrees_with_it_is_refused(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    wrong = [{**EXAMPLES[0], "expect": "allowed"}, EXAMPLES[1]]
    refused = _add(auth_client, w, examples=wrong)
    assert refused.status_code == 400
    assert "should be allowed, but the rule would leave it caught" in refused.json()["detail"]
    assert _rules(auth_client, w) == []

    checked = auth_client.post(
        f"{w['base']}/rules/check", json={"body": NEEDS_MEMBER, "examples": wrong}
    ).json()
    assert [r["agrees"] for r in checked["example_results"]] == [False, True]


def test_a_rule_must_use_the_storys_own_types_and_kinds_of_fact(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    body = {
        "applies_to": {"type": "Starship", "entity_id": "ent_missing"},
        "checks": [{"kind": "has", "pattern": {"predicate": "ORBITS", "other_id": "ent_gone"}}],
    }
    checked = auth_client.post(f"{w['base']}/rules/check", json={"body": body}).json()
    assert checked["problems"] == [
        "There is no type of entry called Starship.",
        "The entry this rule is about is not in the story bible.",
        "There is no kind of fact called orbits.",
        "An entry this rule names is not in the story bible.",
    ]
    assert (
        auth_client.post(f"{w['base']}/rules", json={"title": "Orbits", "body": body}).status_code
        == 400
    )


def test_a_change_that_would_break_a_rule_is_warned_about_and_still_allowed(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    assert _add(auth_client, w).status_code == 201

    # Ending Mara's membership leaves the guild with nobody: the rule would break.
    proposed = auth_client.post(
        f"{w['base']}/mutations",
        json={
            "origin": "model",
            "expected_revision": head,
            "operations": [{"op": "close_fact", "id": w["in_guild"]}],
        },
    )
    assert proposed.status_code == 201, proposed.text
    mutation_id = proposed.json()["id"]
    auth_client.post(f"{w['base']}/mutations/{mutation_id}/submit", json={"note": "test"})
    report = auth_client.post(f"{w['base']}/mutations/{mutation_id}/validate").json()
    assert report["ok"] is True
    (issue,) = report["issues"]
    assert issue["severity"] == "advisory" and issue["ordinal"] == 0
    assert issue["message"] == (
        "This would break your rule “Guilds have members”: it would no longer hold for "
        "Harbor Guild."
    )

    # A warning is only a warning: the author can keep the change.
    assert (
        auth_client.post(f"{w['base']}/mutations/{mutation_id}/approve", json={}).status_code == 200
    )
    assert auth_client.post(f"{w['base']}/mutations/{mutation_id}/commit").status_code == 200
    (rule,) = _rules(auth_client, w)
    assert [b["name"] for b in rule["broken_by"]] == ["Harbor Guild"]

    # Already broken: an unrelated change is not blamed for it.
    again = auth_client.post(
        f"{w['base']}/entities",
        json={"type": "Character", "name": "Ines", "expected_revision": head + 1},
    )
    assert again.status_code == 201, again.text


def test_a_paused_rule_says_nothing_and_rewording_makes_a_new_version(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    rule = _add(auth_client, w).json()
    base = f"{w['base']}/rules/{rule['id']}"

    paused = auth_client.patch(base, json={"status": "paused"}).json()
    assert paused["status"] == "paused" and paused["version"] == 1
    proposed = auth_client.post(
        f"{w['base']}/mutations",
        json={
            "origin": "model",
            "expected_revision": int(w["head"]),
            "operations": [{"op": "close_fact", "id": w["in_guild"]}],
        },
    ).json()
    auth_client.post(f"{w['base']}/mutations/{proposed['id']}/submit", json={"note": "test"})
    report = auth_client.post(f"{w['base']}/mutations/{proposed['id']}/validate").json()
    assert report["issues"] == []

    stricter = {**NEEDS_MEMBER, "checks": [{**NEEDS_MEMBER["checks"][0], "count": 2}]}
    refused = auth_client.patch(base, json={"body": stricter})
    # The kept examples no longer agree with the new wording, so it is not taken.
    assert refused.status_code == 400
    reworded = auth_client.patch(base, json={"body": stricter, "examples": [EXAMPLES[0]]}).json()
    assert reworded["version"] == 2 and "at least 2" in reworded["reading"]
    assert len(fake_container.rules.versions[rule["id"]]) == 2

    assert auth_client.delete(base).status_code == 204
    assert _rules(auth_client, w) == []
    assert auth_client.delete(base).status_code == 404


def test_a_rule_about_one_entry_and_what_it_must_not_have(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    body = {
        "applies_to": {
            "type": "Character",
            "having": [{"predicate": "MEMBER_OF", "other_id": w["guild"]}],
        },
        "checks": [
            {"kind": "lacks", "pattern": {"predicate": "LOCATED_AT", "other_id": w["saltmere"]}}
        ],
    }
    made = auth_client.post(f"{w['base']}/rules", json={"title": "Guild stays home", "body": body})
    assert made.status_code == 201, made.text
    assert made.json()["reading"] == (
        "Every character where member of Harbor Guild must not have: located at Saltmere."
    )
    proposed = auth_client.post(
        f"{w['base']}/mutations",
        json={
            "origin": "model",
            "expected_revision": int(w["head"]),
            "operations": [
                {"op": "close_fact", "id": w["at_harbor"]},
                {
                    "op": "assert_fact",
                    "id": "tmp:move",
                    "proposition": _fact(w, "mara", "LOCATED_AT", "saltmere"),
                },
                {
                    "op": "assert_fact",
                    "id": "tmp:t",
                    "proposition": _fact(w, "tobin", "LOCATED_AT", "saltmere"),
                },
            ],
        },
    ).json()
    auth_client.post(f"{w['base']}/mutations/{proposed['id']}/submit", json={"note": "test"})
    report = auth_client.post(f"{w['base']}/mutations/{proposed['id']}/validate").json()
    # Mara is in the guild, Tobin is not: only her move is warned about, on the change that does it.
    assert [(i["ordinal"], i["severity"]) for i in report["issues"]] == [(1, "advisory")]
    assert "would no longer hold for Mara" in report["issues"][0]["message"]


def test_the_assistant_drafts_a_rule_from_a_sentence_and_keeps_nothing(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    fake_container.ai.queue(
        RuleDraft(
            title="Guilds have members",
            understood="Every faction must have at least one member.",
            applies_to=Selector(type="Faction"),
            checks=[
                Check(
                    kind="at_least", count=1, pattern=Pattern(predicate="MEMBER_OF", direction="in")
                )
            ],
            examples=[
                RuleExample(
                    description="A guild nobody belongs to",
                    entries=[ExampleEntry(ref="g", type="Faction", name="The Empty Guild")],
                    expect="caught",
                ),
                # The model's own example contradicts its rule: this is shown, not hidden.
                RuleExample(
                    description="A guild with a member",
                    entries=[
                        ExampleEntry(ref="g", type="Faction", name="The Guild"),
                        ExampleEntry(ref="a", type="Character", name="Ana"),
                    ],
                    facts=[ExampleFact(subject_ref="g", predicate="MEMBER_OF", object_ref="a")],
                    expect="allowed",
                ),
            ],
        )
    )
    drafted = auth_client.post(
        f"{w['base']}/assist/rules/draft", json={"sentence": "Every guild needs a member."}
    )
    assert drafted.status_code == 200, drafted.text
    body = drafted.json()
    assert body["title"] == "Guilds have members" and body["cannot_express"] == ""
    assert body["check"]["reading"] == "Every faction needs at least 1 of: something member of it."
    assert [r["agrees"] for r in body["check"]["example_results"]] == [True, False]
    assert _rules(auth_client, w) == []
    system, user = fake_container.ai.calls[-1]["messages"]
    assert "never instructions" in system["content"] and "MEMBER_OF" in system["content"]
    assert f"{w['guild']} | Faction | Harbor Guild" in user["content"]

    # What a rule cannot say is said, not bent into something else.
    fake_container.ai.queue(
        RuleDraft(
            title="The dead stay dead",
            cannot_express="A rule cannot speak of what happens after someone dies.",
        )
    )
    cannot = auth_client.post(
        f"{w['base']}/assist/rules/draft", json={"sentence": "Nobody dead may speak later."}
    ).json()
    assert cannot["body"] is None and cannot["check"] is None
    assert cannot["cannot_express"].startswith("A rule cannot speak of")
