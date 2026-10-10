"""Conversations with the assistant are kept: resumable while open, evidence once proposed."""

import pytest

from backend.services.canon_change_workflow import ChangePlan, EntityChange
from backend.services.canon_character_workflow import CharacterQuestion, CharacterQuestionSheet
from backend.services.canon_creation_workflow import CreationDraft, DraftEntity
from tests.contract.test_canon_assist import _setup
from tests.contract.test_god_hand import _world

pytestmark = pytest.mark.contract

BRIEF = {"entity_type": "Character", "name": "Mara Venn", "description": "A harbor pilot."}
ANSWER = {"question_id": "home_1", "question": "Where does she live?", "answer": "At the harbor."}


def _ask(client, container, base: str) -> str:
    container.ai.queue(
        CharacterQuestionSheet(
            questions=[
                CharacterQuestion(id="home_1", question="Where does she live?", category="place")
            ]
        )
    )
    response = client.post(f"{base}/assist/questions", json=BRIEF)
    assert response.status_code == 200, response.text
    return response.json()["session_id"]


def test_an_interview_is_kept_and_can_be_picked_up_again(auth_client, fake_container) -> None:
    base, _ = _setup(auth_client, fake_container)
    session_id = _ask(auth_client, fake_container, base)
    calls = len(fake_container.ai.calls)

    saved = auth_client.put(
        f"{base}/assist/sessions/{session_id}/answers", json={"answers": [ANSWER]}
    )
    assert saved.status_code == 200, saved.text

    waiting = auth_client.get(f"{base}/assist/sessions").json()["items"]
    assert [item["id"] for item in waiting] == [session_id]
    kept = waiting[0]
    assert kept["status"] == "open" and kept["kind"] == "create"
    assert kept["description"] == "A harbor pilot." and kept["name"] == "Mara Venn"
    assert [q["question"] for q in kept["questions"]] == ["Where does she live?"]
    assert kept["answers"] == [ANSWER]
    # Saving and reading a conversation never calls the model.
    assert len(fake_container.ai.calls) == calls


def test_a_finished_interview_is_the_evidence_behind_its_proposal(
    auth_client, fake_container
) -> None:
    base, _ = _setup(auth_client, fake_container)
    session_id = _ask(auth_client, fake_container, base)
    fake_container.ai.queue(
        CreationDraft(entities=[DraftEntity(ref="mara", type="Character", name="Mara Venn")])
    )

    proposal = auth_client.post(
        f"{base}/assist/proposals",
        json={**BRIEF, "session_id": session_id, "answers": [ANSWER], "expected_revision": 1},
    )

    assert proposal.status_code == 201, proposal.text
    body = proposal.json()
    # One id throughout: the proposal's evidence points at the conversation that was kept.
    assert body["session_id"] == session_id
    found = auth_client.get(
        f"{base}/assist/sessions", params={"mutation_id": body["mutation_id"]}
    ).json()["items"]
    assert [item["id"] for item in found] == [session_id]
    assert found[0]["status"] == "proposed" and found[0]["answers"] == [ANSWER]
    detail = auth_client.get(f"{base}/mutations/{body['mutation_id']}").json()
    cited = {
        evidence["source_artefact_id"]
        for operation in detail["operations"]
        for evidence in operation["payload"].get("evidence", [])
    }
    assert cited == {session_id}

    # It is no longer waiting, and it can no longer be altered.
    assert auth_client.get(f"{base}/assist/sessions").json()["items"] == []
    for method, path, payload in (
        ("put", f"/assist/sessions/{session_id}/answers", {"answers": []}),
        ("post", f"/assist/sessions/{session_id}/close", None),
    ):
        refused = getattr(auth_client, method)(f"{base}{path}", json=payload)
        assert refused.status_code == 409, refused.text
    assert auth_client.get(f"{base}/assist/sessions/{session_id}").json()["answers"] == [ANSWER]


def test_a_draft_without_questions_is_kept_too(auth_client, fake_container) -> None:
    base, _ = _setup(auth_client, fake_container)
    fake_container.ai.queue(
        CreationDraft(entities=[DraftEntity(ref="mara", type="Character", name="Mara Venn")])
    )

    body = auth_client.post(
        f"{base}/assist/proposals", json={**BRIEF, "expected_revision": 1}
    ).json()

    kept = auth_client.get(f"{base}/assist/sessions/{body['session_id']}").json()
    assert kept["status"] == "proposed" and kept["mutation_id"] == body["mutation_id"]
    assert kept["description"] == "A harbor pilot." and kept["questions"] == []


