"""PostgreSQL store for the author's in-app notices (CON-011)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.notices import Notice

_SELECT = (
    "SELECT workspace_id, project_id, id, user_id, kind, job_id, status, branch_id, "
    "schedule_id, plan_id, scenes, notes, checks, prompt_tokens, completion_tokens, "
    "cost_usd, created_at, seen_at FROM author_notices"
)


def _row(row: Any) -> Notice:
    return Notice(
        workspace_id=row[0],
        project_id=row[1],
        id=row[2],
        user_id=row[3],
        kind=row[4],
        job_id=row[5],
        status=row[6],
        branch_id=row[7],
        schedule_id=row[8],
        plan_id=row[9],
        scenes=int(row[10]),
        notes=int(row[11]),
        checks=int(row[12]),
        prompt_tokens=int(row[13]),
        completion_tokens=int(row[14]),
        cost_usd=float(row[15]) if row[15] is not None else None,
        created_at=row[16],
        seen_at=row[17],
    )


class PostgresNoticeStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def add(self, notice: Notice) -> bool:
        """Keep a notice; one already kept for the same audit is left as it is."""
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    "INSERT INTO author_notices (workspace_id, project_id, id, user_id, kind, "
                    "job_id, status, branch_id, schedule_id, plan_id, scenes, notes, checks, "
                    "prompt_tokens, completion_tokens, cost_usd) VALUES (:w, :p, :id, :u, "
                    ":kind, :job, :status, :b, :s, :plan, :scenes, :notes, :checks, :pt, :ct, "
                    ":cost) ON CONFLICT DO NOTHING"
                ),
                {
                    "w": notice.workspace_id,
                    "p": notice.project_id,
                    "id": notice.id,
                    "u": notice.user_id,
                    "kind": notice.kind,
                    "job": notice.job_id,
                    "status": notice.status,
                    "b": notice.branch_id,
                    "s": notice.schedule_id,
                    "plan": notice.plan_id,
                    "scenes": notice.scenes,
                    "notes": notice.notes,
                    "checks": notice.checks,
                    "pt": notice.prompt_tokens,
                    "ct": notice.completion_tokens,
                    "cost": notice.cost_usd,
                },
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def for_user(
        self,
        *,
        workspace_id: str,
        project_id: str,
        user_id: str,
        limit: int,
        job_id: str | None = None,
        unseen_only: bool = False,
    ) -> list[Notice]:
        """This author's notices in the story, newest first (one audit's, with ``job_id``;
        only those not yet marked read, with ``unseen_only``, before the limit is applied)."""
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        _SELECT + " WHERE workspace_id = :w AND project_id = :p AND user_id = :u "
                        "AND (CAST(:job AS TEXT) IS NULL OR job_id = :job) "
                        "AND (NOT CAST(:unseen AS BOOLEAN) OR seen_at IS NULL) "
                        "ORDER BY created_at DESC, id DESC LIMIT :n"
                    ),
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "u": user_id,
                        "job": job_id,
                        "unseen": unseen_only,
                        "n": limit,
                    },
                )
            ).all()
        return [_row(row) for row in rows]

    async def mark_seen(
        self, *, workspace_id: str, project_id: str, user_id: str, notice_id: str, at: datetime
    ) -> bool:
        async with session_scope(self._session_factory) as db:
            found = await db.execute(
                text(
                    "UPDATE author_notices SET seen_at = COALESCE(seen_at, :at) "
                    "WHERE workspace_id = :w AND project_id = :p AND user_id = :u AND id = :id"
                ),
                {"w": workspace_id, "p": project_id, "u": user_id, "id": notice_id, "at": at},
            )
            return bool(found.rowcount)  # type: ignore[attr-defined]


__all__ = ["PostgresNoticeStore"]
