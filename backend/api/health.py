from __future__ import annotations

import asyncio
import hmac

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import PlainTextResponse

from backend.api.dependencies import get_container
from backend.container import Container
from backend.core.metrics import metrics
from backend.core.observability import client_config, release_name, sentry_enabled

router = APIRouter(tags=["health"])


@router.get("/health/metrics", include_in_schema=False)
def operational_metrics(
    request: Request, container: Container = Depends(get_container)
) -> Response:
    """Counters and timings in Prometheus text format; never ids or content."""
    settings = container.settings
    if not settings.METRICS_ENABLED:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if settings.METRICS_TOKEN:
        supplied = request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied, f"Bearer {settings.METRICS_TOKEN}"):
            return Response(status_code=status.HTTP_401_UNAUTHORIZED)
    elif settings.APP_ENV == "production":
        # Fail closed: production metrics are never served unauthenticated.
        return Response(status_code=status.HTTP_403_FORBIDDEN)
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")


@router.get("/health/live")
def liveness() -> dict[str, str]:
    return {"status": "alive"}


# A job waiting longer than this to start means the worker is stuck or overloaded.
STALE_QUEUE_SECONDS = 300
# How long the optional checks may take before they count as down.
OPTIONAL_CHECK_SECONDS = 3.0


async def _job_worker(
    container: Container,
) -> tuple[bool, str | None, dict[str, object], str | None]:
    """Whether background work (drafts, checks, readings) is being picked up: whether it
    is, the plain reason, the counts, and the reason again without any numbers."""
    runner = container.job_runner
    facts: dict[str, object] = {"running": bool(runner.running)}
    problems: list[str] = []
    brief: list[str] = []
    # A worker turned off on purpose (tests, one-off commands) runs work when asked.
    detail = None if runner.autostart else "Background work runs only when asked for here."
    if runner.autostart and not runner.running:
        problems.append(
            "The background worker is not running, so drafts, checks and readings wait "
            "until it starts again."
        )
        brief.append(problems[-1])
    if container.jobs is not None and hasattr(container.jobs, "waiting"):
        try:
            waiting, oldest = await asyncio.wait_for(
                container.jobs.waiting(), timeout=OPTIONAL_CHECK_SECONDS
            )
        except Exception:  # noqa: BLE001 - reported in plain words, never the error text
            problems.append("The list of waiting background work could not be read.")
            brief.append(problems[-1])
        else:
            facts["waiting"] = waiting
            facts["oldest_waiting_seconds"] = None if oldest is None else round(oldest)
            if runner.autostart and oldest is not None and oldest > STALE_QUEUE_SECONDS:
                problems.append(
                    f"The oldest waiting piece of work has waited {round(oldest / 60)} minutes; "
                    "the worker may be stuck or too busy."
                )
                brief.append(
                    "Background work has waited too long to start; the worker may be stuck "
                    "or too busy."
                )
    if problems:
        return False, " ".join(problems), facts, " ".join(brief)
    return True, detail, facts, detail


async def _vector_store(container: Container) -> tuple[bool, str | None]:
    """Whether finding passages by meaning can work. Names and links work without it."""
    if not getattr(container.vectors, "dimension_supported", lambda: True)():
        return False, (
            "The embedding size set for this server does not match the search store, so "
            "finding passages by meaning is off. Search by name still works."
        )
    try:
        healthy = await asyncio.wait_for(
            asyncio.to_thread(container.vectors.health), timeout=OPTIONAL_CHECK_SECONDS
        )
    except Exception:  # noqa: BLE001 - a slow or failing store is simply down
        healthy = False
    if not healthy:
        index = getattr(container.vectors, "index", None)
        reachable = False
        if index is not None and hasattr(index, "ping"):
            try:
                reachable = bool(await asyncio.to_thread(index.ping))
            except Exception:  # noqa: BLE001 - unreachable is simply down
                reachable = False
        if reachable:
            return False, (
                "The search index in use was built with another model, so finding passages "
                "by meaning is off until it is rebuilt. Search by name still works."
            )
        return False, (
            "The search store could not be reached, so finding passages by meaning is off. "
            "Search by name still works."
        )
    return True, None


