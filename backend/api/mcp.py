"""The MCP endpoint: other assistants read a story, and may suggest changes (QRY-003, ADR-024).

Streamable HTTP at ``/mcp``, POST only, one JSON-RPC message per request, always answered
with one JSON object (never a stream: no tool here runs long). It speaks both eras of the
Model Context Protocol on the same endpoint:

* **2026-07-28** (stateless): every request carries its protocol version in
  ``params._meta``; the ``MCP-Protocol-Version``, ``Mcp-Method`` and ``Mcp-Name``
  headers must match the body (400 ``HeaderMismatch`` otherwise); ``server/discover``.
* **2025-11-25, 2025-06-18 and 2025-03-26** (an ``initialize`` handshake): answered
  without a session id, which those versions allow, so every request stands alone.

Only tools are offered. A request is authenticated with an access key as a bearer token,
checked on every request (``AccessKeyService.authenticate``): an expired, withdrawn or
orphaned key is refused, and so is a request whose browser ``Origin`` is not this site's
(DNS rebinding). Tools run inside this request, with the key holder's own scope.

Logs and use counts name the method, the tool and the key, never arguments or answers.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from backend.api.mcp_tools import ALL_TOOLS, ToolCall, tools_for
from backend.core.logging import log_event
from backend.domain.errors import DomainError, RateLimitedError, UnauthorizedError
from backend.domain.journal import HeadConflict, MutationNotFound, RevisionConflict
from backend.domain.operations import OperationValidationError
from backend.domain.review import IllegalTransition
from backend.domain.validation import CanonValidationError
from backend.services.access_key_service import AccessKeyService, MachineCaller

logger = logging.getLogger("backend.api.mcp")

router = APIRouter()

MODERN_VERSIONS: tuple[str, ...] = ("2026-07-28",)
LEGACY_VERSIONS: tuple[str, ...] = ("2025-11-25", "2025-06-18", "2025-03-26")
SUPPORTED_VERSIONS: tuple[str, ...] = (*MODERN_VERSIONS, *LEGACY_VERSIONS)
# Clients before 2025-06-18 sent no version header (Streamable HTTP, 2026-07-28).
HEADERLESS_VERSION = "2025-03-26"

MAX_BODY_BYTES = 1_000_000
SERVER_NAME = "novel-engine"
META = "io.modelcontextprotocol/"

# JSON-RPC 2.0 and MCP error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
UNAUTHORIZED = -32001  # implementation-defined range
RATE_LIMITED = -32002
HEADER_MISMATCH = -32020
UNSUPPORTED_VERSION = -32022

INSTRUCTIONS = (
    "Novel Engine keeps an author's story: its bible of people, places and things, the facts "
    "and beliefs about them, the plan, the timeline and the scenes. Start with list_stories. "
    "Read with read_entry (as_known_by keeps to one character's point of view), list_plan, "
    "read_scene, timeline and record_history. Nothing you do here changes the story: "
    "suggest_changes, when this key allows it, only sends suggestions to the author's review."
)


class _Refusal(Exception):
    """A request refused before any tool runs: an HTTP status and a JSON-RPC error."""

    def __init__(
        self,
        status: int,
        code: int,
        message: str,
        *,
        data: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.data = data
        self.headers = headers or {}


def _error(message_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": message_id, "error": error}


def _answer(status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> Response:
    return JSONResponse(status_code=status, content=body, headers=headers)


def _server_info(request: Request) -> dict[str, str]:
    settings = request.app.state.container.settings
    return {"name": SERVER_NAME, "title": "Novel Engine", "version": settings.PROJECT_VERSION}


def _capabilities() -> dict[str, Any]:
    return {"tools": {"listChanged": False}}


_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})


def _origin_allowed(request: Request) -> bool:
    """A browser's Origin must be this site's own; other clients send none.

    The Host header is never trusted for this: under DNS rebinding a hostile page's
    Origin and Host agree. A hosted site allows only its configured address; a local
    install only pages served from this machine.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return True
    settings = request.app.state.container.settings
    if not settings.is_hosted:
        return (urlsplit(origin).hostname or "") in _LOOPBACK
    allowed = set()
    for configured in (settings.OIDC_REDIRECT_URI, settings.OIDC_POST_LOGOUT_REDIRECT_URI):
        if configured:
            parts = urlsplit(configured)
            allowed.add(f"{parts.scheme}://{parts.netloc}")
    return origin in allowed


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def _decode_header(value: str) -> str:
    """Undo the Base64 sentinel form MCP uses for header values that are not plain ASCII."""
    if value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[len("=?base64?") : -2], validate=True).decode("utf-8")
        except ValueError:
            raise _Refusal(400, HEADER_MISMATCH, "A header is not valid Base64.") from None
    return value


