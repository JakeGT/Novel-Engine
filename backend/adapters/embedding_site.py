"""A delegating embedder that follows the site's search setup as it changes.

The server builds one embedder at start-up and hands it to the vector store, the index
service, diagnostics and the CLI. This wrapper keeps that single-handle contract while
the embedder actually used changes, without a restart, by three rules:

1. The server's settings say ``EMBEDDING_PROVIDER=api`` and have a key (or a local
   address): those settings (source "server"); the page shows them and cannot change them.
2. Otherwise a setup saved on the page: an ``ApiTextEmbedding`` from it and its sealed
   key (source "page").
3. Otherwise the server's own model as today (source "built_in"), or nothing
   (source "off" — ``none``, or the model is not installed, as in the Docker image).

It re-reads the stored setup when its version changes, polling at most every ten
seconds; a change made in this process (a save or a delete) is picked up at once through
``refresh()``. The key is opened only while the embedder is built, held in memory only,
and never appears in a log or a repr.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from backend.adapters.embedding import api_embedder
from backend.adapters.embedding_api import WhereWordsGo, is_local_host
from backend.core.logging import log_event
from backend.domain.credentials import CredentialUnavailable
from backend.domain.embedding import (
    PLAIN_TASKS,
    EmbeddingHealth,
    EmbeddingInput,
    EmbeddingSpace,
    EmbeddingTask,
    EmbeddingUnavailableError,
    EmbeddingVector,
    Modality,
)
from backend.domain.embedding_providers import embedding_provider

REFRESH_SECONDS = 10.0

logger = logging.getLogger(__name__)

_OFF_SPACE = EmbeddingSpace(
    model="off",
    revision="off",
    dimension=1,
    native_dimension=1,
    modalities=frozenset({Modality.TEXT}),
    tasks=dict(PLAIN_TASKS),
    task_set="plain",
)


class SiteEmbedder:
    def __init__(self, *, settings: Any, store: Any, built_in: Any, transport: Any = None) -> None:
        self._settings = settings
        self._store = store
        self._built_in = built_in
        # A test seam: the HTTP transport for embedders built from the page setup.
        self._transport = transport
        self._current: Any = None
        self._source = "off"
        self._version = -1
        self._resolved = False
        self._checked_at = 0.0
        self._unreadable_logged = False
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"SiteEmbedder(source={self._source!r})"

    # --- which embedder is in use ------------------------------------------------

    def _server_embedder(self) -> Any | None:
        provider = str(getattr(self._settings, "EMBEDDING_PROVIDER", "minilm")).lower()
        if provider == "api" and self._built_in is not None and self._built_in.available():
            return self._built_in
        return None

    def _page_embedder(self) -> Any | None:
        setup = self._store.get_setup()
        if setup is None:
            return None
        spec = embedding_provider(setup.provider)
        base_url = str(setup.base_url or (spec.base_url if spec is not None else ""))
        host = str(urlsplit(base_url).hostname or "").lower()
        api_key = ""
        if not is_local_host(host):
            try:
                api_key = self._store.resolve_plaintext()
            except CredentialUnavailable:
                api_key = ""  # no key (or the KEK is gone): the embedder is not usable
        return api_embedder(
            provider_id=setup.provider,
            base_url=base_url,
            api_key=api_key,
            model=setup.model,
            dimension=setup.dimension,
            name=spec.label if spec is not None else "",
            transport=self._transport,
        )

    def _choose(self) -> tuple[Any | None, str]:
        server = self._server_embedder()
        if server is not None:
            return server, "server"
        page = self._page_embedder()
        if page is not None:
            return page, "page"
        if self._built_in is not None and self._built_in.available():
            return self._built_in, "built_in"
        return None, "off"

    def _choose_without_page(self) -> tuple[Any | None, str]:
        server = self._server_embedder()
        if server is not None:
            return server, "server"
        if self._built_in is not None and self._built_in.available():
            return self._built_in, "built_in"
        return None, "off"

    def refresh(self) -> None:
        """Re-read the stored setup and rebuild the embedder when its version changed.

        An explicit call (after a save or a delete) may raise if the store cannot be
        read; the periodic poll uses :meth:`_refresh_periodic` instead.
        """
        version = self._store.current_version_number()
        with self._lock:
            if self._resolved and version == self._version:
                self._checked_at = time.monotonic()
                self._unreadable_logged = False
                return
        current, source = self._choose()
        with self._lock:
            self._current = current
            self._source = source
            self._version = version
            self._resolved = True
            self._checked_at = time.monotonic()
            self._unreadable_logged = False

    def _refresh_periodic(self) -> None:
        """Poll for changes, surviving a store outage without breaking search.

        The embedder already chosen is kept; before one has ever been chosen, the
        server's own model (else off) stands in until the page setup can be read again.
        One warning is logged per run of failures, and the next interval tries again.
        """
        try:
            self.refresh()
        except Exception as error:  # noqa: BLE001 - a transient store outage must not break search
            if not self._unreadable_logged:
                log_event(
                    logger,
                    "site.search_setup_unreadable",
                    level=logging.WARNING,
                    error_type=type(error).__name__,
                )
                self._unreadable_logged = True
            with self._lock:
                if not self._resolved:
                    self._current, self._source = self._choose_without_page()
                self._checked_at = time.monotonic()

    def _current_embedder(self) -> Any | None:
        if time.monotonic() - self._checked_at >= REFRESH_SECONDS:
            self._refresh_periodic()
        return self._current

    @property
    def source(self) -> str:
        self._current_embedder()
        return self._source

    def current(self) -> Any | None:
        """The embedder in use now (or None when off); for building and checking."""
        return self._current_embedder()

    def built_in_info(self) -> dict[str, Any] | None:
        provider = str(getattr(self._settings, "EMBEDDING_PROVIDER", "minilm")).lower()
        # Only a model that can run: otherwise turning a service off means off.
        if (
            provider in ("minilm", "embeddinggemma", "fake")
            and self._built_in is not None
            and self._built_in.available()
        ):
            return {
                "model": self._built_in.space.model,
                "dimension": self._built_in.space.dimension,
            }
        return None

    # --- the port -----------------------------------------------------------------

    @property
    def space(self) -> EmbeddingSpace:
        current = self._current_embedder()
        return current.space if current is not None else _OFF_SPACE

    def available(self) -> bool:
        current = self._current_embedder()
        return current is not None and current.available()

    def loaded(self) -> bool:
        current = self._current_embedder()
        return current is not None and current.loaded()

    def health(self) -> EmbeddingHealth:
        current = self._current_embedder()
        if current is None:
            reason = "search by meaning is switched off"
            provider = str(getattr(self._settings, "EMBEDDING_PROVIDER", "minilm")).lower()
            if provider != "none" and self._built_in is not None:
                reason = self._built_in.health().reason or reason
            return EmbeddingHealth(
                available=False, loaded=False, modalities=frozenset(), reason=reason
            )
        return current.health()

    def embed(
        self, inputs: Sequence[EmbeddingInput], *, task: EmbeddingTask
    ) -> list[EmbeddingVector]:
        current = self._current_embedder()
        if current is None:
            raise EmbeddingUnavailableError("search by meaning is switched off")
        return current.embed(inputs, task=task)

    def where_words_go(self) -> WhereWordsGo:
        current = self._current_embedder()
        if current is None:
            return WhereWordsGo(kind="off")
        where = getattr(current, "where_words_go", None)
        if where is not None:
            return where()
        return WhereWordsGo(kind="server")


__all__ = ["REFRESH_SECONDS", "SiteEmbedder"]
