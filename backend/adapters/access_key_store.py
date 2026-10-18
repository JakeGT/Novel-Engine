"""PostgreSQL store for access keys and their use counts (QRY-003).

Only the SHA-256 digest of a key is written. Use is counted per key, day and tool;
nothing a tool read or was asked is stored.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.access_keys import AccessKey, AccessKeyUse, RevokedBecause

# A key's last-used time is written at most this often, so a busy assistant does
# not turn every read into a write.
_TOUCH_SECONDS = 60


def _key(row: Any) -> AccessKey:
    return AccessKey(
        id=row.id,
        workspace_id=row.workspace_id,
        user_id=row.user_id,
        project_id=row.project_id,
        name=row.name,
        hint=row.hint,
        may_suggest=row.may_suggest,
        token_hash=row.token_hash,
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
        revoked_reason=RevokedBecause(row.revoked_reason) if row.revoked_reason else None,
    )


class PostgresAccessKeyStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def create(self, key: AccessKey) -> AccessKey:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    """
                    INSERT INTO access_keys (
                        id, workspace_id, user_id, project_id, name, hint, may_suggest,
                        token_hash, created_at, expires_at
                    ) VALUES (
                        :id, :ws, :user, :project, :name, :hint, :suggest,
                        :hash, :created, :expires
                    )
                    """
                ),
                {
                    "id": key.id,
                    "ws": key.workspace_id,
                    "user": key.user_id,
                    "project": key.project_id,
                    "name": key.name,
                    "hint": key.hint,
                    "suggest": key.may_suggest,
                    "hash": key.token_hash,
                    "created": key.created_at,
                    "expires": key.expires_at,
                },
            )
        return key

    async def get(self, *, workspace_id: str, key_id: str) -> AccessKey | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text("SELECT * FROM access_keys WHERE workspace_id = :ws AND id = :id"),
                    {"ws": workspace_id, "id": key_id},
                )
            ).first()
        return _key(row) if row is not None else None

    async def get_by_hash(self, token_hash: str) -> AccessKey | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text("SELECT * FROM access_keys WHERE token_hash = :hash"),
                    {"hash": token_hash},
                )
            ).first()
        return _key(row) if row is not None else None

    async def list_keys(self, *, workspace_id: str, user_id: str | None = None) -> list[AccessKey]:
        query = "SELECT * FROM access_keys WHERE workspace_id = :ws"
        values: dict[str, Any] = {"ws": workspace_id}
        if user_id is not None:
            query += " AND user_id = :user"
            values["user"] = user_id
        async with session_scope(self._session_factory) as db:
            rows = (await db.execute(text(query + " ORDER BY created_at DESC, id"), values)).all()
        return [_key(row) for row in rows]

    async def revoke(self, *, key_id: str, reason: str, at: datetime) -> bool:
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    "UPDATE access_keys SET revoked_at = :at, revoked_reason = :reason "
                    "WHERE id = :id AND revoked_at IS NULL"
                ),
                {"id": key_id, "reason": reason, "at": at},
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def revoke_for_member(
        self, *, workspace_id: str, user_id: str, reason: str, at: datetime
    ) -> list[str]:
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        "UPDATE access_keys SET revoked_at = :at, revoked_reason = :reason "
                        "WHERE workspace_id = :ws AND user_id = :user AND revoked_at IS NULL "
                        "RETURNING id"
                    ),
                    {"ws": workspace_id, "user": user_id, "reason": reason, "at": at},
                )
            ).all()
        return sorted(row.id for row in rows)

    async def touch(self, *, key_id: str, at: datetime) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "UPDATE access_keys SET last_used_at = :at WHERE id = :id AND "
                    "(last_used_at IS NULL OR last_used_at < :at - make_interval(secs => :gap))"
                ),
                {"id": key_id, "at": at, "gap": _TOUCH_SECONDS},
            )

    async def record_use(
        self, *, key_id: str, workspace_id: str, day: str, tool: str, refused: bool
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    """
                    INSERT INTO access_key_uses (key_id, workspace_id, day, tool, calls, refused)
                    VALUES (:id, :ws, :day, :tool, 1, :refused)
                    ON CONFLICT (key_id, day, tool) DO UPDATE SET
                        calls = access_key_uses.calls + 1,
                        refused = access_key_uses.refused + EXCLUDED.refused
                    """
                ),
                {
                    "id": key_id,
                    "ws": workspace_id,
                    "day": date.fromisoformat(day),
                    "tool": tool,
                    "refused": 1 if refused else 0,
                },
            )

    async def uses(self, *, key_id: str, since_day: str) -> list[AccessKeyUse]:
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT day, tool, calls, refused FROM access_key_uses "
                        "WHERE key_id = :id AND day >= :since ORDER BY day DESC, tool"
                    ),
                    {"id": key_id, "since": date.fromisoformat(since_day)},
                )
            ).all()
        return [
            AccessKeyUse(
                day=row.day.isoformat(), tool=row.tool, calls=row.calls, refused=row.refused
            )
            for row in rows
        ]

    async def delete_workspace(self, workspace_id: str) -> int:
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text("DELETE FROM access_keys WHERE workspace_id = :ws"), {"ws": workspace_id}
            )
            return result.rowcount or 0  # type: ignore[attr-defined]

    async def delete_project(self, workspace_id: str, project_id: str) -> int:
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text("DELETE FROM access_keys WHERE workspace_id = :ws AND project_id = :p"),
                {"ws": workspace_id, "p": project_id},
            )
            return result.rowcount or 0  # type: ignore[attr-defined]
