"""PostgreSQL store for connected computers and their request queue (SCN-002A, ADR-025).

Only digests of device codes and credentials are written. A request's body and answer
are cleared by ``close`` as soon as the request that waited for them is finished, and by
``sweep`` when the waiter died before it could (a server stopped mid-request).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from backend.core.database import session_scope
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

# Finished requests (no words left in them) are kept this long for usage and audit.
_KEEP_FINISHED = timedelta(days=30)
# Codes that ran out are removed after this long.
_KEEP_EXPIRED_CODES = timedelta(days=1)


def _code(row: Any) -> PendingCode:
    return PendingCode(
        device_code_hash=row.device_code_hash,
        user_code=row.user_code,
        device_name=row.device_name,
        connector_version=row.connector_version,
        status=CodeStatus(row.status),
        created_at=row.created_at,
        expires_at=row.expires_at,
        interval_seconds=row.interval_seconds,
        workspace_id=row.workspace_id,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        device_id=row.device_id,
        last_polled_at=row.last_polled_at,
    )


def _device(row: Any) -> Device:
    return Device(
        id=row.id,
        workspace_id=row.workspace_id,
        name=row.name,
        registered_by=row.registered_by,
        status=DeviceStatus(row.status),
        credential_hash=row.credential_hash,
        credential_issued_at=row.credential_issued_at,
        created_at=row.created_at,
        connector_version=row.connector_version,
        capabilities=DeviceCapabilities.from_json(row.capabilities),
        last_seen_at=row.last_seen_at,
        in_use=row.in_use,
        previous_hash=row.previous_hash,
        previous_valid_until=row.previous_valid_until,
        revoked_reason=RevokedBecause(row.revoked_reason) if row.revoked_reason else None,
        revoked_by=row.revoked_by,
        revoked_at=row.revoked_at,
        credential_last_used_at=row.credential_last_used_at,
    )


def _request(row: Any) -> ChannelRequest:
    return ChannelRequest(
        id=row.id,
        workspace_id=row.workspace_id,
        device_id=row.device_id,
        actor_user_id=row.actor_user_id,
        kind=row.kind,
        status=RequestStatus(row.status),
        created_at=row.created_at,
        payload=row.payload,
        result=row.result,
        result_status=row.result_status,
        failure=row.failure,
        model=row.model,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        taken_at=row.taken_at,
        finished_at=row.finished_at,
        project_id=row.project_id,
    )


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


class PostgresConnectorStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    # --- registration codes ---------------------------------------------------------

    async def add_code(self, code: PendingCode) -> bool:
        try:
            async with session_scope(self._session_factory) as db:
                await db.execute(
                    text("DELETE FROM connector_codes WHERE expires_at < :before"),
                    {"before": code.created_at - _KEEP_EXPIRED_CODES},
                )
                await db.execute(
                    text(
                        """
                        INSERT INTO connector_codes (
                            device_code_hash, user_code, device_name, connector_version,
                            status, interval_seconds, created_at, expires_at
                        ) VALUES (
                            :hash, :user_code, :name, :version,
                            :status, :interval, :created, :expires
                        )
                        """
                    ),
                    {
                        "hash": code.device_code_hash,
                        "user_code": code.user_code,
                        "name": code.device_name,
                        "version": code.connector_version,
                        "status": code.status.value,
                        "interval": code.interval_seconds,
                        "created": code.created_at,
                        "expires": code.expires_at,
                    },
                )
        except IntegrityError:
            return False
        return True

    async def code_by_device_hash(self, device_code_hash: str) -> PendingCode | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text("SELECT * FROM connector_codes WHERE device_code_hash = :hash"),
                    {"hash": device_code_hash},
                )
            ).first()
        return _code(row) if row is not None else None

    async def code_by_user_code(self, user_code: str) -> PendingCode | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text("SELECT * FROM connector_codes WHERE user_code = :code"),
                    {"code": user_code},
                )
            ).first()
        return _code(row) if row is not None else None

    async def decide_code(
        self, *, user_code: str, approve: bool, workspace_id: str, decided_by: str, at: datetime
    ) -> PendingCode | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        """
                        UPDATE connector_codes
                        SET status = :status, workspace_id = :ws, decided_by = :by,
                            decided_at = :at
                        WHERE user_code = :code AND status = 'pending' AND expires_at > :at
                        RETURNING *
                        """
                    ),
                    {
                        "status": (CodeStatus.APPROVED if approve else CodeStatus.DENIED).value,
                        "ws": workspace_id,
                        "by": decided_by,
                        "at": at,
                        "code": user_code,
                    },
                )
            ).first()
        return _code(row) if row is not None else None

    async def touch_code(self, *, device_code_hash: str, at: datetime, interval: int) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "UPDATE connector_codes SET last_polled_at = :at, interval_seconds = :interval "
                    "WHERE device_code_hash = :hash"
                ),
                {"at": at, "interval": interval, "hash": device_code_hash},
            )

    async def issue_code(self, *, device_code_hash: str, device: Device) -> bool:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        """
                        UPDATE connector_codes SET status = 'issued', device_id = :device
                        WHERE device_code_hash = :hash AND status = 'approved'
                        RETURNING device_code_hash
                        """
                    ),
                    {"device": device.id, "hash": device_code_hash},
                )
            ).first()
            if row is None:
                return False
            await db.execute(
                text(
                    """
                    INSERT INTO connector_devices (
                        id, workspace_id, name, registered_by, status, credential_hash,
                        credential_issued_at, connector_version, capabilities, created_at,
                        last_seen_at
                    ) VALUES (
                        :id, :ws, :name, :by, 'active', :hash,
                        :issued, :version, CAST(:caps AS JSONB), :created,
                        :seen
                    )
                    """
                ),
                {
                    "id": device.id,
                    "ws": device.workspace_id,
                    "name": device.name,
                    "by": device.registered_by,
                    "hash": device.credential_hash,
                    "issued": device.credential_issued_at,
                    "version": device.connector_version,
                    "caps": _json(device.capabilities.to_json()),
                    "created": device.created_at,
                    "seen": device.last_seen_at,
                },
            )
        return True

    # --- devices ----------------------------------------------------------------------

    async def device(self, device_id: str) -> Device | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text("SELECT * FROM connector_devices WHERE id = :id"), {"id": device_id}
                )
            ).first()
        return _device(row) if row is not None else None

    async def device_by_credential(self, credential_hash: str) -> Device | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT * FROM connector_devices "
                        "WHERE credential_hash = :hash OR previous_hash = :hash LIMIT 1"
                    ),
                    {"hash": credential_hash},
                )
            ).first()
        return _device(row) if row is not None else None

    async def devices(self, workspace_id: str) -> list[Device]:
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT * FROM connector_devices WHERE workspace_id = :ws "
                        "ORDER BY created_at DESC"
                    ),
                    {"ws": workspace_id},
                )
            ).all()
        return [_device(row) for row in rows]

    async def seen(
        self, *, device_id: str, at: datetime, version: str, capabilities: dict | None
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    """
                    UPDATE connector_devices
                    SET last_seen_at = :at, connector_version = :version,
                        capabilities = COALESCE(CAST(:caps AS JSONB), capabilities)
                    WHERE id = :id AND status = 'active'
                    """
                ),
                {"at": at, "version": version, "caps": _json(capabilities), "id": device_id},
            )

    async def rotate(
        self,
        *,
        device_id: str,
        expected_hash: str,
        new_hash: str,
        previous_hash: str,
        at: datetime,
        grace_until: datetime,
        only_if_unused: bool = False,
    ) -> bool:
        """Replace the credential. ``only_if_unused``: only while the current one has never
        been used (a rotation asked for with the credential it replaced)."""
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        """
                        UPDATE connector_devices
                        SET previous_hash = :previous, previous_valid_until = :grace,
                            credential_hash = :new, credential_issued_at = :at,
                            credential_last_used_at = NULL
                        WHERE id = :id AND status = 'active' AND credential_hash = :expected
                          AND (NOT :unused OR credential_last_used_at IS NULL)
                        RETURNING id
                        """
                    ),
                    {
                        "grace": grace_until,
                        "new": new_hash,
                        "at": at,
                        "id": device_id,
                        "previous": previous_hash,
                        "expected": expected_hash,
                        "unused": only_if_unused,
                    },
                )
            ).first()
        return row is not None

    async def credential_used(self, *, device_id: str, credential_hash: str, at: datetime) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "UPDATE connector_devices SET credential_last_used_at = :at "
                    "WHERE id = :id AND status = 'active' AND credential_hash = :hash"
                ),
                {"at": at, "id": device_id, "hash": credential_hash},
            )

    async def revoke(
        self, *, workspace_id: str, device_id: str, reason: str, by: str | None, at: datetime
    ) -> Device | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        """
                        UPDATE connector_devices
                        SET status = 'revoked', revoked_reason = :reason, revoked_by = :by,
                            revoked_at = :at, in_use = false, previous_hash = NULL,
                            previous_valid_until = NULL
                        WHERE workspace_id = :ws AND id = :id AND status = 'active'
                        RETURNING *
                        """
                    ),
                    {"reason": reason, "by": by, "at": at, "ws": workspace_id, "id": device_id},
                )
            ).first()
            if row is not None:
                # Nothing waiting for it is sent once it is disconnected.
                await db.execute(
                    text(
                        """
                        UPDATE connector_requests
                        SET status = 'failed', payload = NULL, result = NULL,
                            failure = 'disconnected', finished_at = :at
                        WHERE device_id = :id AND status IN ('waiting', 'taken')
                        """
                    ),
                    {"id": device_id, "at": at},
                )
        return _device(row) if row is not None else None

    async def set_in_use(self, *, workspace_id: str, device_id: str | None) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text("UPDATE connector_devices SET in_use = false WHERE workspace_id = :ws"),
                {"ws": workspace_id},
            )
            if device_id is not None:
                await db.execute(
                    text(
                        "UPDATE connector_devices SET in_use = true "
                        "WHERE workspace_id = :ws AND id = :id AND status = 'active'"
                    ),
                    {"ws": workspace_id, "id": device_id},
                )

    async def in_use(self, workspace_id: str) -> Device | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT * FROM connector_devices "
                        "WHERE workspace_id = :ws AND in_use AND status = 'active'"
                    ),
                    {"ws": workspace_id},
                )
            ).first()
        return _device(row) if row is not None else None

    # --- the request queue ----------------------------------------------------------------

    async def enqueue(self, request: ChannelRequest) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "DELETE FROM connector_requests WHERE device_id = :device "
                    "AND finished_at < :before"
                ),
                {"device": request.device_id, "before": request.created_at - _KEEP_FINISHED},
            )
            await db.execute(
                text(
                    """
                    INSERT INTO connector_requests (
                        id, workspace_id, project_id, device_id, actor_user_id, kind, status,
                        payload, model, created_at
                    ) VALUES (
                        :id, :ws, :project, :device, :actor, :kind, 'waiting',
                        CAST(:payload AS JSONB), :model, :created
                    )
                    """
                ),
                {
                    "id": request.id,
                    "ws": request.workspace_id,
                    "project": request.project_id,
                    "device": request.device_id,
                    "actor": request.actor_user_id,
                    "kind": request.kind,
                    "payload": _json(request.payload),
                    "model": request.model,
                    "created": request.created_at,
                },
            )

    async def take_next(
        self, *, device_id: str, at: datetime, since: datetime
    ) -> ChannelRequest | None:
        """The oldest request waiting for this computer, asked for after ``since`` (an older
        one has no one waiting for it any more), while the computer is still connected."""
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        """
                        UPDATE connector_requests SET status = 'taken', taken_at = :at
                        WHERE id = (
                            SELECT r.id FROM connector_requests r
                            JOIN connector_devices d ON d.id = r.device_id
                            WHERE r.device_id = :device AND r.status = 'waiting'
                              AND r.created_at > :since AND d.status = 'active'
                            ORDER BY r.created_at
                            LIMIT 1
                            FOR UPDATE OF r SKIP LOCKED
                        )
                        RETURNING *
                        """
                    ),
                    {"at": at, "device": device_id, "since": since},
                )
            ).first()
        return _request(row) if row is not None else None

    async def sweep(self, *, at: datetime, open_before: datetime, read_before: datetime) -> int:
        """Clear the words a crash left behind: requests still open that were asked for
        before ``open_before`` are expired, and finished ones not read by ``read_before``
        lose their answer. Returns how many rows were cleared."""
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    """
                    UPDATE connector_requests
                    SET payload = NULL, result = NULL,
                        status = CASE WHEN status IN ('waiting', 'taken')
                                      THEN 'expired' ELSE status END,
                        failure = CASE WHEN status IN ('waiting', 'taken')
                                       THEN 'abandoned' ELSE failure END,
                        finished_at = COALESCE(finished_at, :at)
                    WHERE (payload IS NOT NULL OR result IS NOT NULL)
                      AND (
                        (status IN ('waiting', 'taken') AND created_at < :open_before)
                        OR (status NOT IN ('waiting', 'taken') AND finished_at < :read_before)
                      )
                    """
                ),
                {"at": at, "open_before": open_before, "read_before": read_before},
            )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def request(self, request_id: str) -> ChannelRequest | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text("SELECT * FROM connector_requests WHERE id = :id"), {"id": request_id}
                )
            ).first()
        return _request(row) if row is not None else None

    async def answer(
        self,
        *,
        device_id: str,
        request_id: str,
        status: str,
        result: dict | None,
        result_status: int | None,
        failure: str | None,
        model: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        at: datetime,
    ) -> bool:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        """
                        UPDATE connector_requests
                        SET status = :status, result = CAST(:result AS JSONB),
                            result_status = :result_status, failure = :failure,
                            model = COALESCE(:model, model), prompt_tokens = :prompt,
                            completion_tokens = :completion, finished_at = :at,
                            payload = NULL
                        WHERE id = :id AND device_id = :device AND status = 'taken'
                        RETURNING id
                        """
                    ),
                    {
                        "status": status,
                        "result": _json(result),
                        "result_status": result_status,
                        "failure": failure,
                        "model": model,
                        "prompt": prompt_tokens,
                        "completion": completion_tokens,
                        "at": at,
                        "id": request_id,
                        "device": device_id,
                    },
                )
            ).first()
        return row is not None

    async def close(self, *, request_id: str, failure: str | None, at: datetime) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    """
                    UPDATE connector_requests
                    SET payload = NULL, result = NULL,
                        status = CASE WHEN status IN ('waiting', 'taken')
                                      THEN 'expired' ELSE status END,
                        failure = CASE WHEN status IN ('waiting', 'taken')
                                       THEN :failure ELSE failure END,
                        finished_at = COALESCE(finished_at, :at)
                    WHERE id = :id
                    """
                ),
                {"failure": failure, "at": at, "id": request_id},
            )

    async def delete_project(self, workspace_id: str, project_id: str) -> int:
        """Requests made for a story go with it (anything still waiting is not sent)."""
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    "DELETE FROM connector_requests "
                    "WHERE workspace_id = :ws AND project_id = :project"
                ),
                {"ws": workspace_id, "project": project_id},
            )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def delete_workspace(self, workspace_id: str) -> int:
        async with session_scope(self._session_factory) as db:
            total = 0
            for table in ("connector_requests", "connector_devices", "connector_codes"):
                result = await db.execute(
                    text(f"DELETE FROM {table} WHERE workspace_id = :ws"),  # noqa: S608 - fixed names
                    {"ws": workspace_id},
                )
                total += result.rowcount or 0  # type: ignore[attr-defined]
        return total


__all__ = ["PostgresConnectorStore"]
