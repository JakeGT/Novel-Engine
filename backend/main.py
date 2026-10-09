from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api.auth import router as auth_router
from backend.api.health import router as health_router
from backend.api.middleware import CorrelationIdMiddleware
from backend.api.problems import register_exception_handlers
from backend.api.v1.canon_archive import router as canon_archive_router
from backend.api.v1.canon_assertions import router as canon_assertions_router
from backend.api.v1.canon_characters import router as canon_characters_router
from backend.api.v1.canon_entities import router as canon_entities_router
from backend.api.v1.canon_history import router as canon_history_router
from backend.api.v1.canon_mutations import router as canon_mutations_router
from backend.api.v1.credentials import router as credentials_router
from backend.api.v1.media import router as media_router
from backend.api.v1.projection import router as projection_router
from backend.api.v1.router import router as v1_router
from backend.api.v1.schema_versions import router as schema_versions_router
from backend.api.v1.tenancy import router as tenancy_router
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
    )
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app)

    app.state.container = container
    app.include_router(v1_router, prefix="/api/v1")
    app.include_router(tenancy_router, prefix="/api/v1")
    app.include_router(credentials_router, prefix="/api/v1")
    app.include_router(schema_versions_router, prefix="/api/v1")
    app.include_router(canon_entities_router, prefix="/api/v1")
    app.include_router(canon_assertions_router, prefix="/api/v1")
    app.include_router(canon_history_router, prefix="/api/v1")
    app.include_router(canon_mutations_router, prefix="/api/v1")
    app.include_router(canon_characters_router, prefix="/api/v1")
    app.include_router(canon_archive_router, prefix="/api/v1")
    app.include_router(media_router, prefix="/api/v1")
    app.include_router(projection_router, prefix="/api/v1")
    app.include_router(health_router)
    app.include_router(auth_router)

    @app.get("/")
    def read_root():
        return {"message": "Welcome to the Novel Engine API"}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
