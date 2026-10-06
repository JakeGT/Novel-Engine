from __future__ import annotations

from fastapi import Depends, Request

from backend.container import Container
from backend.ports import (
    AIGateway,
    EmbeddingPort,
    GraphRepository,
    SchemaRegistryPort,
    VectorStorePort,
)
from backend.services.character_service import CharacterService


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_graph(container: Container = Depends(get_container)) -> GraphRepository:
    return container.graph


def get_vectors(container: Container = Depends(get_container)) -> VectorStorePort:
    return container.vectors


def get_embeddings(container: Container = Depends(get_container)) -> EmbeddingPort:
    return container.embeddings


def get_ai(container: Container = Depends(get_container)) -> AIGateway:
    return container.ai


def get_schema_registry(container: Container = Depends(get_container)) -> SchemaRegistryPort:
    return container.schema_registry


def get_character_service(
    container: Container = Depends(get_container),
) -> CharacterService:
    return container.character_service
