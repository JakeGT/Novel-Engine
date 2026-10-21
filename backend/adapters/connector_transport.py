"""Requests for a model on the author's own computer, sent through the connector queue.

The OpenAI client used for every provider is given this transport for a computer's
route (``device://<workspace>/<device>``), so structured answers, the prompted fallback
and usage are handled exactly as for any provider. Nothing goes over the network here:
the request is put in the queue for that one computer, and the answer it returns is
handed back to the client as if the model server had answered (SCN-002A, ADR-025).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

try:  # The HTTP library the OpenAI client is built on (``httpx2`` from its 3.x releases).
    import httpx2 as http_impl
except ImportError:  # pragma: no cover - an older OpenAI client
    import httpx as http_impl  # type: ignore[no-redef]

from backend.ports import AIDeviceUnavailableError, AIProviderError

Relay = Callable[..., Awaitable[tuple[int, dict]]]

# The address the client is pointed at; never resolved or contacted.
PLACEHOLDER_BASE_URL = "http://connector.invalid/v1"


class DeviceTransport(http_impl.AsyncBaseTransport):
    def __init__(self, relay: Relay, *, workspace_id: str, device_id: str) -> None:
        self._relay = relay
        self._workspace_id = workspace_id
        self._device_id = device_id

    async def handle_async_request(self, request: http_impl.Request) -> http_impl.Response:
        path = request.url.path
        if path.startswith("/v1/"):
            path = path[len("/v1") :]
        body: Any = None
        content = await request.aread()
        if content:
            try:
                body = json.loads(content)
            except ValueError as error:
                raise AIProviderError("The request could not be read.") from error
        status, answer = await self._relay(
            self._workspace_id,
            self._device_id,
            method=request.method,
            path=path.rstrip("/"),
            body=body if isinstance(body, dict) else None,
        )
        return http_impl.Response(status, json=answer, request=request)


def device_failure(error: BaseException) -> AIDeviceUnavailableError | None:
    """The computer's own failure behind a client error, so its plain words reach the
    author instead of a generic provider failure."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        if isinstance(current, AIDeviceUnavailableError):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


__all__ = ["PLACEHOLDER_BASE_URL", "DeviceTransport", "device_failure", "http_impl"]
