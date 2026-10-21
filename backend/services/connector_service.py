"""Connecting a model on the author's own computer (SCN-002A, ADR-025).

Three kinds of caller use this service:

- **The connector** on the person's computer, before it is connected (asking for a code,
  polling for its credential) and after (polling for requests, answering them, rotating
  its credential). It authenticates with its device credential only.
- **A member in the app**, who confirms a code, sees the computers connected, chooses
  one for the assistant, and disconnects one. Only workspace owners and admins may.
- **The AI gateway**, which relays a model request to the computer the workspace chose
  and waits for the answer (``relay``). The request's words go only to that computer.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.core.security import constant_time_equals, hash_token
from backend.domain import connector as c
from backend.domain.connector import (
    ChannelRequest,
    CodeStatus,
    Device,
    DeviceCapabilities,
    DeviceStatus,
    PendingCode,
    RequestStatus,
    RevokedBecause,
)
from backend.domain.errors import (
    CapabilityUnavailableError,
    DomainError,
    ForbiddenError,
    NotFoundError,
    RateLimitedError,
    UnauthorizedError,
    ValidationError,
)
from backend.domain.identity import AccessScope, AuthMode, utcnow
from backend.domain.providers import ProviderSpec, StructuredMode
from backend.domain.tenancy import Permission
from backend.ports import AIDeviceUnavailableError, AIProviderError

logger = logging.getLogger(__name__)

# Who the request being served is for, and the story it is about, so a relayed request
# records them (set with the route).
_request_actor: ContextVar[str] = ContextVar("connector_request_actor", default="")
_request_project: ContextVar[str | None] = ContextVar("connector_request_project", default=None)
# How often the queue is swept for words a crash left behind.
_SWEEP_EVERY = timedelta(seconds=60)


async def limit_reached(limiter: Any, key: str, *, limit: int, window: int) -> bool:
    """Whether ``key`` already used its budget, without using any of it. A limiter that
    cannot say is taken as not reached (its ``allow`` still counts)."""
    exhausted = getattr(limiter, "exhausted", None)
    if limiter is None or exhausted is None:
        return False
    return bool(await exhausted(key, limit=limit, window_seconds=window))


class ConnectorTooOld(DomainError):
    status_code = 426
    code = "connector_too_old"
    title = "Connector too old"


class AnswerTooLarge(DomainError):
    """The answer was refused for its size; the request it was for has failed."""

    status_code = 413
    code = "too_large"
    title = "Answer too large"


# What a computer's failure is called, in plain words. The connector sends only these codes,
# never its model server's own text.
FAILURES: dict[str, str] = {
    "local_server_unreachable": (
        "The connector is running, but it could not reach the model server on your computer. "
        "Check that Ollama, LM Studio or your model server is open."
    ),
    "local_server_error": "The model server on your computer could not answer this request.",
    "local_server_timeout": "The model server on your computer took too long to answer.",
    "too_large": "The answer from your computer was too large to use.",
    "disconnected": "This computer was disconnected, so the request was not sent.",
    "too_old": "The connector on your computer is too old. Update it, then try again.",
}
_OFFLINE = "Your computer is not connected right now. Start the connector on it, then try again."
_GONE = (
    "The computer chosen for the assistant is no longer connected. Connect it again, or "
    "choose another model, under AI assistant."
)
_NOT_TAKEN = (
    "Your computer did not pick up the request. Check that the connector is still running, "
    "then try again."
)
_TOO_SLOW = "Your computer took too long to answer, so the assistant stopped waiting."


@dataclass(frozen=True)
class StartedCode:
    device_code: str
    user_code: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class Exchange:
    """What a connector polling with its device code is told (RFC 8628 section 3.5)."""

    error: str | None = None
    interval: int = c.POLL_INTERVAL_SECONDS
    device_id: str | None = None
    credential: str | None = None
    workspace_id: str | None = None


@dataclass(frozen=True)
class AuthenticatedDevice:
    device: Device
    # The credential presented was the one replaced moments ago (inside the grace window).
    previous: bool
    presented_hash: str


@dataclass(frozen=True)
class PollResult:
    request: ChannelRequest | None
    rotate: bool


def _plain_failure(code: str | None) -> str:
    return FAILURES.get(code or "", "Your computer could not answer this request.")


class ConnectorService:
    def __init__(
        self,
        store: Any,
        *,
        tenancy: Any = None,
        rate_limiter: Any = None,
        ai_settings: Any = None,
        settings: Any = None,
        now: Callable[[], datetime] = utcnow,
        step_seconds: float = 0.25,
        pickup_seconds: float = c.PICKUP_SECONDS,
        answer_seconds: float = c.ANSWER_SECONDS,
        long_poll_seconds: float = c.LONG_POLL_SECONDS,
        max_step_seconds: float = c.MAX_POLL_STEP_SECONDS,
        minimum_version: str = c.MINIMUM_CONNECTOR_VERSION,
    ) -> None:
        self._store = store
        self._tenancy = tenancy
        self._limiter = rate_limiter
        self._ai_settings = ai_settings
        self._settings = settings
        self._now = now
        self._step = step_seconds
        self._pickup = pickup_seconds
        self._answer = answer_seconds
        self._long_poll = long_poll_seconds
        self._max_step = max(step_seconds, max_step_seconds)
        self.minimum_version = minimum_version
        # What each computer said it can do, for the gateway's synchronous questions.
        self._known: dict[str, DeviceCapabilities] = {}
        # Polls each computer holds open on this server right now.
        self._open_polls: dict[str, int] = {}
        self._swept_at: datetime | None = None
        if tenancy is not None and hasattr(tenancy, "attach_member_devices"):
            # Removing a member disconnects the computers they connected (as with keys).
            tenancy.attach_member_devices(self)

    # ------------------------------------------------------------------ who may

    @property
    def _local(self) -> bool:
        return self._settings is None or not getattr(self._settings, "is_hosted", False)

    async def _require_admin(self, scope: AccessScope, workspace_id: str) -> None:
        if scope.auth_mode == AuthMode.LOCAL or self._local:
            local_id = getattr(self._settings, "AUTH_LOCAL_WORKSPACE_ID", workspace_id)
            if workspace_id != local_id:
                raise NotFoundError("Workspace not found.")
            return
        await self._tenancy.require_permission(scope, workspace_id, Permission.WORKSPACE_ADMIN)

    async def _audit(
        self, scope: AccessScope, workspace_id: str, action: str, target: str, detail: dict
    ) -> None:
        if self._tenancy is None:
            return
        await self._tenancy.record_workspace_audit(
            scope,
            workspace_id=workspace_id,
            action=action,
            target_type="connector_device",
            target_id=target,
            detail=detail,
        )

    def _scope_for(self, user_id: str, workspace_id: str) -> AccessScope:
        return AccessScope(
            actor_user_id=user_id,
            auth_mode=AuthMode.LOCAL if self._local else AuthMode.HOSTED,
            workspace_id=workspace_id,
        )

    async def _allow(self, key: str, *, limit: int, window: int) -> bool:
        if self._limiter is None:
            return True
        return bool(await self._limiter.allow(key, limit=limit, window_seconds=window))

    # ------------------------------------------------------------------ versions

    def versions(self) -> dict[str, Any]:
        return {
            "minimum": self.minimum_version,
            "latest": c.LATEST_CONNECTOR_VERSION,
            "protocol": c.PROTOCOL_VERSION,
        }

    def check_version(self, version: str) -> None:
        if not c.version_accepted(version, self.minimum_version):
            raise ConnectorTooOld(
                f"This connector is too old for this site. Update it to version "
                f"{self.minimum_version} or later, then start it again."
            )

    # ------------------------------------------------------------------ registration

    async def start(self, *, device_name: str, version: str, address: str) -> StartedCode:
        """A connector asks for a code (RFC 8628 section 3.1)."""
        name = c.clean_device_name(device_name)
        if not name:
            raise ValidationError("Give this computer a name.")
        self.check_version(version)
        if not await self._allow(
            f"connector:codes:{address}", limit=c.CODES_PER_ADDRESS, window=600
        ):
            raise RateLimitedError("Too many codes were asked for. Wait a few minutes.")
        now = self._now()
        for _ in range(5):
            device_code = c.generate_device_code()
            code = PendingCode(
                device_code_hash=hash_token(device_code),
                user_code=c.generate_user_code(),
                device_name=name,
                connector_version=version,
                status=CodeStatus.PENDING,
                created_at=now,
                expires_at=now + c.CODE_LIFETIME,
            )
            if await self._store.add_code(code):
                log_event(logger, "connector.code_started")
                return StartedCode(
                    device_code=device_code,
                    user_code=c.display_user_code(code.user_code),
                    expires_in=int(c.CODE_LIFETIME.total_seconds()),
                    interval=code.interval_seconds,
                )
        raise CapabilityUnavailableError("A code could not be made. Try again.")

    async def _code_for(self, scope: AccessScope, user_code: str) -> PendingCode:
        # RFC 8628 section 5.1: wrong codes are limited per person. Once they ran out, no
        # code is looked up at all, right or wrong, until the window has passed.
        wrong = f"connector:wrong:{scope.actor_user_id}"
        too_many = RateLimitedError("Too many wrong codes. Wait ten minutes, then try again.")
        if await limit_reached(
            self._limiter, wrong, limit=c.WRONG_CODES_PER_WINDOW, window=c.WRONG_CODE_WINDOW_SECONDS
        ):
            raise too_many
        if not await self._allow(f"connector:look:{scope.actor_user_id}", limit=30, window=600):
            raise RateLimitedError("Too many tries. Wait a few minutes, then try again.")
        normalized = c.normalize_user_code(user_code)
        code = await self._store.code_by_user_code(normalized) if normalized else None
        if code is None or code.status is not CodeStatus.PENDING or code.expired(self._now()):
            if not await self._allow(
                wrong, limit=c.WRONG_CODES_PER_WINDOW, window=c.WRONG_CODE_WINDOW_SECONDS
            ):
                raise too_many
            raise NotFoundError(
                "That code was not found, or it ran out. Check the code the connector shows, "
                "or start the connector again for a new one."
            )
        return code

    async def describe_code(
        self, scope: AccessScope, workspace_id: str, user_code: str
    ) -> PendingCode:
        """What the person is about to connect: shown before they confirm (RFC 8628 5.4)."""
        await self._require_admin(scope, workspace_id)
        return await self._code_for(scope, user_code)

    async def decide_code(
        self, scope: AccessScope, workspace_id: str, user_code: str, *, approve: bool
    ) -> PendingCode:
        await self._require_admin(scope, workspace_id)
        code = await self._code_for(scope, user_code)
        if approve:
            self.check_version(code.connector_version)
            active = [d for d in await self._store.devices(workspace_id) if d.status == "active"]
            if len(active) >= c.MAX_DEVICES_PER_WORKSPACE:
                raise ValidationError(
                    f"A workspace can have {c.MAX_DEVICES_PER_WORKSPACE} computers connected. "
                    "Disconnect one first."
                )
        decided = await self._store.decide_code(
            user_code=code.user_code,
            approve=approve,
            workspace_id=workspace_id,
            decided_by=scope.actor_user_id,
            at=self._now(),
        )
        if decided is None:
            raise NotFoundError("That code ran out. Start the connector again for a new one.")
        log_event(logger, "connector.code_decided", approved=approve)
        return decided  # type: ignore[no-any-return]

    async def exchange(self, device_code: str) -> Exchange:
        """A connector polls with its device code (RFC 8628 section 3.4)."""
        code = await self._store.code_by_device_hash(hash_token(device_code or ""))
        if code is None or code.status is CodeStatus.ISSUED:
            return Exchange(error="invalid_grant")
        now = self._now()
        if code.expired(now):
            return Exchange(error="expired_token")
        if code.status is CodeStatus.DENIED:
            return Exchange(error="access_denied")
        interval = code.interval_seconds
        # A second of leeway for clocks and networks; quicker than that is told to slow down.
        too_soon = code.last_polled_at is not None and now - code.last_polled_at < timedelta(
            seconds=max(1, interval - 1)
        )
        if too_soon:
            interval += c.SLOW_DOWN_STEP_SECONDS
            await self._store.touch_code(
                device_code_hash=code.device_code_hash, at=now, interval=interval
            )
            return Exchange(error="slow_down", interval=interval)
        await self._store.touch_code(
            device_code_hash=code.device_code_hash, at=now, interval=interval
        )
        if code.status is CodeStatus.PENDING:
            return Exchange(error="authorization_pending", interval=interval)
        assert code.workspace_id is not None and code.decided_by is not None
        credential = c.generate_credential()
        device = Device(
            id=f"dev_{uuid7().hex}",
            workspace_id=code.workspace_id,
            name=code.device_name,
            registered_by=code.decided_by,
            status=DeviceStatus.ACTIVE,
            credential_hash=hash_token(credential),
            credential_issued_at=now,
            created_at=now,
            connector_version=code.connector_version,
            last_seen_at=now,
        )
        if not await self._store.issue_code(device_code_hash=code.device_code_hash, device=device):
            return Exchange(error="invalid_grant")
        await self._audit(
            self._scope_for(code.decided_by, code.workspace_id),
            code.workspace_id,
            "connector.device_connected",
            device.id,
            {"name": device.name, "version": device.connector_version},
        )
        log_event(logger, "connector.device_connected", device_id=device.id)
        return Exchange(device_id=device.id, credential=credential, workspace_id=code.workspace_id)

    # ------------------------------------------------------------------ the device itself

    async def authenticate(self, credential: str, *, version: str) -> AuthenticatedDevice:
        refused = UnauthorizedError(
            "This computer is not connected. Start the connector again to connect it."
        )
        if not c.credential_well_formed(credential or ""):
            raise refused
        presented = hash_token(credential)
        device = await self._store.device_by_credential(presented)
        if device is None or device.status is not DeviceStatus.ACTIVE:
            raise refused
        now = self._now()
        previous = not constant_time_equals(device.credential_hash, presented)
        if previous and (
            device.previous_hash is None
            or not constant_time_equals(device.previous_hash, presented)
            or device.previous_valid_until is None
            or now > device.previous_valid_until
        ):
            raise refused
        if now - device.credential_issued_at > c.CREDENTIAL_MAX_AGE:
            await self._store.revoke(
                workspace_id=device.workspace_id,
                device_id=device.id,
                reason=RevokedBecause.EXPIRED.value,
                by=None,
                at=now,
            )
            raise refused
        if not self._local and self._tenancy is not None:
            try:
                # Only owners and admins may connect a computer, so one whose member is no
                # longer either stops (a demotion, or leaving the workspace).
                await self._tenancy.require_permission(
                    self._scope_for(device.registered_by, device.workspace_id),
                    device.workspace_id,
                    Permission.WORKSPACE_ADMIN,
                )
            except ForbiddenError:
                await self._store.revoke(
                    workspace_id=device.workspace_id,
                    device_id=device.id,
                    reason=RevokedBecause.ACCESS_LOST.value,
                    by=None,
                    at=now,
                )
                raise refused from None
        self.check_version(version)
        if not previous and (
            device.credential_last_used_at is None
            or now - device.credential_last_used_at >= timedelta(minutes=1)
        ):
            await self._store.credential_used(
                device_id=device.id, credential_hash=presented, at=now
            )
        return AuthenticatedDevice(device=device, previous=previous, presented_hash=presented)

    async def rotate(self, caller: AuthenticatedDevice) -> str:
        """A new credential for the computer; the current one keeps working briefly.

        Rotating with the credential it replaced is allowed only while the current one has
        never been used (its answer was lost on the way). Once the current one is in use,
        the replaced one coming back means two copies exist, and the computer is
        disconnected (RFC 9700 section 4.14.2, refresh token reuse detection)."""
        device = caller.device
        now = self._now()
        refused = UnauthorizedError(
            "This computer is not connected. Start the connector again to connect it."
        )
        if caller.previous and device.credential_last_used_at is not None:
            await self._disconnect_reused(device, now)
            raise refused
        credential = c.generate_credential()
        rotated = await self._store.rotate(
            device_id=device.id,
            expected_hash=device.credential_hash,
            new_hash=hash_token(credential),
            # The current credential, never the one presented: rotating with the replaced
            # one must not drop the one the computer may already hold.
            previous_hash=device.credential_hash,
            at=now,
            grace_until=now + c.ROTATION_GRACE,
            only_if_unused=caller.previous,
        )
        if not rotated:
            if caller.previous:
                await self._disconnect_reused(device, now)
                raise refused
            raise UnauthorizedError("The credential changed meanwhile. Try again.")
        log_event(logger, "connector.credential_rotated", device_id=device.id)
        return credential

    async def _disconnect_reused(self, device: Device, now: datetime) -> None:
        if await self._store.revoke(
            workspace_id=device.workspace_id,
            device_id=device.id,
            reason=RevokedBecause.REUSED.value,
            by=None,
            at=now,
        ):
            self._known.pop(device.id, None)
            log_event(logger, "connector.credential_reused", device_id=device.id)

    async def poll(
        self,
        caller: AuthenticatedDevice,
        *,
        version: str,
        capabilities: dict | None,
        wait: float,
        take: bool = True,
    ) -> PollResult:
        """Hold the poll open until a request waits for this computer, or the time is up.
        With ``take`` false it only says the computer is still there (while it works on a
        long request), and takes nothing."""
        device = caller.device
        if not await self._allow(
            f"connector:poll:{device.id}", limit=c.POLLS_PER_MINUTE, window=60
        ):
            raise RateLimitedError("This computer is asking for work too often. Wait a minute.")
        if self._open_polls.get(device.id, 0) >= c.MAX_OPEN_POLLS:
            raise RateLimitedError("This computer already has polls open. Wait for them first.")
        self._open_polls[device.id] = self._open_polls.get(device.id, 0) + 1
        try:
            return await self._poll(
                caller, version=version, capabilities=capabilities, wait=wait, take=take
            )
        finally:
            left = self._open_polls.get(device.id, 1) - 1
            if left > 0:
                self._open_polls[device.id] = left
            else:
                self._open_polls.pop(device.id, None)

    async def _poll(
        self,
        caller: AuthenticatedDevice,
        *,
        version: str,
        capabilities: dict | None,
        wait: float,
        take: bool,
    ) -> PollResult:
        device = caller.device
        reported = (
            DeviceCapabilities.from_json(capabilities).to_json()
            if capabilities is not None
            else None
        )
        if reported is not None:
            self._known[device.id] = DeviceCapabilities.from_json(reported)
        await self._store.seen(
            device_id=device.id, at=self._now(), version=version, capabilities=reported
        )
        rotate = self._now() - device.credential_issued_at >= c.ROTATE_AFTER or caller.previous
        if not take:
            return PollResult(request=None, rotate=rotate)
        await self._sweep()
        deadline = asyncio.get_running_loop().time() + max(0.0, min(wait, self._long_poll))
        last_seen = asyncio.get_running_loop().time()
        step = self._step
        while True:
            now = self._now()
            # A request older than the pickup window has no one waiting for it any more.
            taken = await self._store.take_next(
                device_id=device.id, at=now, since=now - timedelta(seconds=self._pickup)
            )
            if taken is not None:
                return PollResult(request=taken, rotate=rotate)
            clock = asyncio.get_running_loop().time()
            if clock >= deadline:
                return PollResult(request=None, rotate=rotate)
            if clock - last_seen >= 10:
                # Still here: keep it online, and stop at once if it was disconnected.
                current = await self._store.device(device.id)
                if current is None or current.status is not DeviceStatus.ACTIVE:
                    raise UnauthorizedError("This computer was disconnected.")
                await self._store.seen(
                    device_id=device.id, at=self._now(), version=version, capabilities=None
                )
                last_seen = clock
            await asyncio.sleep(min(step, max(0.0, deadline - clock)))
            # Checked less often the longer nothing comes, to spare the database.
            step = min(step * 2, self._max_step)

    async def _sweep(self) -> None:
        """Clear words a crash left in the queue, at most once a minute on this server."""
        now = self._now()
        if self._swept_at is not None and now - self._swept_at < _SWEEP_EVERY:
            return
        self._swept_at = now
        cleared = await self._store.sweep(
            at=now,
            open_before=now - timedelta(seconds=self._pickup + self._answer),
            read_before=now - timedelta(seconds=c.FINISHED_WORDS_SECONDS),
        )
        if cleared:
            log_event(logger, "connector.requests_swept", count=cleared)

    async def answer(
        self,
        caller: AuthenticatedDevice,
        request_id: str,
        *,
        status_code: int | None,
        body: dict | None,
        failure: str | None,
    ) -> None:
        model: str | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        if failure is not None and failure not in FAILURES:
            failure = "local_server_error"
        too_large = failure is None and body is not None and _size(body) > c.MAX_ANSWER_BYTES
        if too_large:
            # Measured as received, whatever the request said its length was.
            failure, body = "too_large", None
        if failure is None and isinstance(body, dict):
            raw_model = body.get("model")
            model = str(raw_model)[: c.MAX_MODEL_NAME] if raw_model else None
            usage = body.get("usage")
            if isinstance(usage, dict):
                # A computer costs nothing per call: a cost it reports is not kept.
                usage.pop("cost", None)
                prompt_tokens = _count(usage.get("prompt_tokens"))
                completion_tokens = _count(usage.get("completion_tokens"))
        answered = await self._store.answer(
            device_id=caller.device.id,
            request_id=request_id,
            status=(RequestStatus.FAILED if failure else RequestStatus.DONE).value,
            result=None if failure else body,
            result_status=None if failure else status_code,
            failure=failure,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            at=self._now(),
        )
        if not answered:
            raise NotFoundError("That request is no longer waiting for an answer.")
        if too_large:
            raise AnswerTooLarge("The answer was too large to use, so it was not kept.")

    # ------------------------------------------------------------------ members in the app

    async def devices(self, scope: AccessScope, workspace_id: str) -> list[Device]:
        await self._require_admin(scope, workspace_id)
        return list(await self._store.devices(workspace_id))

    async def revoke(self, scope: AccessScope, workspace_id: str, device_id: str) -> Device:
        await self._require_admin(scope, workspace_id)
        device = await self._store.revoke(
            workspace_id=workspace_id,
            device_id=device_id,
            reason=RevokedBecause.OWNER.value,
            by=scope.actor_user_id,
            at=self._now(),
        )
        if device is None:
            raise NotFoundError("That computer was not found, or is already disconnected.")
        self._known.pop(device_id, None)
        await self._audit(scope, workspace_id, "connector.device_disconnected", device_id, {})
        return device  # type: ignore[no-any-return]

    async def choose(
        self, scope: AccessScope, workspace_id: str, device_id: str, *, model: str
    ) -> Device:
        """Use this computer, and one of its models, for the workspace's assistant."""
        await self._require_admin(scope, workspace_id)
        device = await self._store.device(device_id)
        if (
            device is None
            or device.workspace_id != workspace_id
            or device.status is not DeviceStatus.ACTIVE
        ):
            raise NotFoundError("That computer was not found, or is disconnected.")
        model = model.strip()
        names = {m.name for m in device.capabilities.models}
        if model not in names:
            raise ValidationError("Choose one of the models this computer listed.")
        if self._ai_settings is None:
            raise CapabilityUnavailableError("Model choice is not available on this site.")
        await self._store.set_in_use(workspace_id=workspace_id, device_id=device_id)
        await self._ai_settings.set(
            workspace_id=workspace_id,
            provider=c.MY_COMPUTER,
            model=model,
            actor_user_id=scope.actor_user_id,
        )
        await self._audit(
            scope, workspace_id, "connector.device_chosen", device_id, {"model": model}
        )
        chosen = await self._store.device(device_id)
        return chosen if chosen is not None else device  # type: ignore[no-any-return]

    async def stop_using(self, scope: AccessScope, workspace_id: str) -> None:
        """Stop using a computer: the assistant goes back to a key, or the server's model."""
        await self._require_admin(scope, workspace_id)
        stored = await self._ai_settings.get(workspace_id) if self._ai_settings else None
        await self._store.set_in_use(workspace_id=workspace_id, device_id=None)
        if stored is not None and stored[0] == c.MY_COMPUTER:
            await self._ai_settings.delete_workspace(workspace_id)
        await self._audit(scope, workspace_id, "connector.device_unchosen", workspace_id, {})

    async def withdraw_for_member(
        self, scope: AccessScope, workspace_id: str, user_id: str
    ) -> list[str]:
        """The member left: every computer they connected here is disconnected for good."""
        stopped: list[str] = []
        for device in await self._store.devices(workspace_id):
            if device.registered_by != user_id or device.status is not DeviceStatus.ACTIVE:
                continue
            if await self._store.revoke(
                workspace_id=workspace_id,
                device_id=device.id,
                reason=RevokedBecause.ACCESS_LOST.value,
                by=scope.actor_user_id,
                at=self._now(),
            ):
                stopped.append(device.id)
                self._known.pop(device.id, None)
                await self._audit(
                    scope,
                    workspace_id,
                    "connector.device_disconnected",
                    device.id,
                    {"reason": RevokedBecause.ACCESS_LOST.value},
                )
        return stopped

    # ------------------------------------------------------------------ the route

    async def chosen(self, workspace_id: str) -> bool:
        stored = await self._ai_settings.get(workspace_id) if self._ai_settings else None
        return stored is not None and stored[0] == c.MY_COMPUTER

    async def route(self, scope: AccessScope, workspace_id: str) -> Any | None:
        """The route for an assistant call when the workspace chose its own computer; None
        when it did not. Callers have checked the person may use the assistant."""
        stored = await self._ai_settings.get(workspace_id) if self._ai_settings else None
        if stored is None or stored[0] != c.MY_COMPUTER:
            return None
        device = await self._store.in_use(workspace_id)
        if device is None:
            # Never a quiet fall back to a cloud key: the author chose their own computer.
            raise AIDeviceUnavailableError(_GONE)
        self._known[device.id] = device.capabilities
        _request_actor.set(scope.actor_user_id)
        _request_project.set(scope.project_id)
        from backend.services.credential_service import ProviderRoute

        return ProviderRoute(
            credential_id="",
            # Never sent anywhere: the computer is reached through the queue, not a key.
            api_key="connector",
            provider=ProviderSpec(
                id=c.MY_COMPUTER,
                label=c.MY_COMPUTER_LABEL,
                base_url=c.device_address(workspace_id, device.id),
                structured=StructuredMode.SCHEMA
                if device.capabilities.structured_output
                else StructuredMode.PROMPTED,
                key_url="",
                default_model=None,
            ),
            model=stored[1],
        )

    def known_capabilities(self, device_id: str) -> DeviceCapabilities | None:
        return self._known.get(device_id)

    # ------------------------------------------------------------------ the relay

    async def relay(
        self, workspace_id: str, device_id: str, *, method: str, path: str, body: dict | None
    ) -> tuple[int, dict]:
        """Send one model request to the computer and wait for its answer."""
        if (method, path) not in c.RELAYED_CALLS:
            raise AIProviderError("That request cannot be sent to a computer.")
        device = await self._store.device(device_id)
        if (
            device is None
            or device.workspace_id != workspace_id
            or device.status is not DeviceStatus.ACTIVE
        ):
            raise AIDeviceUnavailableError(_GONE)
        if not device.online(self._now()):
            raise AIDeviceUnavailableError(_OFFLINE)
        payload = {"method": method, "path": path, "body": body}
        if len(json.dumps(payload)) > c.MAX_REQUEST_BYTES:
            raise AIProviderError("The request is too large to send to a computer.")
        request = ChannelRequest(
            id=f"creq_{uuid7().hex}",
            workspace_id=workspace_id,
            device_id=device_id,
            actor_user_id=_request_actor.get(),
            kind=f"{method} {path}",
            status=RequestStatus.WAITING,
            created_at=self._now(),
            payload=payload,
            model=str((body or {}).get("model") or "")[: c.MAX_MODEL_NAME] or None,
            project_id=_request_project.get(),
        )
        await self._sweep()
        await self._store.enqueue(request)
        loop = asyncio.get_running_loop()
        started = loop.time()
        taken_at: float | None = None
        failure: str | None = "gave_up"
        try:
            while True:
                found = await self._store.request(request.id)
                if found is None:
                    raise AIDeviceUnavailableError(_GONE)
                if found.status is RequestStatus.DONE:
                    failure = None
                    return int(found.result_status or 200), dict(found.result or {})
                if found.status is RequestStatus.FAILED:
                    failure = None
                    raise AIDeviceUnavailableError(_plain_failure(found.failure))
                if found.status is RequestStatus.EXPIRED:
                    raise AIDeviceUnavailableError(_NOT_TAKEN)
                clock = loop.time()
                if found.status is RequestStatus.WAITING and clock - started > self._pickup:
                    failure = "not_taken"
                    raise AIDeviceUnavailableError(_NOT_TAKEN)
                if found.status is RequestStatus.TAKEN:
                    taken_at = taken_at if taken_at is not None else clock
                    if clock - taken_at > self._answer:
                        failure = "too_slow"
                        raise AIDeviceUnavailableError(_TOO_SLOW)
                await asyncio.sleep(self._step)
        finally:
            # The words are cleared whatever happened, even when the caller gave up.
            await asyncio.shield(
                self._store.close(request_id=request.id, failure=failure, at=self._now())
            )


def _size(body: Any) -> int:
    """The answer's size as the connector sends it (UTF-8 JSON, letters unescaped)."""
    return len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


__all__ = [
    "FAILURES",
    "AnswerTooLarge",
    "AuthenticatedDevice",
    "ConnectorService",
    "ConnectorTooOld",
    "Exchange",
    "PollResult",
    "StartedCode",
    "limit_reached",
]