def _check_modern_headers(request: Request, version: str, method: str, params: dict) -> None:
    header_version = request.headers.get("mcp-protocol-version")
    if header_version != version:
        raise _Refusal(
            400,
            HEADER_MISMATCH,
            "The MCP-Protocol-Version header must match the request's protocol version.",
        )
    if request.headers.get("mcp-method") != method:
        raise _Refusal(400, HEADER_MISMATCH, "The Mcp-Method header must match the method.")
    if method == "tools/call":
        named = request.headers.get("mcp-name")
        if named is None or _decode_header(named) != params.get("name"):
            raise _Refusal(400, HEADER_MISMATCH, "The Mcp-Name header must match the tool name.")


def _protocol(request: Request, method: str, params: dict) -> tuple[str, bool]:
    """The protocol version this request speaks, and whether it is the stateless era."""
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    stated = meta.get(f"{META}protocolVersion") if meta else None
    if isinstance(stated, str):
        if stated not in MODERN_VERSIONS:
            raise _Refusal(
                400,
                UNSUPPORTED_VERSION,
                "Unsupported protocol version",
                data={"supported": list(SUPPORTED_VERSIONS), "requested": stated},
            )
        _check_modern_headers(request, stated, method, params)
        return stated, True
    if method == "initialize":
        asked = params.get("protocolVersion")
        return (asked if asked in LEGACY_VERSIONS else LEGACY_VERSIONS[0]), False
    header_version = request.headers.get("mcp-protocol-version")
    if header_version is None:
        return HEADERLESS_VERSION, False
    if header_version not in LEGACY_VERSIONS:
        raise _Refusal(
            400,
            UNSUPPORTED_VERSION,
            "Unsupported protocol version",
            data={"supported": list(SUPPORTED_VERSIONS), "requested": header_version},
        )
    return header_version, False


async def _caller(request: Request) -> MachineCaller:
    service: AccessKeyService = request.app.state.container.access_key_service
    client = request.client.host if request.client else "unknown"
    try:
        return await service.authenticate(_bearer(request), client=client)
    except UnauthorizedError as error:
        raise _Refusal(
            401,
            UNAUTHORIZED,
            error.detail,
            headers={
                "WWW-Authenticate": 'Bearer realm="Novel Engine", error="invalid_token"',
            },
        ) from None
    except RateLimitedError as error:
        raise _Refusal(429, RATE_LIMITED, error.detail, headers={"Retry-After": "60"}) from None


def _tool_failure(error: Exception) -> str:
    """Plain words for why a tool could not do what it was asked."""
    if isinstance(error, DomainError):
        return error.detail
    if isinstance(error, MutationNotFound):
        return "That was not found."
    if isinstance(error, (HeadConflict, RevisionConflict)):
        return "The story changed meanwhile. Try again."
    if isinstance(error, (CanonValidationError, OperationValidationError, IllegalTransition)):
        return f"Those changes cannot be suggested: {error}"
    raise error


async def _call_tool(
    request: Request, caller: MachineCaller, params: dict
) -> tuple[dict[str, Any], str, bool]:
    """Run one tool. Returns the result, the tool's name for counting, and whether refused."""
    name = params.get("name")
    tool = ALL_TOOLS.get(name) if isinstance(name, str) else None
    if tool is None:
        raise _Refusal(200, INVALID_PARAMS, f"There is no tool called {str(name)[:60]!r}.")
    raw = params.get("arguments") or {}
    if not isinstance(raw, dict):
        raise _Refusal(200, INVALID_PARAMS, "Tool arguments must be an object.")
    try:
        if tool not in tools_for(caller):
            raise _ToolRefused("This key may only read.")
        try:
            arguments: BaseModel = tool.arguments.model_validate(raw)
        except PydanticValidationError as error:
            first = error.errors()[0]
            where = ".".join(str(part) for part in first.get("loc", ())) or "arguments"
            raise _ToolRefused(f"The argument {where!r} is not right: {first['msg']}.") from None
        call = ToolCall(
            container=request.app.state.container,
            caller=caller,
            arguments=arguments,
            correlation_id=getattr(request.state, "correlation_id", ""),
        )
        try:
            answer = await tool.run(call)
        except Exception as error:  # noqa: BLE001 - mapped to words, or raised again
            try:
                failure = _tool_failure(error)
            except Exception:
                # Unexpected: still counted against the key, as a call that failed.
                await request.app.state.container.access_key_service.record_use(
                    caller, tool.name, refused=True
                )
                raise
            raise _ToolRefused(failure) from None
    except _ToolRefused as refused:
        return (
            {"content": [{"type": "text", "text": refused.message}], "isError": True},
            tool.name,
            True,
        )
    text = json.dumps(answer, ensure_ascii=False, separators=(",", ":"))
    return (
        {
            "content": [{"type": "text", "text": text}],
            "structuredContent": answer,
            "isError": False,
        },
        tool.name,
        False,
    )


class _ToolRefused(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _log(caller: MachineCaller | None, method: str, tool: str | None, outcome: str) -> None:
    log_event(
        logger,
        "mcp.request",
        method=method[:60],
        tool=tool,
        key_id=caller.key.id if caller else None,
        workspace_id=caller.key.workspace_id if caller else None,
        outcome=outcome,
    )


async def _read_capped(request: Request) -> bytes:
    """The body, read only up to the cap: a chunked body has no length to check first."""
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise _Refusal(413, INVALID_REQUEST, "That request is too large.")
    return bytes(body)


def _valid_id(value: Any) -> bool:
    """MCP request ids are strings or integers (never null, a boolean or a structure)."""
    return isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool))


