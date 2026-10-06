"""Previewable, redacted diagnostics/support bundle.

Emits environment, versions, capability flags, and (optionally) migration status
and record counts. It never includes secrets, DSNs, tokens, or manuscript text.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from collections.abc import Sequence
from typing import Any

from backend.core.config import Settings
from backend.core.config import settings as default_settings
from backend.core.logging import redact_text

VERSION = "1"


def _safe_dsn_host(host: str) -> str:
    return host or "unset"


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
            "ai_configured": bool(settings.OPENAI_API_KEY),
            "sentry_enabled": bool(settings.SENTRY_DSN_BACKEND) and settings.APP_ENV != "test",
            "credentials_configured": bool(settings.CREDENTIAL_KEK),
            "embeddings": {
                "model": settings.EMBEDDING_MODEL_NAME,
                "dimension": settings.EMBEDDING_DIMENSION,
            },
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
    args = parser.parse_args(argv)

    payload = build_diagnostics(default_settings)
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
