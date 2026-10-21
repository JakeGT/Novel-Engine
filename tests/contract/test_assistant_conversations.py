"""Batch 8 B: merging as its own reviewed change, the kept Change anything thread, starting
from a premise, and how suggestions fare (AST-EXP-005, -007, -013, -017)."""

import pytest

from backend.adapters.embedding_fake import DeterministicEmbedding
from backend.domain.bible_listing import ListingPolicy
from backend.services.canon_change_workflow import ChangePlan, MergeRequest, PlanChange, RecordRef
from backend.services.canon_creation_workflow import DraftEntity, DraftFact, PremiseRound
from backend.services.canon_revise_workflow import LinkPlan, SuggestedLink
from backend.services.plan_drafts import DraftPlanItem, DraftPlanRef
from backend.services.plan_workflow import PlanDraft
from tests.contract.test_god_hand import _world

pytestmark = pytest.mark.contract


def _head(client, base: str) -> int:
    return int(client.get(f"{base}/revisions").json()["items"][0]["revision"])


def _accept(client, base: str, mutation_id: str) -> int:
    approved = client.post(f"{base}/mutations/{mutation_id}/approve", json={})
    assert approved.status_code == 200, approved.text
    committed = client.post(f"{base}/mutations/{mutation_id}/commit")
    assert committed.status_code == 200, committed.text
    return int(committed.json()["revision"])


def _twin(client, w: dict[str, str]) -> str:
    """A second Mara, known as Mara V., in Saltmere with a ship."""
    base = w["base"]
    created = client.post(
        f"{base}/entities",
        json={
            "type": "Character",
            "name": "Mara V.",
            "aliases": ["Captain Venn"],
            "attributes": {"ship": "The Gull"},
            "expected_revision": int(w["head"]),
        },
    )
    assert created.status_code == 201, created.text
    twin = created.json()["id"]
    fact = client.post(
        f"{base}/facts",
        json={
            "proposition": {
                "subject_id": w["tobin"],
                "predicate": "KNOWS",
                "object": {"kind": "entity_ref", "entity_id": twin, "project_id": w["project"]},
            },
            "expected_revision": int(w["head"]) + 1,
        },
    )
    assert fact.status_code == 201, fact.text
    w["head"] = str(int(w["head"]) + 2)
    return twin


def _world_with_project(client, container) -> dict[str, str]:
    w = _world(client, container)
    w["project"] = w["base"].rsplit("/", 1)[-1]
    return w


def test_a_merge_by_hand_is_previewed_reviewed_kept_and_undone_in_one_step(
    auth_client, fake_container
) -> None:
    w = _world_with_project(auth_client, fake_container)
    base = w["base"]
    twin = _twin(auth_client, w)

    preview = auth_client.post(
        f"{base}/merges/preview", json={"entity_id": twin, "into": w["mara"]}
    )
    assert preview.status_code == 200, preview.text
    shown = preview.json()
    assert shown["name"] == "Mara"
    assert shown["aliases"] == ["The Fox", "Mara V.", "Captain Venn"]
    assert shown["details"] == {"role": "pilot", "eyes": "green", "ship": "The Gull"}
    assert shown["facts_moved"] == ["Tobin Ashe knows Mara V."]
    assert shown["facts_ended"] == []
    assert shown["change_count"] == 2

    refused = auth_client.post(
        f"{base}/merges/preview", json={"entity_id": w["harbor"], "into": w["mara"]}
    )
    assert refused.status_code == 400 and "same type" in refused.json()["detail"]

    proposed = auth_client.post(
        f"{base}/merges",
        json={"entity_id": twin, "into": w["mara"], "expected_revision": int(w["head"])},
    )
    assert proposed.status_code == 201, proposed.text
    body = proposed.json()
    assert body["status"] == "needs_review" and body["validation_ok"], body
    review = auth_client.get(f"{base}/mutations/{body['mutation_id']}").json()
    assert [op["operation_type"] for op in review["operations"]] == ["merge_entities"]
    assert review["requires_review"] is True
    # Nothing has changed yet.
    assert auth_client.get(f"{base}/entities/{twin}").json()["lifecycle"] == "active"

    merged_at = _accept(auth_client, base, body["mutation_id"])
    assert auth_client.get(f"{base}/entities/{twin}").json()["lifecycle"] == "retired"
    mara = auth_client.get(f"{base}/entities/{w['mara']}").json()
    assert mara["aliases"] == ["The Fox", "Mara V.", "Captain Venn"]
    knows = auth_client.get(f"{base}/facts", params={"subject_id": w["tobin"]}).json()["items"]
    assert [fact["object"]["entity_id"] for fact in knows] == [w["mara"]]

    # Undo: one step brings the merged entry and its fact back.
    undo = auth_client.post(f"{base}/reverts/preview", json={"from_revision": merged_at - 1})
    assert undo.status_code == 200 and not undo.json()["conflicts"], undo.text
    reverted = auth_client.post(
        f"{base}/reverts",
        json={
            "from_revision": merged_at - 1,
            "expected_revision": merged_at,
            "acknowledge_high_impact": True,
        },
    )
    assert reverted.status_code == 200, reverted.text
    assert auth_client.get(f"{base}/entities/{twin}").json()["lifecycle"] == "active"
    assert auth_client.get(f"{base}/entities/{w['mara']}").json()["aliases"] == ["The Fox"]
    knows = auth_client.get(f"{base}/facts", params={"subject_id": w["tobin"]}).json()["items"]
    assert [fact["object"]["entity_id"] for fact in knows] == [twin]


