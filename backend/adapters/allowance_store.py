"""PostgreSQL store for a workspace's daily assistant allowance."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope


class PostgresAllowanceStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def get(self, workspace_id: str) -> int | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT daily_tokens FROM workspace_usage_allowance "
                        "WHERE workspace_id = :ws"
                    ),
                    {"ws": workspace_id},
                )
            ).first()
        return int(row[0]) if row is not None else None

    async def set(
        self, *, workspace_id: str, daily_tokens: int | None, actor_user_id: str | None
    ) -> None:
        async with session_scope(self._session_factory) as db:
            if daily_tokens is None:
                await db.execute(
                    text("DELETE FROM workspace_usage_allowance WHERE workspace_id = :ws"),
                    {"ws": workspace_id},
                )
                return
            await db.execute(
                text(
                    """
                    INSERT INTO workspace_usage_allowance (workspace_id, daily_tokens, updated_by)
                    VALUES (:ws, :tokens, :actor)
                    ON CONFLICT (workspace_id) DO UPDATE SET
                        daily_tokens = EXCLUDED.daily_tokens,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = now()
                    """
                ),
                {"ws": workspace_id, "tokens": daily_tokens, "actor": actor_user_id},
            )

    async def delete_workspace(self, workspace_id: str) -> int:
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text("DELETE FROM workspace_usage_allowance WHERE workspace_id = :ws"),
                {"ws": workspace_id},
            )
            return result.rowcount or 0  # type: ignore[attr-defined]
