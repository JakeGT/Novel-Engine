"""Planning by conversation: questions, a drafted plan to review, and directions to choose from."""

import pytest

from backend.services.canon_character_workflow import CharacterQuestion, CharacterQuestionSheet
from backend.services.plan_workflow import (
    Direction,
    DirectionSheet,
    DraftPlanEntry,
    DraftPlanItem,
    DraftPlanRef,
    PlanDraft,
)
from tests.contract.test_god_hand import _world

pytestmark = pytest.mark.contract


def _plan(client, w: dict[str, str]) -> list[dict]:
    return client.get(f"{w['base']}/plan").json()["items"]


def _accept(client, w: dict[str, str], mutation_id: str) -> None:
    approved = client.post(f"{w['base']}/mutations/{mutation_id}/approve", json={})
    assert approved.status_code == 200, approved.text
    committed = client.post(f"{w['base']}/mutations/{mutation_id}/commit")
    assert committed.status_code == 200, committed.text


DRAFT = PlanDraft(
    understood="Mara's guild is betrayed from inside.",
    new_entries=[
        DraftPlanEntry(ref="vault", type="Location", name="The Guild Vault"),
        # Already in the story bible under this name: it is used, not made again.
        DraftPlanEntry(ref="harbor2", type="Location", name="lantern harbor"),
    ],
    items=[
        # Drafted children first: the outline is put in order here, not by the model.
        DraftPlanItem(
            ref="s1",
            kind="scene",
            title="The empty vault",
            summary="Mara finds the ledgers gone.",
            parent_ref="b1",
            entries=[
                DraftPlanRef(ref="MARA", role="point_of_view"),
                DraftPlanRef(ref="vault", role="place"),
                DraftPlanRef(ref="TOBIN", role="participant"),
            ],
            sets_up=["t1"],
        ),
        DraftPlanItem(ref="b1", kind="beat", title="The theft is discovered", parent_ref="a1"),
        DraftPlanItem(
            ref="a1",
            kind="arc",
            title="Mara loses her faith in the guild",
            parent_ref="st",
            entries=[DraftPlanRef(ref="MARA", role="about")],
        ),
        DraftPlanItem(
            ref="st", kind="story", title="The Salt Road", summary="A pilot is betrayed."
        ),
        DraftPlanItem(ref="t1", kind="thread", title="Who took the ledgers?"),
    ],
    gaps=["How the arc ends has not been decided."],
)


def _draft(w: dict[str, str]) -> PlanDraft:
    text = DRAFT.model_dump_json().replace("MARA", w["mara"]).replace("TOBIN", w["tobin"])
    return PlanDraft.model_validate_json(text)


