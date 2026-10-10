"""PostgreSQL store for assistant conversations."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.assist import (
    AssistAnswer,
    AssistKind,
    AssistQuestion,
    AssistSession,
    AssistStatus,
    SchemaSuggestion,
)

_COLUMNS = (
    "id, workspace_id, project_id, actor_user_id, kind, status, description, entity_type, "
    "name, entity_id, questions, answers, understood, notes, mutation_id, created_at, updated_at, "
    "suggestions"
)


def _from_row(row: Any) -> AssistSession:
    return AssistSession(
        id=row[0],
        workspace_id=row[1],
        project_id=row[2],
        actor_user_id=row[3],
        kind=AssistKind(row[4]),
        status=AssistStatus(row[5]),
        description=row[6],
        entity_type=row[7],
        name=row[8],
        entity_id=row[9],
        questions=[AssistQuestion.model_validate(item) for item in (row[10] or [])],
        answers=[AssistAnswer.model_validate(item) for item in (row[11] or [])],
        understood=row[12],
        notes=[str(item) for item in (row[13] or [])],
        mutation_id=row[14],
        created_at=row[15],
        updated_at=row[16],
        suggestions=[SchemaSuggestion.model_validate(item) for item in (row[17] or [])],
    )


def _params(session: AssistSession) -> dict[str, Any]:
    return {
        "id": session.id,
        "workspace_id": session.workspace_id,
        "project_id": session.project_id,
        "actor_user_id": session.actor_user_id,
        "kind": session.kind.value,
        "status": session.status.value,
        "description": session.description,
        "entity_type": session.entity_type,
        "name": session.name,
        "entity_id": session.entity_id,
        "questions": json.dumps([item.model_dump() for item in session.questions]),
        "answers": json.dumps([item.model_dump() for item in session.answers]),
        "understood": session.understood,
        "notes": json.dumps(session.notes),
        "mutation_id": session.mutation_id,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "suggestions": json.dumps([item.model_dump() for item in session.suggestions]),
    }


class PostgresAssistSessionStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def save(self, session: AssistSession) -> AssistSession:
        """Insert or replace; the id and its scope never change."""
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    f"""
                    INSERT INTO assist_sessions ({_COLUMNS}) VALUES (
                        :id, :workspace_id, :project_id, :actor_user_id, :kind, :status,
                        :description, :entity_type, :name, :entity_id,
                        CAST(:questions AS JSONB), CAST(:answers AS JSONB), :understood,
                        CAST(:notes AS JSONB), :mutation_id, :created_at, :updated_at,
                        CAST(:suggestions AS JSONB)
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        status = EXCLUDED.status,
                        description = EXCLUDED.description,
                        entity_type = EXCLUDED.entity_type,
                        name = EXCLUDED.name,
                        entity_id = EXCLUDED.entity_id,
                        questions = EXCLUDED.questions,
                        answers = EXCLUDED.answers,
                        understood = EXCLUDED.understood,
                        notes = EXCLUDED.notes,
                        mutation_id = EXCLUDED.mutation_id,
                        suggestions = EXCLUDED.suggestions,
                        updated_at = EXCLUDED.updated_at
                    WHERE assist_sessions.workspace_id = EXCLUDED.workspace_id
                      AND assist_sessions.project_id = EXCLUDED.project_id
                    """
                ),
                _params(session),
            )
        return session

    async def get(
        self, *, workspace_id: str, project_id: str, session_id: str
    ) -> AssistSession | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM assist_sessions WHERE id = :id "
                        "AND workspace_id = :ws AND project_id = :project"
                    ),
                    {"id": session_id, "ws": workspace_id, "project": project_id},
                )
            ).first()
        return _from_row(row) if row is not None else None

    async def list_open(
        self, *, workspace_id: str, project_id: str, actor_user_id: str, limit: int
    ) -> list[AssistSession]:
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM assist_sessions WHERE workspace_id = :ws "
                        "AND project_id = :project AND actor_user_id = :actor "
                        "AND status = 'open' ORDER BY updated_at DESC, id DESC LIMIT :limit"
                    ),
                    {
                        "ws": workspace_id,
                        "project": project_id,
                        "actor": actor_user_id,
                        "limit": limit,
                    },
                )
            ).all()
        return [_from_row(row) for row in rows]

    async def for_mutation(
        self, *, workspace_id: str, project_id: str, mutation_id: str
    ) -> AssistSession | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM assist_sessions WHERE workspace_id = :ws "
                        "AND project_id = :project AND mutation_id = :mutation "
                        "ORDER BY updated_at DESC LIMIT 1"
                    ),
                    {"ws": workspace_id, "project": project_id, "mutation": mutation_id},
                )
            ).first()
        return _from_row(row) if row is not None else None

    async def delete(self, *, workspace_id: str, project_id: str, session_id: str) -> int:
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    "DELETE FROM assist_sessions WHERE id = :id AND workspace_id = :ws "
                    "AND project_id = :project"
                ),
                {"id": session_id, "ws": workspace_id, "project": project_id},
            )
            return result.rowcount or 0  # type: ignore[attr-defined]

    async def delete_workspace(self, workspace_id: str) -> int:
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text("DELETE FROM assist_sessions WHERE workspace_id = :ws"),
                {"ws": workspace_id},
            )
            return result.rowcount or 0  # type: ignore[attr-defined]

    async def delete_project(self, workspace_id: str, project_id: str) -> int:
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    "DELETE FROM assist_sessions WHERE workspace_id = :ws AND project_id = :project"
                ),
                {"ws": workspace_id, "project": project_id},
            )
            return result.rowcount or 0  # type: ignore[attr-defined]
