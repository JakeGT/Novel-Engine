from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from backend.core.config import Settings
from backend.core.database import check_database_ready, create_engine, create_session_factory
from backend.ports import MigrationStatus


class PostgresDatabase:
    """Owns the async PostgreSQL engine/pool and reports readiness and migration state."""

    def __init__(self, settings: Settings) -> None:
        self._engine: AsyncEngine = create_engine(settings)
        self.session_factory: async_sessionmaker[AsyncSession] = create_session_factory(
            self._engine
        )

    async def ready(self) -> bool:
        return await check_database_ready(self._engine)

    async def migration_status(self) -> MigrationStatus:
        from backend.migrate import postgres_current_revision, postgres_head_revision

        head = postgres_head_revision()
        current = postgres_current_revision()
        if head is not None and current == head:
            return MigrationStatus(current=True, detail=current or "")
        return MigrationStatus(current=False, detail=f"current={current} head={head}")

    async def aclose(self) -> None:
        await self._engine.dispose()
