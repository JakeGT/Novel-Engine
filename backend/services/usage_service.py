"""Provider usage recording and limit enforcement.

Limits are checked before a provider call (estimates) and usage is recorded after
each attempt. There is no platform balance; costs are provider-reported.
"""

from __future__ import annotations

from datetime import timedelta

from backend.domain.identity import utcnow
from backend.domain.usage import UsageLimit, UsageLimitExceeded, UsageRecord, UsageTotals
from backend.ports import ProviderUsagePort


def _enabled(value: int | float | None) -> bool:
    return value is not None and value > 0


class ProviderUsageService:
    def __init__(self, store: ProviderUsagePort, *, limit: UsageLimit) -> None:
        self._store = store
        self._limit = limit

    async def ensure_within_limits(
        self,
        *,
        workspace_id: str,
        actor_user_id: str,
        estimated_tokens: int = 0,
        estimated_cost_usd: float = 0.0,
        idempotency_key: str | None = None,
    ) -> None:
        limit = self._limit
        job = (
            await self._store.totals_for_job(idempotency_key=idempotency_key)
            if idempotency_key
            else UsageTotals()
        )
        if _enabled(limit.per_job_token_limit) and (
            job.total_tokens + estimated_tokens > limit.per_job_token_limit
        ):
            raise UsageLimitExceeded("Per-job token limit exceeded.")
        if _enabled(limit.per_job_cost_limit) and (
            job.provider_cost_usd + estimated_cost_usd > limit.per_job_cost_limit
        ):
            raise UsageLimitExceeded("Per-job cost limit exceeded.")

        since = utcnow() - timedelta(seconds=limit.period_seconds)
        period = await self._store.totals(
            workspace_id=workspace_id, actor_user_id=actor_user_id, since=since
        )
        if _enabled(limit.period_token_limit) and (
            period.total_tokens + estimated_tokens > limit.period_token_limit
        ):
            raise UsageLimitExceeded("Rolling-period token limit exceeded.")
        if _enabled(limit.period_cost_limit) and (
            period.provider_cost_usd + estimated_cost_usd > limit.period_cost_limit
        ):
            raise UsageLimitExceeded("Rolling-period cost limit exceeded.")

    async def would_duplicate(self, idempotency_key: str) -> bool:
        return await self._store.has_idempotency_key(idempotency_key=idempotency_key)

    async def record(self, record: UsageRecord) -> UsageRecord:
        return await self._store.record(record)
