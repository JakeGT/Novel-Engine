"""A model on the author's own computer: the connector's endpoints and the app's (SCN-002A).

Two sets of endpoints, both under ``/api/v1``:

- ``/connector/...`` for the connector program on the person's computer. Before it is
  connected it asks for a code and polls for its credential, in the shape of the OAuth
  device authorization grant (RFC 8628; the token endpoint answers errors the RFC's way,
  ``{"error": "authorization_pending"}``). Afterwards it authenticates with its device
  credential as a bearer token and sends its version in ``X-Connector-Version``.
- ``/ai/connector/...`` for a workspace owner in the app: confirm a code, list, choose
  and disconnect computers.

Logs name the device and the outcome, never a request's words, a code or a credential.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.api.auth import get_workspace_scope, require_workspace_scope
from backend.api.dependencies import get_container
from backend.container import Container
from backend.domain import connector as c
from backend.domain.connector import Device, DeviceStatus, PendingCode
from backend.domain.errors import (
    CapabilityUnavailableError,
    RateLimitedError,
    UnauthorizedError,
)
from backend.domain.identity import AccessScope, utcnow
from backend.services.connector_service import (
    AnswerTooLarge,
    AuthenticatedDevice,
    ConnectorService,
    limit_reached,
)

router = APIRouter(tags=["connector"])

# Failed device sign-ins one address may make in a minute.
FAILED_PER_MINUTE = 30


def get_connector_service(container: Container = Depends(get_container)) -> ConnectorService:
    service = getattr(container, "connector_service", None)
    if service is None:
        raise CapabilityUnavailableError("Computers cannot be connected on this site.")
    return service  # type: ignore[no-any-return]


def _address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _device(
    request: Request,
    authorization: str | None = Header(default=None),
    x_connector_version: str = Header(default=""),
    service: ConnectorService = Depends(get_connector_service),
    container: Container = Depends(get_container),
) -> AuthenticatedDevice:
    scheme, _, credential = (authorization or "").partition(" ")
    limiter = container.rate_limiter
    failed = f"connector:fail:{_address(request)}"
    # Once an address used up its failed tries, nothing it sends is checked, right or wrong,
    # until the minute has passed.
    if await limit_reached(limiter, failed, limit=FAILED_PER_MINUTE, window=60):
        raise RateLimitedError("Too many failed tries. Wait a minute.")
    try:
        return await service.authenticate(
            credential.strip() if scheme.lower() == "bearer" else "",
            version=x_connector_version,
        )
    except UnauthorizedError:
        if limiter is not None:
            allowed = await limiter.allow(failed, limit=FAILED_PER_MINUTE, window_seconds=60)
            if not allowed:
                raise RateLimitedError("Too many failed tries. Wait a minute.") from None
        raise


# --- the connector -------------------------------------------------------------------


class ConnectorVersionModel(BaseModel):
    minimum: str
    latest: str
    protocol: int


class ConnectorCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=40)


class ConnectorCodeModel(BaseModel):
    """RFC 8628 section 3.2."""

    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class ConnectorTokenRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    device_code: str = Field(min_length=1, max_length=200)
    grant_type: str = "urn:ietf:params:oauth:grant-type:device_code"


class IssuedCredentialModel(BaseModel):
    device_id: str
    credential: str
    token_type: str = "Bearer"


class ComputerModelInfo(BaseModel):
    name: str = Field(max_length=c.MAX_MODEL_NAME)
    context_tokens: int | None = None


class ComputerCapabilitiesModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    server: str = Field(default=c.ServerKind.OTHER.value, max_length=40)
    models: list[ComputerModelInfo] = Field(default_factory=list, max_length=c.MAX_MODELS)
    structured_output: bool = False


class ConnectorPollRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    capabilities: ComputerCapabilitiesModel | None = None
    wait: float = Field(default=c.LONG_POLL_SECONDS, ge=0, le=c.LONG_POLL_SECONDS)
    # False while the connector is busy with a request: it is still there, but takes nothing.
    take: bool = True


class RelayedRequestModel(BaseModel):
    id: str
    method: str
    path: str
    body: dict[str, Any] | None = None


class ConnectorPollModel(BaseModel):
    request: RelayedRequestModel | None
    # The connector should rotate its credential now.
    rotate: bool
    minimum_version: str


class ConnectorAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The model server's HTTP status, when it answered.
    status: int | None = Field(default=None, ge=100, le=599)
    body: dict[str, Any] | None = None
    # A failure code from the connector's fixed list, never the server's own words.
    failure: str | None = Field(default=None, max_length=40)


class RotatedCredentialModel(BaseModel):
    credential: str


@router.get("/connector/version")
async def connector_version(
    service: ConnectorService = Depends(get_connector_service),
) -> ConnectorVersionModel:
    """Which connector versions this site works with. Nothing is ever downloaded."""
    return ConnectorVersionModel(**service.versions())


@router.post("/connector/codes", status_code=status.HTTP_201_CREATED)
async def start_code(
    body: ConnectorCodeRequest,
    request: Request,
    service: ConnectorService = Depends(get_connector_service),
) -> ConnectorCodeModel:
    started = await service.start(
        device_name=body.name, version=body.version, address=_address(request)
    )
    return ConnectorCodeModel(
        device_code=started.device_code,
        user_code=started.user_code,
        verification_uri=str(request.base_url),
        expires_in=started.expires_in,
        interval=started.interval,
    )


@router.post("/connector/token", response_model=IssuedCredentialModel)
async def exchange_code(
    body: ConnectorTokenRequest, service: ConnectorService = Depends(get_connector_service)
) -> Any:
    exchange = await service.exchange(body.device_code)
    if exchange.error is not None:
        # RFC 8628 section 3.5: an OAuth error object with status 400.
        return JSONResponse(
            {"error": exchange.error, "interval": exchange.interval},
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )
    assert exchange.device_id is not None and exchange.credential is not None
    return JSONResponse(
        IssuedCredentialModel(
            device_id=exchange.device_id, credential=exchange.credential
        ).model_dump(),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/connector/poll")
async def poll(
    body: ConnectorPollRequest,
    x_connector_version: str = Header(default=""),
    caller: AuthenticatedDevice = Depends(_device),
    service: ConnectorService = Depends(get_connector_service),
) -> ConnectorPollModel:
    found = await service.poll(
        caller,
        version=x_connector_version,
        capabilities=body.capabilities.model_dump() if body.capabilities else None,
        wait=body.wait,
        take=body.take,
    )
    relayed = None
    if found.request is not None and found.request.payload is not None:
        payload = found.request.payload
        relayed = RelayedRequestModel(
            id=found.request.id,
            method=str(payload.get("method", "")),
            path=str(payload.get("path", "")),
            body=payload.get("body"),
        )
    return ConnectorPollModel(
        request=relayed, rotate=found.rotate, minimum_version=service.minimum_version
    )


@router.post("/connector/requests/{request_id}/answer", status_code=204)
async def answer(
    request_id: str,
    body: ConnectorAnswerRequest,
    request: Request,
    caller: AuthenticatedDevice = Depends(_device),
    service: ConnectorService = Depends(get_connector_service),
) -> Response:
    declared = request.headers.get("content-length") or ""
    if declared.isdigit() and int(declared) > c.MAX_ANSWER_BYTES:
        await service.answer(caller, request_id, status_code=None, body=None, failure="too_large")
        raise AnswerTooLarge("The answer was too large to use, so it was not kept.")
    # The service measures the answer as parsed too, whatever its stated length.
    await service.answer(
        caller, request_id, status_code=body.status, body=body.body, failure=body.failure
    )
    return Response(status_code=204)


@router.post("/connector/rotate")
async def rotate(
    caller: AuthenticatedDevice = Depends(_device),
    service: ConnectorService = Depends(get_connector_service),
) -> JSONResponse:
    credential = await service.rotate(caller)
    return JSONResponse(
        RotatedCredentialModel(credential=credential).model_dump(),
        headers={"Cache-Control": "no-store"},
    )


# --- the app --------------------------------------------------------------------------


class ComputerCodeCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=20)


class ComputerCodeDecisionRequest(ComputerCodeCheckRequest):
    approve: bool


class ComputerCodeModel(BaseModel):
    """What is about to be connected, shown before the person confirms (RFC 8628 5.4)."""

    name: str
    connector_version: str
    asked_at: datetime
    expires_at: datetime
    status: str


class ConnectedComputerModel(BaseModel):
    id: str
    name: str
    # "connected" or "disconnected".
    status: str
    # Whether the connector has asked for work in the last minute or so.
    online: bool
    last_seen_at: datetime | None
    connected_at: datetime
    connector_version: str
    # False when the connector is older than the site accepts.
    up_to_date: bool
    server: str
    models: list[ComputerModelInfo]
    structured_output: bool
    in_use: bool
    # Why it stopped: "owner", "access_lost", "expired" or "reused".
    disconnected_because: str | None
    disconnected_at: datetime | None


class UseComputerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=c.MAX_MODEL_NAME)


def _pending(code: PendingCode) -> ComputerCodeModel:
    return ComputerCodeModel(
        name=code.device_name,
        connector_version=code.connector_version,
        asked_at=code.created_at,
        expires_at=code.expires_at,
        status=code.status.value,
    )


def _device_model(device: Device, service: ConnectorService) -> ConnectedComputerModel:
    caps = device.capabilities
    return ConnectedComputerModel(
        id=device.id,
        name=device.name,
        status="connected" if device.status is DeviceStatus.ACTIVE else "disconnected",
        online=device.online(utcnow()),
        last_seen_at=device.last_seen_at,
        connected_at=device.created_at,
        connector_version=device.connector_version,
        up_to_date=c.version_accepted(device.connector_version, service.minimum_version),
        server=c.SERVER_LABELS[caps.server],
        models=[
            ComputerModelInfo(name=model.name, context_tokens=model.context_tokens)
            for model in caps.models
        ],
        structured_output=caps.structured_output,
        in_use=device.in_use,
        disconnected_because=device.revoked_reason.value if device.revoked_reason else None,
        disconnected_at=device.revoked_at,
    )


@router.post("/ai/connector/codes/check")
async def check_code(
    body: ComputerCodeCheckRequest,
    scope: AccessScope = Depends(require_workspace_scope),
    service: ConnectorService = Depends(get_connector_service),
) -> ComputerCodeModel:
    assert scope.workspace_id is not None
    return _pending(await service.describe_code(scope, scope.workspace_id, body.code))


@router.post("/ai/connector/codes/decide")
async def decide_code(
    body: ComputerCodeDecisionRequest,
    scope: AccessScope = Depends(require_workspace_scope),
    service: ConnectorService = Depends(get_connector_service),
) -> ComputerCodeModel:
    assert scope.workspace_id is not None
    return _pending(
        await service.decide_code(scope, scope.workspace_id, body.code, approve=body.approve)
    )


@router.get("/ai/connector/devices")
async def list_devices(
    scope: AccessScope = Depends(get_workspace_scope),
    service: ConnectorService = Depends(get_connector_service),
) -> list[ConnectedComputerModel]:
    assert scope.workspace_id is not None
    return [_device_model(d, service) for d in await service.devices(scope, scope.workspace_id)]


@router.post("/ai/connector/devices/{device_id}/use")
async def use_device(
    device_id: str,
    body: UseComputerRequest,
    scope: AccessScope = Depends(require_workspace_scope),
    service: ConnectorService = Depends(get_connector_service),
) -> ConnectedComputerModel:
    assert scope.workspace_id is not None
    device = await service.choose(scope, scope.workspace_id, device_id, model=body.model)
    return _device_model(device, service)


@router.delete("/ai/connector/use", status_code=204)
async def stop_using(
    scope: AccessScope = Depends(require_workspace_scope),
    service: ConnectorService = Depends(get_connector_service),
) -> Response:
    assert scope.workspace_id is not None
    await service.stop_using(scope, scope.workspace_id)
    return Response(status_code=204)


@router.post("/ai/connector/devices/{device_id}/disconnect")
async def disconnect_device(
    device_id: str,
    scope: AccessScope = Depends(require_workspace_scope),
    service: ConnectorService = Depends(get_connector_service),
) -> ConnectedComputerModel:
    assert scope.workspace_id is not None
    return _device_model(await service.revoke(scope, scope.workspace_id, device_id), service)


__all__ = ["get_connector_service", "router"]
