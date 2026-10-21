from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api.auth import RotatedSessionCookieMiddleware, project_access
from backend.api.auth import router as auth_router
from backend.api.frontend import SecurityHeadersMiddleware, frontend_directory, mount_frontend
from backend.api.health import router as health_router
from backend.api.mcp import router as mcp_router
from backend.api.middleware import CorrelationIdMiddleware
from backend.api.problems import register_exception_handlers
from backend.api.v1.access_keys import router as access_keys_router
from backend.api.v1.ai_settings import router as ai_settings_router
from backend.api.v1.assistant_settings import router as assistant_settings_router
from backend.api.v1.audits import router as audits_router
from backend.api.v1.canon_archive import router as canon_archive_router
from backend.api.v1.canon_assertions import router as canon_assertions_router
from backend.api.v1.canon_assist import router as canon_assist_router
from backend.api.v1.canon_characters import router as canon_characters_router
from backend.api.v1.canon_entities import router as canon_entities_router
from backend.api.v1.canon_history import router as canon_history_router
from backend.api.v1.canon_mutations import router as canon_mutations_router
from backend.api.v1.consistency_runs import router as consistency_runs_router
from backend.api.v1.context import router as context_router
from backend.api.v1.credentials import router as credentials_router
from backend.api.v1.delegation import router as delegation_router
from backend.api.v1.impact import router as impact_router
from backend.api.v1.knowledge import router as knowledge_router
from backend.api.v1.media import router as media_router
from backend.api.v1.pictures import router as pictures_router
from backend.api.v1.plan import router as plan_router
from backend.api.v1.plan_assist import router as plan_assist_router
from backend.api.v1.plan_help import router as plan_help_router
from backend.api.v1.projection import router as projection_router
from backend.api.v1.review_dismissals import router as review_dismissals_router
from backend.api.v1.router import router as v1_router
from backend.api.v1.rules import router as rules_router
from backend.api.v1.schema_versions import router as schema_versions_router
from backend.api.v1.sources import router as sources_router
from backend.api.v1.sources_assist import router as sources_assist_router
from backend.api.v1.story_time import router as story_time_router
from backend.api.v1.story_views import router as story_views_router
from backend.api.v1.studio import router as studio_router
from backend.api.v1.tenancy import router as tenancy_router
from backend.api.v1.what_ifs import router as what_ifs_router
from backend.container import Container, build_container
from backend.core.config import Settings
from backend.core.config import settings as default_settings
from backend.core.logging import configure_logging, log_event

