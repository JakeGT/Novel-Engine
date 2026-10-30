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


HOSTED_ISSUER = "https://idp.test/application/o/novel-engine/"


def _build_fake_container(settings, tmp_path):
    from backend.adapters.artifacts import NullArtifactStore
    from backend.container import Container
    from backend.domain.usage import UsageLimit
    from backend.graph.schema import SchemaRegistry
    from backend.services.auth_service import AuthService
    from backend.services.character_service import CharacterService
    from backend.services.credential_service import CredentialService
    from backend.services.mutation_service import MutationService
    from backend.services.tenancy_service import TenancyService
    from backend.services.usage_service import ProviderUsageService
    from tests.doubles import (
        FakeClock,
        FakeDatabase,
        FakeEmbeddingProvider,
        FakeIdentityProvider,
        FakeMigrationVerifier,
        InMemoryAiSettingsStore,
        InMemoryAllowanceStore,
        InMemoryAssistSessionStore,
        InMemoryCanonJournal,
        InMemoryCanonRepository,
        InMemoryCanonWriter,
        InMemoryCredentialVault,
        InMemoryGraphRepository,
        InMemoryIdentityStore,
        InMemoryLoginAttemptStore,
        InMemoryManifestStore,
        InMemoryMediaStore,
        InMemoryObjectStore,
        InMemoryPassageQueue,
        InMemoryPassageStore,
        InMemoryProviderUsageStore,
        InMemoryRateLimiter,
        InMemoryRuleStore,
        InMemorySchemaVersionStore,
        InMemorySessionStore,
        InMemorySourceStore,
        InMemoryStoryClockStore,
        InMemoryTenancyStore,
        InMemoryVectorStore,
        OrderedIdGenerator,
        ScriptedAIGateway,
    )

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
    identity_provider = FakeIdentityProvider(issuer=settings.OIDC_ISSUER or HOSTED_ISSUER)
    identity_store = InMemoryIdentityStore()
    session_store = InMemorySessionStore()
    auth_service = AuthService(
        settings=settings,
        identity_provider=identity_provider,
        identity_store=identity_store,
        session_store=session_store,
        login_attempt_store=InMemoryLoginAttemptStore(),
    )
    tenancy = InMemoryTenancyStore()
    rate_limiter = InMemoryRateLimiter()
    vault = InMemoryCredentialVault()
    usage_store = InMemoryProviderUsageStore()
    from backend.adapters.purge import StoreWorkspacePurge

    objects = InMemoryObjectStore()
    assist_sessions = InMemoryAssistSessionStore()
    ai_settings = InMemoryAiSettingsStore()
    allowances = InMemoryAllowanceStore()
    sources = InMemorySourceStore()
    rules = InMemoryRuleStore()
    purger = StoreWorkspacePurge(
        graph=graph,
        vectors=vectors,
        credentials=vault,
        usage=usage_store,
        objects=objects,
        artifacts=artifacts,
        assist=assist_sessions,
        ai_settings=ai_settings,
        allowances=allowances,
        sources=sources,
        rules=rules,
    )
    tenancy_service = TenancyService(
        tenancy,
        identity_store=identity_store,
        session_store=session_store,
        rate_limiter=rate_limiter,
        settings=settings,
        purger=purger,
    )
    credential_service = CredentialService(
        vault, tenancy=tenancy_service, ai_settings=ai_settings, ai=ai
    )
    from backend.services.connector_service import ConnectorService
    from tests.doubles import InMemoryConnectorStore

    connector_store = InMemoryConnectorStore()
    purger.attach_connector(connector_store)
    connector_service = ConnectorService(
        connector_store,
        tenancy=tenancy_service,
        rate_limiter=rate_limiter,
        ai_settings=ai_settings,
        settings=settings,
        step_seconds=0.01,
        long_poll_seconds=0.2,
    )
    credential_service.attach_devices(connector_service)
    from backend.services.access_key_service import AccessKeyService
    from tests.doubles import InMemoryAccessKeyStore

    access_keys = InMemoryAccessKeyStore()
    purger.attach_access_keys(access_keys)
    access_key_service = AccessKeyService(
        access_keys,
        tenancy=tenancy_service,
        identity_store=identity_store,
        rate_limiter=rate_limiter,
        settings=settings,
    )
    usage_service = ProviderUsageService(
        usage_store,
        allowances=allowances,
        limit=UsageLimit(per_job_token_limit=200_000, period_token_limit=2_000_000),
    )
    from backend.services.schema_service import SchemaService

    schema_versions = InMemorySchemaVersionStore()
    schema_service = SchemaService(
        schema_versions, tenancy=tenancy_service, local_mode=not settings.is_hosted
    )
    journal = InMemoryCanonJournal()
    canon_writer = InMemoryCanonWriter()
    media_store = InMemoryMediaStore()
    canon_repository = InMemoryCanonRepository(
        journal=journal, media=media_store, schemas=schema_versions
    )
    purger.attach_canon(canon=canon_repository, canon_writer=canon_writer)
    from backend.domain.validation import AuthoritativeValidator
    from backend.services.rule_service import RuleService

    rule_service = RuleService(
        rules,
        reader=canon_repository,
        schema_store=schema_versions,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
    )
    mutation_service = MutationService(
        journal,
        canon_repository,
        local_mode=not settings.is_hosted,
        schema_store=schema_versions,
        media=media_store,
        validator=AuthoritativeValidator(evaluators=[rule_service]),
    )
    from backend.services.outbox_consumer import OutboxConsumer
    from backend.services.projection_service import GraphProjectionService

    projection_service = GraphProjectionService(canon=canon_repository, writer=canon_writer)
    outbox_consumer = OutboxConsumer(journal=journal, projector=projection_service)
    from backend.services.canon_entity_service import CanonEntityService

    canon_entity_service = CanonEntityService(
        mutation=mutation_service,
        reader=canon_repository,
        schema_store=schema_versions,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
    )
    from backend.services.plan_service import PlanService

    plan_service = PlanService(
        mutation=mutation_service,
        reader=canon_repository,
        schema_store=schema_versions,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
    )
    from backend.services.source_service import SourceService

    source_service = SourceService(
        sources,
        reader=canon_repository,
        entities=canon_entity_service,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
    )
    from backend.services.canon_assertion_service import CanonAssertionService

    canon_assertion_service = CanonAssertionService(
        mutation=mutation_service,
        reader=canon_repository,
        schema_store=schema_versions,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
    )
    from backend.services.story_time_service import StoryTimeService
    from backend.services.what_if_service import WhatIfService

    story_clocks = InMemoryStoryClockStore()
    plan_service.clocks = story_clocks
    story_time_service = StoryTimeService(
        reader=canon_repository,
        clocks=story_clocks,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
    )
    from backend.services.knowledge_service import KnowledgeService

    knowledge_service = KnowledgeService(
        reader=canon_repository,
        mutation=mutation_service,
        schema_store=schema_versions,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
        journal=journal,
        clocks=story_clocks,
    )
    from backend.services.context_service import ContextService
    from backend.services.passage_index import PassageIndex

    passage_store = InMemoryPassageStore()
    manifests = InMemoryManifestStore()
    passage_queue = InMemoryPassageQueue()
    passage_index = PassageIndex(
        store=passage_store,
        sources=source_service,
        reader=canon_repository,
        journal=journal,
        embeddings=embeddings,
        vectors=vectors,
        queue=passage_queue,
        # No background worker runs in these tests: waiting texts are read in as soon as
        # they are asked for, through the same waiting list (CTX-005).
        deliver_now=True,
    )
    source_service.passages = passage_index
    context_service = ContextService(
        reader=canon_repository,
        plan=plan_service,
        rules=rules,
        sources=source_service,
        passages=passage_index,
        passage_store=passage_store,
        manifests=manifests,
        clocks=story_clocks,
        tenancy=tenancy_service,
        journal=journal,
        local_mode=not settings.is_hosted,
    )
    from backend.services.job_runner import JobRunner
    from backend.services.studio_service import StudioService
    from tests.doubles import InMemoryJobStore, InMemoryStudioStore

    jobs = InMemoryJobStore()
    job_runner = JobRunner(
        store=jobs,
        journal=journal,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
        autostart=False,
    )
    studio_store = InMemoryStudioStore()
    canon_repository.studio = studio_store
    context_service.studio = studio_store
    studio_service = StudioService(
        store=studio_store,
        sources=source_service,
        plan=plan_service,
        context=context_service,
        reader=canon_repository,
        journal=journal,
        jobs=jobs,
        runner=job_runner,
        ai=ai,
        settings=settings,
        credentials=credential_service,
        usage=usage_service,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
    )
    studio_service.register_jobs()
    from tests.doubles import InMemoryJobStreamStore

    job_streams = InMemoryJobStreamStore(jobs)
    studio_service.streams = job_streams
    from backend.services.send_preview import SendPreviewService

    studio_service.previews = SendPreviewService(
        settings=settings, credentials=credential_service, ai=ai
    )
    from backend.services.consistency_runs import ConsistencyRunService
    from tests.doubles import InMemoryConsistencyRunStore

    consistency_run_store = InMemoryConsistencyRunStore()
    consistency_runs = ConsistencyRunService(store=consistency_run_store, studio=studio_service)
    studio_service.runs = consistency_runs
    canon_repository.consistency_runs = consistency_run_store
    from backend.services.review_dismissals import ReviewDismissalService
    from tests.doubles import InMemoryReviewDismissalStore

    review_dismissals = ReviewDismissalService(
        store=InMemoryReviewDismissalStore(), mutations=mutation_service, rules=rules
    )
    from backend.services.scoped_dismissals import ScopedDismissalService
    from tests.doubles import InMemoryScopedDismissalStore

    scoped_dismissal_store = InMemoryScopedDismissalStore()
    scoped_dismissals = ScopedDismissalService(store=scoped_dismissal_store, studio=studio_service)
    studio_service.scoped = scoped_dismissals
    canon_repository.scoped_dismissals = scoped_dismissal_store
    source_service.studio = studio_service
    from backend.services.assistant_settings_service import AssistantSettingsService, AssistBudget
    from tests.doubles import InMemoryAssistantSettingsStore

    assistant_settings_store = InMemoryAssistantSettingsStore()
    assistant_settings = AssistantSettingsService(
        assistant_settings_store, tenancy=tenancy_service, local_mode=not settings.is_hosted
    )
    studio_service.controls = assistant_settings
    what_if_service = WhatIfService(
        journal=journal,
        canon=canon_repository,
        mutation=mutation_service,
        schema_store=schema_versions,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
    )
    from backend.services.assist_session_service import AssistSessionService
    from backend.services.canon_equivalence import CanonEquivalenceService
    from backend.services.conversation_service import ConversationService
    from backend.services.merge_service import MergeService
    from tests.doubles import InMemoryConversationStore

    conversation_store = InMemoryConversationStore()
    conversation_service = ConversationService(
        conversation_store,
        journal=journal,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
    )
    from backend.services.rule_workflow import RuleWorkflow

    canon_character_workflow = RuleWorkflow(
        ai=ai,
        mutation=mutation_service,
        reader=canon_repository,
        schema_store=schema_versions,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
        clocks=story_clocks,
        passages=passage_store,
    )
    from backend.services.scene_reading import SceneReadingService

    scene_reading = SceneReadingService(
        studio=studio_service,
        store=studio_store,
        sources=source_service,
        workflow=canon_character_workflow,
        canon=canon_repository,
        journal=journal,
        runner=job_runner,
        settings=settings,
        credentials=credential_service,
        usage=usage_service,
        jobs=jobs,
    )
    scene_reading.register_jobs()
    from backend.services.scene_plan_card import ScenePlanCardService

    scene_plan_card = ScenePlanCardService(
        studio=studio_service,
        sources=source_service,
        mutation=mutation_service,
        canon=canon_repository,
        journal=journal,
        runner=job_runner,
        ai=ai,
        settings=settings,
        credentials=credential_service,
        usage=usage_service,
    )
    scene_plan_card.register_jobs()
    from backend.services.planning_help import PlanningHelpService

    planning_help = PlanningHelpService(
        studio=studio_service,
        plan=plan_service,
        sources=source_service,
        mutation=mutation_service,
        canon=canon_repository,
        reader=canon_repository,
        journal=journal,
        runner=job_runner,
        ai=ai,
        settings=settings,
        credentials=credential_service,
        usage=usage_service,
    )
    planning_help.register_jobs()
    from backend.services.scene_summaries import SceneSummaryService
    from tests.doubles import InMemorySceneSummaryStore

    scene_summaries = SceneSummaryService(
        store=InMemorySceneSummaryStore(),
        studio=studio_service,
        sources=source_service,
        runner=job_runner,
        ai=ai,
        settings=settings,
        credentials=credential_service,
        usage=usage_service,
    )
    scene_summaries.register_jobs()
    context_service.summaries = scene_summaries
    from backend.services.scene_summaries import PartSummaryService
    from tests.doubles import InMemoryPartSummaryStore

    # Summaries of whole arcs and beats, on the scenes' kept summaries (CTX-006).
    scene_summaries.parts = PartSummaryService(
        store=InMemoryPartSummaryStore(),
        scenes=scene_summaries,
        runner=job_runner,
        ai=ai,
        settings=settings,
        credentials=credential_service,
        usage=usage_service,
    )
    scene_summaries.parts.register_jobs()
    equivalence_service = CanonEquivalenceService(
        canon=canon_repository, journal=journal, writer=canon_writer
    )
    from backend.services.canon_history_service import CanonHistoryService

    canon_history_service = CanonHistoryService(
        canon=canon_repository,
        journal=journal,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
        mutation=mutation_service,
    )
    from backend.services.delegation_service import DelegationService
    from tests.doubles import InMemoryDelegationStore

    delegation_store = InMemoryDelegationStore()
    delegation_service = DelegationService(
        delegation_store,
        mutation=mutation_service,
        reader=canon_repository,
        history=canon_history_service,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
    )
    mutation_service.delegation = delegation_service
    # The commit transaction checks a rule is still on, as PostgreSQL does.
    canon_repository.delegation = delegation_store
    from backend.services.media_service import MediaService
    from tests.doubles import FakeImageProcessor, InMemoryMediaProcessingStore

    media_processing = InMemoryMediaProcessingStore(media_store)
    image_processor = FakeImageProcessor()
    media_service = MediaService(
        media=media_store,
        objects=objects,
        mutation=mutation_service,
        reader=canon_repository,
        journal=journal,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
        processing=media_processing,
        processor=image_processor,
        runner=job_runner,
        quota_bytes=settings.MEDIA_WORKSPACE_QUOTA_BYTES,
    )
    media_service.register_jobs()
    from backend.services.media_observations import MediaObservationService
    from tests.doubles import InMemoryObservationStore

    observation_store = InMemoryObservationStore()
    media_observations = MediaObservationService(
        store=observation_store,
        media=media_service,
        reader=canon_repository,
        mutation=mutation_service,
        journal=journal,
        runner=job_runner,
        ai=ai,
        settings=settings,
        credentials=credential_service,
        usage=usage_service,
        studio=studio_service,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
    )
    media_observations.register_jobs()
    media_service.observation_store = observation_store
    context_service.pictures = media_observations
    from backend.services.canon_archive import CanonArchiveService

    archive_service = CanonArchiveService(
        canon=canon_repository,
        journal=journal,
        schemas=schema_versions,
        media=media_store,
        objects=objects,
        projector=projection_service,
        equivalence=equivalence_service,
        tenancy=tenancy_service,
        audit=tenancy,
        canon_writer=canon_writer,
        local_mode=not settings.is_hosted,
        sources=sources,
        rules=rules,
        clocks=story_clocks,
        studio=studio_store,
        assists=assist_sessions,
    )
    archive_service.pictures = media_service
    archive_service.picture_notes = media_observations
    archive_service.scene_summaries = scene_summaries
    archive_service.assistant_threads = conversation_service
    from backend.services.run_archive import RunArchive

    archive_service.check_runs = RunArchive(consistency_run_store)
    from backend.services.dismissal_archive import DismissalArchive

    archive_service.dismissal_archive = DismissalArchive(
        review=review_dismissals._store, scoped=scoped_dismissal_store
    )
    from backend.services.read_models import ReadModelConsumer, ReadModelService
    from tests.read_model_doubles import InMemoryReadModelStore

    read_model_store = InMemoryReadModelStore(journal)
    read_models = ReadModelService(
        store=read_model_store,
        reader=canon_repository,
        journal=journal,
        tenancy=tenancy_service,
        local_mode=not settings.is_hosted,
    )
    story_time_service.views = read_models
    knowledge_service.views = read_models
    container = Container(
        read_model_store=read_model_store,
        read_models=read_models,
        read_model_consumer=ReadModelConsumer(store=read_model_store, reader=canon_repository),
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
        identity_provider=identity_provider,
        auth_service=auth_service,
        tenancy=tenancy,
        tenancy_service=tenancy_service,
        rate_limiter=rate_limiter,
        credentials=vault,
        credential_service=credential_service,
        access_keys=access_keys,
        access_key_service=access_key_service,
        connector_store=connector_store,
        connector_service=connector_service,
        usage=usage_store,
        usage_service=usage_service,
        objects=objects,
        schema_versions=schema_versions,
        schema_service=schema_service,
        journal=journal,
        canon_writer=canon_writer,
        canon_repository=canon_repository,
        media_store=media_store,
        media_service=media_service,
        media_processing=media_processing,
        image_processor=image_processor,
        observation_store=observation_store,
        media_observations=media_observations,
        archive_service=archive_service,
        projection_service=projection_service,
        outbox_consumer=outbox_consumer,
        mutation_service=mutation_service,
        canon_entity_service=canon_entity_service,
        plan_service=plan_service,
        sources=sources,
        source_service=source_service,
        rules=rules,
        rule_service=rule_service,
        canon_assertion_service=canon_assertion_service,
        canon_history_service=canon_history_service,
        story_clocks=story_clocks,
        story_time_service=story_time_service,
        what_if_service=what_if_service,
        knowledge_service=knowledge_service,
        passage_store=passage_store,
        manifests=manifests,
        passage_index=passage_index,
        context_service=context_service,
        jobs=jobs,
        job_runner=job_runner,
        studio_store=studio_store,
        studio_service=studio_service,
        job_streams=job_streams,
        consistency_run_store=consistency_run_store,
        consistency_runs=consistency_runs,
        review_dismissals=review_dismissals,
        scoped_dismissals=scoped_dismissals,
        scene_reading=scene_reading,
        scene_plan_card=scene_plan_card,
        planning_help=planning_help,
        scene_summaries=scene_summaries,
        canon_character_workflow=canon_character_workflow,
        equivalence_service=equivalence_service,
        assist_sessions=assist_sessions,
        ai_settings=ai_settings,
        assist_session_service=AssistSessionService(
            assist_sessions, tenancy=tenancy_service, local_mode=not settings.is_hosted
        ),
        conversation_store=conversation_store,
        conversation_service=conversation_service,
        merge_service=MergeService(
            mutation=mutation_service,
            reader=canon_repository,
            schema_store=schema_versions,
            tenancy=tenancy_service,
            local_mode=not settings.is_hosted,
        ),
        assistant_settings_store=assistant_settings_store,
        assistant_settings=assistant_settings,
        assist_budget=AssistBudget(assistant_settings, assist_sessions),
        delegation_store=delegation_store,
        delegation_service=delegation_service,
    )
    from backend.services.voice_audit_wiring import wire_voice_and_audits
    from tests.notice_doubles import InMemoryNoticeStore
    from tests.voice_doubles import (
        InMemoryAuditScheduleStore,
        InMemoryCorrectionPlanStore,
        InMemoryVoiceBaselineStore,
    )

    # The author's voice, audits on a schedule and correction plans (CON-018, CON-011).
    wire_voice_and_audits(
        container,
        baselines=InMemoryVoiceBaselineStore(),
        schedules=InMemoryAuditScheduleStore(),
        corrections=InMemoryCorrectionPlanStore(),
        notices=InMemoryNoticeStore(),
    )
    return container


