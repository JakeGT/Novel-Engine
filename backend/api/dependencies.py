from __future__ import annotations

from typing import Any

from fastapi import Depends, Request

from backend.container import Container
from backend.core.config import Settings
from backend.ports import (
    AIGateway,
    EmbeddingPort,
    GraphRepository,
    SchemaRegistryPort,
    VectorStorePort,
)
from backend.services.auth_service import AuthService
from backend.services.canon_assertion_service import CanonAssertionService
from backend.services.canon_entity_service import CanonEntityService
from backend.services.character_service import CharacterService
from backend.services.credential_service import CredentialService
from backend.services.schema_service import SchemaService
from backend.services.tenancy_service import TenancyService
from backend.services.usage_service import ProviderUsageService


def get_container(request: Request) -> Container:
    return request.app.state.container


def get_settings(container: Container = Depends(get_container)) -> Settings:
    return container.settings


def get_auth_service(container: Container = Depends(get_container)) -> AuthService:
    return container.auth_service


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


def get_tenancy_service(container: Container = Depends(get_container)) -> TenancyService:
    return container.tenancy_service


def get_credential_service(
    container: Container = Depends(get_container),
) -> CredentialService:
    return container.credential_service


def get_usage_service(
    container: Container = Depends(get_container),
) -> ProviderUsageService:
    return container.usage_service


def get_schema_service(container: Container = Depends(get_container)) -> SchemaService:
    return container.schema_service


def get_canon_entity_service(
    container: Container = Depends(get_container),
) -> CanonEntityService:
    return container.canon_entity_service


def get_canon_assertion_service(
    container: Container = Depends(get_container),
) -> CanonAssertionService:
    return container.canon_assertion_service


def get_journal(container: Container = Depends(get_container)) -> Any:
    return container.journal


def get_projection_service(container: Container = Depends(get_container)) -> Any:
    return container.projection_service


def get_outbox_consumer(container: Container = Depends(get_container)) -> Any:
    return container.outbox_consumer


def get_canon_history_service(container: Container = Depends(get_container)) -> Any:
    return container.canon_history_service
