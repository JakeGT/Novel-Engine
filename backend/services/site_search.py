"""The site's search-by-meaning setup: what it is, saving it, and building its index.

A platform administrator sets the site's embedding service (or "your own" address), a
model and a size on a page. The key is checked against the service before it is kept,
then sealed and never returned. Changing the provider or the address is as sensitive as
the key, so it needs a recent sign-in and, when a key is kept, it is the stored one only
while the provider and address are unchanged. The single fixed word ``"lighthouse"`` is
embedded to check (never any author's words).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlsplit

from backend.adapters.embedding import api_embedder
from backend.adapters.embedding_api import is_local_host
from backend.core.logging import log_event
from backend.domain.embedding import EmbeddingError
from backend.domain.embedding_providers import (
    EMBEDDING_PROVIDERS,
    MAX_DIMENSION,
    default_dimension,
    dimension_choices,
    embedding_provider,
)
from backend.domain.errors import ConflictError, ValidationError
from backend.domain.site_search import SiteSearchRefused, SiteSearchSetup
from backend.services.credential_service import _MODEL_NAME, MAX_MODEL_LENGTH
from backend.services.embedding_index import BuildCancelled

logger = logging.getLogger(__name__)

# The fixed word embedded to check a new setup, so no author's writing ever goes to the
# service as part of saving a key.
CHECK_WORD = "lighthouse"
MAX_KEY_LENGTH = 512


def _host_of(provider_id: str, base_url: str | None) -> str:
    spec = embedding_provider(provider_id)
    resolved = base_url or (spec.base_url if spec is not None else "")
    return str(urlsplit(resolved).hostname or "").lower()


def _validate_base_url(value: str) -> str:
    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    host = str(parts.hostname or "").lower()
    if scheme not in ("http", "https") or not host:
        raise ValidationError("That is not a web address for an embedding service.")
    if parts.username or parts.password:
        raise ValidationError("The address must not carry a user name or password.")
    if parts.query or parts.fragment:
        raise ValidationError("The address must not carry a query or fragment.")
    if scheme != "https" and not is_local_host(host):
        raise ValidationError(
            "The embedding service must be reached over https unless it is on this machine."
        )
    return host


class SiteSearchService:
    def __init__(
        self,
        *,
        settings: Any,
        store: Any,
        embedder: Any,
        index: Any,
    ) -> None:
        self._settings = settings
        self._store = store
        self._embedder = embedder
        self._index = index
        # The one background build at a time (site-wide, not a stored job).
        self._build_task: asyncio.Task | None = None

    # --- the providers table -----------------------------------------------------

    def providers(self) -> list[dict[str, Any]]:
        return [
            {
                "id": spec.id,
                "label": spec.label,
                "group": spec.group,
                "base_url": spec.base_url,
                "needs_url": spec.base_url is None,
                "key_url": spec.key_url,
                "key_hint": spec.key_hint,
                "key_optional_when_local": not spec.needs_key,
                "docs": spec.docs,
                "models": [
                    {
                        "id": model.id,
                        "dimension": model.dimension,
                        "adjustable": model.adjustable,
                        "default_dimension": default_dimension(model.id),
                    }
                    for model in spec.models
                ],
                "dimension_choices": dimension_choices(spec.id),
            }
            for spec in EMBEDDING_PROVIDERS
        ]

    # --- the current state -------------------------------------------------------

    def _read_setup(self) -> tuple[SiteSearchSetup | None, Any]:
        return self._store.get_setup(), self._store.get_secret()

    def _embedder_state(self) -> tuple[str, bool, dict[str, Any] | None]:
        return (
            str(self._embedder.source),
            bool(self._embedder.available()),
            self._embedder.built_in_info(),
        )

    def _embedder_source(self) -> str:
        return str(self._embedder.source)

    async def _setup_shape(self) -> dict[str, Any] | None:
        setup, secret = await asyncio.to_thread(self._read_setup)
        if setup is None:
            return None
        spec = embedding_provider(setup.provider)
        return {
            "provider": setup.provider,
            "provider_label": spec.label if spec is not None else setup.provider,
            "model": setup.model,
            "dimension": setup.dimension,
            "base_url": setup.base_url,
            "host": _host_of(setup.provider, setup.base_url),
            "key": {
                "set": secret is not None,
                "hint": secret.key_last4 if secret is not None else None,
                "updated_at": secret.updated_at if secret is not None else None,
            },
            "verified": setup.verified,
            "updated_at": setup.updated_at,
        }

    def _build_state(self) -> str:
        task = self._build_task
        if task is None or task.cancelled():
            return "waiting"
        if not task.done():
            return "running"
        return "failed" if task.exception() is not None else "waiting"

    async def state(self, *, sign_in_needed: bool) -> dict[str, Any]:
        status = await self._index.status()
        source, available, built_in = await asyncio.to_thread(self._embedder_state)
        active_model = status.active["model"] if status.active else None
        building = status.building
        build: dict[str, Any] | None = None
        if building is not None:
            done = int(building["rows"])
            total = done + int((status.building_lag or {}).get("total", 0))
            build = {"done": done, "total": total, "state": self._build_state()}
        if available:
            needs_build = not (status.searchable and status.lag.get("total", 0) == 0)
        else:
            needs_build = False
        return {
            "source": source,
            "editable": source != "server",
            "kek_configured": self._store.is_configured,
            "setup": await self._setup_shape(),
            "built_in": built_in,
            "index": {
                "searchable": status.searchable,
                "active_model": active_model,
                "building": building is not None,
                "build": build,
                "needs_build": needs_build,
            },
            "sign_in_needed": sign_in_needed,
        }

    # --- saving and forgetting ---------------------------------------------------

    async def save(
        self,
        *,
        body: Any,
        actor_user_id: str,
        sign_in_needed: bool,
    ) -> dict[str, Any]:
        if await asyncio.to_thread(self._embedder_source) == "server":
            raise ConflictError(
                "The search setup is set by this server's settings and cannot be changed here."
            )
        spec = embedding_provider(body.provider)
        if spec is None:
            raise ValidationError("Unknown embedding provider.")
        model = (body.model or "").strip()
        if not model or len(model) > MAX_MODEL_LENGTH or not _MODEL_NAME.match(model):
            raise ValidationError("That does not look like a model name.")
        dimension = body.dimension
        if not 1 <= dimension <= MAX_DIMENSION:
            raise ValidationError(f"The size must be between 1 and {MAX_DIMENSION}.")
        choices = dimension_choices(body.provider)
        if choices is not None and dimension not in choices:
            raise ValidationError(
                f"{spec.label} only makes vectors of these sizes: "
                + ", ".join(str(size) for size in choices)
                + "."
            )
        base_url = (body.base_url or "").strip() if body.base_url else ""
        if spec.base_url is None and not base_url:
            raise ValidationError("Give the address of your own embedding service.")
        if base_url:
            host = _validate_base_url(base_url)
        else:
            host = _host_of(body.provider, None)
        resolved_base_url = base_url or (spec.base_url or "")

        local = is_local_host(host)
        key_needed = not local
        if key_needed and not self._store.is_configured:
            raise ConflictError(
                "This site has no credential key set (CREDENTIAL_KEK), so a search key "
                "cannot be stored."
            )

        api_key = body.api_key
        store_key: str | None = None
        check_key = ""
        secret_snapshot = None
        if api_key is not None:
            api_key = api_key.strip()
            if not 8 <= len(api_key) <= MAX_KEY_LENGTH:
                raise ValidationError("The key must be between 8 and 512 characters.")
            if any(character.isspace() for character in api_key):
                raise ValidationError("The key must not contain spaces or new lines.")
            check_key = api_key
            store_key = api_key
        else:
            secret_snapshot = await asyncio.to_thread(self._store.get_secret)
            if key_needed:
                unchanged = (
                    secret_snapshot is not None
                    and secret_snapshot.provider == body.provider
                    and secret_snapshot.host == host
                )
                if not unchanged:
                    raise ValidationError(
                        "Changing the provider or the address needs the key typed again."
                    )
                check_key = await asyncio.to_thread(self._store.resolve_plaintext)

        probe = api_embedder(
            provider_id=body.provider,
            base_url=resolved_base_url,
            api_key=check_key,
            model=model,
            dimension=dimension,
            name=spec.label,
            timeout_seconds=15.0,
        )
        result = await asyncio.to_thread(probe.check, CHECK_WORD)
        if result.ok:
            verified: bool | None = True
            check = "ok"
            message = f"{spec.label} accepted the setup."
        elif result.status in (401, 403):
            raise self._refused(
                "key_refused",
                f"{spec.label} did not accept that key. Check it was copied in full and "
                "has not been revoked.",
            )
        elif result.status in (400, 404, 422):
            raise self._refused(
                "model_refused",
                f"{spec.label} did not accept the model {model} or the size {dimension}. "
                "Check the model name.",
            )
        elif result.unreadable:
            raise self._refused(
                "not_a_service",
                f"{host} answered, but not as an embedding service. Check the address.",
            )
        elif result.length is not None:
            raise self._refused(
                "size_mismatch",
                f"{model} gave {result.length} numbers, not {dimension}. "
                f"Set the size to {result.length}.",
            )
        else:
            # Unreachable, a 5xx, or a timeout: the person may be right and the network
            # wrong, so it is kept but not marked as checked.
            verified = False
            check = "unchecked"
            message = f"{spec.label} could not be reached, so the key was kept unchecked."

        await asyncio.to_thread(
            self._store.save_setup,
            provider=body.provider,
            model=model,
            dimension=dimension,
            base_url=base_url or None,
            updated_by=actor_user_id,
            verified=verified,
        )
        if store_key is not None:
            await asyncio.to_thread(
                self._store.save_secret,
                provider=body.provider,
                host=host,
                plaintext_key=store_key,
                created_by=actor_user_id,
            )
        elif (
            not key_needed
            and secret_snapshot is not None
            and (secret_snapshot.provider != body.provider or secret_snapshot.host != host)
        ):
            # A local setup keeps no key, so a key sealed for another provider or host
            # must not stay behind as if it were in use.
            await asyncio.to_thread(self._store.delete_secret)
        await asyncio.to_thread(self._embedder.refresh)
        log_event(
            logger,
            "site.search_setup_changed",
            provider=body.provider,
            host=host,
            model=model,
            dimension=dimension,
            verified=verified,
        )
        return {
            "check": check,
            "message": message,
            "state": await self.state(sign_in_needed=sign_in_needed),
        }

    @staticmethod
    def _refused(code: str, detail: str) -> ValidationError:
        return SiteSearchRefused(code, detail)

    async def forget(self, *, sign_in_needed: bool) -> dict[str, Any]:
        if await asyncio.to_thread(self._embedder_source) == "server":
            raise ConflictError(
                "The search setup is set by this server's settings and cannot be changed here."
            )
        await asyncio.to_thread(self._store.delete_all)
        await asyncio.to_thread(self._embedder.refresh)
        return await self.state(sign_in_needed=sign_in_needed)

    # --- building the index ------------------------------------------------------

    async def start_build(self, *, sign_in_needed: bool) -> dict[str, Any]:
        if self._build_task is not None and not self._build_task.done():
            raise ConflictError("A search index is already being built.")
        try:
            generation = await self._index.start_build(note="Built from the site's setup page.")
        except EmbeddingError as error:
            raise ConflictError(str(error)) from error
        self._build_task = asyncio.create_task(self._build(generation.id))
        self._build_task.add_done_callback(self._build_finished)
        return await self.state(sign_in_needed=sign_in_needed)

    def _build_finished(self, task: asyncio.Task) -> None:
        """Retrieve the build's outcome and log it, so no failure goes unread."""
        if task.cancelled():
            log_event(logger, "site.search_build_stopped", error_type="CancelledError")
            return
        error = task.exception()
        if error is None:
            return
        if isinstance(error, BuildCancelled):
            log_event(
                logger,
                "site.search_build_stopped",
                error_type=type(error).__name__,
            )
        else:
            log_event(
                logger,
                "site.search_build_failed",
                level=logging.ERROR,
                error_type=type(error).__name__,
            )

    async def _build(self, generation_id: str) -> None:
        for _attempt in range(3):
            await self._index.build_all(generation_id)
            try:
                await self._index.activate(generation_id)
                return
            except EmbeddingError:
                # A text changed while it built: catch up once more, then switch.
                continue
        raise EmbeddingError("the new search index is still missing rows")

    async def cancel_build(self, *, sign_in_needed: bool) -> dict[str, Any]:
        await self._index.cancel()
        return await self.state(sign_in_needed=sign_in_needed)


__all__ = ["CHECK_WORD", "SiteSearchService", "sign_in_needed"]


def sign_in_needed(settings: Any, session: Any) -> bool:
    """Whether a change would be refused now: hosted, and signed in long ago."""
    from backend.domain.site_search import RECENT_SIGN_IN_SECONDS

    if not settings.is_hosted:
        return False
    authenticated_at = getattr(session, "authenticated_at", None)
    if authenticated_at is None:
        return True
    from backend.domain.identity import utcnow

    return (utcnow() - authenticated_at).total_seconds() > RECENT_SIGN_IN_SECONDS
