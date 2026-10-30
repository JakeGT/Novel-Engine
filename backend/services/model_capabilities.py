"""What each model can take (CTX-013): pictures, how much it reads, how long it answers.

A route used to be described by one setting for every model behind it ("this route sees
pictures"). Models behind one provider differ, so each is looked up by name, from:

1. the operator's table (``AI_MODEL_CAPABILITIES``), which always wins;
2. the provider's own list of models, where it publishes one with these details
   (OpenRouter's ``GET /api/v1/models``: ``architecture.input_modalities``,
   ``context_length``, ``top_provider.max_completion_tokens``, ``supported_parameters``),
   read at most once a day and kept in ``model_capabilities`` (migration 0059);
3. what the connector found on the author's computer (SCN-002A), kept by the connector;
4. otherwise nothing is known, and the route's own settings decide, as before.

Whether pictures may go to a route at all stays the operator's choice (``AI_IMAGE_INPUT``,
``AI_IMAGE_PROVIDERS``): a list saying a model sees pictures never sends them where the
operator has not allowed it; a list saying a model does not see them makes the route
give their descriptions instead.

Only the request for the list goes to the provider: no key, no story words.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import replace
from typing import Any

from backend.core.logging import log_event
from backend.domain.model_capabilities import (
    OPENROUTER,
    ModelCapability,
    openrouter_capabilities,
    parse_operator_table,
)

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
# A provider's list is read again after a day; a failed read is not tried for an hour.
REFRESH_SECONDS = 24 * 3600
RETRY_SECONDS = 3600
# The list is a few megabytes at most; anything larger is not read.
MAX_LIST_BYTES = 16 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 10.0
# The most a request (a preview, a job's limits) waits for a list being read.
ENSURE_WAIT_SECONDS = 5.0

Fetch = Callable[[str], Awaitable[bytes]]


async def fetch_list(url: str) -> bytes:
    """Read a provider's public model list: no key, no redirects, a size limit."""
    import httpx

    async with (
        httpx.AsyncClient(timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=False) as client,
        client.stream("GET", url, headers={"Accept": "application/json"}) as response,
    ):
        response.raise_for_status()
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > MAX_LIST_BYTES:
                raise ValueError("the model list is larger than expected")
            chunks.append(chunk)
    return b"".join(chunks)


# Providers whose public list says what each model takes, and where it is.
LISTS: dict[str, tuple[str, Callable[[Any], list[ModelCapability]]]] = {
    OPENROUTER: (OPENROUTER_MODELS_URL, openrouter_capabilities),
}


class ModelCapabilityService:
    """Looks a model up (synchronously, from memory) and keeps providers' lists fresh."""

    def __init__(
        self,
        *,
        table: str = "",
        store: Any = None,
        fetch: Fetch | None = fetch_list,
        read_lists: bool = True,
        clock: Callable[[], float] = time.time,
        wait_seconds: float = ENSURE_WAIT_SECONDS,
    ) -> None:
        self._table = parse_operator_table(table)
        self._store = store
        self._fetch = fetch
        self._read_lists = read_lists and fetch is not None
        self._clock = clock
        self._known: dict[str, dict[str, ModelCapability]] = {}
        # provider -> when its list was read (or last tried and failed).
        self._fresh_at: dict[str, float] = {}
        self._failed_at: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # provider -> the reading under way, shared by every request waiting for it.
        self._reading: dict[str, asyncio.Task[None]] = {}
        self._wait = wait_seconds

    def lookup(self, provider: str, model: str) -> ModelCapability | None:
        """What is known of one model: the operator's table over the provider's list."""
        listed = self._known.get(provider, {}).get(model)
        chosen = self._operator(provider, model)
        if chosen is None:
            return listed
        return chosen.over(listed) if listed is not None else replace(chosen, model=model)

    def _operator(self, provider: str, model: str) -> ModelCapability | None:
        exact = self._table.get(f"{provider}/{model}")
        if exact is not None:
            return exact
        best: ModelCapability | None = None
        for entry in self._table.values():
            if entry.provider != provider or not entry.model.endswith("*"):
                continue
            prefix = entry.model[:-1]
            if model.startswith(prefix) and (best is None or len(prefix) > len(best.model) - 1):
                best = entry
        return best

    def has_list(self, provider: str) -> bool:
        return provider in LISTS

    async def ensure(self, provider: str) -> None:
        """Make the provider's list fresh, when it publishes one. Never raises: a list
        that cannot be read leaves what was known (or nothing) and the route's settings.

        Never waits long either: a slow list (or a slow store) is waited for at most
        ``ENSURE_WAIT_SECONDS``; after that the request goes on with what is known (or
        nothing), and the list is still read in the background for the next one."""
        if provider not in LISTS:
            return
        now = self._clock()
        if now - self._fresh_at.get(provider, float("-inf")) < REFRESH_SECONDS:
            return
        if now - self._failed_at.get(provider, float("-inf")) < RETRY_SECONDS:
            return
        task = self._reading.get(provider)
        if task is None or task.done():
            task = asyncio.create_task(self._read(provider))
            self._reading[provider] = task
        try:
            # Shielded: a request that stops waiting never stops the reading.
            await asyncio.wait_for(asyncio.shield(task), timeout=self._wait)
        except TimeoutError:
            log_event(logger, "ai.models.list_slow", level=logging.INFO, provider=provider)

    async def _read(self, provider: str) -> None:
        lock = self._locks.setdefault(provider, asyncio.Lock())
        async with lock:
            if self._clock() - self._fresh_at.get(provider, float("-inf")) < REFRESH_SECONDS:
                return
            try:
                await self._refresh(provider)
            except Exception as error:  # noqa: BLE001 - a missing list never stops work
                self._failed_at[provider] = self._clock()
                log_event(
                    logger,
                    "ai.models.list_unread",
                    level=logging.WARNING,
                    provider=provider,
                    error_type=type(error).__name__,
                )

    async def _refresh(self, provider: str) -> None:
        if self._store is not None and provider not in self._known:
            stored, fetched_at = await self._store.load(provider)
            if stored:
                self._remember(provider, stored, fetched_at)
                if self._clock() - fetched_at < REFRESH_SECONDS:
                    return
        if not self._read_lists or self._fetch is None:
            if provider not in self._fresh_at:
                self._failed_at[provider] = self._clock()
            return
        url, parse = LISTS[provider]
        raw = await self._fetch(url)
        # A list of a few megabytes is parsed off the event loop (the performance rule).
        found = await asyncio.to_thread(lambda: parse(json.loads(raw)))
        if not found:
            raise ValueError("the model list was empty")
        now = self._clock()
        self._remember(provider, found, now)
        if self._store is not None:
            await self._store.save(provider, found)
        log_event(logger, "ai.models.list_read", provider=provider, models=len(found))

    def _remember(self, provider: str, found: Iterable[ModelCapability], at: float) -> None:
        self._known[provider] = {item.model: item for item in found}
        self._fresh_at[provider] = at

    def known_models(self, provider: str) -> int:
        return len(self._known.get(provider, {}))


__all__ = [
    "LISTS",
    "OPENROUTER",
    "OPENROUTER_MODELS_URL",
    "ModelCapability",
    "ModelCapabilityService",
    "fetch_list",
    "openrouter_capabilities",
    "parse_operator_table",
]
