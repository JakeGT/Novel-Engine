import pytest

from backend.services.character_service import (
    CharacterQuestion,
    CharacterQuestionSheet,
    GraphUpdate,
    NodeUpdate,
)

pytestmark = pytest.mark.contract


def test_character_init(auth_client) -> None:
    response = auth_client.post("/api/v1/characters/init", json={"name": "Arin", "bio": "A scout."})

    assert response.status_code == 201
    assert response.json()["id"] == "char_arin"
    assert response.json()["type"] == "Character"


def test_character_generate_questions(auth_client, fake_container) -> None:
    auth_client.post("/api/v1/characters/init", json={"name": "Arin", "bio": "A scout."})
    fake_container.ai.queue(
        CharacterQuestionSheet(
            character_name="Arin",
            character_id="",
            questions=[CharacterQuestion(id="q1", question="Why?", category="motivation")],
        )
    )

    response = auth_client.post("/api/v1/characters/generate-questions", json={"id": "char_arin"})

    assert response.status_code == 200
    body = response.json()
    assert body["character_id"] == "char_arin"
    assert body["questions"][0]["id"] == "q1"


def test_character_generate_questions_missing_character(auth_client) -> None:
    response = auth_client.post("/api/v1/characters/generate-questions", json={"id": "ghost"})

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_character_process_answers_writes_graph(auth_client, fake_container) -> None:
    auth_client.post("/api/v1/characters/init", json={"name": "Arin", "bio": "A scout."})
    fake_container.ai.queue(
        GraphUpdate(nodes=[NodeUpdate(id="x", type="Appearance", properties={})], edges=[])
    )

    response = auth_client.post(
        "/api/v1/characters/process-answers",
        json={"id": "char_arin", "chapter": 1, "answers": {"Q": "A"}},
    )

    assert response.status_code == 200
    assert response.json()["nodes"][0]["id"] == "app_char_arin"
    assert (
        fake_container.graph.get_node("app_char_arin", workspace_id="local-workspace") is not None
    )