async def _search_index(
    container: Container,
) -> tuple[bool, str | None, dict[str, object]] | None:
    """Which search index is in use, what is being built, and how far behind each is
    (CTX-012). Counts and model names only, never content."""
    service = getattr(container, "embedding_index_service", None)
    if service is None:
        return None
    try:
        status = await asyncio.wait_for(service.status(), timeout=OPTIONAL_CHECK_SECONDS)
    except TimeoutError:
        try:
            status = await asyncio.wait_for(
                service.status(with_lag=False), timeout=OPTIONAL_CHECK_SECONDS
            )
        except Exception:  # noqa: BLE001 - a slow or failing store is simply down
            return False, "The search index could not be read.", {}
    except Exception:  # noqa: BLE001 - a slow or failing store is simply down
        return False, "The search index could not be read.", {}
    active = status.active or {}
    building = status.building or {}
    facts: dict[str, object] = {
        "active_generation": active.get("id"),
        "active_model": active.get("model"),
        "active_dimension": active.get("dimension"),
        "active_rows": active.get("rows"),
        "lag": status.lag.get("total") if status.lag else None,
        # What media still has to reach the index in use (OPS-007): accepted picture
        # descriptions and prepared pictures not yet embedded. Counts only.
        "caption_lag": status.lag.get("captions") if status.lag else None,
        "picture_lag": status.lag.get("pictures") if status.lag else None,
        "building_generation": building.get("id"),
        "building_lag": (status.building_lag or {}).get("total") if building else None,
    }
    switched_off = service.embedder is None
    return (status.searchable or switched_off), status.reason, facts


# Writing waiting longer than this to be read into the passage index means the worker is
# stuck or behind (a failure is tried again after at most ten minutes).
STALE_PASSAGE_SECONDS = 900


async def _passage_index(
    container: Container,
) -> tuple[bool, str | None, dict[str, object]] | None:
    """How far behind reading the author's writing into the passage index is (CTX-005):
    how many texts wait, the oldest wait, and how many failed. Counts only, never which
    stories; None when texts are read in at once."""
    index = getattr(container, "passage_index", None)
    if index is None or not hasattr(index, "lag"):
        return None
    try:
        lag = await asyncio.wait_for(index.lag(), timeout=OPTIONAL_CHECK_SECONDS)
    except Exception:  # noqa: BLE001 - reported in plain words, never the error text
        return False, "How far reading in your writing has got could not be read.", {}
    if lag is None:
        return None
    oldest = lag.oldest_seconds
    facts: dict[str, object] = {
        "waiting": int(lag.count),
        "oldest_waiting_seconds": None if oldest is None else round(float(oldest)),
        "failing": int(lag.failing),
    }
    if oldest is not None and float(oldest) > STALE_PASSAGE_SECONDS:
        return (
            False,
            f"Writing has waited about {round(float(oldest) / 60)} minutes to be read in, so "
            "the assistant may not find what it says yet. The writing itself is safe.",
            facts,
        )
    return True, None, facts


# A picture waiting longer than this to be prepared means picture work is stuck or behind.
STALE_PICTURE_SECONDS = 900


async def _picture_processing(
    container: Container,
) -> tuple[bool, str | None, dict[str, object]] | None:
    """Whether pictures are being prepared (OPS-007): how many wait, the oldest wait and
    how many failed. Counts only; None when this server does not prepare pictures."""
    media = getattr(container, "media_service", None)
    if media is None or not getattr(media, "processing_enabled", False):
        return None
    try:
        lag = await asyncio.wait_for(media.processing_lag(), timeout=OPTIONAL_CHECK_SECONDS)
    except Exception:  # noqa: BLE001 - reported in plain words, never the error text
        return False, "How far picture preparation has got could not be read.", {}
    if lag is None:
        return None
    oldest = lag.get("oldest_seconds")
    facts: dict[str, object] = {
        "waiting": int(lag.get("waiting") or 0),
        "oldest_waiting_seconds": None if oldest is None else round(float(oldest)),
        "failed": int(lag.get("failed") or 0),
    }
    if oldest is not None and float(oldest) > STALE_PICTURE_SECONDS:
        return (
            False,
            "Pictures have waited too long to be prepared, so lists show the full pictures "
            "for now. The pictures themselves are safe.",
            facts,
        )
    return True, None, facts


