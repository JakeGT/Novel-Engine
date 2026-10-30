"""PostgreSQL store for a workspace's chosen provider and model."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope


class PostgresAiSettingsStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def get(self, workspace_id: str) -> tuple[str, str] | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT provider, model FROM workspace_ai_settings WHERE workspace_id = :ws"
                    ),
                    {"ws": workspace_id},
                )
            ).first()
        return (row[0], row[1]) if row is not None else None

    async def set(
        self, *, workspace_id: str, provider: str, model: str, actor_user_id: str | None
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    """
                    INSERT INTO workspace_ai_settings (workspace_id, provider, model, updated_by)
                    VALUES (:ws, :provider, :model, :actor)
                    ON CONFLICT (workspace_id) DO UPDATE SET
                        provider = EXCLUDED.provider,
                        model = EXCLUDED.model,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = now()
                    """
                ),
                {"ws": workspace_id, "provider": provider, "model": model, "actor": actor_user_id},
            )

    async def delete_workspace(self, workspace_id: str) -> int:
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text("DELETE FROM workspace_ai_settings WHERE workspace_id = :ws"),
                {"ws": workspace_id},
            )
            return result.rowcount or 0  # type: ignore[attr-defined]
