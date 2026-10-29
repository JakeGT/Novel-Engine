"""Previewable, redacted diagnostics/support bundle.

Emits environment, versions, capability flags, and (optionally) migration status
and record counts. It never includes secrets, DSNs, tokens, or manuscript text.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
import time
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel

from backend.core.config import Settings
from backend.core.config import settings as default_settings
from backend.core.logging import redact_text

VERSION = "1"


def _safe_dsn_host(host: str) -> str:
    return host or "unset"


def ai_route(settings: Settings) -> dict[str, Any]:
    """Which model route a self-hosted install uses: a provider, a model on this machine, or none."""
    base = settings.AI_BASE_URL.strip()
    host = urlsplit(base).hostname or "" if base else ""
    local = host in {"localhost", "127.0.0.1", "::1", "host.docker.internal"} or host.endswith(
        ".local"
    )
    if not settings.OPENAI_API_KEY and not base:
        kind = "none"
    elif local:
        kind = "local"
    else:
        kind = "provider"
    return {
        "kind": kind,
        "provider": settings.AI_PROVIDER,
        # The host only: a URL can carry credentials or tokens in its path or query.
        "host": host or ("api.openai.com" if settings.OPENAI_API_KEY else ""),
        "model": settings.AI_MODEL,
    }


def embedding_route(settings: Settings) -> dict[str, Any]:
    """Which model makes search vectors, and for a service its host only (never the key)."""
    provider = settings.EMBEDDING_PROVIDER
    if provider == "api":
        host = urlsplit(settings.EMBEDDING_API_BASE_URL.strip()).hostname or ""
        return {
            "provider": provider,
            "host": host,
            "model": settings.EMBEDDING_API_MODEL,
            "dimension": settings.EMBEDDING_API_DIMENSION,
            "key_set": bool(settings.EMBEDDING_API_KEY),
        }
    if provider == "embeddinggemma":
        return {
            "provider": provider,
            "model": settings.EMBEDDING_GEMMA_MODEL,
            "dimension": settings.EMBEDDING_GEMMA_DIMENSION,
        }
    return {
        "provider": provider,
        "model": settings.EMBEDDING_MODEL_NAME,
        "dimension": settings.EMBEDDING_DIMENSION,
    }


class _Ready(BaseModel):
    ready: bool


async def check_ai(settings: Settings, gateway: Any = None) -> dict[str, Any]:
    """Ask the configured route two tiny questions: one in prose, one with a required shape.

    Nothing from the author's story is sent, and no answer text is kept; only whether each
    worked, how long it took, and the kind of error if it did not."""
    if gateway is None:
        from backend.adapters.ai import OpenAIGateway

        gateway = OpenAIGateway(
            settings.OPENAI_API_KEY,
            settings.AI_MODEL,
            provider=settings.AI_PROVIDER,
            base_url=settings.AI_BASE_URL,
        )
    result: dict[str, Any] = {"route": ai_route(settings)}
    if result["route"]["kind"] == "none":
        result["ok"] = False
        result["problem"] = "No model is configured: set AI_BASE_URL or OPENAI_API_KEY."
        return result
    messages = [{"role": "user", "content": "Reply with the single word: ready"}]
    for name, call in (
        ("prose", lambda: gateway.generate_text(messages=messages, max_tokens=8)),
        (
            "shaped",
            lambda: gateway.generate_structured(
                messages=[{"role": "user", "content": "Is this working? Answer ready=true."}],
                response_format=_Ready,
            ),
        ),
    ):
        start = time.perf_counter()
        try:
            await call()
            result[name] = {"ok": True, "ms": round((time.perf_counter() - start) * 1000)}
        except Exception as error:  # noqa: BLE001 - every failure is reported, by kind only
            result[name] = {"ok": False, "error": type(error).__name__}
    result["ok"] = bool(result["prose"]["ok"] and result["shaped"]["ok"])
    if not result["ok"]:
        result["problem"] = (
            "The model route did not answer as needed. Check that the server is running, the "
            "model name exists there, and (for a local server) that it is reachable from here."
        )
    aclose = getattr(gateway, "aclose", None)
    if aclose is not None:
        await aclose()
    return result


def picture_capability(settings: Settings) -> dict[str, Any]:
    """Whether pictures can be prepared here (OPS-007): Pillow present, its version, the
    formats it can read and write, the processor version and the worker's limits."""
    from backend.adapters.image_processor import PROCESSOR_VERSION, pillow_capability

    found = pillow_capability()
    return {
        "enabled": settings.MEDIA_PROCESSING_ENABLED,
        "pillow_available": bool(found.get("available")),
        "pillow_version": found.get("version"),
        "formats": {key: bool(found.get(key)) for key in ("png", "jpeg", "webp")},
        "processor_version": PROCESSOR_VERSION,
        "limits": {
            "max_pixels": settings.MEDIA_MAX_PIXELS,
            "max_dimension": settings.MEDIA_MAX_DIMENSION,
            "worker_memory_bytes": settings.MEDIA_WORKER_MEMORY_BYTES,
            "worker_cpu_seconds": settings.MEDIA_WORKER_CPU_SECONDS,
            "worker_timeout_seconds": settings.MEDIA_WORKER_TIMEOUT_SECONDS,
            "workspace_quota_bytes": settings.MEDIA_WORKSPACE_QUOTA_BYTES,
        },
    }


def build_diagnostics(
    settings: Settings,
    *,
    postgres_status: str | None = None,
    neo4j_status: str | None = None,
    counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "diagnostics_version": VERSION,
        "python": platform.python_version(),
        "platform": platform.system(),
        "application": {
            "project": settings.PROJECT_NAME,
            "version": settings.PROJECT_VERSION,
            "app_env": settings.APP_ENV,
            "auth_mode": settings.AUTH_MODE,
            "is_hosted": settings.is_hosted,
        },
        "capabilities": {
            "ai_configured": bool(settings.OPENAI_API_KEY or settings.AI_BASE_URL),
            "ai_route": ai_route(settings),
            "sentry_enabled": bool(settings.SENTRY_DSN_BACKEND) and settings.APP_ENV != "test",
            "credentials_configured": bool(settings.CREDENTIAL_KEK),
            "embeddings": embedding_route(settings),
            "pictures": picture_capability(settings),
        },
        "stores": {
            "postgres_host": _safe_dsn_host(settings.POSTGRES_HOST),
            "postgres_db": settings.POSTGRES_DB,
            "postgres_status": postgres_status,
            "neo4j_host": redact_text(settings.NEO4J_URI),
            "neo4j_status": neo4j_status,
        },
        "counts": counts or {},
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="backend.devtools.diagnostics")
    parser.add_argument("--output", default=None, help="write JSON to a file instead of stdout")
    parser.add_argument(
        "--check-ai",
        action="store_true",
        help="ask the configured model two tiny questions (no story text is sent)",
    )
    args = parser.parse_args(argv)

    payload = build_diagnostics(default_settings)
    if args.check_ai:
        payload["ai_check"] = asyncio.run(check_ai(default_settings))
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(rendered)
        print(f"diagnostics written to {args.output}")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
