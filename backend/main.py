from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.api.endpoints import router as legacy_api_router
from backend.api.health import router as health_router
from backend.api.middleware import CorrelationIdMiddleware
from backend.api.problems import register_exception_handlers
from backend.api.v1.router import router as v1_router
from backend.container import Container, build_container
from backend.core.config import Settings
from backend.core.config import settings as default_settings


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    resolved_settings = settings or default_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active = container or build_container(resolved_settings)
        app.state.container = active
        try:
            neo4j_status = active.neo4j_migrations.status()
            if not neo4j_status.current:
                raise RuntimeError(
                    "Neo4j schema is not migrated; run 'make migrate'. " + neo4j_status.detail
                )

            if active.database is not None:
                database_status = await active.database.migration_status()
                if not database_status.current:
                    raise RuntimeError(
                        "PostgreSQL schema is not migrated; run 'make migrate'. "
                        + database_status.detail
                    )
            yield
        finally:
            await active.aclose()

    app = FastAPI(
        title=resolved_settings.PROJECT_NAME,
        version=resolved_settings.PROJECT_VERSION,
        description=(
            "Novel Engine API. Canonical routes are under /api/v1. Legacy prototype "
            "routes remain mounted at /api as temporary, deprecated aliases."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(app)

    app.state.container = container
    app.include_router(v1_router, prefix="/api/v1")
    app.include_router(legacy_api_router, prefix="/api")
    app.include_router(health_router)

    @app.get("/")
    def read_root():
        return {"message": "Welcome to the Novel Engine API"}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)