@pytest.fixture
def fake_container(settings_factory, tmp_path):
    return _build_fake_container(settings_factory(APP_ENV="test"), tmp_path)


@pytest.fixture
def hosted_container(settings_factory, tmp_path):
    settings = settings_factory(
        APP_ENV="test",
        AUTH_MODE="hosted",
        OIDC_ISSUER=HOSTED_ISSUER,
        OIDC_CLIENT_ID="test-client",
        OIDC_CLIENT_SECRET="test-secret",
        OIDC_REDIRECT_URI="https://novel-engine.jaketalling.com/auth/callback",
        OIDC_POST_LOGOUT_REDIRECT_URI="https://novel-engine.jaketalling.com/",
        # Scoping tests provision workspaces through the API; provisioning policy
        # itself is exercised directly in tests/application/test_workspace_provisioning.py.
        ALLOW_WORKSPACE_SELF_CREATION=True,
    )
    return _build_fake_container(settings, tmp_path)


@pytest.fixture
def api_client(fake_container):
    from fastapi.testclient import TestClient

    from backend.main import create_app

    app = create_app(container=fake_container)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def auth_client(api_client, fake_container):
    """Local-mode client with a session and the CSRF header pre-set.

    Graph/context/character operations are workspace-scoped and require an
    authenticated scope; this fixture provides the local workspace session.
    """
    api_client.get("/auth/login", follow_redirects=False)
    csrf = api_client.cookies.get(fake_container.settings.CSRF_COOKIE_NAME)
    assert csrf, "local login did not set a CSRF cookie"
    api_client.headers["X-CSRF-Token"] = csrf
    return api_client


@pytest.fixture
def hosted_client(hosted_container):
    from fastapi.testclient import TestClient

    from backend.main import create_app

    app = create_app(container=hosted_container)
    # HTTPS base URL so Secure session cookies are stored and resent by the client.
    with TestClient(app, base_url="https://testserver") as client:
        yield client
