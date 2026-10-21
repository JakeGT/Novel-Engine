"""PostgreSQL store for dismissals wider than one scene (CON-016)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.dismissals import DismissalScope, DismissReason, FindingKind, ScopedDismissal

_SELECT = (
    "SELECT workspace_id, project_id, branch_id, id, kind, code, scope, entity_id, "
    "from_plan_id, to_plan_id, reason, note, expires_at, created_by, created_at, withdrawn_at "
    "FROM scoped_dismissals"
)


def _row(row: Any) -> ScopedDismissal:
    return ScopedDismissal(
        workspace_id=row[0],
        project_id=row[1],
        branch_id=row[2],
        id=row[3],
        kind=FindingKind(row[4]),
        code=row[5],
        scope=DismissalScope(row[6]),
        entity_id=row[7],
        from_plan_id=row[8],
        to_plan_id=row[9],
        reason=DismissReason(row[10]),
        note=row[11],
        expires_at=row[12],
        created_by=row[13],
        created_at=row[14],
        withdrawn_at=row[15],
    )


class PostgresScopedDismissalStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def list(
        self, *, workspace_id: str, project_id: str, branch_id: str
    ) -> list[ScopedDismissal]:
        """Every wider dismissal in one line of the story, withdrawn ones too, oldest first."""
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        _SELECT + " WHERE workspace_id = :w AND project_id = :p "
                        "AND branch_id = :b ORDER BY created_at, id"
                    ),
                    {"w": workspace_id, "p": project_id, "b": branch_id},
                )
            ).all()
        return [_row(row) for row in rows]

    async def count(self, *, workspace_id: str, project_id: str, branch_id: str) -> int:
        async with session_scope(self._session_factory) as db:
            found = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM scoped_dismissals WHERE workspace_id = :w "
                        "AND project_id = :p AND branch_id = :b"
                    ),
                    {"w": workspace_id, "p": project_id, "b": branch_id},
                )
            ).scalar_one()
        return int(found)

    async def add(self, item: ScopedDismissal) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "INSERT INTO scoped_dismissals (workspace_id, project_id, branch_id, id, "
                    "kind, code, scope, entity_id, from_plan_id, to_plan_id, reason, note, "
                    "expires_at, created_by, created_at) VALUES (:w, :p, :b, :id, :kind, :code, "
                    ":scope, :entity, :first, :last, :reason, :note, :expires, :by, :at)"
                ),
                {
                    "w": item.workspace_id,
                    "p": item.project_id,
                    "b": item.branch_id,
                    "id": item.id,
                    "kind": item.kind.value,
                    "code": item.code,
                    "scope": item.scope.value,
                    "entity": item.entity_id,
                    "first": item.from_plan_id,
                    "last": item.to_plan_id,
                    "reason": item.reason.value,
                    "note": item.note,
                    "expires": item.expires_at,
                    "by": item.created_by,
                    "at": item.created_at,
                },
            )

    async def withdraw(
        self, *, workspace_id: str, project_id: str, branch_id: str, dismissal_id: str, at: Any
    ) -> bool:
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    "UPDATE scoped_dismissals SET withdrawn_at = :at WHERE workspace_id = :w "
                    "AND project_id = :p AND branch_id = :b AND id = :id "
                    "AND withdrawn_at IS NULL"
                ),
                {"w": workspace_id, "p": project_id, "b": branch_id, "id": dismissal_id, "at": at},
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]


__all__ = ["PostgresScopedDismissalStore"]
