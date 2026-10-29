"""PostgreSQL store for what providers' lists say each model can take (CTX-013, 0059).

Install-wide and public metadata: no workspace, no key, no story words. One provider's
list is replaced whole, in one transaction, so a reader never sees half of two lists.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.model_capabilities import ModelCapability


class PostgresModelCapabilityStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def load(self, provider: str) -> tuple[list[ModelCapability], float]:
        """The provider's kept list and when it was read (seconds since the epoch)."""
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT provider, model, sees_images, context_tokens, max_output_tokens, "
                        "structured, source, EXTRACT(EPOCH FROM fetched_at) AS at "
                        "FROM model_capabilities WHERE provider = :p"
                    ),
                    {"p": provider},
                )
            ).all()
        found = [
            ModelCapability(
                provider=row.provider,
                model=row.model,
                sees_images=row.sees_images,
                context_tokens=row.context_tokens,
                max_output_tokens=row.max_output_tokens,
                structured=row.structured,
                source=row.source,
            )
            for row in rows
        ]
        return found, min((float(row.at) for row in rows), default=0.0)

    async def save(self, provider: str, found: list[ModelCapability]) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text("DELETE FROM model_capabilities WHERE provider = :p"), {"p": provider}
            )
            if found:
                await db.execute(
                    text(
                        "INSERT INTO model_capabilities (provider, model, sees_images, "
                        "context_tokens, max_output_tokens, structured, source) VALUES "
                        "(:provider, :model, :sees, :context, :output, :structured, :source) "
                        "ON CONFLICT (provider, model) DO NOTHING"
                    ),
                    [
                        {
                            "provider": provider,
                            "model": item.model,
                            "sees": item.sees_images,
                            "context": item.context_tokens,
                            "output": item.max_output_tokens,
                            "structured": item.structured,
                            "source": item.source,
                        }
                        for item in found
                    ],
                )


__all__ = ["PostgresModelCapabilityStore"]
