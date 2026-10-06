"""Fail-closed guards for destructive development commands.

A reset must never be able to touch production. The guard refuses when the
environment is production, when it is unknown, or when the environment was not
explicitly declared and the configured stores are not local. A missing
``APP_ENV`` therefore cannot silently authorize a reset against remote stores.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from urllib.parse import urlparse

from backend.core.config import Settings

ALLOWED_ENVIRONMENTS = ("development", "test")
LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "db", "postgres", "neo4j"}


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
    explicitly_declared = declared is not None or "APP_ENV" in settings.model_fields_set
    app_env = declared or settings.APP_ENV

    if app_env == "production":
        raise ResetNotAllowed("refusing to reset a production environment")
    if app_env not in ALLOWED_ENVIRONMENTS:
        raise ResetNotAllowed(f"refusing to reset unknown environment {app_env!r}")

    if not confirm:
        raise ResetNotAllowed("reset requires explicit confirmation (--force)")

    if not explicitly_declared:
        postgres_local = _is_local_host(settings.POSTGRES_HOST)
        neo4j_local = _is_local_host(_uri_host(settings.NEO4J_URI))
        if not (postgres_local and neo4j_local):
            raise ResetNotAllowed(
                "APP_ENV is not explicitly set and stores are not local; refusing to reset"
            )

    return app_env
