"""Fail-closed guards for destructive development commands.

A reset must never be able to touch production or any non-local store. The guard
refuses when the environment is production, when it is unknown, when explicit
confirmation is missing, or when either configured store is not a local
development host — regardless of how ``APP_ENV`` was declared. A missing or
explicit ``APP_ENV`` therefore cannot authorize a reset against remote stores.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

from backend.core.config import Settings

ALLOWED_ENVIRONMENTS = ("development", "test")
LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "db", "postgres", "neo4j"}
REPO_ROOT = Path(__file__).resolve().parents[2]


class ResetNotAllowed(RuntimeError):
    """Raised when a destructive command is not permitted in the current environment."""


def ensure_seed_allowed(settings: Settings) -> None:
    """Refuse to write seed canon into production."""
    if settings.APP_ENV == "production":
        raise ResetNotAllowed("refusing to seed a production environment")


def _is_local_host(host: str) -> bool:
    return host.strip().lower() in LOCAL_HOSTNAMES


def _uri_host(uri: str) -> str:
    parsed = urlparse(uri)
    return parsed.hostname or ""


def ensure_reset_allowed(
    settings: Settings,
    *,
    confirm: bool,
    environ: Mapping[str, str] | None = None,
) -> str:
    environment = dict(environ if environ is not None else os.environ)
    declared = environment.get("APP_ENV")
    app_env = declared or settings.APP_ENV

    if app_env == "production":
        raise ResetNotAllowed("refusing to reset a production environment")
    if app_env not in ALLOWED_ENVIRONMENTS:
        raise ResetNotAllowed(f"refusing to reset unknown environment {app_env!r}")

    if not confirm:
        raise ResetNotAllowed("reset requires explicit confirmation (--force)")

    postgres_host = settings.POSTGRES_HOST
    neo4j_host = _uri_host(settings.NEO4J_URI)
    if not (_is_local_host(postgres_host) and _is_local_host(neo4j_host)):
        raise ResetNotAllowed(
            "refusing to reset non-local stores "
            f"(postgres={postgres_host!r}, neo4j={neo4j_host!r}); "
            "reset only supports local development stores"
        )

    return app_env


def ensure_artifact_dir_allowed(directory: str, *, repo_root: Path = REPO_ROOT) -> Path:
    """Resolve a debug artifact directory and require it to be inside the repository.

    This prevents a misconfigured absolute or broad ``ARTIFACT_STORE_DIR`` from
    recursively deleting data outside the project.
    """
    root = Path(directory).expanduser()
    if not root.is_absolute():
        root = repo_root / root
    resolved = root.resolve()
    repo = repo_root.resolve()
    if resolved == repo or repo not in resolved.parents:
        raise ResetNotAllowed(
            f"refusing to clean artifact directory outside the repository: {resolved}"
        )
    return resolved
