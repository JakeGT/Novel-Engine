from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from backend.api.dependencies import get_container
from backend.container import Container

router = APIRouter(tags=["health"])


@router.get("/health/live")
def liveness() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness(
    response: Response, container: Container = Depends(get_container)
) -> dict[str, object]:
    checks: dict[str, dict[str, object]] = {}
    ok = True

    migration = container.neo4j_migrations.status()
    checks["neo4j_migrations"] = {"ok": migration.current, "detail": migration.detail}
    ok = ok and migration.current

    graph_ok = container.graph.ping()
    checks["neo4j"] = {"ok": graph_ok}
    ok = ok and graph_ok

    if container.database is not None:
        database_ok = await container.database.ready()
        checks["postgres"] = {"ok": database_ok}
        ok = ok and database_ok

        database_migration = await container.database.migration_status()
        checks["postgres_migrations"] = {
            "ok": database_migration.current,
            "detail": database_migration.detail,
        }
        ok = ok and database_migration.current

    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": "ready" if ok else "degraded", "checks": checks}


@router.get("/health/capabilities")
def capabilities(container: Container = Depends(get_container)) -> dict[str, object]:
    embeddings_ok = container.embeddings.available()
    ai_ok = container.ai.is_available()
    vectors_ok = container.vectors.health()

    return {
        "status": "ok",
        "capabilities": {
            "embeddings": {
                "available": embeddings_ok,
                "loaded": container.embeddings.loaded(),
                "optional": True,
                "model": container.embeddings.model,
                "version": container.embeddings.version,
                "dimension": container.embeddings.dimension,
            },
            "ai": {"available": ai_ok, "optional": True},
            "vector": {
                "available": vectors_ok,
                "optional": True,
                "detail": "transitional Chroma adapter; pgvector is the target implementation",
            },
        },
    }