def test_change_anything_merges_as_its_own_change_and_changes_the_plan(
    auth_client, fake_container
) -> None:
    w = _world_with_project(auth_client, fake_container)
    base = w["base"]
    twin = _twin(auth_client, w)
    head = int(w["head"])
    scene = auth_client.post(
        f"{base}/plan",
        json={
            "kind": "scene",
            "title": "The quay",
            "refs": [{"entity_id": twin, "role": "point_of_view"}],
            "expected_revision": head,
        },
    )
    assert scene.status_code == 201, scene.text
    old_scene = auth_client.post(
        f"{base}/plan",
        json={"kind": "scene", "title": "Cut me", "expected_revision": head + 1},
    ).json()
    head += 2
    fake_container.ai.queue(
        ChangePlan(
            understood="Mara V. is Mara; the quay scene is renamed and one is cut.",
            merge_entities=[MergeRequest(merge=twin, keep=w["mara"])],
            # Changes to the merged entry wait for the merge.
            add_facts=[DraftFact(subject_ref=twin, predicate="LOCATED_AT", object_ref=w["harbor"])],
            plan_changes=[
                PlanChange(
                    id=scene.json()["id"],
                    new_title="The night quay",
                    add_entries=[DraftPlanRef(ref=w["saltmere"], role="place")],
                )
            ],
            new_plan_items=[DraftPlanItem(ref="t", kind="thread", title="Who is Mara really?")],
            remove_plan_items=[RecordRef(id=old_scene["id"])],
        )
    )
    response = auth_client.post(
        f"{base}/assist/changes",
        json={"instruction": "Mara V. is Mara. Rename the quay scene.", "expected_revision": head},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    review = auth_client.get(f"{base}/mutations/{body['mutation_id']}").json()
    assert [op["operation_type"] for op in review["operations"]] == [
        "update_plan_item",
        "retire_plan_item",
        "create_plan_item",
        "merge_entities",
    ]
    assert any("waits until the merge is kept" in d["reason"] for d in body["discarded"])
    assert any("is merged into Mara" in note for note in body["notes"])
    prompt = fake_container.ai.calls[-1]["messages"][1]["content"]
    assert "The plan (what the author intends to happen; never a fact):" in prompt
    assert scene.json()["id"] in prompt

    _accept(auth_client, base, body["mutation_id"])
    assert auth_client.get(f"{base}/entities/{twin}").json()["lifecycle"] == "retired"
    plan = {item["title"]: item for item in auth_client.get(f"{base}/plan").json()["items"]}
    assert "Cut me" not in plan and "Who is Mara really?" in plan
    renamed = plan["The night quay"]
    assert {(ref["entity_id"], ref["role"]) for ref in renamed["refs"]} == {
        (w["mara"], "point_of_view"),
        (w["saltmere"], "place"),
    }


def test_change_anything_keeps_the_thread_across_visits_until_cleared(
    auth_client, fake_container
) -> None:
    w = _world_with_project(auth_client, fake_container)
    base, head = w["base"], int(w["head"])
    fake_container.ai.queue(ChangePlan(understood="Which one?", questions=["Which harbor?"]))
    first = auth_client.post(
        f"{base}/assist/changes",
        json={"instruction": "Burn the harbor down.", "expected_revision": head},
    )
    assert first.status_code == 200, first.text
    fake_container.ai.queue(ChangePlan(understood="Nothing more.", questions=["Sure?"]))
    second = auth_client.post(
        f"{base}/assist/changes",
        json={"instruction": "The old one, I said.", "expected_revision": head},
    )
    assert second.status_code == 200
    prompt = fake_container.ai.calls[-1]["messages"][1]["content"]
    assert "Earlier in this conversation:" in prompt
    assert "The author said: Burn the harbor down." in prompt
    assert "You asked: Which harbor?" in prompt
    # The new instruction comes last, outside the markers.
    assert prompt.rstrip().endswith("Instruction:\nThe old one, I said.")

    thread = auth_client.get(f"{base}/assist/threads/change").json()
    assert [turn["role"] for turn in thread["turns"]] == [
        "author",
        "assistant",
        "author",
        "assistant",
    ]
    assert thread["turns"][1]["questions"] == ["Which harbor?"]

    cleared = auth_client.delete(f"{base}/assist/threads/change")
    assert cleared.status_code == 200 and cleared.json() == {"cleared": True}
    assert auth_client.get(f"{base}/assist/threads/change").json()["turns"] == []
    fake_container.ai.queue(ChangePlan(understood="?", questions=["What?"]))
    auth_client.post(
        f"{base}/assist/changes", json={"instruction": "Again.", "expected_revision": head}
    )
    assert (
        "Earlier in this conversation" not in fake_container.ai.calls[-1]["messages"][1]["content"]
    )
    assert auth_client.get(f"{base}/assist/threads/nothing").status_code == 400


def test_a_large_story_finds_what_the_instruction_means_by_meaning(
    auth_client, fake_container, monkeypatch
) -> None:
    w = _world_with_project(auth_client, fake_container)
    base, head = w["base"], int(w["head"])
    created = auth_client.post(
        f"{base}/entities",
        json={
            "type": "Location",
            "name": "The Drowned Bell",
            "attributes": {"what": "the smugglers tavern"},
            "expected_revision": head,
        },
    )
    assert created.status_code == 201
    tavern = created.json()["id"]
    # A listing too small to hold everything, so the tavern is left out by name.
    monkeypatch.setattr(
        "backend.services.canon_change_workflow.CHANGE_POLICY",
        ListingPolicy(
            version="change-story/test",
            purpose="test",
            sections=("entries", "facts"),
            budget=12,
        ),
    )
    fake_container.canon_character_workflow.embedder = DeterministicEmbedding()
    fake_container.ai.queue(ChangePlan(understood="Which?", questions=["Which tavern?"]))
    response = auth_client.post(
        f"{base}/assist/changes",
        json={"instruction": "The smugglers tavern should flood.", "expected_revision": head + 1},
    )
    assert response.status_code == 200, response.text
    context = response.json()["context"]
    assert context["by_meaning"] is True and context["found_by_meaning"] >= 1
    assert tavern in fake_container.ai.calls[-1]["messages"][1]["content"]
    assert any("found by meaning" in note for note in response.json()["notes"])


def test_starting_from_a_premise_asks_and_suggests_in_turn(auth_client, fake_container) -> None:
    w = _world_with_project(auth_client, fake_container)
    base, head = w["base"], int(w["head"])
    fake_container.ai.queue(
        PremiseRound(
            understood="A lamplighter looks for her brother.",
            questions=["What is her brother's name?", "Who keeps the ledger?"],
            entities=[
                DraftEntity(ref="wren", type="Character", name="Wren Hollis"),
                DraftEntity(ref="bro", type="Character", name="Wren's brother", needs_more=True),
            ],
            facts=[DraftFact(subject_ref="wren", predicate="LOCATED_AT", object_ref=w["saltmere"])],
        )
    )
    first = auth_client.post(
        f"{base}/assist/premise",
        json={
            "premise": "Wren Hollis lights the lamps of Saltmere. Her brother is gone.",
            "expected_revision": head,
        },
    )
    assert first.status_code == 200, first.text
    one = first.json()
    assert one["questions"] == ["What is her brother's name?", "Who keeps the ledger?"]
    assert one["status"] == "needs_review" and one["operation_count"] == 3
    assert one["session_id"]
    prompt = fake_container.ai.calls[-1]["messages"][1]["content"]
    assert "<<<PREMISE-1>>>" in prompt and w["saltmere"] in prompt
    thread = auth_client.get(f"{base}/assist/threads/premise").json()
    assert thread["open_questions"] == one["questions"]

    # The next round: the answers, and what is waiting for review is never suggested again.
    fake_container.ai.queue(
        PremiseRound(
            understood="Her brother is Tam.",
            entities=[
                DraftEntity(ref="wren", type="Character", name="Wren Hollis"),
                DraftEntity(ref="tam", type="Character", name="Tam Hollis"),
            ],
        )
    )
    second = auth_client.post(
        f"{base}/assist/premise",
        json={
            "answers": [
                {"question": "What is her brother's name?", "answer": "Tam."},
                {"question": "Who keeps the ledger?", "answer": ""},
            ],
            "expected_revision": head,
        },
    )
    assert second.status_code == 200, second.text
    two = second.json()
    assert two["questions"] == [] and two["operation_count"] == 1
    assert [d["reason"] for d in two["discarded"]] == [
        "Wren Hollis: already suggested and waiting for your review"
    ]
    prompt = fake_container.ai.calls[-1]["messages"][1]["content"]
    assert "Q: What is her brother's name?\nA: Tam." in prompt
    assert "Who keeps the ledger?\nA:" not in prompt  # a skipped question is not sent
    assert "- Wren Hollis (Character)" in prompt  # waiting for review
    assert "Wren Hollis lights the lamps" in prompt  # the premise travels with every round

    runs = auth_client.get(f"{base}/assist/runs", params={"kind": "premise"}).json()["items"]
    assert [run["suggested"] for run in runs] == [1, 3]
    nothing = auth_client.post(f"{base}/assist/premise", json={"expected_revision": head})
    assert nothing.status_code == 400


def test_link_suggestions_by_meaning_record_how_many_the_author_kept(
    auth_client, fake_container
) -> None:
    w = _world_with_project(auth_client, fake_container)
    base, head = w["base"], int(w["head"])
    for name, details in (("Ivo Venn", "pilot of the harbor guild"), ("Ada Venn", "pilot")):
        made = auth_client.post(
            f"{base}/entities",
            json={
                "type": "Character",
                "name": name,
                "attributes": {"role": details},
                "expected_revision": head,
            },
        )
        assert made.status_code == 201
        w[name] = made.json()["id"]
        head += 1
    fake_container.canon_character_workflow.embedder = DeterministicEmbedding()
    fake_container.ai.queue(
        LinkPlan(
            links=[
                SuggestedLink(
                    subject_ref=w["Ivo Venn"],
                    predicate="KNOWS",
                    object_ref=w["Ada Venn"],
                    why="Both are pilots named Venn.",
                ),
                SuggestedLink(
                    subject_ref=w["Ivo Venn"],
                    predicate="MEMBER_OF",
                    object_ref=w["guild"],
                    why="Pilot of the harbor guild.",
                ),
            ]
        )
    )
    response = auth_client.post(f"{base}/assist/links", json={"expected_revision": head})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["operation_count"] == 2 and body["candidates"] >= 1
    assert body["from_candidates"] >= 1 and body["context"]["by_meaning"] is True
    assert "Pairs worth checking:" in fake_container.ai.calls[-1]["messages"][1]["content"]

    runs = auth_client.get(f"{base}/assist/runs", params={"kind": "links"}).json()["items"]
    assert runs[0]["source"] == "meaning" and runs[0]["decided"] is False
    assert runs[0]["precision"] is None  # not decided yet: never guessed

    # The author skips one and keeps the other.
    skipped = auth_client.post(
        f"{base}/mutations/{body['mutation_id']}/operations/1/decision",
        json={"decision": "rejected"},
    )
    assert skipped.status_code == 200, skipped.text
    _accept(auth_client, base, body["mutation_id"])
    run = auth_client.get(f"{base}/assist/runs", params={"kind": "links"}).json()["items"][0]
    assert (run["suggested"], run["kept"], run["precision"], run["decided"]) == (2, 1, 0.5, True)


def test_link_suggestions_without_a_model_for_search_use_the_listing(
    auth_client, fake_container
) -> None:
    w = _world_with_project(auth_client, fake_container)
    fake_container.canon_character_workflow.embedder = None
    fake_container.ai.queue(
        LinkPlan(
            links=[
                SuggestedLink(
                    subject_ref=w["tobin"], predicate="KNOWS", object_ref=w["mara"], why="x"
                )
            ]
        )
    )
    response = auth_client.post(
        f"{w['base']}/assist/links", json={"expected_revision": int(w["head"])}
    )
    assert response.status_code == 200, response.text
    assert response.json()["candidates"] == 0
    assert "Pairs worth checking" not in fake_container.ai.calls[-1]["messages"][1]["content"]
    rejected = auth_client.post(
        f"{w['base']}/mutations/{response.json()['mutation_id']}/reject", json={}
    )
    assert rejected.status_code == 200, rejected.text
    run = auth_client.get(f"{w['base']}/assist/runs", params={"kind": "links"}).json()["items"][0]
    assert (run["source"], run["kept"], run["precision"]) == ("listing", 0, 0.0)


def test_plan_suggestions_record_which_quote_the_author_and_which_are_invented(
    auth_client, fake_container
) -> None:
    w = _world_with_project(auth_client, fake_container)
    fake_container.ai.queue(
        PlanDraft(
            understood="Mara's arc.",
            items=[
                DraftPlanItem(
                    ref="a1",
                    kind="arc",
                    title="Mara learns who betrayed her",
                    quote="Mara learns who betrayed her father",
                ),
                DraftPlanItem(
                    ref="b1",
                    kind="beat",
                    title="A storm",
                    parent_ref="a1",
                    # A quote that is nowhere in what the assistant was given.
                    quote="the storm that sank the fleet",
                ),
                DraftPlanItem(ref="b2", kind="beat", title="A letter", parent_ref="a1"),
            ],
        )
    )
    response = auth_client.post(
        f"{w['base']}/assist/plan/proposals",
        json={
            "description": "Mara learns who betrayed her father.",
            "depth": "beats",
            "expected_revision": int(w["head"]),
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["grounded"], body["invented"]) == (1, 2)
    assert body["grounding"][0] == {
        "title": "Mara learns who betrayed her",
        "quote": "Mara learns who betrayed her father",
        "grounded": True,
    }
    assert body["grounding"][1]["quote"] is None and not body["grounding"][1]["grounded"]
    run = auth_client.get(f"{w['base']}/assist/runs", params={"kind": "plan_draft"}).json()[
        "items"
    ][0]
    assert (run["grounded"], run["invented"]) == (1, 2)
    assert run["groundedness"] == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_kept_threads_travel_with_the_archive_and_restore_once(
    auth_client, fake_container
) -> None:
    from backend.domain.conversations import ThreadKind

    w = _world_with_project(auth_client, fake_container)
    base, head = w["base"], int(w["head"])
    fake_container.ai.queue(
        ChangePlan(understood="Tobin is archived.", archive_entities=[RecordRef(id=w["tobin"])])
    )
    proposed = auth_client.post(
        f"{base}/assist/changes", json={"instruction": "Cut Tobin.", "expected_revision": head}
    ).json()
    _accept(auth_client, base, proposed["mutation_id"])

    archive = auth_client.post(f"{base}/archive/export").json()
    (exported,) = archive["assistant_threads"]
    assert exported["kind"] == "change"
    assert [turn["body"] for turn in exported["turns"]] == ["Cut Tobin.", "Tobin is archived."]
    assert exported["turns"][1]["commit_id"] and "mutation_id" not in exported["turns"][1]

    service = fake_container.archive_service
    workspace_id = archive["project"]["workspace_id"]
    me = exported["actor_user_id"]
    result = await service.import_project(workspace_id, "restored", archive, actor_user_id=me)
    assert result["assistant_threads"] == 1
    store = fake_container.conversation_store
    (thread,) = await store.list_threads(workspace_id=workspace_id, project_id="restored")
    assert thread.kind is ThreadKind.CHANGE and thread.actor_user_id == me
    assert [turn.body for turn in thread.turns] == ["Cut Tobin.", "Tobin is archived."]
    assert thread.turns[1].mutation_id  # the restored proposal behind the saved change
    again = await fake_container.conversation_service.restore_threads(
        workspace_id,
        "restored",
        archive["assistant_threads"],
        {"main": "main"},
        _no_mutation,
        actor_user_id=me,
    )
    assert again == 0


async def _no_mutation(_commit_id):
    return None


def _review(client, base: str, mutation_id: str) -> dict:
    review = client.get(f"{base}/mutations/{mutation_id}")
    assert review.status_code == 200, review.text
    return review.json()


def test_premise_suggestions_cite_the_conversation_that_is_kept(
    auth_client, fake_container
) -> None:
    w = _world_with_project(auth_client, fake_container)
    base, head = w["base"], int(w["head"])
    fake_container.ai.queue(
        PremiseRound(
            understood="A lamplighter.",
            questions=["Who is she?"],
            entities=[DraftEntity(ref="wren", type="Character", name="Wren Hollis")],
        )
    )
    round_ = auth_client.post(
        f"{base}/assist/premise",
        json={"premise": "Wren Hollis lights the lamps of Saltmere.", "expected_revision": head},
    ).json()
    session_id = round_["session_id"]
    review = _review(auth_client, base, round_["mutation_id"])
    cited = {
        item["source_artefact_id"]
        for operation in review["operations"]
        for item in operation["payload"].get("evidence", [])
    }
    # Every suggestion cites the conversation kept behind it, which can be opened.
    assert cited == {session_id}
    kept = auth_client.get(f"{base}/assist/sessions/{session_id}")
    assert kept.status_code == 200, kept.text
    assert kept.json()["status"] == "proposed"
    assert kept.json()["description"] == "Wren Hollis lights the lamps of Saltmere."

    # A round that only asks keeps nothing beside the thread.
    fake_container.ai.queue(PremiseRound(understood="And?", questions=["Where is Saltmere?"]))
    asked = auth_client.post(
        f"{base}/assist/premise",
        json={
            "answers": [{"question": "Who is she?", "answer": "A widow."}],
            "expected_revision": head,
        },
    ).json()
    assert asked["session_id"] is None
    assert auth_client.get(f"{base}/assist/sessions").json()["items"] == []


def test_suggestions_from_a_premise_are_never_kept_by_a_rule(auth_client, fake_container) -> None:
    w = _world_with_project(auth_client, fake_container)
    calls: list[str] = []
    original = fake_container.mutation_service.keep_if_allowed

    async def watched(scope, mutation, *, validation=None, via="session"):
        calls.append(via)
        return await original(scope, mutation, validation=validation, via=via)

    fake_container.mutation_service.keep_if_allowed = watched
    fake_container.ai.queue(
        PremiseRound(
            understood="A lamplighter.",
            entities=[DraftEntity(ref="wren", type="Character", name="Wren Hollis")],
        )
    )
    response = auth_client.post(
        f"{w['base']}/assist/premise",
        json={"premise": "Wren Hollis lights the lamps.", "expected_revision": int(w["head"])},
    )
    assert response.status_code == 200, response.text
    assert calls == ["premise"]


def test_clearing_a_thread_erases_the_words_its_conversations_kept(
    auth_client, fake_container
) -> None:
    w = _world_with_project(auth_client, fake_container)
    base, head = w["base"], int(w["head"])
    fake_container.ai.queue(
        PremiseRound(
            understood="A lamplighter.",
            questions=["Who is her brother?"],
            entities=[DraftEntity(ref="wren", type="Character", name="Wren Hollis")],
        )
    )
    premise = auth_client.post(
        f"{base}/assist/premise",
        json={"premise": "Wren Hollis keeps a secret ledger.", "expected_revision": head},
    ).json()
    fake_container.ai.queue(ChangePlan(understood="Which one?", questions=["Which harbor?"]))
    change = auth_client.post(
        f"{base}/assist/changes",
        json={"instruction": "Burn the secret harbor down.", "expected_revision": head},
    ).json()
    assert auth_client.get(f"{base}/assist/sessions/{change['session_id']}").status_code == 200

    assert auth_client.delete(f"{base}/assist/threads/premise").json() == {"cleared": True}
    assert auth_client.delete(f"{base}/assist/threads/change").json() == {"cleared": True}
    # The unfinished change conversation is gone; the evidence behind the suggested
    # entries stays, without the author's words.
    assert auth_client.get(f"{base}/assist/sessions/{change['session_id']}").status_code == 404
    kept = auth_client.get(f"{base}/assist/sessions/{premise['session_id']}").json()
    assert kept["status"] == "proposed"
    assert (kept["description"], kept["understood"]) == ("", "")
    assert all(not item["question"] for item in kept["questions"])
    sessions = fake_container.assist_sessions.sessions
    assert not any("secret" in session.model_dump_json() for session in sessions.values())


def test_a_quote_of_the_listing_is_not_the_author_s_words(auth_client, fake_container) -> None:
    w = _world_with_project(auth_client, fake_container)
    fake_container.ai.queue(
        PlanDraft(
            understood="Mara's arc.",
            items=[
                # Words the app wrote in the listing it showed the assistant.
                DraftPlanItem(ref="a1", kind="arc", title="An arc", quote="There is no plan yet."),
                DraftPlanItem(
                    ref="a2", kind="arc", title="Another", quote="Mara learns who betrayed her"
                ),
            ],
        )
    )
    response = auth_client.post(
        f"{w['base']}/assist/plan/proposals",
        json={
            "description": "Mara learns who betrayed her father.",
            "depth": "arcs",
            "expected_revision": int(w["head"]),
        },
    )
    assert response.status_code == 201, response.text
    assert "There is no plan yet." in fake_container.ai.calls[-1]["messages"][1]["content"]
    assert [item["grounded"] for item in response.json()["grounding"]] == [False, True]


def test_change_anything_merges_one_entry_into_another_at_a_time_and_never_cuts_a_merge(
    auth_client, fake_container
) -> None:
    from backend.services.canon_change_workflow import MAX_CHANGES

    w = _world_with_project(auth_client, fake_container)
    base = w["base"]
    twin = _twin(auth_client, w)
    other = auth_client.post(
        f"{base}/entities",
        json={"type": "Character", "name": "Marra", "expected_revision": int(w["head"])},
    )
    assert other.status_code == 201, other.text
    head = int(w["head"]) + 1
    fake_container.ai.queue(
        ChangePlan(
            understood="Both are Mara; and a great many new people and scenes.",
            merge_entities=[
                MergeRequest(merge=twin, keep=w["mara"]),
                MergeRequest(merge=other.json()["id"], keep=w["mara"]),
            ],
            new_entities=[
                DraftEntity(ref=f"p{n}", type="Character", name=f"Passer-by {n}")
                for n in range(MAX_CHANGES)
            ],
            add_facts=[
                DraftFact(subject_ref=f"p{n}", predicate="KNOWS", object_ref=w["tobin"])
                for n in range(MAX_CHANGES)
            ],
            new_plan_items=[
                DraftPlanItem(ref=f"s{n}", kind="scene", title=f"Scene {n}")
                for n in range(MAX_CHANGES)
            ],
        )
    )
    response = auth_client.post(
        f"{base}/assist/changes",
        json={"instruction": "Both are Mara.", "expected_revision": head},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert any(
        "only one entry is merged into Mara at a time" in item["reason"]
        for item in body["discarded"]
    )
    review = auth_client.get(f"{base}/mutations/{body['mutation_id']}").json()
    kinds = [op["operation_type"] for op in review["operations"]]
    # The merge the notes announce is kept; other changes are cut to make room.
    assert kinds.count("merge_entities") == 1 and len(kinds) == MAX_CHANGES * 2
    assert any("is merged into Mara" in note for note in body["notes"])
