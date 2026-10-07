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

WS = "local-workspace"
PROJECT = "local-project"


def test_init_anchor_stores_node_with_embedding(fake_container) -> None:
    service = fake_container.character_service

    node = service.init_character_anchor(
        "Arin Vale", bio="A scout.", workspace_id=WS, project_id=PROJECT
    )

    assert node.id == "char_arin_vale"
    assert node.type == "Character"
    assert node.embedding is not None
    stored = fake_container.graph.get_node("char_arin_vale", workspace_id=WS, project_id=PROJECT)
    assert stored is not None
    assert stored.properties["description"] == "A scout."


@pytest.mark.asyncio
async def test_generate_questions_uses_injected_gateway(fake_container) -> None:
    service = fake_container.character_service
    service.init_character_anchor("Arin", bio="A scout.", workspace_id=WS, project_id=PROJECT)
    sheet = CharacterQuestionSheet(
        character_name="Arin",
        character_id="",
        questions=[CharacterQuestion(id="q1", question="Why?", category="motivation")],
    )
    fake_container.ai.queue(sheet)

    result = await service.generate_questions("char_arin", workspace_id=WS, project_id=PROJECT)

    assert result.character_id == "char_arin"
    assert result.questions[0].id == "q1"
    assert fake_container.ai.calls[0]["response_format"] is CharacterQuestionSheet


@pytest.mark.asyncio
async def test_process_answers_rewrites_ids_and_applies_updates(fake_container) -> None:
    service = fake_container.character_service
    service.init_character_anchor("Arin", bio="A scout.", workspace_id=WS, project_id=PROJECT)
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

    result = await service.process_answers(
        "char_arin", 2, {"Q": "A"}, workspace_id=WS, project_id=PROJECT
    )

    assert [node.id for node in result.nodes] == ["app_char_arin", "state_char_arin_ch2"]


@pytest.mark.asyncio
async def test_process_answers_rejects_edge_from_other_source(fake_container) -> None:
    service = fake_container.character_service
    service.init_character_anchor("Arin", bio="A scout.", workspace_id=WS, project_id=PROJECT)
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
        await service.process_answers(
            "char_arin", 1, {"Q": "A"}, workspace_id=WS, project_id=PROJECT
        )


@pytest.mark.asyncio
async def test_generate_questions_propagates_normalized_ai_error(fake_container) -> None:
    service = fake_container.character_service
    service.init_character_anchor("Arin", bio="A scout.", workspace_id=WS, project_id=PROJECT)
    fake_container.ai.queue(AIProviderError("provider unavailable"))

    with pytest.raises(AIProviderError):
        await service.generate_questions("char_arin", workspace_id=WS, project_id=PROJECT)


@pytest.mark.asyncio
async def test_process_answers_rewrites_edge_endpoints_via_id_map(fake_container) -> None:
    service = fake_container.character_service
    service.init_character_anchor("Arin", bio="A scout.", workspace_id=WS, project_id=PROJECT)
    fake_container.ai.queue(
        GraphUpdate(
            nodes=[NodeUpdate(id="shadow", type="Appearance", properties={})],
            edges=[
                EdgeUpdate(
                    source="char_arin",
                    target="shadow",
                    type="HAS_APPEARANCE",
                    properties={},
                )
            ],
        )
    )

    result = await service.process_answers(
        "char_arin", 1, {"Q": "A"}, workspace_id=WS, project_id=PROJECT
    )

    assert result.edges[0].target == "app_char_arin"


@pytest.mark.asyncio
async def test_process_answers_rejects_unknown_node_type(fake_container) -> None:
    service = fake_container.character_service
    service.init_character_anchor("Arin", bio="A scout.", workspace_id=WS, project_id=PROJECT)
    fake_container.ai.queue(
        GraphUpdate(nodes=[NodeUpdate(id="x", type="Alien", properties={})], edges=[])
    )

    with pytest.raises(ValueError, match="Unknown node type"):
        await service.process_answers(
            "char_arin", 1, {"Q": "A"}, workspace_id=WS, project_id=PROJECT
        )


@pytest.mark.asyncio
async def test_process_answers_rejects_duplicate_normalized_ids(fake_container) -> None:
    service = fake_container.character_service
    service.init_character_anchor("Arin", bio="A scout.", workspace_id=WS, project_id=PROJECT)
    fake_container.ai.queue(
        GraphUpdate(
            nodes=[
                NodeUpdate(id="a", type="Appearance", properties={}),
                NodeUpdate(id="b", type="Appearance", properties={}),
            ],
            edges=[],
        )
    )

    with pytest.raises(ValueError, match="Duplicate"):
        await service.process_answers(
            "char_arin", 1, {"Q": "A"}, workspace_id=WS, project_id=PROJECT
        )


@pytest.mark.asyncio
async def test_process_answers_rejects_missing_edge_target(fake_container) -> None:
    service = fake_container.character_service
    service.init_character_anchor("Arin", bio="A scout.", workspace_id=WS, project_id=PROJECT)
    fake_container.ai.queue(
        GraphUpdate(
            nodes=[NodeUpdate(id="x", type="Appearance", properties={})],
            edges=[
                EdgeUpdate(
                    source="char_arin",
                    target="ghost",
                    type="HAS_APPEARANCE",
                    properties={},
                )
            ],
        )
    )

    with pytest.raises(ValueError, match="Edge target"):
        await service.process_answers(
            "char_arin", 1, {"Q": "A"}, workspace_id=WS, project_id=PROJECT
        )


@pytest.mark.asyncio
async def test_generate_questions_threads_credential_and_records_usage(fake_container) -> None:
    service = fake_container.character_service
    service.init_character_anchor("Arin", bio="A scout.", workspace_id=WS, project_id=PROJECT)
    fake_container.ai.queue(
        CharacterQuestionSheet(
            character_name="Arin",
            character_id="",
            questions=[CharacterQuestion(id="q1", question="Why?", category="motivation")],
        )
    )
    seen: list[tuple[object, str]] = []

    async def sink(usage, model) -> None:
        seen.append((usage, model))

    await service.generate_questions(
        "char_arin",
        workspace_id=WS,
        project_id=PROJECT,
        credential="sk-user-key",
        usage_sink=sink,
    )

    assert fake_container.ai.calls[0]["api_key"] == "sk-user-key"
    assert len(seen) == 1
