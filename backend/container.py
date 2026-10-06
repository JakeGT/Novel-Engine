from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.adapters.ai import OpenAIGateway
from backend.adapters.artifacts import FilesystemArtifactStore, NullArtifactStore
from backend.adapters.embedding import SentenceTransformerEmbedding
from backend.adapters.graph import Neo4jGraphRepository, Neo4jMigrationVerifier
from backend.adapters.postgres import PostgresDatabase
from backend.adapters.system import SystemClock, TimeOrderedIdGenerator
from backend.adapters.vector import PgVectorStore
from backend.core.config import Settings
from backend.core.config import settings as default_settings
from backend.graph.manager import GraphManager
from backend.graph.migrations import Neo4jMigrationRunner
from backend.graph.schema import SchemaRegistry
from backend.ports import (
    AIGateway,
    ArtifactStore,
    Clock,
    DatabasePort,
    EmbeddingPort,
    GraphRepository,
    IdGenerator,
    MigrationVerifier,
    SchemaRegistryPort,
    VectorStorePort,
)


@dataclass
class Container:
    settings: Settings
    graph: GraphRepository
    vectors: VectorStorePort
    embeddings: EmbeddingPort
    ai: AIGateway
    schema_registry: SchemaRegistryPort
    clock: Clock
    ids: IdGenerator
    artifacts: ArtifactStore
    neo4j_migrations: MigrationVerifier
    database: DatabasePort | None = None
    character_service: Any = None

    async def aclose(self) -> None:
        self.graph.close()
        await self.ai.aclose()
        if self.database is not None:
            await self.database.aclose()


def build_container(settings: Settings | None = None) -> Container:
    resolved = settings or default_settings
    manager = GraphManager(resolved)
    artifacts: ArtifactStore = (
        FilesystemArtifactStore(resolved.ARTIFACT_STORE_DIR)
        if resolved.ENABLE_DEBUG_ARTIFACTS
        else NullArtifactStore()
    )

    embeddings = SentenceTransformerEmbedding(
        model_name=resolved.EMBEDDING_MODEL_NAME,
        dimension=resolved.EMBEDDING_DIMENSION,
    )

    container = Container(
        settings=resolved,
        graph=Neo4jGraphRepository(manager),
        vectors=PgVectorStore(
            resolved.postgres_dsn_psycopg,
            embeddings,
            dimension=resolved.EMBEDDING_DIMENSION,
        ),
        embeddings=embeddings,
        ai=OpenAIGateway(api_key=resolved.OPENAI_API_KEY, default_model=resolved.AI_MODEL),
        schema_registry=SchemaRegistry(resolved.CUSTOM_SCHEMA_PATH),
        clock=SystemClock(),
        ids=TimeOrderedIdGenerator(),
        artifacts=artifacts,
        neo4j_migrations=Neo4jMigrationVerifier(Neo4jMigrationRunner(manager.driver)),
        database=PostgresDatabase(resolved),
    )

    from backend.services.character_service import CharacterService

    container.character_service = CharacterService(
        graph=container.graph,
        ai=container.ai,
        embeddings=container.embeddings,
        schema_registry=container.schema_registry,
        artifacts=container.artifacts,
    )
    return container
