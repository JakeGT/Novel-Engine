"""In-memory store for the author's in-app notices (CON-011), standing in for
``backend.adapters.notice_store``."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from backend.domain.identity import utcnow
from backend.domain.notices import Notice


class InMemoryNoticeStore:
    def __init__(self) -> None:
        self.notices: list[Notice] = []

    async def add(self, notice: Notice) -> bool:
        if any(
            (item.workspace_id, item.project_id) == (notice.workspace_id, notice.project_id)
            and (item.id == notice.id or item.job_id == notice.job_id)
            for item in self.notices
        ):
            return False
        self.notices.append(replace(notice, created_at=notice.created_at or utcnow()))
        return True

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
        found = [
            item
            for item in self.notices
            if (item.workspace_id, item.project_id, item.user_id)
            == (workspace_id, project_id, user_id)
            and (job_id is None or item.job_id == job_id)
            and not (unseen_only and item.seen_at is not None)
        ]
        return list(reversed(found))[:limit]

    async def mark_seen(
        self, *, workspace_id: str, project_id: str, user_id: str, notice_id: str, at: datetime
    ) -> bool:
        for index, item in enumerate(self.notices):
            if (item.workspace_id, item.project_id, item.user_id, item.id) == (
                workspace_id,
                project_id,
                user_id,
                notice_id,
            ):
                self.notices[index] = replace(item, seen_at=item.seen_at or at)
                return True
        return False
