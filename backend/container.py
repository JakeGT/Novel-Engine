from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.adapters.ai import OpenAIGateway
from backend.adapters.artifacts import FilesystemArtifactStore, NullArtifactStore
from backend.adapters.embedding import SentenceTransformerEmbedding
from backend.adapters.graph import Neo4jGraphRepository, Neo4jMigrationVerifier
from backend.adapters.oidc import OIDCIdentityProvider
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
    objects: Any = None
    character_service: Any = None
    identity_provider: Any = None
    auth_service: Any = None
    tenancy: Any = None
    tenancy_service: Any = None
    rate_limiter: Any = None
    credentials: Any = None
    credential_service: Any = None
    usage: Any = None
    usage_service: Any = None

    async def aclose(self) -> None:
        self.graph.close()
        await self.ai.aclose()
        if self.identity_provider is not None:
            await self.identity_provider.aclose()
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
    from backend.adapters.objects import FilesystemObjectStore

    objects = FilesystemObjectStore(resolved.OBJECT_STORE_DIR)

    embeddings = SentenceTransformerEmbedding(
        model_name=resolved.EMBEDDING_MODEL_NAME,
        dimension=resolved.EMBEDDING_DIMENSION,
    )
    database = PostgresDatabase(resolved)
    identity_provider = OIDCIdentityProvider(
        issuer=resolved.OIDC_ISSUER,
        client_id=resolved.OIDC_CLIENT_ID,
        client_secret=resolved.OIDC_CLIENT_SECRET,
        scopes=resolved.oidc_scopes,
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
        ai=OpenAIGateway(
            api_key=resolved.OPENAI_API_KEY,
            default_model=resolved.AI_MODEL,
            provider=resolved.AI_PROVIDER,
            base_url=resolved.AI_BASE_URL,
        ),
        schema_registry=SchemaRegistry(resolved.CUSTOM_SCHEMA_PATH),
        clock=SystemClock(),
        ids=TimeOrderedIdGenerator(),
        artifacts=artifacts,
        objects=objects,
        neo4j_migrations=Neo4jMigrationVerifier(Neo4jMigrationRunner(manager.driver)),
        database=database,
        identity_provider=identity_provider,
    )

    from backend.adapters.identity_store import (
        PostgresIdentityStore,
        PostgresLoginAttemptStore,
        PostgresSessionStore,
    )
    from backend.adapters.tenancy_store import PostgresTenancyStore
    from backend.services.auth_service import AuthService
    from backend.services.character_service import CharacterService
    from backend.services.tenancy_service import TenancyService

    identity_store = PostgresIdentityStore(database.session_factory)
    session_store = PostgresSessionStore(database.session_factory)
    container.character_service = CharacterService(
        graph=container.graph,
        ai=container.ai,
        embeddings=container.embeddings,
        schema_registry=container.schema_registry,
        artifacts=container.artifacts,
    )
    container.auth_service = AuthService(
        settings=resolved,
        identity_provider=identity_provider,
        identity_store=identity_store,
        session_store=session_store,
        login_attempt_store=PostgresLoginAttemptStore(database.session_factory),
    )
    container.tenancy = PostgresTenancyStore(database.session_factory)
    from backend.adapters.rate_limit import PostgresRateLimiter

    container.rate_limiter = PostgresRateLimiter(database.session_factory)

    from backend.adapters.credentials import PostgresCredentialVault
    from backend.adapters.usage import PostgresProviderUsageStore
    from backend.domain.usage import UsageLimit
    from backend.services.credential_service import CredentialService
    from backend.services.usage_service import ProviderUsageService

    container.credentials = PostgresCredentialVault(
        database.session_factory,
        kek=resolved.CREDENTIAL_KEK,
        key_version=resolved.CREDENTIAL_KEK_VERSION,
        previous_keks=resolved.previous_credential_keks,
    )
    container.usage = PostgresProviderUsageStore(database.session_factory)

    from backend.adapters.purge import StoreWorkspacePurge

    container.tenancy_service = TenancyService(
        container.tenancy,
        identity_store=identity_store,
        session_store=session_store,
        rate_limiter=container.rate_limiter,
        settings=resolved,
        purger=StoreWorkspacePurge(
            graph=container.graph,
            vectors=container.vectors,
            credentials=container.credentials,
            usage=container.usage,
            objects=container.objects,
            artifacts=container.artifacts,
        ),
    )
    container.credential_service = CredentialService(
        container.credentials, tenancy=container.tenancy_service
    )
    container.usage_service = ProviderUsageService(
        container.usage,
        limit=UsageLimit(
            per_job_token_limit=resolved.PROVIDER_PER_JOB_TOKEN_LIMIT,
            per_job_cost_limit=resolved.PROVIDER_PER_JOB_COST_LIMIT,
            period_seconds=resolved.PROVIDER_PERIOD_SECONDS,
            period_token_limit=resolved.PROVIDER_PERIOD_TOKEN_LIMIT,
            period_cost_limit=resolved.PROVIDER_PERIOD_COST_LIMIT,
        ),
    )
    return container
