"""Shared deterministic fixtures for the Phase 0 test harness."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from backend.core.config import Settings
from tests.doubles import FakeClock, OrderedIdGenerator, ScriptedAIClient


@pytest.fixture
def fixed_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def id_generator() -> OrderedIdGenerator:
    return OrderedIdGenerator()


@pytest.fixture
def settings_factory() -> Callable[..., Settings]:
    def _factory(**overrides: Any) -> Settings:
        values: dict[str, Any] = {
            "OPENAI_API_KEY": "",
            "NEO4J_URI": "bolt://localhost:7687",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASSWORD": "password",
        }
        values.update(overrides)
        return Settings(_env_file=None, **values)

    return _factory


@pytest.fixture
def scripted_ai() -> Callable[[list[Any]], ScriptedAIClient]:
    def _factory(responses: list[Any] | None = None) -> ScriptedAIClient:
        return ScriptedAIClient(responses)

    return _factory


@pytest.fixture
def fake_container(settings_factory, tmp_path):
    from backend.adapters.artifacts import NullArtifactStore
    from backend.container import Container
    from backend.graph.schema import SchemaRegistry
    from backend.services.character_service import CharacterService
    from tests.doubles import (
        FakeClock,
        FakeDatabase,
        FakeEmbeddingProvider,
        FakeMigrationVerifier,
        InMemoryGraphRepository,
        InMemoryVectorStore,
        OrderedIdGenerator,
        ScriptedAIGateway,
    )

    settings = settings_factory(APP_ENV="test")
    graph = InMemoryGraphRepository()
    vectors = InMemoryVectorStore()
    embeddings = FakeEmbeddingProvider()
    ai = ScriptedAIGateway()
    registry = SchemaRegistry(config_path=str(tmp_path / "custom_schema.json"))
    artifacts = NullArtifactStore()
    service = CharacterService(
        graph=graph,
        ai=ai,
        embeddings=embeddings,
        schema_registry=registry,
        artifacts=artifacts,
    )
    return Container(
        settings=settings,
        graph=graph,
        vectors=vectors,
        embeddings=embeddings,
        ai=ai,
        schema_registry=registry,
        clock=FakeClock(),
        ids=OrderedIdGenerator(),
        artifacts=artifacts,
        neo4j_migrations=FakeMigrationVerifier(current=True),
        database=FakeDatabase(ready=True),
        character_service=service,
    )
