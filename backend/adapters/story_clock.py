"""PostgreSQL store for the unit a story counts its time in (ADR-020)."""

from __future__ import annotations

import json
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

    async def get_how_long(self, *, workspace_id: str, project_id: str) -> tuple[Any, int]:
        """The author's table of travel times and lengths, and its version (0 when none)."""
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT how_long, how_long_version FROM story_clocks "
                        "WHERE workspace_id = :ws AND project_id = :pj"
                    ),
                    {"ws": workspace_id, "pj": project_id},
                )
            ).first()
        if row is None:
            return {}, 0
        return row[0] or {}, int(row[1])

    async def set_how_long(
        self, *, workspace_id: str, project_id: str, table: Any, expected_version: int
    ) -> int | None:
        """Replace the table if it is still at ``expected_version``; the new version, or
        ``None`` when someone else saved it first."""
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "INSERT INTO story_clocks (workspace_id, project_id, how_long, "
                        "how_long_version) "
                        "SELECT :ws, :pj, CAST(:table AS JSONB), 1 WHERE :expected = 0 "
                        "ON CONFLICT (workspace_id, project_id) DO UPDATE SET "
                        "how_long = EXCLUDED.how_long, "
                        "how_long_version = story_clocks.how_long_version + 1, "
                        "updated_at = now() "
                        "WHERE story_clocks.how_long_version = :expected "
                        "RETURNING how_long_version"
                    ),
                    {
                        "ws": workspace_id,
                        "pj": project_id,
                        "table": json.dumps(table),
                        "expected": expected_version,
                    },
                )
            ).first()
            if row is None and expected_version > 0:
                # No row to update: only an existing table can be at a later version.
                row = (
                    await db.execute(
                        text(
                            "UPDATE story_clocks SET how_long = CAST(:table AS JSONB), "
                            "how_long_version = how_long_version + 1, updated_at = now() "
                            "WHERE workspace_id = :ws AND project_id = :pj "
                            "AND how_long_version = :expected RETURNING how_long_version"
                        ),
                        {
                            "ws": workspace_id,
                            "pj": project_id,
                            "table": json.dumps(table),
                            "expected": expected_version,
                        },
                    )
                ).first()
        return int(row[0]) if row is not None else None
