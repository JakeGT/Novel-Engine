from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from backend.api.dependencies import get_container
from backend.container import Container
from backend.core.observability import sentry_enabled

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

    def record(name: str, check_ok: bool, detail: str | None = None) -> None:
        nonlocal ok
        entry: dict[str, object] = {"ok": check_ok}
        if detail:
            entry["detail"] = detail
        checks[name] = entry
        ok = ok and check_ok

    try:
        migration = container.neo4j_migrations.status()
        record("neo4j_migrations", migration.current, migration.detail or None)
    except Exception as error:
        record("neo4j_migrations", False, f"migration check failed: {type(error).__name__}")

    try:
        record("neo4j", container.graph.ping())
    except Exception as error:
        record("neo4j", False, f"connectivity check failed: {type(error).__name__}")

    if container.database is not None:
        try:
            record("postgres", await container.database.ready())
        except Exception as error:
            record("postgres", False, f"readiness check failed: {type(error).__name__}")

        try:
            database_migration = await container.database.migration_status()
            record(
                "postgres_migrations",
                database_migration.current,
                database_migration.detail or None,
            )
        except Exception as error:
            record(
                "postgres_migrations",
                False,
                f"migration check failed: {type(error).__name__}",
            )

    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": "ready" if ok else "degraded", "checks": checks}


@router.get("/health/capabilities")
def capabilities(container: Container = Depends(get_container)) -> dict[str, object]:
    embeddings_ok = container.embeddings.available()
    ai_ok = container.ai.is_available()
    vectors_ok = container.vectors.health()
    vectors_dimension_ok = getattr(container.vectors, "dimension_supported", lambda: True)()
    ai_caps = container.ai.capabilities() if hasattr(container.ai, "capabilities") else None
    sentry_ok = sentry_enabled(container.settings)
    identity_provider = getattr(container, "identity_provider", None)
    oidc_configured = bool(identity_provider is not None and identity_provider.is_available())

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
                "reason": None if embeddings_ok else "sentence-transformers is not importable",
            },
            "ai": {
                "available": ai_ok,
                "optional": True,
                "reason": None if ai_ok else "OPENAI_API_KEY is not configured",
                "capabilities": None
                if ai_caps is None
                else {
                    "provider": ai_caps.provider,
                    "model": ai_caps.model,
                    "input_modalities": sorted(ai_caps.input_modalities),
                    "output_modalities": sorted(ai_caps.output_modalities),
                    "structured_output": ai_caps.structured_output,
                    "max_context_tokens": ai_caps.max_context_tokens,
                    "supports_streaming": ai_caps.supports_streaming,
                },
            },
            "telemetry": {
                "sentry_backend": sentry_ok,
                "optional": True,
                "reason": None if sentry_ok else "SENTRY_DSN_BACKEND is not configured",
            },
            "auth": {
                "mode": container.settings.AUTH_MODE,
                "oidc_configured": oidc_configured,
                "optional": False,
            },
            "vector": {
                "available": vectors_ok,
                "optional": True,
                "dimension_supported": vectors_dimension_ok,
                "detail": "pgvector-backed store with recorded embedding provenance",
                "reason": (
                    None
                    if vectors_ok
                    else "vector_documents is unreachable or the configured embedding "
                    "dimension does not match the schema"
                ),
            },
        },
    }
