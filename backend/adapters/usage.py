"""PostgreSQL provider-usage store."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.usage import UsageOutcome, UsageRecord, UsageTotals


class PostgresProviderUsageStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def record(self, record: UsageRecord) -> UsageRecord:
        async with session_scope(self._session_factory) as session:
            await session.execute(
                text(
                    """
                    INSERT INTO provider_usage (
                        id, workspace_id, project_id, actor_user_id, provider, model,
                        requested_route, actual_route, prompt_tokens, completion_tokens,
                        latency_ms, attempt, outcome, provider_cost_usd, idempotency_key, created_at
                    ) VALUES (
                        :id, :workspace_id, :project_id, :actor_user_id, :provider, :model,
                        :requested_route, :actual_route, :prompt_tokens, :completion_tokens,
                        :latency_ms, :attempt, :outcome, :provider_cost_usd, :idempotency_key,
                        :created_at
                    )
                    """
                ),
                {
                    "id": record.id,
                    "workspace_id": record.workspace_id,
                    "project_id": record.project_id,
                    "actor_user_id": record.actor_user_id,
                    "provider": record.provider,
                    "model": record.model,
                    "requested_route": record.requested_route,
                    "actual_route": record.actual_route,
                    "prompt_tokens": record.prompt_tokens,
                    "completion_tokens": record.completion_tokens,
                    "latency_ms": record.latency_ms,
                    "attempt": record.attempt,
                    "outcome": record.outcome.value,
                    "provider_cost_usd": record.provider_cost_usd,
                    "idempotency_key": record.idempotency_key,
                    "created_at": record.created_at,
                },
            )
        return record

    async def totals(
        self, *, workspace_id: str, actor_user_id: str, since: datetime
    ) -> UsageTotals:
        return await self._totals(
            where="workspace_id = :workspace_id AND actor_user_id = :actor_user_id "
            "AND created_at >= :since",
            params={"workspace_id": workspace_id, "actor_user_id": actor_user_id, "since": since},
        )

    async def totals_for_job(self, *, idempotency_key: str) -> UsageTotals:
        return await self._totals(
            where="idempotency_key = :idempotency_key",
            params={"idempotency_key": idempotency_key},
        )

    async def has_idempotency_key(self, *, idempotency_key: str) -> bool:
        async with session_scope(self._session_factory) as session:
            found = (
                await session.execute(
                    text("SELECT 1 FROM provider_usage WHERE idempotency_key = :key LIMIT 1"),
                    {"key": idempotency_key},
                )
            ).first()
            return found is not None

    async def _totals(self, *, where: str, params: dict[str, Any]) -> UsageTotals:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT count(*), coalesce(sum(prompt_tokens), 0), "
                        "coalesce(sum(completion_tokens), 0), coalesce(sum(provider_cost_usd), 0) "
                        f"FROM provider_usage WHERE {where}"
                    ),
                    params,
                )
            ).one()
        return UsageTotals(
            request_count=int(row[0]),
            prompt_tokens=int(row[1]),
            completion_tokens=int(row[2]),
            provider_cost_usd=float(row[3]),
        )


def usage_record_from_row(row: Any) -> UsageRecord:
    return UsageRecord(
        id=row[0],
        workspace_id=row[1],
        project_id=row[2],
        actor_user_id=row[3],
        provider=row[4],
        model=row[5],
        requested_route=row[6],
        actual_route=row[7],
        prompt_tokens=row[8],
        completion_tokens=row[9],
        latency_ms=row[10],
        attempt=row[11],
        outcome=UsageOutcome(row[12]),
        provider_cost_usd=row[13],
        idempotency_key=row[14],
        created_at=row[15],
    )
