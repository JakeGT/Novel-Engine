"""Search vectors from an embedding service over HTTP (``EMBEDDING_PROVIDER=api``).

Any service with an OpenAI-compatible ``POST {base}/embeddings`` works: OpenAI, Google
(Gemini), Mistral, OpenRouter, or a model on this machine (Ollama, LM Studio). Text
only; pictures stay out of search by meaning with this option.

Choosing it sends every passage of every story to that service in the background, to
make it searchable; the site says so to its authors (``where_words_go``). Nothing here
logs the words sent, the service's answer, or the key.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from backend.core.logging import log_event
from backend.domain.embedding import (
    PLAIN_TASKS,
    EmbeddingError,
    EmbeddingHealth,
    EmbeddingInput,
    EmbeddingSpace,
    EmbeddingTask,
    EmbeddingUnavailableError,
    EmbeddingVector,
    Modality,
    TextInput,
    format_text,
    normalize,
)

logger = logging.getLogger(__name__)


class EmbeddingApiRefused(EmbeddingUnavailableError):
    """The service answered with a clear refusal; its status is kept so a caller can
    tell a refused key (401/403) from a refused model or size (400/404/422)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class EmbeddingCheck:
    """What checking a setup against the service found (one word, one try)."""

    # True when the service answered and the vector is the size asked for.
    ok: bool
    # The service's status when it refused; None when it could not be reached.
    status: int | None = None
    # The vector's size when it differs from the one asked for.
    length: int | None = None
    # A 2xx answer that is not an embeddings response (an HTML page, say).
    unreadable: bool = False
    # The error type when the service could not be reached.
    failure: str | None = None


LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "host.docker.internal"})
# Well under the common limits (OpenAI: 2,048 inputs and 300,000 tokens a request).
MAX_INPUTS_PER_REQUEST = 64
MAX_CHARACTERS_PER_REQUEST = 120_000
# Busy or failing services are tried again, briefly; then the passage waits its turn.
ATTEMPTS = 3
MAX_WAIT_SECONDS = 10.0
# A query is made while someone waits for a page: one quick try, then search falls back to
# words. Writing into the index happens in the background and can wait longer.
QUERY_TIMEOUT_SECONDS = 8.0


def is_local_host(host: str) -> bool:
    return host in LOCAL_HOSTS or host.endswith(".local")


@dataclass(frozen=True)
class WhereWordsGo:
    """Where an author's writing is sent to be searchable by meaning, in plain terms."""

    # "server" (this site's own computer), "service" (another company's service) or "off".
    kind: str
    # The service's address (host only) when it is not this server.
    host: str | None = None
    # The name the operator gave it, or the host.
    name: str | None = None


def _requests(texts: Sequence[str]) -> list[list[int]]:
    """Positions of ``texts`` in groups that fit one request each."""
    groups: list[list[int]] = []
    current: list[int] = []
    size = 0
    for index, text in enumerate(texts):
        length = len(text)
        if current and (
            len(current) >= MAX_INPUTS_PER_REQUEST or size + length > MAX_CHARACTERS_PER_REQUEST
        ):
            groups.append(current)
            current, size = [], 0
        current.append(index)
        size += length
    if current:
        groups.append(current)
    return groups