def test_answers_survive_a_draft_that_fails(auth_client, fake_container) -> None:
    base, _ = _setup(auth_client, fake_container)
    session_id = _ask(auth_client, fake_container, base)
    # The model drafts nothing usable, so no proposal is made.
    fake_container.ai.queue(CreationDraft())

    failed = auth_client.post(
        f"{base}/assist/proposals",
        json={
            "description": "Something about the sea.",
            "session_id": session_id,
            "answers": [ANSWER],
            "expected_revision": 1,
        },
    )

    assert failed.status_code == 400, failed.text
    kept = auth_client.get(f"{base}/assist/sessions/{session_id}").json()
    assert kept["status"] == "open" and kept["answers"] == [ANSWER]


def test_a_conversation_can_be_put_away(auth_client, fake_container) -> None:
    base, _ = _setup(auth_client, fake_container)
    session_id = _ask(auth_client, fake_container, base)

    closed = auth_client.post(f"{base}/assist/sessions/{session_id}/close")

    assert closed.status_code == 200 and closed.json()["status"] == "closed"
    assert auth_client.get(f"{base}/assist/sessions").json()["items"] == []
    # Put away means erased: the author's words are not kept anywhere.
    assert auth_client.get(f"{base}/assist/sessions/{session_id}").status_code == 404
    assert fake_container.assist_sessions.sessions == {}
    assert auth_client.get(f"{base}/assist/sessions/missing").status_code == 404


def test_a_change_that_needed_questions_stays_open_then_becomes_evidence(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    fake_container.ai.queue(ChangePlan(understood="Unclear.", questions=["Which harbor?"]))
    asked = auth_client.post(
        f"{w['base']}/assist/changes",
        json={"instruction": "Rename the harbor.", "expected_revision": int(w["head"])},
    ).json()
    assert asked["mutation_id"] is None

    waiting = auth_client.get(f"{w['base']}/assist/sessions").json()["items"]
    assert [item["id"] for item in waiting] == [asked["session_id"]]
    assert waiting[0]["kind"] == "change" and waiting[0]["description"] == "Rename the harbor."
    assert [q["question"] for q in waiting[0]["questions"]] == ["Which harbor?"]

    fake_container.ai.queue(
        ChangePlan(
            understood="Rename Lantern Harbor to Lanternhaven.",
            entity_changes=[EntityChange(ref=w["harbor"], new_name="Lanternhaven")],
            notes=["Mara is located there."],
        )
    )
    proposed = auth_client.post(
        f"{w['base']}/assist/changes",
        json={
            "instruction": "Rename Lantern Harbor to Lanternhaven.",
            "session_id": asked["session_id"],
            "expected_revision": int(w["head"]),
        },
    ).json()

    assert proposed["session_id"] == asked["session_id"] and proposed["mutation_id"]
    kept = auth_client.get(
        f"{w['base']}/assist/sessions", params={"mutation_id": proposed["mutation_id"]}
    ).json()["items"][0]
    assert kept["status"] == "proposed"
    assert kept["description"] == "Rename Lantern Harbor to Lanternhaven."
    assert kept["understood"] == "Rename Lantern Harbor to Lanternhaven."
    assert kept["notes"] == ["Mara is located there."]
    assert auth_client.get(f"{w['base']}/assist/sessions").json()["items"] == []


def test_reading_conversations_needs_no_write_token(auth_client, fake_container) -> None:
    """Reads are plain GETs. The browser sends the write token only with changes, so a
    read that demanded one failed on the live site while every test here passed."""
    base, _ = _setup(auth_client, fake_container)
    session_id = _ask(auth_client, fake_container, base)
    token = auth_client.headers.pop("X-CSRF-Token")
    try:
        assert auth_client.get(f"{base}/assist/sessions").status_code == 200
        assert auth_client.get(f"{base}/assist/sessions/{session_id}").status_code == 200
        found = auth_client.get(f"{base}/assist/sessions", params={"mutation_id": "mut_x"})
        assert found.status_code == 200
        # Changes still need it.
        refused = auth_client.post(f"{base}/assist/sessions/{session_id}/close")
        assert refused.status_code == 403
    finally:
        auth_client.headers["X-CSRF-Token"] = token


def test_conversations_require_a_session(api_client) -> None:
    assert api_client.get("/api/v1/projects/any/assist/sessions").status_code == 401
