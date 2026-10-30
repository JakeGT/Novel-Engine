"""PostgreSQL store for a story's (or a workspace's) assistant settings (CON-009, OPS-002)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.usage_controls import AssistantSettings, CheckProfile

# The workspace's own row has no story.
WORKSPACE = ""


class PostgresAssistantSettingsStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def get(self, *, workspace_id: str, project_id: str = WORKSPACE) -> AssistantSettings:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT check_profile, scene_tokens, check_minutes, "
                        "confirm_above_tokens, conversation_tokens FROM assistant_settings "
                        "WHERE workspace_id = :w AND project_id = :p"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).first()
        if row is None:
            return AssistantSettings()
        return AssistantSettings(
            check_profile=CheckProfile(row[0]),
            scene_tokens=int(row[1]),
            check_minutes=int(row[2]),
            confirm_above_tokens=row[3],
            conversation_tokens=row[4],
        )

    async def set(
        self,
        *,
        workspace_id: str,
        project_id: str = WORKSPACE,
        settings: AssistantSettings,
        actor_user_id: str | None,
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    """
                    INSERT INTO assistant_settings (
                        workspace_id, project_id, check_profile, scene_tokens, check_minutes,
                        confirm_above_tokens, conversation_tokens, updated_by
                    ) VALUES (:w, :p, :profile, :scene, :minutes, :confirm, :conversation, :by)
                    ON CONFLICT (workspace_id, project_id) DO UPDATE SET
                        check_profile = EXCLUDED.check_profile,
                        scene_tokens = EXCLUDED.scene_tokens,
                        check_minutes = EXCLUDED.check_minutes,
                        confirm_above_tokens = EXCLUDED.confirm_above_tokens,
                        conversation_tokens = EXCLUDED.conversation_tokens,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = now()
                    """
                ),
                {
                    "w": workspace_id,
                    "p": project_id,
                    "profile": settings.check_profile.value,
                    "scene": settings.scene_tokens,
                    "minutes": settings.check_minutes,
                    "confirm": settings.confirm_above_tokens,
                    "conversation": settings.conversation_tokens,
                    "by": actor_user_id,
                },
            )


__all__ = ["WORKSPACE", "PostgresAssistantSettingsStore"]
