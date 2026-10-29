"""PostgreSQL store for drafts while they are written (SCN-002, migration 0059).

One row per draft of a job. The worker writing it appends each batch of words; the
studio's stream reads only what it has not seen yet (``substr`` from its offset), so a
long draft is never sent to the browser twice. The words are a passing copy: cleared
when the whole draft is stored or the work stops or fails, and the rows of ended work
deleted by a sweep (with any words a stopped server left behind). A row goes with its job.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope


@dataclass(frozen=True)
class StreamPart:
    """What one draft of a job has added since the offset asked about."""

    number: int
    # Characters written so far, and the new ones from the offset.
    chars: int
    text: str
    offset: int
    # True once the whole draft is stored (or the work ended): no more words will come.
    done: bool


class PostgresJobStreamStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def begin(
        self, *, workspace_id: str, project_id: str, job_id: str, number: int, attempt: int = 0
    ) -> None:
        """Start (or start again, for a job taken over) one draft's words. A worker that
        lost the job to a newer attempt never starts the newer one's words again."""
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "INSERT INTO job_streams (workspace_id, project_id, job_id, number, attempt) "
                    "VALUES (:w, :p, :j, :n, :a) ON CONFLICT (workspace_id, project_id, job_id, "
                    "number) DO UPDATE SET attempt = EXCLUDED.attempt, text = '', chars = 0, "
                    "done = false, updated_at = now() "
                    "WHERE job_streams.attempt <= EXCLUDED.attempt"
                ),
                {"w": workspace_id, "p": project_id, "j": job_id, "n": number, "a": attempt},
            )

    async def append(
        self,
        *,
        workspace_id: str,
        project_id: str,
        job_id: str,
        number: int,
        piece: str,
        attempt: int = 0,
    ) -> None:
        """Add a batch of words to the end of what is kept (never rewritten from Python)."""
        if not piece:
            return
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "UPDATE job_streams SET text = text || :t, chars = chars + :c, "
                    "updated_at = now() WHERE workspace_id = :w AND project_id = :p "
                    "AND job_id = :j AND number = :n AND attempt = :a AND NOT done"
                ),
                {
                    "t": piece,
                    "c": len(piece),
                    "w": workspace_id,
                    "p": project_id,
                    "j": job_id,
                    "n": number,
                    "a": attempt,
                },
            )

    async def end(
        self,
        *,
        workspace_id: str,
        project_id: str,
        job_id: str,
        number: int | None = None,
        attempt: int | None = None,
    ) -> None:
        """One draft is stored (``number``), or the work ended: clear the words. With
        ``attempt``, only words that attempt wrote (one draft: exactly it; the whole job:
        it or an older one), so a worker that lost the job never ends the newer one's."""
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "UPDATE job_streams SET text = '', done = true, updated_at = now() "
                    "WHERE workspace_id = :w AND project_id = :p AND job_id = :j "
                    "AND (CAST(:n AS INTEGER) IS NULL OR number = CAST(:n AS INTEGER)) "
                    "AND (CAST(:a AS INTEGER) IS NULL OR (CASE WHEN CAST(:n AS INTEGER) "
                    "IS NULL THEN attempt <= CAST(:a AS INTEGER) "
                    "ELSE attempt = CAST(:a AS INTEGER) END))"
                ),
                {"w": workspace_id, "p": project_id, "j": job_id, "n": number, "a": attempt},
            )

    async def read(
        self, *, workspace_id: str, project_id: str, job_id: str, offsets: dict[int, int]
    ) -> list[StreamPart]:
        """Each draft's words after the offset already sent (0 for a draft not seen). A
        draft with nothing new is not read at all: only its count is."""
        wanted = {str(number): max(0, int(offset)) for number, offset in offsets.items()}
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT number, chars, done, offset_at, "
                        "CASE WHEN chars > offset_at THEN substr(text, offset_at + 1) "
                        "ELSE '' END AS piece FROM ("
                        "SELECT number, chars, done, text, LEAST(chars, COALESCE("
                        "(CAST(:o AS JSONB) ->> number::text)::int, 0)) AS offset_at "
                        "FROM job_streams WHERE workspace_id = :w AND project_id = :p "
                        "AND job_id = :j) AS parts ORDER BY number"
                    ),
                    {"w": workspace_id, "p": project_id, "j": job_id, "o": json.dumps(wanted)},
                )
            ).all()
        return [
            StreamPart(
                number=int(row.number),
                chars=int(row.chars),
                text=str(row.piece or ""),
                offset=int(row.offset_at),
                done=bool(row.done),
            )
            for row in rows
        ]

    async def sweep(self) -> int:
        """Delete the rows of work that has ended (its drafts are stored, or it stopped,
        failed, or a server stopped mid-draft): nothing is left behind, words or rows."""
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    "DELETE FROM job_streams AS s USING jobs AS j "
                    "WHERE j.workspace_id = s.workspace_id "
                    "AND j.project_id = s.project_id AND j.id = s.job_id "
                    "AND j.status IN ('succeeded', 'failed', 'cancelled')"
                )
            )
        return int(getattr(result, "rowcount", 0) or 0)


__all__ = ["PostgresJobStreamStore", "StreamPart"]