logger = logging.getLogger("backend.main")


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    if settings is not None:
        resolved_settings = settings
    elif container is not None:
        resolved_settings = container.settings
    else:
        resolved_settings = default_settings
    configure_logging(resolved_settings.LOG_LEVEL)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active = container or build_container(resolved_settings)
        app.state.container = active
        try:
            neo4j_status = active.neo4j_migrations.status()
            if not neo4j_status.current:
                log_event(
                    logger,
                    "app.startup.failed",
                    level=logging.ERROR,
                    reason="neo4j_migrations_pending",
                    detail=neo4j_status.detail,
                )
                raise RuntimeError(
                    "Neo4j schema is not migrated; run 'make migrate'. " + neo4j_status.detail
                )

            if active.database is not None:
                database_status = await active.database.migration_status()
                if not database_status.current:
                    log_event(
                        logger,
                        "app.startup.failed",
                        level=logging.ERROR,
                        reason="postgres_migrations_pending",
                        detail=database_status.detail,
                    )
                    raise RuntimeError(
                        "PostgreSQL schema is not migrated; run 'make migrate'. "
                        + database_status.detail
                    )

            from backend.core.observability import init_sentry

            telemetry_enabled = init_sentry(resolved_settings)

            oidc_configured = bool(
                active.identity_provider is not None and active.identity_provider.is_available()
            )
            if resolved_settings.is_hosted and not oidc_configured:
                log_event(
                    logger,
                    "app.startup.auth_degraded",
                    level=logging.ERROR,
                    reason="hosted mode without a configured OIDC provider",
                )
            log_event(
                logger,
                "app.startup",
                env=resolved_settings.APP_ENV,
                auth_mode=resolved_settings.AUTH_MODE,
                oidc_configured=oidc_configured,
                sentry_enabled=telemetry_enabled,
                ai_available=active.ai.is_available(),
                embeddings_available=active.embeddings.available(),
            )
            if active.job_runner is not None:
                active.job_runner.start()
            if active.projection_worker is not None:
                # The graph follows the story without anyone running a command.
                active.projection_worker.start()
            yield
        finally:
            await active.aclose()
            log_event(logger, "app.shutdown", env=resolved_settings.APP_ENV)

    app = FastAPI(
        title=resolved_settings.PROJECT_NAME,
        version=resolved_settings.PROJECT_VERSION,
        description=(
            "Novel Engine API. Canonical and explicitly namespaced development routes "
            "are mounted under /api/v1; the deprecated /api aliases have been removed."
        ),
        lifespan=lifespan,
        # The docs describe the entire API surface, so a hosted or production
        # site does not publish them.
        docs_url="/docs" if resolved_settings.api_docs_enabled else None,
        redoc_url="/redoc" if resolved_settings.api_docs_enabled else None,
        openapi_url="/openapi.json" if resolved_settings.api_docs_enabled else None,
    )
    if resolved_settings.is_hosted or resolved_settings.is_production:
        app.add_middleware(SecurityHeadersMiddleware, settings=resolved_settings)
    app.add_middleware(RotatedSessionCookieMiddleware, settings=resolved_settings)
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app)

    app.state.container = container
    app.include_router(v1_router, prefix="/api/v1")
    app.include_router(tenancy_router, prefix="/api/v1")
    app.include_router(credentials_router, prefix="/api/v1")
    app.include_router(ai_settings_router, prefix="/api/v1")
    app.include_router(assistant_settings_router, prefix="/api/v1")
    app.include_router(schema_versions_router, prefix="/api/v1")
    app.include_router(canon_entities_router, prefix="/api/v1")
    app.include_router(canon_assertions_router, prefix="/api/v1")
    app.include_router(canon_history_router, prefix="/api/v1")
    app.include_router(canon_mutations_router, prefix="/api/v1")
    app.include_router(canon_characters_router, prefix="/api/v1")
    app.include_router(canon_assist_router, prefix="/api/v1")
    app.include_router(plan_router, prefix="/api/v1")
    app.include_router(plan_assist_router, prefix="/api/v1")
    app.include_router(plan_help_router, prefix="/api/v1")
    app.include_router(sources_router, prefix="/api/v1")
    app.include_router(sources_assist_router, prefix="/api/v1")
    app.include_router(rules_router, prefix="/api/v1")
    app.include_router(story_time_router, prefix="/api/v1")
    app.include_router(what_ifs_router, prefix="/api/v1")
    app.include_router(knowledge_router, prefix="/api/v1")
    app.include_router(context_router, prefix="/api/v1")
    app.include_router(studio_router, prefix="/api/v1")
    app.include_router(audits_router, prefix="/api/v1")
    app.include_router(consistency_runs_router, prefix="/api/v1")
    app.include_router(review_dismissals_router, prefix="/api/v1")
    app.include_router(canon_archive_router, prefix="/api/v1")
    app.include_router(impact_router, prefix="/api/v1")
    app.include_router(delegation_router, prefix="/api/v1")
    app.add_api_route(
        "/api/v1/projects/{project_id}/access",
        project_access,
        methods=["GET"],
        tags=["tenancy"],
        summary="Caller permissions in a project",
    )
    app.include_router(media_router, prefix="/api/v1")
    app.include_router(pictures_router, prefix="/api/v1")
    app.include_router(story_views_router, prefix="/api/v1")
    app.include_router(projection_router, prefix="/api/v1")
    app.include_router(access_keys_router, prefix="/api/v1")
    # Other assistants, with an access key (QRY-003). JSON-RPC, so not in the OpenAPI document.
    app.include_router(mcp_router)
    app.include_router(health_router)
    app.include_router(auth_router)

    frontend = frontend_directory(resolved_settings)
    if frontend is not None:
        # Registered last so every API route above takes precedence.
        mount_frontend(app, frontend, resolved_settings)
    else:

        @app.get("/")
        def read_root():
            return {"message": "Welcome to the Novel Engine API"}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