# What a failed required check says to a caller who may not see the details.
_BRIEF = {
    "neo4j_migrations": "The story graph's layout is not up to date.",
    "neo4j": "The story graph cannot be reached.",
    "postgres": "The database cannot be reached.",
    "postgres_migrations": "The database's layout is not up to date.",
}


def _may_see_details(request: Request, container: Container) -> bool:
    """Counts and details are for the person running the server: on a local install, or
    with the metrics token (the same one ``/health/metrics`` asks for)."""
    settings = container.settings
    if not settings.is_hosted:
        return True
    if not settings.METRICS_TOKEN:
        return False
    supplied = request.headers.get("Authorization", "")
    return hmac.compare_digest(supplied, f"Bearer {settings.METRICS_TOKEN}")


@router.get("/health/ready")
async def readiness(
    request: Request, response: Response, container: Container = Depends(get_container)
) -> dict[str, object]:
    """Ready when the required stores are; degraded (still 200) when an optional part is
    down, with plain reasons; unavailable (503) when a required store is down. Never
    reports addresses, credentials or error messages that could carry them."""
    # On a hosted server, how much work is waiting and for how long are shown only with
    # the metrics token; without it each part says only whether it is up, with a reason
    # in plain words (the docstring above is the public API description).
    checks: dict[str, dict[str, object]] = {}
    briefs: dict[str, str] = {}
    ok = True
    optional_ok = True

    def record(
        name: str,
        check_ok: bool,
        detail: str | None = None,
        *,
        optional: bool = False,
        brief: str | None = None,
        **facts: object,
    ) -> None:
        nonlocal ok, optional_ok
        entry: dict[str, object] = {"ok": check_ok, "optional": optional, **facts}
        if detail:
            entry["detail"] = detail
        checks[name] = entry
        if not check_ok:
            briefs[name] = brief or _BRIEF.get(name) or detail or "A part of the server is down."
        if optional:
            optional_ok = optional_ok and check_ok
        else:
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

    if container.job_runner is not None:
        worker_ok, worker_detail, facts, worker_brief = await _job_worker(container)
        record("job_worker", worker_ok, worker_detail, optional=True, brief=worker_brief, **facts)

    worker = getattr(container, "projection_worker", None)
    if worker is not None:
        graph_ok = not worker.autostart or worker.running
        record(
            "graph_worker",
            graph_ok,
            None
            if graph_ok
            else "The graph is not kept up to date in the background; it catches up when the "
            "server restarts or the projection command runs. The story itself is safe.",
            optional=True,
        )

    if container.vectors is not None:
        vectors_ok, vectors_detail = await _vector_store(container)
        record("vector_store", vectors_ok, vectors_detail, optional=True)

    search_index = await _search_index(container)
    if search_index is not None:
        index_ok, index_detail, index_facts = search_index
        record(
            "search_index",
            index_ok,
            index_detail,
            optional=True,
            brief="Finding things by meaning is off for now; names and links still work.",
            **index_facts,
        )

    passages = await _passage_index(container)
    if passages is not None:
        passages_ok, passages_detail, passage_facts = passages
        record(
            "passage_index",
            passages_ok,
            passages_detail,
            optional=True,
            brief="Writing is waiting too long to be read in; the writing itself is safe.",
            **passage_facts,
        )

    pictures = await _picture_processing(container)
    if pictures is not None:
        pictures_ok, pictures_detail, picture_facts = pictures
        record(
            "media_processing",
            pictures_ok,
            pictures_detail,
            optional=True,
            brief="Pictures are not being prepared on time; the full pictures are shown instead.",
            **picture_facts,
        )

    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        overall = "unavailable"
    else:
        overall = "ready" if optional_ok else "degraded"
    if not _may_see_details(request, container):
        return {
            "status": overall,
            "checks": {
                name: {"ok": entry["ok"], "optional": entry["optional"]}
                for name, entry in checks.items()
            },
            "reasons": list(briefs.values()),
        }
    reasons = [
        str(entry["detail"]) for entry in checks.values() if not entry["ok"] and entry.get("detail")
    ]
    return {"status": overall, "checks": checks, "reasons": reasons}