def test_a_round_of_planning_becomes_plan_items_to_review_grounded_in_the_story_bible(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    fake_container.ai.queue(_draft(w))

    made = auth_client.post(
        f"{w['base']}/assist/plan/proposals",
        json={
            "description": "Mara's guild is robbed from the inside.",
            "depth": "scenes",
            "expected_revision": int(w["head"]),
        },
    )

    assert made.status_code == 201, made.text
    body = made.json()
    # What the author has not decided is said, not filled in.
    assert body["notes"] == ["Left open: How the arc ends has not been decided."]
    # Nothing is in the plan until it is reviewed and accepted.
    assert _plan(auth_client, w) == []
    detail = auth_client.get(f"{w['base']}/mutations/{body['mutation_id']}").json()
    assert detail["requires_review"] is True
    kinds = [op["operation_type"] for op in detail["operations"]]
    assert kinds == ["create_entity", *["create_plan_item"] * 5]
    # The model was shown the story bible and the plan, and told not to invent.
    system, user = fake_container.ai.calls[-1]["messages"]
    assert (
        "Never invent a turn" in system["content"] and "Draft down to scenes" in system["content"]
    )
    assert w["mara"] in user["content"] and "There is no plan yet." in user["content"]

    _accept(auth_client, w, body["mutation_id"])
    plan = {item["title"]: item for item in _plan(auth_client, w)}
    scene, beat, arc, story = (
        plan["The empty vault"],
        plan["The theft is discovered"],
        plan["Mara loses her faith in the guild"],
        plan["The Salt Road"],
    )
    assert (scene["parent_id"], beat["parent_id"], arc["parent_id"]) == (
        beat["id"],
        arc["id"],
        story["id"],
    )
    assert scene["sets_up"] == [plan["Who took the ledgers?"]["id"]]
    entries = {e["name"]: e for e in auth_client.get(f"{w['base']}/entities").json()["items"]}
    assert {(ref["entity_id"], ref["role"]) for ref in scene["refs"]} == {
        (w["mara"], "point_of_view"),
        (entries["The Guild Vault"]["id"], "place"),
        (w["tobin"], "participant"),
    }
    # The place the plan needed is a bare entry, on the list of things to fill in.
    todo = auth_client.get(f"{w['base']}/entities/needs-detail").json()["items"]
    assert "The Guild Vault" in [item["name"] for item in todo]
    assert len([name for name in entries if name.lower() == "lantern harbor"]) == 1
    # The conversation is kept as where the plan came from.
    kept = auth_client.get(
        f"{w['base']}/assist/sessions", params={"mutation_id": body["mutation_id"]}
    ).json()["items"]
    assert kept[0]["kind"] == "plan" and "robbed from the inside" in kept[0]["description"]


def test_a_draft_that_makes_no_sense_is_bounded_and_explained_not_trusted(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    fake_container.ai.queue(
        PlanDraft(
            items=[
                DraftPlanItem(ref="ok", kind="arc", title="A real arc"),
                DraftPlanItem(ref="x1", kind="chapter", title="Not a kind"),
                DraftPlanItem(
                    ref="x2", kind="arc", title="Under nothing known", parent_ref="pln_nowhere"
                ),
                DraftPlanItem(ref="x3", kind="arc", title="Under a beat", parent_ref="x4"),
                DraftPlanItem(ref="x4", kind="beat", title="A beat", parent_ref="ok"),
                DraftPlanItem(ref="x5", kind="scene", title="N" * 500),
                DraftPlanItem(ref="loop1", kind="beat", title="Loop one", parent_ref="loop2"),
                DraftPlanItem(ref="loop2", kind="beat", title="Loop two", parent_ref="loop1"),
                DraftPlanItem(
                    ref="sc",
                    kind="scene",
                    title="A scene",
                    parent_ref="x4",
                    entries=[
                        # A place that is a person, someone from another project, a made-up role.
                        DraftPlanRef(ref=w["mara"], role="place"),
                        DraftPlanRef(ref="ent_elsewhere", role="participant"),
                        DraftPlanRef(ref=w["tobin"], role="owner"),
                        DraftPlanRef(ref=w["harbor"], role="place"),
                    ],
                    sets_up=["ok", "nothing"],
                ),
                *[
                    DraftPlanItem(ref=f"f{i}", kind="thread", title=f"Flood {i}")
                    for i in range(200)
                ],
            ]
        )
    )

    made = auth_client.post(
        f"{w['base']}/assist/plan/proposals",
        json={"description": "x", "depth": "scenes", "expected_revision": int(w["head"])},
    )

    assert made.status_code == 201, made.text
    detail = auth_client.get(f"{w['base']}/mutations/{made.json()['mutation_id']}").json()
    operations = [op["payload"] for op in detail["operations"]]
    assert len(operations) <= 60
    titles = {op["title"] for op in operations}
    assert {"A real arc", "A beat", "A scene"} <= titles
    assert not titles & {
        "Not a kind",
        "Under nothing known",
        "Under a beat",
        "Loop one",
        "Loop two",
    }
    scene = next(op for op in operations if op["title"] == "A scene")
    assert scene["refs"] == [{"entity_id": w["harbor"], "role": "place"}]
    # An arc is not a thread, so it cannot be "set up".
    assert scene["sets_up"] == []
    reasons = " | ".join(item["reason"] for item in made.json()["discarded"])
    for expected in ("unknown kind", "is not in the plan", "cannot sit under a beat", "circular"):
        assert expected in reasons
    # Every one of them passes the same checks a hand-made change does.
    assert auth_client.post(f"{w['base']}/mutations/{detail['id']}/validate").json()["ok"] is True


def test_planning_asks_questions_first_and_keeps_the_answers(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    fake_container.ai.queue(
        CharacterQuestionSheet(
            questions=[
                CharacterQuestion(
                    id="stakes", question="What does Mara stand to lose?", category=""
                ),
                CharacterQuestion(id="bad id!", question="Whose arc is it?", category=""),
            ]
        )
    )
    asked = auth_client.post(
        f"{w['base']}/assist/plan/questions", json={"description": "A betrayal.", "depth": "arcs"}
    )
    assert asked.status_code == 200, asked.text
    assert [q["id"] for q in asked.json()["questions"]] == ["stakes", "q2"]
    assert "asking, never by deciding" in fake_container.ai.calls[-1]["messages"][0]["content"]

    fake_container.ai.queue(
        PlanDraft(items=[DraftPlanItem(ref="a", kind="arc", title="Mara's fall")])
    )
    made = auth_client.post(
        f"{w['base']}/assist/plan/proposals",
        json={
            "description": "A betrayal.",
            "depth": "arcs",
            "session_id": asked.json()["session_id"],
            "answers": [
                {
                    "question_id": "stakes",
                    "question": "What does Mara stand to lose?",
                    "answer": "Her licence.",
                }
            ],
            "expected_revision": int(w["head"]),
        },
    )
    assert made.status_code == 201, made.text
    assert made.json()["session_id"] == asked.json()["session_id"]
    assert "A: Her licence." in fake_container.ai.calls[-1]["messages"][1]["content"]

    nothing = auth_client.post(
        f"{w['base']}/assist/plan/proposals",
        json={"description": "", "depth": "arcs", "expected_revision": int(w["head"])},
    )
    assert nothing.status_code == 400
    odd = auth_client.post(
        f"{w['base']}/assist/plan/questions", json={"description": "x", "depth": "chapters"}
    )
    assert odd.status_code == 400


def test_what_happens_next_offers_directions_and_only_the_chosen_one_becomes_changes(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    fake_container.ai.queue(
        DirectionSheet(
            directions=[
                Direction(
                    title="Mara confronts Tobin",
                    what_happens="She accuses him at the harbor.",
                    what_it_changes=["Their friendship ends.", ""],
                    items=[
                        DraftPlanItem(
                            ref="s",
                            kind="scene",
                            title="The accusation",
                            entries=[DraftPlanRef(ref=w["harbor"], role="place")],
                        )
                    ],
                ),
                Direction(
                    title="Mara leaves for Saltmere",
                    what_happens="She goes quietly.",
                    what_it_changes=["The guild loses its pilot."],
                    items=[DraftPlanItem(ref="b", kind="beat", title="She sails at dawn")],
                ),
                # Adds nothing usable, so it is not offered.
                Direction(title="Nothing happens", what_happens="", items=[]),
                Direction(
                    title="A fourth",
                    what_happens="x",
                    items=[DraftPlanItem(ref="z", kind="beat", title="Z")],
                ),
            ]
        )
    )

    offered = auth_client.post(f"{w['base']}/assist/plan/directions", json={})

    assert offered.status_code == 200, offered.text
    directions = offered.json()["directions"]
    assert [d["title"] for d in directions] == ["Mara confronts Tobin", "Mara leaves for Saltmere"]
    assert directions[0]["what_it_changes"] == ["Their friendship ends."]
    # Offering is not proposing: nothing is waiting for review and nothing is planned.
    assert auth_client.get(f"{w['base']}/mutations?status=needs_review").json()["items"] == []
    assert _plan(auth_client, w) == []

    calls = len(fake_container.ai.calls)
    chosen = auth_client.post(
        f"{w['base']}/assist/plan/directions/choose",
        json={"draft": directions[0]["draft"], "expected_revision": int(w["head"])},
    )
    assert chosen.status_code == 201, chosen.text
    # Choosing calls no model, and what comes back from the browser is checked like a model's.
    assert len(fake_container.ai.calls) == calls
    _accept(auth_client, w, chosen.json()["mutation_id"])
    assert [item["title"] for item in _plan(auth_client, w)] == ["The accusation"]

    forged = auth_client.post(
        f"{w['base']}/assist/plan/directions/choose",
        json={
            "draft": {"items": [{"ref": "x", "kind": "admin", "title": "Take over"}]},
            "expected_revision": int(w["head"]) + 1,
        },
    )
    assert forged.status_code == 400


def test_planning_requires_a_session_and_uses_provider_safe_formats(api_client) -> None:
    from openai.lib._pydantic import to_strict_json_schema

    for path in ("questions", "proposals", "directions", "directions/choose"):
        response = api_client.post(f"/api/v1/projects/local-project/assist/plan/{path}", json={})
        assert response.status_code == 401, path

    def free_form(node: object) -> bool:
        if isinstance(node, dict):
            extra = node.get("additionalProperties")
            return (node.get("type") == "object" and extra is not False) or any(
                free_form(value) for value in node.values()
            )
        return isinstance(node, list) and any(free_form(value) for value in node)

    assert not free_form(to_strict_json_schema(PlanDraft))
    assert not free_form(to_strict_json_schema(DirectionSheet))


def test_a_drafted_scene_says_what_it_is_for_only_in_the_authors_words(
    auth_client, fake_container
) -> None:
    """STY-003: plan drafting sets a scene's goal, obstacle and outcome when the author's
    material states them, each part quoting their words; anything else is dropped."""
    from backend.services.plan_workflow import DraftIntent

    w = _world(auth_client, fake_container)
    fake_container.ai.queue(
        PlanDraft(
            items=[
                DraftPlanItem(
                    ref="s1",
                    kind="scene",
                    title="The empty vault",
                    entries=[DraftPlanRef(ref=w["mara"], role="point_of_view")],
                )
            ],
            intents=[
                DraftIntent(
                    ref="s1",
                    goal="To find the guild's ledgers",
                    goal_words="Mara wants the ledgers back",
                    obstacle="Tobin, who has hidden them",
                    obstacle_words="Tobin hid them",
                    # Not said by the author: dropped.
                    outcome="no_and",
                    outcome_words="and it all goes wrong",
                ),
                # Not a drafted scene: ignored.
                DraftIntent(ref="nowhere", goal="Anything", goal_words="Mara wants the ledgers"),
            ],
        )
    )
    made = auth_client.post(
        f"{w['base']}/assist/plan/proposals",
        json={
            "description": "In the vault scene Mara wants the ledgers back, but Tobin hid them.",
            "depth": "scenes",
            "expected_revision": int(w["head"]),
        },
    )
    assert made.status_code == 201, made.text
    body = made.json()
    assert any(
        "part of what it is for was left out" in item["reason"] for item in body["discarded"]
    )
    system = fake_container.ai.calls[-1]["messages"][0]["content"]
    assert "only what the author said it is for" in system
    _accept(auth_client, w, body["mutation_id"])
    (scene,) = [item for item in _plan(auth_client, w) if item["kind"] == "scene"]
    assert scene["intent"] == {
        "goal": "To find the guild's ledgers",
        "obstacle": "Tobin, who has hidden them",
        "outcome": None,
    }
