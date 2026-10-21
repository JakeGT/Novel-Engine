"""PostgreSQL store for the assistant's kept threads and for suggestion runs (0054).

Threads and turns hold the author's words: nothing here logs them. Runs hold counts.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.conversations import (
    KEPT_FOR_YOU,
    MAX_TURNS,
    RunKind,
    RunSource,
    SuggestionRun,
    Thread,
    ThreadKind,
    Turn,
    TurnAnswer,
    TurnRole,
)

_THREAD = (
    "id, workspace_id, project_id, actor_user_id, kind, branch_id, created_at, updated_at, "
    "tokens_used"
)
_RUN = (
    "id, workspace_id, project_id, kind, actor_user_id, branch_id, mutation_id, source, "
    "prompt_version, suggested, grounded, invented, kept, settled_at, created_at, details"
)


def _thread(row: Any, turns: list[Turn]) -> Thread:
    return Thread(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        actor_user_id=row.actor_user_id,
        kind=ThreadKind(row.kind),
        branch_id=row.branch_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        turns=turns,
        tokens_used=int(row.tokens_used or 0),
    )


def _turn(row: Any) -> Turn:
    details = row.details if isinstance(row.details, dict) else json.loads(row.details or "{}")
    return Turn(
        position=row.position,
        role=TurnRole(row.role),
        body=row.body,
        questions=[str(item) for item in details.get("questions", [])],
        answers=[TurnAnswer.model_validate(item) for item in details.get("answers", [])],
        notes=[str(item) for item in details.get("notes", [])],
        mutation_id=details.get("mutation_id"),
        operation_count=int(details.get("operation_count", 0)),
        session_id=details.get("session_id"),
        created_at=row.created_at,
    )


def _details(turn: Turn) -> str:
    return json.dumps(
        {
            "questions": turn.questions,
            "answers": [item.model_dump() for item in turn.answers],
            "notes": turn.notes,
            "mutation_id": turn.mutation_id,
            "operation_count": turn.operation_count,
            "session_id": turn.session_id,
        }
    )


def _run(row: Any) -> SuggestionRun:
    details = row.details if isinstance(row.details, dict) else json.loads(row.details or "{}")
    return SuggestionRun(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        kind=RunKind(row.kind),
        actor_user_id=row.actor_user_id,
        branch_id=row.branch_id,
        mutation_id=row.mutation_id,
        source=RunSource(row.source),
        prompt_version=row.prompt_version,
        suggested=row.suggested,
        grounded=row.grounded,
        invented=row.invented,
        kept=row.kept,
        settled_at=row.settled_at,
        created_at=row.created_at,
        details={str(key): int(value) for key, value in details.items()},
    )


class PostgresConversationStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def _turns(self, db: Any, workspace_id: str, project_id: str, thread_id: str) -> list:
        rows = (
            await db.execute(
                text(
                    "SELECT position, role, body, details, created_at FROM assistant_turns "
                    "WHERE workspace_id = :w AND project_id = :p AND thread_id = :t "
                    "ORDER BY position"
                ),
                {"w": workspace_id, "p": project_id, "t": thread_id},
            )
        ).all()
        return [_turn(row) for row in rows]

    async def find_thread(
        self,
        *,
        workspace_id: str,
        project_id: str,
        actor_user_id: str,
        kind: ThreadKind,
        branch_id: str | None,
    ) -> Thread | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        f"SELECT {_THREAD} FROM assistant_threads WHERE workspace_id = :w "
                        "AND project_id = :p AND actor_user_id = :a AND kind = :k "
                        "AND COALESCE(branch_id, '') = COALESCE(:b, '')"
                    ),
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "a": actor_user_id,
                        "k": kind.value,
                        "b": branch_id,
                    },
                )
            ).first()
            if row is None:
                return None
            return _thread(row, await self._turns(db, workspace_id, project_id, row.id))

    async def start_thread(self, thread: Thread) -> Thread:
        """Insert a thread; when one is already running for its author, kind and line,
        that one is returned instead."""
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    f"INSERT INTO assistant_threads ({_THREAD}) VALUES (:id, :w, :p, :a, :k, :b, "
                    ":created, :updated, 0) ON CONFLICT DO NOTHING"
                ),
                {
                    "id": thread.id,
                    "w": thread.workspace_id,
                    "p": thread.project_id,
                    "a": thread.actor_user_id,
                    "k": thread.kind.value,
                    "b": thread.branch_id,
                    "created": thread.created_at,
                    "updated": thread.updated_at,
                },
            )
        found = await self.find_thread(
            workspace_id=thread.workspace_id,
            project_id=thread.project_id,
            actor_user_id=thread.actor_user_id,
            kind=thread.kind,
            branch_id=thread.branch_id,
        )
        assert found is not None
        return found

    async def add_turns(
        self,
        *,
        workspace_id: str,
        project_id: str,
        thread_id: str,
        turns: list[Turn],
        now: datetime,
    ) -> None:
        """Append turns in order and keep only the newest ``MAX_TURNS``, and every turn
        the thread must keep (``pinned``: what a premise is made of)."""
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT id, kind FROM assistant_threads WHERE workspace_id = :w AND "
                        "project_id = :p AND id = :t FOR UPDATE"
                    ),
                    {"w": workspace_id, "p": project_id, "t": thread_id},
                )
            ).first()
            if row is None:
                return
            last = (
                await db.execute(
                    text(
                        "SELECT COALESCE(MAX(position), 0) FROM assistant_turns WHERE "
                        "workspace_id = :w AND project_id = :p AND thread_id = :t"
                    ),
                    {"w": workspace_id, "p": project_id, "t": thread_id},
                )
            ).scalar_one()
            for offset, turn in enumerate(turns, start=1):
                await db.execute(
                    text(
                        "INSERT INTO assistant_turns (workspace_id, project_id, thread_id, "
                        "position, role, body, details, created_at) VALUES (:w, :p, :t, :pos, "
                        ":role, :body, CAST(:details AS JSONB), :created)"
                    ),
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "t": thread_id,
                        "pos": int(last) + offset,
                        "role": turn.role.value,
                        "body": turn.body,
                        "details": _details(turn),
                        "created": now,
                    },
                )
            await db.execute(
                text(
                    "DELETE FROM assistant_turns WHERE workspace_id = :w AND project_id = :p "
                    "AND thread_id = :t AND position <= :cut "
                    # The same rule as ``pinned``.
                    "AND NOT (:premise AND role = 'author' AND body <> '')"
                ),
                {
                    "w": workspace_id,
                    "p": project_id,
                    "t": thread_id,
                    "cut": int(last) + len(turns) - MAX_TURNS,
                    "premise": row.kind == ThreadKind.PREMISE.value,
                },
            )
            await db.execute(
                text(
                    "UPDATE assistant_threads SET updated_at = :now WHERE workspace_id = :w "
                    "AND project_id = :p AND id = :t"
                ),
                {"w": workspace_id, "p": project_id, "t": thread_id, "now": now},
            )

    async def add_tokens(
        self, *, workspace_id: str, project_id: str, thread_id: str, tokens: int
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "UPDATE assistant_threads SET tokens_used = tokens_used + :tokens "
                    "WHERE workspace_id = :w AND project_id = :p AND id = :t"
                ),
                {"tokens": max(0, tokens), "w": workspace_id, "p": project_id, "t": thread_id},
            )

    async def delete_thread(self, *, workspace_id: str, project_id: str, thread_id: str) -> int:
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    "DELETE FROM assistant_threads WHERE workspace_id = :w AND project_id = :p "
                    "AND id = :t"
                ),
                {"w": workspace_id, "p": project_id, "t": thread_id},
            )
            return result.rowcount or 0  # type: ignore[attr-defined]

    async def delete_member_threads(self, *, workspace_id: str, actor_user_id: str) -> int:
        """Erase every thread a person kept in a workspace (they were removed from it)."""
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    "DELETE FROM assistant_threads WHERE workspace_id = :w AND actor_user_id = :a"
                ),
                {"w": workspace_id, "a": actor_user_id},
            )
            return result.rowcount or 0  # type: ignore[attr-defined]

    async def list_threads(self, *, workspace_id: str, project_id: str) -> list[Thread]:
        """Every kept thread in a story, oldest first (for the project archive)."""
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        f"SELECT {_THREAD} FROM assistant_threads WHERE workspace_id = :w "
                        "AND project_id = :p ORDER BY created_at, id"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).all()
            return [
                _thread(row, await self._turns(db, workspace_id, project_id, row.id))
                for row in rows
            ]

    async def add_run(self, run: SuggestionRun) -> SuggestionRun:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    f"INSERT INTO suggestion_runs ({_RUN}) VALUES (:id, :w, :p, :kind, :actor, "
                    ":branch, :mutation, :source, :prompt, :suggested, :grounded, :invented, "
                    ":kept, :settled, :created, CAST(:details AS JSONB)) ON CONFLICT DO NOTHING"
                ),
                {
                    "id": run.id,
                    "w": run.workspace_id,
                    "p": run.project_id,
                    "kind": run.kind.value,
                    "actor": run.actor_user_id,
                    "branch": run.branch_id,
                    "mutation": run.mutation_id,
                    "source": run.source.value,
                    "prompt": run.prompt_version,
                    "suggested": run.suggested,
                    "grounded": run.grounded,
                    "invented": run.invented,
                    "kept": run.kept,
                    "settled": run.settled_at,
                    "created": run.created_at,
                    "details": json.dumps(run.details),
                },
            )
        return run

    async def list_runs(
        self,
        *,
        workspace_id: str,
        project_id: str,
        kind: RunKind | None,
        limit: int,
        branch_id: str | None = None,
    ) -> list[SuggestionRun]:
        """Runs in one line of the story (``branch_id`` None: the story itself)."""
        clauses = ["workspace_id = :w", "project_id = :p", "COALESCE(branch_id, '') = :b"]
        params: dict[str, Any] = {
            "w": workspace_id,
            "p": project_id,
            "b": branch_id or "",
            "limit": limit,
        }
        if kind is not None:
            clauses.append("kind = :kind")
            params["kind"] = kind.value
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        f"SELECT {_RUN} FROM suggestion_runs WHERE {' AND '.join(clauses)} "
                        "ORDER BY created_at DESC, id DESC LIMIT :limit"
                    ),
                    params,
                )
            ).all()
        return [_run(row) for row in rows]

    async def settle_run(
        self,
        *,
        workspace_id: str,
        project_id: str,
        run_id: str,
        kept: int,
        settled_at: datetime,
        kept_for_you: int | None = None,
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "UPDATE suggestion_runs SET kept = :kept, settled_at = :settled, "
                    "details = details || CAST(:extra AS JSONB) "
                    "WHERE workspace_id = :w AND project_id = :p AND id = :id "
                    "AND settled_at IS NULL"
                ),
                {
                    "kept": kept,
                    "settled": settled_at,
                    "extra": json.dumps(
                        {} if kept_for_you is None else {KEPT_FOR_YOU: kept_for_you}
                    ),
                    "w": workspace_id,
                    "p": project_id,
                    "id": run_id,
                },
            )


__all__ = ["PostgresConversationStore"]
