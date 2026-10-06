from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope


class PostgresRateLimiter:
    """Durable sliding-window limiter shared across application workers.

    Each allowed attempt inserts a timestamped row; the window is pruned on each
    check. This replaces the single-process in-memory limiter so limits hold when
    the hosted deployment runs more than one worker.
    """

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        cutoff = datetime.now(UTC) - timedelta(seconds=window_seconds)
        async with session_scope(self._session_factory) as session:
            await session.execute(
                text("DELETE FROM rate_limit_hits WHERE key = :key AND occurred_at < :cutoff"),
                {"key": key, "cutoff": cutoff},
            )
            count = (
                await session.execute(
                    text("SELECT count(*) FROM rate_limit_hits WHERE key = :key"), {"key": key}
                )
            ).scalar() or 0
            if count >= limit:
                return False
            await session.execute(
                text("INSERT INTO rate_limit_hits (key, occurred_at) VALUES (:key, :now)"),
                {"key": key, "now": datetime.now(UTC)},
            )
        return True