def _search_model(container: Container) -> dict[str, object]:
    """The typed embedder this server is set up with (CTX-011): what it can read."""
    embedder = getattr(container, "multimodal_embedder", None)
    if embedder is None:
        return {"available": False, "optional": True, "reason": "switched off"}
    health = embedder.health()
    space = embedder.space
    return {
        "available": health.available,
        "loaded": health.loaded,
        "optional": True,
        "model": space.model,
        "revision": space.revision,
        "dimension": space.dimension,
        "modalities": sorted(m.value for m in space.modalities),
        "task_set": space.task_set,
        "reason": health.reason,
    }


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
                # The page reports its own errors only when it is given a DSN of its own.
                "sentry_web": client_config(container.settings) is not None,
                "release": release_name(container.settings),
                "optional": True,
                "reason": None if sentry_ok else "SENTRY_DSN_BACKEND is not configured",
            },
            "pictures": _picture_capability(container),
            "auth": {
                "mode": container.settings.AUTH_MODE,
                "oidc_configured": oidc_configured,
                "optional": False,
            },
            "search_model": _search_model(container),
            "vector": {
                "available": vectors_ok,
                "optional": True,
                "dimension_supported": vectors_dimension_ok,
                "detail": "pgvector-backed store with recorded embedding provenance",
                "reason": (
                    None
                    if vectors_ok
                    else "the search index is unreachable, or was built with a different "
                    "model from the configured one (rebuild it), or the configured embedding "
                    "dimension does not match the model"
                ),
            },
        },
    }


def _picture_capability(container: Container) -> dict[str, object]:
    """Whether this server can prepare pictures (Pillow present and able to read PNG,
    JPEG and WebP and write WebP), and whether it is switched on. Never breaks startup."""
    from backend.adapters.image_processor import PROCESSOR_VERSION, pillow_capability

    found = pillow_capability()
    enabled = bool(container.settings.MEDIA_PROCESSING_ENABLED)
    available = bool(found.get("available")) and enabled
    reason = None
    if not enabled:
        reason = "picture preparation is switched off (MEDIA_PROCESSING_ENABLED)"
    elif not found.get("available"):
        reason = "Pillow is missing or cannot read PNG, JPEG and WebP and write WebP"
    return {
        "available": available,
        "optional": True,
        "enabled": enabled,
        "pillow_version": found.get("version"),
        "formats": {key: bool(found.get(key)) for key in ("png", "jpeg", "webp")},
        "processor_version": PROCESSOR_VERSION,
        "isolated": True,
        "sandbox": _picture_sandbox(container) if available else None,
        "reason": reason,
    }


def _picture_sandbox(container: Container) -> dict[str, object] | None:
    """Which sandbox layers the picture worker gets here (AST-006): the level ("full",
    "partial", "limits_only" or "unknown"), each layer, what a worker was refused, and a
    plain reason when it is less than full. Asked once and remembered."""
    processor = getattr(container, "image_processor", None)
    status_of = getattr(processor, "sandbox_status", None)
    if status_of is None:
        return None
    status = dict(status_of())
    level = status.get("level")
    reasons = {
        "full": None,
        "partial": (
            "Pictures are opened with only part of the sandbox this machine could give "
            "(Landlock or seccomp is missing); the limits still apply."
        ),
        "limits_only": (
            "Pictures are opened with memory, time and file limits only: this machine "
            "gives no Landlock or seccomp sandbox, or it is switched off."
        ),
    }
    status["reason"] = reasons.get(
        str(level), "Whether pictures are opened in a sandbox could not be found out."
    )
    return status