@router.post("/mcp", include_in_schema=False)
async def mcp_endpoint(request: Request) -> Response:
    caller: MachineCaller | None = None
    message_id: Any = None
    method = ""
    try:
        if not _origin_allowed(request):
            raise _Refusal(403, INVALID_REQUEST, "Requests from that web page are not allowed.")
        caller = await _caller(request)
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            raise _Refusal(413, INVALID_REQUEST, "That request is too large.")
        body = await _read_capped(request)
        try:
            message = json.loads(body)
        except (ValueError, RecursionError):
            # RecursionError: JSON nested too deeply to read.
            raise _Refusal(400, PARSE_ERROR, "The request is not valid JSON.") from None
        if not isinstance(message, dict):
            raise _Refusal(400, INVALID_REQUEST, "Send one JSON-RPC message per request.")
        if "method" not in message and ("result" in message or "error" in message):
            # A client's answer to a request: this server sends none, so nothing to do.
            _log(caller, "response", None, "accepted")
            return Response(status_code=202)
        if "id" in message and not _valid_id(message["id"]):
            raise _Refusal(400, INVALID_REQUEST, "A request id must be a string or a whole number.")
        message_id = message.get("id")
        named = message.get("method")
        method = named if isinstance(named, str) else ""
        if message.get("jsonrpc") != "2.0" or not method:
            raise _Refusal(400, INVALID_REQUEST, "That is not a JSON-RPC 2.0 request.")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            raise _Refusal(400, INVALID_PARAMS, "params must be an object.")
        if "id" not in message:
            # A notification (initialized, cancelled): nothing to answer.
            _log(caller, method, None, "notification")
            return Response(status_code=202)
        version, modern = _protocol(request, method, params)
        result = await _dispatch(request, caller, method, params, version, modern)
        if modern:
            result.setdefault("resultType", "complete")
            result.setdefault("_meta", {})[f"{META}serverInfo"] = _server_info(request)
        return _answer(200, {"jsonrpc": "2.0", "id": message_id, "result": result})
    except _Refusal as refusal:
        _log(caller, method, None, f"refused_{refusal.status}")
        return _answer(
            refusal.status,
            _error(message_id, refusal.code, refusal.message, refusal.data),
            refusal.headers,
        )
    except Exception:
        logger.exception("mcp.request.failed", extra={"method": method[:60]})
        return _answer(500, _error(message_id, INTERNAL_ERROR, "Something went wrong."))


async def _dispatch(
    request: Request,
    caller: MachineCaller,
    method: str,
    params: dict,
    version: str,
    modern: bool,
) -> dict[str, Any]:
    if method == "initialize" and not modern:
        _log(caller, method, None, "ok")
        return {
            "protocolVersion": version,
            "capabilities": _capabilities(),
            "serverInfo": _server_info(request),
            "instructions": INSTRUCTIONS,
        }
    if method == "server/discover" and modern:
        _log(caller, method, None, "ok")
        return {
            "supportedVersions": list(SUPPORTED_VERSIONS),
            "capabilities": _capabilities(),
            "instructions": INSTRUCTIONS,
            "ttlMs": 3_600_000,
            "cacheScope": "private",
        }
    if method == "ping" and not modern:
        return {}
    if method == "tools/list":
        _log(caller, method, None, "ok")
        listed: dict[str, Any] = {"tools": [tool.definition() for tool in tools_for(caller)]}
        if modern:
            # What a key may do can change with its holder's role: cache briefly, privately.
            listed.update({"ttlMs": 60_000, "cacheScope": "private"})
        return listed
    if method == "tools/call":
        result, tool, refused = await _call_tool(request, caller, params)
        await request.app.state.container.access_key_service.record_use(
            caller, tool, refused=refused
        )
        _log(caller, method, tool, "refused" if refused else "ok")
        return result
    # Resources, prompts and the rest are not offered.
    raise _Refusal(404 if modern else 200, METHOD_NOT_FOUND, f"Method not found: {method[:60]}")


@router.api_route("/mcp", methods=["GET", "DELETE"], include_in_schema=False)
async def mcp_other_methods() -> Response:
    """No standalone stream and no sessions here: POST only."""
    return Response(status_code=405, headers={"Allow": "POST"})


@router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
@router.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
@router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
async def no_oauth() -> Response:
    """This endpoint takes access keys made in Novel Engine, not a sign-in through OAuth."""
    return JSONResponse(
        status_code=404,
        content={
            "detail": "Novel Engine's MCP endpoint takes an access key made under People, "
            "sent as a bearer token. It does not use OAuth sign-in."
        },
    )


__all__ = ["LEGACY_VERSIONS", "MODERN_VERSIONS", "SUPPORTED_VERSIONS", "router"]
