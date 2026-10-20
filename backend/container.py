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
    schema_versions: Any = None
    schema_service: Any = None
    journal: Any = None
    canon_writer: Any = None
    canon_repository: Any = None
    mutation_service: Any = None
    projection_service: Any = None
    outbox_consumer: Any = None
    canon_entity_service: Any = None
    plan_service: Any = None
    sources: Any = None
    source_service: Any = None
    rules: Any = None
    rule_service: Any = None
    canon_assertion_service: Any = None
    canon_history_service: Any = None
    story_clocks: Any = None
    story_time_service: Any = None
    what_if_service: Any = None
    knowledge_service: Any = None
    passage_store: Any = None
    manifests: Any = None
    passage_index: Any = None
    context_service: Any = None
    jobs: Any = None
    job_runner: Any = None
    projection_worker: Any = None
    studio_store: Any = None
    studio_service: Any = None
    # Checks kept as runs (CON-001).
    consistency_run_store: Any = None
    consistency_runs: Any = None
    scene_reading: Any = None
    scene_plan_card: Any = None
    media_store: Any = None
    archive_service: Any = None
    media_service: Any = None
    # What a model saw in pictures, reviewed; pictures in context (AST-005, CTX-013).
    observation_store: Any = None
    media_observations: Any = None
    # Picture processing: its state store and the limited decoder (AST-003, AST-006).
    media_processing: Any = None
    image_processor: Any = None
    equivalence_service: Any = None
    canon_character_workflow: Any = None
    assist_sessions: Any = None
    assist_session_service: Any = None
    ai_settings: Any = None
    allowances: Any = None
    # How much the assistant may do and spend on its own (CON-009, OPS-002, AST-EXP-011).
    assistant_settings_store: Any = None
    assistant_settings: Any = None
    assist_budget: Any = None
    # Keep the safe ones for me: the author's rules and what was kept (AST-EXP-015).
    delegation_store: Any = None
    delegation_service: Any = None
    graph_upgrade_service: Any = None
    # Personal access keys for other assistants, and the MCP endpoint's check (QRY-003).
    access_keys: Any = None
    access_key_service: Any = None
    # Search by meaning in model generations (CTX-011/CTX-012): the typed embedder, the
    # generation store, building/switching, and picture search for the context.
    multimodal_embedder: Any = None
    embedding_index: Any = None
    embedding_index_service: Any = None
    media_search: Any = None

    async def aclose(self) -> None:
        if self.job_runner is not None:
            await self.job_runner.stop()
        if self.projection_worker is not None:
            await self.projection_worker.stop()
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
    from backend.adapters.embedding import build_multimodal_embedder
    from backend.adapters.embedding_generations import PgEmbeddingIndexStore

    multimodal_embedder = build_multimodal_embedder(resolved, embeddings)
    embedding_index = PgEmbeddingIndexStore(resolved.postgres_dsn_psycopg)
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
            multimodal_embedder,
            # EMBEDDING_DIMENSION describes the MiniLM text model only.
            dimension=resolved.EMBEDDING_DIMENSION
            if resolved.EMBEDDING_PROVIDER == "minilm"
            else None,
            index=embedding_index,
        ),
        embeddings=embeddings,
        ai=OpenAIGateway(
            api_key=resolved.OPENAI_API_KEY,
            default_model=resolved.AI_MODEL,
            provider=resolved.AI_PROVIDER,
            base_url=resolved.AI_BASE_URL,
            image_input=resolved.AI_IMAGE_INPUT,
            image_max_count=resolved.AI_IMAGE_MAX_COUNT,
            image_max_bytes=resolved.AI_IMAGE_MAX_BYTES,
            image_types=frozenset(
                part.strip() for part in resolved.AI_IMAGE_TYPES.split(",") if part.strip()
            ),
            image_providers=frozenset(
                part.strip() for part in resolved.AI_IMAGE_PROVIDERS.split(",") if part.strip()
            ),
        ),
        schema_registry=SchemaRegistry(resolved.CUSTOM_SCHEMA_PATH),
        clock=SystemClock(),
        ids=TimeOrderedIdGenerator(),
        artifacts=artifacts,
        objects=objects,
        neo4j_migrations=Neo4jMigrationVerifier(Neo4jMigrationRunner(manager.driver)),
        database=database,
        identity_provider=identity_provider,
        multimodal_embedder=multimodal_embedder,
        embedding_index=embedding_index,
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

    from backend.adapters.assist_store import PostgresAssistSessionStore

    container.assist_sessions = PostgresAssistSessionStore(database.session_factory)

    from backend.adapters.ai_settings_store import PostgresAiSettingsStore

    container.ai_settings = PostgresAiSettingsStore(database.session_factory)
    from backend.adapters.allowance_store import PostgresAllowanceStore

    container.allowances = PostgresAllowanceStore(database.session_factory)

    from backend.adapters.source_store import PostgresSourceStore

    container.sources = PostgresSourceStore(database.session_factory)
    from backend.adapters.rule_store import PostgresRuleStore

    container.rules = PostgresRuleStore(database.session_factory)

    from backend.adapters.purge import StoreWorkspacePurge

    purger = StoreWorkspacePurge(
        graph=container.graph,
        vectors=container.vectors,
        credentials=container.credentials,
        usage=container.usage,
        objects=container.objects,
        artifacts=container.artifacts,
        assist=container.assist_sessions,
        ai_settings=container.ai_settings,
        allowances=container.allowances,
        sources=container.sources,
        rules=container.rules,
    )
    container.tenancy_service = TenancyService(
        container.tenancy,
        identity_store=identity_store,
        session_store=session_store,
        rate_limiter=container.rate_limiter,
        settings=resolved,
        purger=purger,
    )
    from backend.adapters.access_key_store import PostgresAccessKeyStore
    from backend.services.access_key_service import AccessKeyService

    container.access_keys = PostgresAccessKeyStore(database.session_factory)
    purger.attach_access_keys(container.access_keys)
    container.access_key_service = AccessKeyService(
        container.access_keys,
        tenancy=container.tenancy_service,
        identity_store=identity_store,
        rate_limiter=container.rate_limiter,
        settings=resolved,
    )
    container.credential_service = CredentialService(
        container.credentials,
        tenancy=container.tenancy_service,
        ai_settings=container.ai_settings,
        ai=container.ai,
        # AI_MODEL has always been the hosted OpenRouter model; it stays its suggestion.
        default_models={"openrouter": resolved.AI_MODEL} if resolved.is_hosted else {},
    )
    container.usage_service = ProviderUsageService(
        container.usage,
        allowances=container.allowances,
        limit=UsageLimit(
            per_job_token_limit=resolved.PROVIDER_PER_JOB_TOKEN_LIMIT,
            per_job_cost_limit=resolved.PROVIDER_PER_JOB_COST_LIMIT,
            period_seconds=resolved.PROVIDER_PERIOD_SECONDS,
            period_token_limit=resolved.PROVIDER_PERIOD_TOKEN_LIMIT,
            period_cost_limit=resolved.PROVIDER_PERIOD_COST_LIMIT,
        ),
    )

    from backend.adapters.schema_store import PostgresSchemaVersionStore
    from backend.services.schema_service import SchemaService

    container.schema_versions = PostgresSchemaVersionStore(database.session_factory)
    container.schema_service = SchemaService(
        container.schema_versions,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
    )
    from backend.adapters.journal import PostgresCanonJournal

    container.journal = PostgresCanonJournal(database.session_factory)

    from backend.adapters.canon_repository import PostgresCanonRepository

    container.canon_repository = PostgresCanonRepository(database.session_factory)

    from backend.adapters.canon_writer import Neo4jCanonWriter
    from backend.adapters.media_store import PostgresMediaStore
    from backend.services.mutation_service import MutationService

    container.canon_writer = Neo4jCanonWriter(manager)
    # Project/workspace deletion must also erase canon and its projection.
    purger.attach_canon(canon=container.canon_repository, canon_writer=container.canon_writer)
    container.media_store = PostgresMediaStore(database.session_factory)
    from backend.domain.validation import AuthoritativeValidator
    from backend.services.rule_service import RuleService

    container.rule_service = RuleService(
        container.rules,
        reader=container.canon_repository,
        schema_store=container.schema_versions,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
    )
    container.mutation_service = MutationService(
        container.journal,
        container.canon_repository,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
        schema_store=container.schema_versions,
        media=container.media_store,
        # The author's own story rules: they can warn about a change, never stop it.
        validator=AuthoritativeValidator(evaluators=[container.rule_service]),
    )
    from backend.services.canon_entity_service import CanonEntityService

    container.canon_entity_service = CanonEntityService(
        mutation=container.mutation_service,
        reader=container.canon_repository,
        schema_store=container.schema_versions,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
    )
    from backend.services.plan_service import PlanService

    container.plan_service = PlanService(
        mutation=container.mutation_service,
        reader=container.canon_repository,
        schema_store=container.schema_versions,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
    )
    from backend.services.source_service import SourceService

    container.source_service = SourceService(
        container.sources,
        reader=container.canon_repository,
        entities=container.canon_entity_service,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
    )
    from backend.services.canon_assertion_service import CanonAssertionService

    container.canon_assertion_service = CanonAssertionService(
        mutation=container.mutation_service,
        reader=container.canon_repository,
        schema_store=container.schema_versions,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
    )
    from backend.adapters.story_clock import PostgresStoryClockStore
    from backend.services.story_time_service import StoryTimeService
    from backend.services.what_if_service import WhatIfService

    container.story_clocks = PostgresStoryClockStore(database.session_factory)
    container.plan_service.clocks = container.story_clocks
    container.story_time_service = StoryTimeService(
        reader=container.canon_repository,
        clocks=container.story_clocks,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
    )
    from backend.services.knowledge_service import KnowledgeService

    container.knowledge_service = KnowledgeService(
        reader=container.canon_repository,
        mutation=container.mutation_service,
        schema_store=container.schema_versions,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
        journal=container.journal,
        clocks=container.story_clocks,
    )
    from backend.adapters.passage_store import PostgresManifestStore, PostgresPassageStore
    from backend.services.context_service import ContextService
    from backend.services.passage_index import PassageIndex

    container.passage_store = PostgresPassageStore(database.session_factory)
    container.manifests = PostgresManifestStore(database.session_factory)
    container.passage_index = PassageIndex(
        store=container.passage_store,
        sources=container.source_service,
        reader=container.canon_repository,
        journal=container.journal,
        # Whether passages are embedded follows the typed embedder (CTX-011).
        embeddings=container.multimodal_embedder,
        vectors=container.vectors,
    )
    container.source_service.passages = container.passage_index
    container.context_service = ContextService(
        reader=container.canon_repository,
        plan=container.plan_service,
        rules=container.rules,
        sources=container.source_service,
        passages=container.passage_index,
        passage_store=container.passage_store,
        manifests=container.manifests,
        clocks=container.story_clocks,
        tenancy=container.tenancy_service,
        journal=container.journal,
        local_mode=not resolved.is_hosted,
    )
    from backend.adapters.job_store import PostgresJobStore
    from backend.adapters.studio_store import PostgresStudioStore
    from backend.services.job_runner import JobRunner
    from backend.services.studio_service import StudioService

    container.jobs = PostgresJobStore(database.session_factory)
    container.job_runner = JobRunner(
        store=container.jobs,
        journal=container.journal,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
    )
    container.studio_store = PostgresStudioStore(database.session_factory)
    container.context_service.studio = container.studio_store
    container.studio_service = StudioService(
        store=container.studio_store,
        sources=container.source_service,
        plan=container.plan_service,
        context=container.context_service,
        reader=container.canon_repository,
        journal=container.journal,
        jobs=container.jobs,
        runner=container.job_runner,
        ai=container.ai,
        settings=resolved,
        credentials=container.credential_service,
        usage=container.usage_service,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
    )
    container.studio_service.register_jobs()
    from backend.adapters.consistency_run_store import PostgresConsistencyRunStore
    from backend.services.consistency_runs import ConsistencyRunService

    container.consistency_run_store = PostgresConsistencyRunStore(database.session_factory)
    container.consistency_runs = ConsistencyRunService(
        store=container.consistency_run_store, studio=container.studio_service
    )
    container.studio_service.runs = container.consistency_runs
    # Which scene a text holds, so names are read as that scene reads them (CTX-016).
    container.source_service.studio = container.studio_service
    container.what_if_service = WhatIfService(
        journal=container.journal,
        canon=container.canon_repository,
        mutation=container.mutation_service,
        schema_store=container.schema_versions,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
    )
    from backend.services.projection_service import GraphProjectionService

    container.projection_service = GraphProjectionService(
        canon=container.canon_repository, writer=container.canon_writer
    )
    from backend.services.outbox_consumer import OutboxConsumer

    container.outbox_consumer = OutboxConsumer(
        journal=container.journal, projector=container.projection_service
    )
    from backend.services.projection_worker import ProjectionWorker

    container.projection_worker = ProjectionWorker(
        journal=container.journal, consumer=container.outbox_consumer
    )
    from backend.services.canon_history_service import CanonHistoryService

    container.canon_history_service = CanonHistoryService(
        canon=container.canon_repository,
        journal=container.journal,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
        mutation=container.mutation_service,
    )
    from backend.adapters.delegation_store import PostgresDelegationStore
    from backend.services.delegation_service import DelegationService

    container.delegation_store = PostgresDelegationStore(database.session_factory)
    container.delegation_service = DelegationService(
        container.delegation_store,
        mutation=container.mutation_service,
        reader=container.canon_repository,
        history=container.canon_history_service,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
    )
    container.mutation_service.delegation = container.delegation_service
    from datetime import timedelta

    from backend.adapters.image_processor import ProcessorLimits, SubprocessImageProcessor
    from backend.adapters.media_store import PostgresMediaProcessingStore
    from backend.services.media_service import MAX_PICTURE_BYTES, MediaService

    container.media_processing = PostgresMediaProcessingStore(database.session_factory)
    if resolved.MEDIA_PROCESSING_ENABLED:
        container.image_processor = SubprocessImageProcessor(
            ProcessorLimits(
                max_bytes=MAX_PICTURE_BYTES,
                max_pixels=resolved.MEDIA_MAX_PIXELS,
                max_dimension=resolved.MEDIA_MAX_DIMENSION,
                memory_bytes=resolved.MEDIA_WORKER_MEMORY_BYTES,
                cpu_seconds=resolved.MEDIA_WORKER_CPU_SECONDS,
                timeout_seconds=resolved.MEDIA_WORKER_TIMEOUT_SECONDS,
            ),
            concurrency=resolved.MEDIA_WORKER_CONCURRENCY,
        )
    container.media_service = MediaService(
        media=container.media_store,
        objects=container.objects,
        mutation=container.mutation_service,
        reader=container.canon_repository,
        journal=container.journal,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
        processing=container.media_processing,
        processor=container.image_processor,
        runner=container.job_runner,
        quota_bytes=resolved.MEDIA_WORKSPACE_QUOTA_BYTES,
        orphan_grace=timedelta(hours=max(1, resolved.MEDIA_ORPHAN_GRACE_HOURS)),
    )
    container.media_service.register_jobs()
    from backend.adapters.observation_store import PostgresObservationStore
    from backend.services.media_observations import MediaObservationService

    container.observation_store = PostgresObservationStore(database.session_factory)
    container.media_observations = MediaObservationService(
        store=container.observation_store,
        media=container.media_service,
        reader=container.canon_repository,
        mutation=container.mutation_service,
        journal=container.journal,
        runner=container.job_runner,
        ai=container.ai,
        settings=resolved,
        credentials=container.credential_service,
        usage=container.usage_service,
        studio=container.studio_service,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
    )
    container.media_observations.register_jobs()
    container.context_service.pictures = container.media_observations
    from backend.services.rule_workflow import RuleWorkflow

    container.canon_character_workflow = RuleWorkflow(
        ai=container.ai,
        mutation=container.mutation_service,
        reader=container.canon_repository,
        schema_store=container.schema_versions,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
        model=resolved.AI_MODEL,
        clocks=container.story_clocks,
        passages=container.passage_store,
    )
    from backend.services.scene_reading import SceneReadingService

    container.scene_reading = SceneReadingService(
        studio=container.studio_service,
        store=container.studio_store,
        sources=container.source_service,
        workflow=container.canon_character_workflow,
        canon=container.canon_repository,
        journal=container.journal,
        runner=container.job_runner,
        settings=resolved,
        credentials=container.credential_service,
        usage=container.usage_service,
        jobs=container.jobs,
    )
    container.scene_reading.register_jobs()
    from backend.services.scene_plan_card import ScenePlanCardService

    container.scene_plan_card = ScenePlanCardService(
        studio=container.studio_service,
        sources=container.source_service,
        mutation=container.mutation_service,
        canon=container.canon_repository,
        journal=container.journal,
        runner=container.job_runner,
        ai=container.ai,
        settings=resolved,
        credentials=container.credential_service,
        usage=container.usage_service,
    )
    container.scene_plan_card.register_jobs()
    from backend.services.assist_session_service import AssistSessionService

    container.assist_session_service = AssistSessionService(
        container.assist_sessions,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
    )
    from backend.adapters.assistant_settings_store import PostgresAssistantSettingsStore
    from backend.services.assistant_settings_service import AssistantSettingsService, AssistBudget

    container.assistant_settings_store = PostgresAssistantSettingsStore(database.session_factory)
    container.assistant_settings = AssistantSettingsService(
        container.assistant_settings_store,
        tenancy=container.tenancy_service,
        local_mode=not resolved.is_hosted,
    )
    container.assist_budget = AssistBudget(container.assistant_settings, container.assist_sessions)
    container.studio_service.controls = container.assistant_settings
    from backend.services.graph_archive_upgrade import GraphArchiveUpgradeService

    container.graph_upgrade_service = GraphArchiveUpgradeService(
        mutation=container.mutation_service,
        reader=container.canon_repository,
        schema_store=container.schema_versions,
    )
    from backend.services.canon_equivalence import CanonEquivalenceService

    container.equivalence_service = CanonEquivalenceService(
        canon=container.canon_repository,
        journal=container.journal,
        writer=container.canon_writer,
    )
    from backend.services.canon_archive import CanonArchiveService

    container.archive_service = CanonArchiveService(
        canon=container.canon_repository,
        journal=container.journal,
        schemas=container.schema_versions,
        media=container.media_store,
        objects=container.objects,
        projector=container.projection_service,
        equivalence=container.equivalence_service,
        tenancy=container.tenancy_service,
        audit=container.tenancy,
        canon_writer=container.canon_writer,
        local_mode=not resolved.is_hosted,
        sources=container.sources,
        rules=container.rules,
        clocks=container.story_clocks,
        studio=container.studio_store,
        assists=container.assist_sessions,
    )
    # Smaller copies of pictures stay out of an archive; a restore queues them again.
    container.archive_service.pictures = container.media_service
    container.archive_service.picture_notes = container.media_observations
    from backend.services.embedding_index import (
        EmbeddingIndexService,
        wire_media,
        wire_observations,
    )
    from backend.services.media_search import MediaSearchService

    container.embedding_index_service = EmbeddingIndexService(
        index=container.embedding_index,
        embedder=container.multimodal_embedder,
        objects=container.objects,
        passages=container.passage_index,
        runner=container.job_runner,
    )
    container.embedding_index_service.register_jobs()
    container.media_search = MediaSearchService(
        index=container.embedding_index, embedder=container.multimodal_embedder
    )
    wire_media(container.media_service, container.embedding_index_service)
    # Reviewed captions, and notes an archive restores, reach the index at once.
    wire_observations(container.media_observations, container.embedding_index_service)
    # Pictures found by meaning join the scene's context after the linked ones (CTX-013).
    container.context_service.search = container.media_search
    return container
