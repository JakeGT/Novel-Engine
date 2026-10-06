from __future__ import annotations

import logging
import time

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from backend.core.config import Settings
from backend.core.database import check_database_ready, create_engine, create_session_factory
from backend.core.logging import log_event
from backend.ports import MigrationStatus

logger = logging.getLogger(__name__)


class PostgresDatabase:
    """Owns the async PostgreSQL engine/pool and reports readiness and migration state."""

    def __init__(self, settings: Settings) -> None:
        self._engine: AsyncEngine = create_engine(settings)
        self.session_factory: async_sessionmaker[AsyncSession] = create_session_factory(
            self._engine
        )

    async def ready(self) -> bool:
        start = time.perf_counter()
        try:
            result = await check_database_ready(self._engine)
        except Exception as error:
            log_event(
                logger,
                "postgres.ready.error",
                level=logging.WARNING,
                duration_ms=round((time.perf_counter() - start) * 1000, 3),
                error_type=type(error).__name__,
            )
            return False
        log_event(
            logger,
            "postgres.ready",
            level=logging.DEBUG,
            duration_ms=round((time.perf_counter() - start) * 1000, 3),
            ready=result,
        )
        return result

    async def migration_status(self) -> MigrationStatus:
        from backend.migrate import postgres_current_revision, postgres_head_revision

        head = postgres_head_revision()
        current = postgres_current_revision()
        if head is not None and current == head:
            return MigrationStatus(current=True, detail=current or "")
        return MigrationStatus(current=False, detail=f"current={current} head={head}")

    async def aclose(self) -> None:
        await self._engine.dispose()
