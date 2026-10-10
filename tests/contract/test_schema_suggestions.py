"""The assistant can say what a project's setup is missing; an owner decides whether to add it."""

import pytest

from backend.domain.schema_defs import SchemaDefinition
from backend.services.canon_change_workflow import ChangePlan
from backend.services.canon_creation_workflow import CreationDraft, DraftEntity, DraftFact
from backend.services.schema_suggestions import (
    SuggestedFactKind,
    SuggestedType,
    add_to_definition,
    clean_suggestions,
)
from tests.contract.test_canon_assist import _setup
from tests.contract.test_god_hand import _world

pytestmark = pytest.mark.contract

TYPES = {"Character", "Location", "Faction"}


def test_suggestions_are_cleaned_bounded_and_limited_to_what_is_new() -> None:
    found = clean_suggestions(
        [
            SuggestedType(name="musical instrument", description="x" * 900, reason="plays one"),
            SuggestedType(name="character"),  # already exists, whatever the capitals
            SuggestedType(name="Musical Instrument"),  # same as the first
            SuggestedType(name="!!!"),
        ],
        [
            SuggestedFactKind(
                name="studied under",
                subject_types=["character"],
                points_to="entry",
                object_types=["Character", "Nonesuch"],
                one_at_a_time=True,
            ),
            # May connect a type suggested alongside it.
            SuggestedFactKind(
                name="plays", subject_types=["Character"], object_types=["MusicalInstrument"]
            ),
            SuggestedFactKind(name="LOCATED_AT", subject_types=["Character"], points_to="text"),
            SuggestedFactKind(name="floats", subject_types=["Nonesuch"], points_to="text"),
            SuggestedFactKind(name="points nowhere", subject_types=["Character"]),
            SuggestedFactKind(name="odd", subject_types=["Character"], points_to="blob"),
            SuggestedFactKind(name="perceives via", subject_types=["Character"], points_to="TEXT"),
        ],
        entity_types=TYPES,
        predicates={"LOCATED_AT"},
    )

    assert [(item.kind, item.name) for item in found] == [
        ("type", "MusicalInstrument"),
        ("fact_kind", "STUDIED_UNDER"),
        ("fact_kind", "PLAYS"),
        ("fact_kind", "PERCEIVES_VIA"),
    ]
    assert len(found[0].description) == 240
    studied = found[1]
    assert studied.subject_types == ["Character"] and studied.object_types == ["Character"]
    assert studied.one_at_a_time is True
    assert found[2].object_types == ["MusicalInstrument"]
    assert found[3].points_to == "text" and found[3].object_types == []
    many = clean_suggestions(
        [SuggestedType(name=f"Kind{n}") for n in range(20)],
        [],
        entity_types=TYPES,
        predicates=set(),
    )
    assert len(many) == 5


def test_adding_suggestions_only_adds() -> None:
    found = clean_suggestions(
        [SuggestedType(name="Instrument")],
        [SuggestedFactKind(name="plays", subject_types=["Character"], object_types=["Instrument"])],
        entity_types=TYPES,
        predicates=set(),
    )
    start = SchemaDefinition(label="Mine")

    once, added = add_to_definition(start, found)
    twice, again = add_to_definition(once, found)

    assert added == ["Instrument", "PLAYS"] and again == []
    assert twice == once
    assert [e.entity_type for e in once.entity_extensions] == ["Instrument"]
    plays = once.predicate_extensions[0]
    assert plays.subject_types == ["Character"] and plays.object_types == ["Instrument"]


