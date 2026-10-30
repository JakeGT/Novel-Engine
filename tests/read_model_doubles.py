"""An in-memory store for the story's ready views (QRY-001), with the same rules as
``backend/adapters/read_model_store.py``: watermarks only move forward, verification is
kept while the builder version stays the same, and pending lines come from the outbox."""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from backend.adapters.read_model_store import PendingLine, StoredView


class InMemoryReadModelStore:
    def __init__(self, journal: Any) -> None:
        self._journal = journal
        self.views: dict[tuple[str, str, str, str], StoredView] = {}
        self.saves = 0
        # Lines whose check lock another process is holding, as the advisory lock would say.
        self.held_elsewhere: set[tuple[str, str, str]] = set()

    async def pending(
        self, *, builder_version: int, views: int, limit: int = 50
    ) -> list[PendingLine]:
        latest: dict[tuple[str, str, str], int] = {}
        for message in self._journal.outbox.values():
            if message.topic != "canon.commit":
                continue
            key = (message.workspace_id, message.project_id, message.branch_id)
            latest[key] = max(latest.get(key, 0), int(message.revision))
        found: list[PendingLine] = []
        for (w, p, b), revision in latest.items():
            mine = [
                view
                for (vw, vp, vb, _), view in self.views.items()
                if (vw, vp, vb) == (w, p, b) and view.builder_version == builder_version
            ]
            if len(mine) >= views and revision <= min(view.revision for view in mine):
                continue
            branch = self._journal.branches.get(b, {})
            found.append(PendingLine(w, p, b, bool(branch.get("is_root", True)), revision))
        return found[:limit]

    async def get(
        self, *, workspace_id: str, project_id: str, branch_id: str, view: str
    ) -> StoredView | None:
        found = self.views.get((workspace_id, project_id, branch_id, view))
        # As read back from JSONB: a copy whose numbers went through JSON.
        if found is None:
            return None
        return replace(found, rows=json.loads(json.dumps(found.rows)))

    async def list_views(
        self, *, workspace_id: str, project_id: str, branch_id: str | None = None
    ) -> list[StoredView]:
        return [
            replace(view, rows=[])
            for (w, p, b, _), view in sorted(self.views.items())
            if (w, p) == (workspace_id, project_id) and (branch_id is None or b == branch_id)
        ]

    async def lines(self) -> list[tuple[str, str, str]]:
        return sorted({key[:3] for key in self.views})

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
        written = 0
        for name, rows in views.items():
            key = (workspace_id, project_id, branch_id, name)
            old = self.views.get(key)
            if old is not None and not (
                old.revision < revision or old.builder_version != builder_version
            ):
                continue
            keep = old is not None and old.builder_version == builder_version
            self.views[key] = StoredView(
                workspace_id=workspace_id,
                project_id=project_id,
                branch_id=branch_id,
                view=name,
                revision=revision,
                builder_version=builder_version,
                rows=copy.deepcopy(list(rows)),  # type: ignore[arg-type]
                row_count=len(rows),
                checksum=checksums[name],
                built_at=datetime.now(UTC),
                verified_revision=old.verified_revision if keep and old else None,
                verified_builder=old.verified_builder if keep and old else None,
                verified_at=old.verified_at if keep and old else None,
                verify_outcome=old.verify_outcome if keep and old else None,
                differences=old.differences if keep and old else None,
            )
            written += 1
        self.saves += written
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
        key = (workspace_id, project_id, branch_id, view)
        old = self.views.get(key)
        if old is None or old.revision != revision or old.builder_version != builder_version:
            return
        self.views[key] = replace(
            old,
            verified_revision=revision,
            verified_builder=builder_version,
            verified_at=datetime.now(UTC),
            verify_outcome="matched" if matched else "differed",
            differences=differences,
        )

    async def forget(
        self, *, workspace_id: str, project_id: str, branch_id: str | None = None
    ) -> int:
        gone = [
            key
            for key in self.views
            if key[:2] == (workspace_id, project_id) and (branch_id is None or key[2] == branch_id)
        ]
        for key in gone:
            del self.views[key]
        return len(gone)

    @asynccontextmanager
    async def check_lock(
        self, *, workspace_id: str, project_id: str, branch_id: str
    ) -> AsyncIterator[bool]:
        yield (workspace_id, project_id, branch_id) not in self.held_elsewhere
