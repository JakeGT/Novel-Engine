"""PostgreSQL store for the views of the story kept ready to read (QRY-001).

Rows hold record ids, predicates and story times only, never names or the author's
words. A save only ever moves a view's watermark forward (or replaces a view built by
another builder version), so two builders racing leave the newer one. Verification
fields are kept while the builder version stays the same and cleared when it changes.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope


@dataclass(frozen=True)
class StoredView:
    workspace_id: str
    project_id: str
    branch_id: str
    view: str
    revision: int
    builder_version: int
    rows: list[dict[str, Any]]
    row_count: int
    checksum: str
    built_at: datetime | None = None
    verified_revision: int | None = None
    verified_builder: int | None = None
    verified_at: datetime | None = None
    verify_outcome: str | None = None
    differences: int | None = None

    def trusted(self, builder_version: int) -> bool:
        """Built and verified by this builder, and its last replay matched."""
        return (
            self.builder_version == builder_version
            and self.verified_builder == builder_version
            and self.verify_outcome == "matched"
        )


@dataclass(frozen=True)
class PendingLine:
    """A line of the story with commits past its views' watermark."""

    workspace_id: str
    project_id: str
    branch_id: str
    is_root: bool
    latest: int


def _stored(row: Any, *, with_rows: bool = True) -> StoredView:
    rows = row.rows if with_rows else []
    if isinstance(rows, str):
        rows = json.loads(rows)
    return StoredView(
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        branch_id=row.branch_id,
        view=row.view,
        revision=int(row.revision),
        builder_version=int(row.builder_version),
        rows=list(rows or []),
        row_count=int(row.row_count),
        checksum=row.checksum,
        built_at=row.built_at,
        verified_revision=row.verified_revision,
        verified_builder=row.verified_builder,
        verified_at=row.verified_at,
        verify_outcome=row.verify_outcome,
        differences=row.differences,
    )


_META = (
    "workspace_id, project_id, branch_id, view, revision, builder_version, row_count, "
    "checksum, built_at, verified_revision, verified_builder, verified_at, verify_outcome, "
    "differences"
)


class PostgresReadModelStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def pending(
        self, *, builder_version: int, views: int, limit: int = 50
    ) -> list[PendingLine]:
        """Lines whose latest commit (from the outbox) is past their views' watermark,
        or whose views are missing or were built by another builder version."""
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT b.workspace_id, b.project_id, b.id AS branch_id, b.is_root,
                               latest.revision AS latest
                        FROM branches b
                        CROSS JOIN LATERAL (
                            SELECT max(o.revision) AS revision FROM outbox_messages o
                            WHERE o.topic = 'canon.commit' AND o.workspace_id = b.workspace_id
                              AND o.project_id = b.project_id AND o.branch_id = b.id
                        ) latest
                        LEFT JOIN LATERAL (
                            SELECT count(*) AS built, min(r.revision) AS revision
                            FROM read_models r
                            WHERE r.workspace_id = b.workspace_id
                              AND r.project_id = b.project_id AND r.branch_id = b.id
                              AND r.builder_version = :bv
                        ) mark ON true
                        WHERE latest.revision IS NOT NULL
                          AND (mark.built < :views OR latest.revision > mark.revision)
                        ORDER BY b.created_at
                        LIMIT :limit
                        """
                    ),
                    {"bv": builder_version, "views": views, "limit": limit},
                )
            ).all()
        return [
            PendingLine(
                workspace_id=row.workspace_id,
                project_id=row.project_id,
                branch_id=row.branch_id,
                is_root=bool(row.is_root),
                latest=int(row.latest),
            )
            for row in rows
        ]

    async def get(
        self, *, workspace_id: str, project_id: str, branch_id: str, view: str
    ) -> StoredView | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        f"SELECT {_META}, rows FROM read_models WHERE workspace_id = :w "
                        "AND project_id = :p AND branch_id = :b AND view = :v"
                    ),
                    {"w": workspace_id, "p": project_id, "b": branch_id, "v": view},
                )
            ).first()
        return _stored(row) if row is not None else None

    async def list_views(
        self, *, workspace_id: str, project_id: str, branch_id: str | None = None
    ) -> list[StoredView]:
        """Every stored view of a story (or of one line), without its rows."""
        clause = " AND branch_id = :b" if branch_id is not None else ""
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT {_META} FROM read_models WHERE workspace_id = :w "
                        f"AND project_id = :p{clause} ORDER BY branch_id, view"
                    ),
                    {"w": workspace_id, "p": project_id, "b": branch_id},
                )
            ).all()
        return [_stored(row, with_rows=False) for row in rows]

    async def lines(self) -> list[tuple[str, str, str]]:
        """Every (workspace, project, line) that has stored views."""
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT DISTINCT workspace_id, project_id, branch_id FROM read_models "
                        "ORDER BY workspace_id, project_id, branch_id"
                    )
                )
            ).all()
        return [(row.workspace_id, row.project_id, row.branch_id) for row in rows]

    async def save(
        self,
        *,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        revision: int,
        builder_version: int,
        views: Mapping[str, Sequence[Mapping[str, Any]]],
        checksums: Mapping[str, str],
    ) -> int:
        """Store each view at ``revision``; returns how many were written (an older
        build than the one stored is ignored)."""
        written = 0
        async with session_scope(self._session_factory) as session:
            for view, rows in views.items():
                result = await session.execute(
                    text(
                        """
                        INSERT INTO read_models (
                            workspace_id, project_id, branch_id, view, revision,
                            builder_version, rows, row_count, checksum
                        )
                        VALUES (:w, :p, :b, :v, :rev, :bv, CAST(:rows AS JSONB), :n, :sum)
                        ON CONFLICT (workspace_id, project_id, branch_id, view) DO UPDATE SET
                            revision = EXCLUDED.revision,
                            rows = EXCLUDED.rows,
                            row_count = EXCLUDED.row_count,
                            checksum = EXCLUDED.checksum,
                            built_at = now(),
                            verified_revision = CASE
                                WHEN read_models.builder_version = EXCLUDED.builder_version
                                THEN read_models.verified_revision END,
                            verified_builder = CASE
                                WHEN read_models.builder_version = EXCLUDED.builder_version
                                THEN read_models.verified_builder END,
                            verified_at = CASE
                                WHEN read_models.builder_version = EXCLUDED.builder_version
                                THEN read_models.verified_at END,
                            verify_outcome = CASE
                                WHEN read_models.builder_version = EXCLUDED.builder_version
                                THEN read_models.verify_outcome END,
                            differences = CASE
                                WHEN read_models.builder_version = EXCLUDED.builder_version
                                THEN read_models.differences END,
                            builder_version = EXCLUDED.builder_version
                        WHERE read_models.revision < EXCLUDED.revision
                           OR read_models.builder_version <> EXCLUDED.builder_version
                        """
                    ),
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "b": branch_id,
                        "v": view,
                        "rev": revision,
                        "bv": builder_version,
                        "rows": json.dumps(list(rows), ensure_ascii=False),
                        "n": len(rows),
                        "sum": checksums[view],
                    },
                )
                written += int(result.rowcount or 0)  # type: ignore[attr-defined]
        return written

    async def record_verification(
        self,
        *,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        view: str,
        revision: int,
        builder_version: int,
        matched: bool,
        differences: int,
    ) -> None:
        """Note a replay's outcome, only if the view is still the one that was checked."""
        async with session_scope(self._session_factory) as session:
            await session.execute(
                text(
                    "UPDATE read_models SET verified_revision = :rev, verified_builder = :bv, "
                    "verified_at = now(), verify_outcome = :outcome, differences = :d "
                    "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b "
                    "AND view = :v AND revision = :rev AND builder_version = :bv"
                ),
                {
                    "w": workspace_id,
                    "p": project_id,
                    "b": branch_id,
                    "v": view,
                    "rev": revision,
                    "bv": builder_version,
                    "outcome": "matched" if matched else "differed",
                    "d": differences,
                },
            )

    async def forget(
        self, *, workspace_id: str, project_id: str, branch_id: str | None = None
    ) -> int:
        """Remove a story's views (or one line's), so they are built again from scratch."""
        clause = " AND branch_id = :b" if branch_id is not None else ""
        async with session_scope(self._session_factory) as session:
            result = await session.execute(
                text(
                    f"DELETE FROM read_models WHERE workspace_id = :w AND project_id = :p{clause}"
                ),
                {"w": workspace_id, "p": project_id, "b": branch_id},
            )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]


__all__ = ["PendingLine", "PostgresReadModelStore", "StoredView"]
