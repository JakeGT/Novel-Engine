"""PostgreSQL store for the unit a story counts its time in (ADR-020)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope


class PostgresStoryClockStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def get_unit(self, *, workspace_id: str, project_id: str) -> str | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT unit FROM story_clocks "
                        "WHERE workspace_id = :ws AND project_id = :pj"
                    ),
                    {"ws": workspace_id, "pj": project_id},
                )
            ).first()
        return str(row[0]) if row else None

    async def set_unit(self, *, workspace_id: str, project_id: str, unit: str) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "INSERT INTO story_clocks (workspace_id, project_id, unit) "
                    "VALUES (:ws, :pj, :unit) "
                    "ON CONFLICT (workspace_id, project_id) "
                    "DO UPDATE SET unit = EXCLUDED.unit, updated_at = now()"
                ),
                {"ws": workspace_id, "pj": project_id, "unit": unit},
            )
