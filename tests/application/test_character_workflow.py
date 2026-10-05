import pytest

from backend.ports import AIProviderError
from backend.services.character_service import (
    CharacterQuestion,
    CharacterQuestionSheet,
    EdgeUpdate,
    GraphUpdate,
    NodeUpdate,
)

pytestmark = pytest.mark.application


def test_init_anchor_stores_node_with_embedding(fake_container) -> None:
    service = fake_container.character_service

    node = service.init_character_anchor("Arin Vale", bio="A scout.")

    assert node.id == "char_arin_vale"
    assert node.type == "Character"
    assert node.embedding is not None
    stored = fake_container.graph.get_node("char_arin_vale")
    assert stored is not None
    assert stored.properties["description"] == "A scout."


@pytest.mark.asyncio
async def test_generate_questions_uses_injected_gateway(fake_container) -> None:
    service = fake_container.character_service
    service.init_character_anchor("Arin", bio="A scout.")
    sheet = CharacterQuestionSheet(
        character_name="Arin",
        character_id="",
        questions=[CharacterQuestion(id="q1", question="Why?", category="motivation")],
    )
    fake_container.ai.queue(sheet)

    result = await service.generate_questions("char_arin")

    assert result.character_id == "char_arin"
    assert result.questions[0].id == "q1"
    assert fake_container.ai.calls[0]["response_format"] is CharacterQuestionSheet


@pytest.mark.asyncio
async def test_process_answers_rewrites_ids_and_applies_updates(fake_container) -> None:
    service = fake_container.character_service
    service.init_character_anchor("Arin", bio="A scout.")
    fake_container.ai.queue(
        GraphUpdate(
            nodes=[
                NodeUpdate(id="ignored", type="Appearance", properties={"hair": "black"}),
                NodeUpdate(id="ignored", type="State", properties={"mood": "wary"}),
            ],
            edges=[
                EdgeUpdate(
                    source="char_arin",
                    target="app_char_arin",
                    type="HAS_APPEARANCE",
                    properties={},
                )
            ],
        )
    )

    result = await service.process_answers("char_arin", 2, {"Q": "A"})

    assert [node.id for node in result.nodes] == ["app_char_arin", "state_char_arin_ch2"]


@pytest.mark.asyncio
async def test_process_answers_rejects_edge_from_other_source(fake_container) -> None:
    service = fake_container.character_service
    service.init_character_anchor("Arin", bio="A scout.")
    fake_container.ai.queue(
        GraphUpdate(
            nodes=[NodeUpdate(id="x", type="Appearance", properties={})],
            edges=[
                EdgeUpdate(
                    source="char_other",
                    target="app_char_arin",
                    type="HAS_APPEARANCE",
                    properties={},
                )
            ],
        )
    )

    with pytest.raises(ValueError):
        await service.process_answers("char_arin", 1, {"Q": "A"})


@pytest.mark.asyncio
async def test_generate_questions_propagates_normalized_ai_error(fake_container) -> None:
    service = fake_container.character_service
    service.init_character_anchor("Arin", bio="A scout.")
    fake_container.ai.queue(AIProviderError("provider unavailable"))

    with pytest.raises(AIProviderError):
        await service.generate_questions("char_arin")