def test_a_draft_reports_what_the_setup_is_missing_and_an_owner_can_add_it(
    auth_client, fake_container
) -> None:
    base, _ = _setup(auth_client, fake_container)
    fake_container.ai.queue(
        CreationDraft(
            entities=[DraftEntity(ref="john", type="Character", name="John Williams")],
            suggested_fact_kinds=[
                SuggestedFactKind(
                    name="experiences world through",
                    description="the sense or medium a character lives through",
                    subject_types=["Character"],
                    points_to="text",
                    reason="He experiences the world through sound.",
                )
            ],
        )
    )

    body = auth_client.post(
        f"{base}/assist/proposals",
        json={"description": "John experiences the world through sound.", "expected_revision": 1},
    ).json()

    assert [item["name"] for item in body["suggestions"]] == ["EXPERIENCES_WORLD_THROUGH"]
    assert "sound" in fake_container.ai.calls[-1]["messages"][1]["content"]
    assert "suggested_fact_kinds" in fake_container.ai.calls[-1]["messages"][0]["content"]
    # Kept with the conversation, so it can be acted on after the dialog has gone.
    kept = auth_client.get(f"{base}/assist/sessions/{body['session_id']}").json()
    assert kept["suggestions"] == body["suggestions"]
    # Suggesting changes nothing.
    before = auth_client.get(f"{base}/schemas/vocabulary").json()
    assert "EXPERIENCES_WORLD_THROUGH" not in {p["predicate"] for p in before["predicates"]}

    refused = auth_client.post(
        f"{base}/assist/sessions/{body['session_id']}/suggestions/apply",
        json={"names": ["SOMETHING_ELSE"]},
    )
    assert refused.status_code == 400
    applied = auth_client.post(
        f"{base}/assist/sessions/{body['session_id']}/suggestions/apply",
        json={"names": ["EXPERIENCES_WORLD_THROUGH"]},
    )

    assert applied.status_code == 200, applied.text
    assert applied.json()["added"] == ["EXPERIENCES_WORLD_THROUGH"]
    after = auth_client.get(f"{base}/schemas/vocabulary").json()
    added = next(p for p in after["predicates"] if p["predicate"] == "EXPERIENCES_WORLD_THROUGH")
    assert added["subject_types"] == ["Character"] and added["object_shape"] == "string"
    assert after["schema_version"] == before["schema_version"] + 1
    # Adding again is harmless and publishes nothing new.
    again = auth_client.post(
        f"{base}/assist/sessions/{body['session_id']}/suggestions/apply",
        json={"names": ["EXPERIENCES_WORLD_THROUGH"]},
    ).json()
    assert again == {"added": [], "schema_version": after["schema_version"]}

    # Drafting again can now say what was left out before.
    fake_container.ai.queue(
        CreationDraft(
            entities=[DraftEntity(ref="john", type="Character", name="John Williams")],
            facts=[
                DraftFact(
                    subject_ref="john", predicate="EXPERIENCES_WORLD_THROUGH", object_value="sound"
                )
            ],
        )
    )
    redraft = auth_client.post(
        f"{base}/assist/proposals",
        json={"description": "John experiences the world through sound.", "expected_revision": 1},
    ).json()
    assert redraft["discarded"] == [] and redraft["operation_count"] == 2


def test_a_change_instruction_can_also_report_what_is_missing(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    fake_container.ai.queue(
        ChangePlan(
            understood="Mara studied under Tobin.",
            questions=["Should a new kind of fact be added for this?"],
            suggested_fact_kinds=[
                SuggestedFactKind(
                    name="STUDIED_UNDER", subject_types=["Character"], object_types=["Character"]
                )
            ],
        )
    )

    body = auth_client.post(
        f"{w['base']}/assist/changes",
        json={"instruction": "Mara studied under Tobin.", "expected_revision": int(w["head"])},
    ).json()

    assert [item["name"] for item in body["suggestions"]] == ["STUDIED_UNDER"]
    kept = auth_client.get(f"{w['base']}/assist/sessions/{body['session_id']}").json()
    assert kept["suggestions"][0]["object_types"] == ["Character"]


def test_the_suggestion_format_is_accepted_by_strict_structured_output() -> None:
    from openai.lib._pydantic import to_strict_json_schema

    def free_form(node: object) -> bool:
        if isinstance(node, dict):
            extra = node.get("additionalProperties")
            return (node.get("type") == "object" and extra is not False) or any(
                free_form(value) for value in node.values()
            )
        return isinstance(node, list) and any(free_form(value) for value in node)

    for model in (CreationDraft, ChangePlan):
        assert not free_form(to_strict_json_schema(model))