class ApiTextEmbedding:
    """Text search vectors from an OpenAI-compatible embeddings endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        dimension: int,
        send_dimension: bool = True,
        name: str = "",
        dimension_field: str = "dimensions",
        query_field: tuple[str, str, str] | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self._base_url = base_url.strip().rstrip("/")
        parts = urlsplit(self._base_url)
        self._host = (parts.hostname or "").lower()
        self._scheme = parts.scheme.lower()
        self._api_key = api_key.strip()
        self._model = model.strip()
        self._send_dimension = send_dimension
        self._dimension_field = dimension_field
        self._query_field = query_field
        self._name = name.strip() or self._host
        self._timeout = timeout_seconds
        self._transport = transport
        self._sleep = sleep
        self._client: httpx.Client | None = None
        self._answered = False
        self._space = EmbeddingSpace(
            model=self._model or "unset",
            # The service is part of the space: the same model name elsewhere may differ.
            revision=f"api:{self._host or 'unset'}",
            dimension=dimension,
            native_dimension=dimension,
            modalities=frozenset({Modality.TEXT}),
            tasks=dict(PLAIN_TASKS),
            task_set="plain",
        )

    @property
    def space(self) -> EmbeddingSpace:
        return self._space

    @property
    def local(self) -> bool:
        return is_local_host(self._host)

    def where_words_go(self) -> WhereWordsGo:
        if self.local:
            return WhereWordsGo(kind="server", host=self._host, name=self._name)
        return WhereWordsGo(kind="service", host=self._host, name=self._name)

    def _missing(self) -> str | None:
        if not self._host or self._scheme not in {"http", "https"}:
            return "no embedding service address is set (EMBEDDING_API_BASE_URL)"
        if self._scheme != "https" and not self.local:
            return "the embedding service must be reached over https"
        if not self._model:
            return "no embedding model is named (EMBEDDING_API_MODEL)"
        if not self._api_key and not self.local:
            return "no key for the embedding service is set (EMBEDDING_API_KEY)"
        return None

    def available(self) -> bool:
        return self._missing() is None

    def loaded(self) -> bool:
        return self._answered

    def health(self) -> EmbeddingHealth:
        # Never calls the service: health is read often and must cost nothing.
        reason = self._missing()
        return EmbeddingHealth(
            available=reason is None,
            loaded=self._answered,
            modalities=self._space.modalities,
            reason=reason,
        )

    def _http(self) -> httpx.Client:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._client = httpx.Client(
                base_url=self._base_url,
                headers=headers,
                timeout=self._timeout,
                transport=self._transport,
                follow_redirects=False,
            )
        return self._client

    def _body(self, texts: list[str], *, task: EmbeddingTask) -> dict[str, Any]:
        body: dict[str, Any] = {"model": self._model, "input": texts, "encoding_format": "float"}
        if self._send_dimension:
            body[self._dimension_field] = self._space.dimension
        if self._query_field is not None:
            field, query_value, document_value = self._query_field
            body[field] = query_value if task is EmbeddingTask.QUERY else document_value
        return body

    def _post(
        self, texts: list[str], *, task: EmbeddingTask, waiting: bool = False
    ) -> list[list[float]]:
        body = self._body(texts, task=task)
        started = time.monotonic()
        status: int | None = None
        failure: str | None = None
        attempts = 1 if waiting else ATTEMPTS
        timeout = min(self._timeout, QUERY_TIMEOUT_SECONDS) if waiting else self._timeout
        for attempt in range(1, attempts + 1):
            response: httpx.Response | None = None
            try:
                response = self._http().post("/embeddings", json=body, timeout=timeout)
            except httpx.HTTPError as error:
                status = None
                failure = type(error).__name__
            else:
                status = response.status_code
                failure = None
                if 200 <= status < 300:
                    vectors = self._read(response, len(texts))
                    log_event(
                        logger,
                        "embedding.api",
                        host=self._host,
                        inputs=len(texts),
                        ms=round((time.monotonic() - started) * 1000, 1),
                        attempts=attempt,
                    )
                    return vectors
                if status in {400, 401, 403, 404, 422}:
                    # Trying again unchanged will not help; the reason is the status alone,
                    # since a service's own message can quote what was sent.
                    log_event(
                        logger,
                        "embedding.api_refused",
                        level=logging.WARNING,
                        host=self._host,
                        status=status,
                    )
                    raise EmbeddingApiRefused(status, _refusal(status))
            if attempt < attempts:
                self._sleep(_wait(response, attempt))
        log_event(
            logger,
            "embedding.api_failed",
            level=logging.WARNING,
            host=self._host,
            status=status,
            error_type=failure,
        )
        raise EmbeddingUnavailableError(
            "the embedding service did not answer; it will be tried again"
        )

    def _read(self, response: httpx.Response, expected: int) -> list[list[float]]:
        try:
            data = response.json()["data"]
            ordered = sorted(data, key=lambda item: int(item["index"]))
            vectors = [[float(value) for value in item["embedding"]] for item in ordered]
        except (ValueError, KeyError, TypeError) as error:
            raise EmbeddingError("the embedding service answered in an unexpected form") from error
        if len(vectors) != expected:
            raise EmbeddingError(
                f"the embedding service returned {len(vectors)} vectors for {expected} texts"
            )
        self._answered = True
        return vectors

    def embed(
        self, inputs: Sequence[EmbeddingInput], *, task: EmbeddingTask
    ) -> list[EmbeddingVector]:
        from backend.adapters.embedding import _refuse_unsupported, _vector

        _refuse_unsupported(self._space, inputs)
        if not inputs:
            return []
        reason = self._missing()
        if reason is not None:
            raise EmbeddingUnavailableError(reason)
        items = [item for item in inputs if isinstance(item, TextInput)]
        texts = [format_text(self._space.tasks, task, item) for item in items]
        raw: list[list[float]] = [[] for _ in texts]
        for group in _requests(texts):
            sent = self._post(
                [texts[i] for i in group],
                task=task,
                waiting=task is EmbeddingTask.QUERY,
            )
            for position, values in zip(group, sent, strict=True):
                raw[position] = values
        out: list[EmbeddingVector] = []
        for item, values in zip(items, raw, strict=True):
            if len(values) != self._space.dimension:
                raise EmbeddingError(
                    f"the embedding service returned {len(values)} numbers, expected "
                    f"{self._space.dimension} (check EMBEDDING_API_DIMENSION)"
                )
            # A shortened vector is not unit length; search compares unit vectors.
            out.append(_vector(self._space, normalize(values), task, item))
        return out

    def check(self, word: str) -> EmbeddingCheck:
        """One embedding of a fixed word, to see whether the service accepts this setup.

        Used when a key and model are being saved on the page: it embeds the single
        fixed word ``"lighthouse"`` (never any author's words), is tried once, and
        reports the outcome without raising. A refused key, a refused model or size,
        an unreachable service and a wrong-sized answer are told apart by the caller.
        """
        body = self._body([word], task=EmbeddingTask.QUERY)
        try:
            response = self._http().post("/embeddings", json=body, timeout=self._timeout)
        except httpx.HTTPError as error:
            return EmbeddingCheck(ok=False, failure=type(error).__name__)
        status = response.status_code
        if not 200 <= status < 300:
            return EmbeddingCheck(ok=False, status=status)
        try:
            data = response.json()["data"]
            ordered = sorted(data, key=lambda item: int(item["index"]))
            vectors = [[float(value) for value in item["embedding"]] for item in ordered]
        except (ValueError, KeyError, TypeError):
            return EmbeddingCheck(ok=False, status=status, unreadable=True)
        if not vectors:
            return EmbeddingCheck(ok=False, status=status, unreadable=True)
        length = len(vectors[0])
        if length != self._space.dimension:
            return EmbeddingCheck(ok=False, length=length)
        self._answered = True
        return EmbeddingCheck(ok=True)


def _refusal(status: int) -> str:
    if status in {401, 403}:
        return "the embedding service refused the key"
    if status == 404:
        return "the embedding service does not know that model or address"
    return "the embedding service refused the request (check the model and EMBEDDING_API_DIMENSION)"


def _wait(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        try:
            asked = float(response.headers.get("Retry-After", ""))
        except ValueError:
            asked = 0.0
        if asked > 0:
            return min(asked, MAX_WAIT_SECONDS)
    return min(float(2 ** (attempt - 1)), MAX_WAIT_SECONDS)


__all__ = [
    "ApiTextEmbedding",
    "EmbeddingApiRefused",
    "EmbeddingCheck",
    "WhereWordsGo",
    "is_local_host",
]
