"""PostgreSQL store for advisory findings dismissed on a proposal in review (CON-016)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.dismissals import DismissReason, ReviewDismissal

_SELECT = (
    "SELECT workspace_id, project_id, id, mutation_id, fingerprint, depends_on, code, reason, "
    "note, created_by, created_at, withdrawn_at FROM review_dismissals"
)


def _row(row: Any) -> ReviewDismissal:
    return ReviewDismissal(
        workspace_id=row[0],
        project_id=row[1],
        id=row[2],
        mutation_id=row[3],
        fingerprint=row[4],
        depends_on=row[5],
        code=row[6],
        reason=DismissReason(row[7]),
        note=row[8],
        created_by=row[9],
        created_at=row[10],
        withdrawn_at=row[11],
    )


class PostgresReviewDismissalStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def for_mutation(
        self, *, workspace_id: str, project_id: str, mutation_id: str
    ) -> list[ReviewDismissal]:
        """Every dismissal on one proposal, withdrawn ones too, oldest first."""
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        _SELECT + " WHERE workspace_id = :w AND project_id = :p "
                        "AND mutation_id = :m ORDER BY created_at, id"
                    ),
                    {"w": workspace_id, "p": project_id, "m": mutation_id},
                )
            ).all()
        return [_row(row) for row in rows]

    async def add(self, dismissal: ReviewDismissal) -> None:
        """Keep a dismissal; one standing for the same finding is withdrawn first."""
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {
                    "key": "review_dismissal\x1f"
                    + "\x1f".join(
                        (
                            dismissal.workspace_id,
                            dismissal.project_id,
                            dismissal.mutation_id,
                            dismissal.fingerprint,
                        )
                    )
                },
            )
            await db.execute(
                text(
                    "UPDATE review_dismissals SET withdrawn_at = :at WHERE workspace_id = :w "
                    "AND project_id = :p AND mutation_id = :m AND fingerprint = :f "
                    "AND withdrawn_at IS NULL"
                ),
                {
                    "w": dismissal.workspace_id,
                    "p": dismissal.project_id,
                    "m": dismissal.mutation_id,
                    "f": dismissal.fingerprint,
                    "at": dismissal.created_at,
                },
            )
            await db.execute(
                text(
                    "INSERT INTO review_dismissals (workspace_id, project_id, id, mutation_id, "
                    "fingerprint, depends_on, code, reason, note, created_by, created_at) "
                    "VALUES (:w, :p, :id, :m, :f, :d, :code, :reason, :note, :by, :at)"
                ),
                {
                    "w": dismissal.workspace_id,
                    "p": dismissal.project_id,
                    "id": dismissal.id,
                    "m": dismissal.mutation_id,
                    "f": dismissal.fingerprint,
                    "d": dismissal.depends_on,
                    "code": dismissal.code,
                    "reason": dismissal.reason.value,
                    "note": dismissal.note,
                    "by": dismissal.created_by,
                    "at": dismissal.created_at,
                },
            )

    async def withdraw(
        self, *, workspace_id: str, project_id: str, mutation_id: str, dismissal_id: str, at: Any
    ) -> bool:
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    "UPDATE review_dismissals SET withdrawn_at = :at WHERE workspace_id = :w "
                    "AND project_id = :p AND mutation_id = :m AND id = :id "
                    "AND withdrawn_at IS NULL"
                ),
                {
                    "w": workspace_id,
                    "p": project_id,
                    "m": mutation_id,
                    "id": dismissal_id,
                    "at": at,
                },
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]


__all__ = ["PostgresReviewDismissalStore"]
