"""Provider usage recording and limit enforcement.

Limits are checked before a provider call (estimates) and usage is recorded after
each attempt. There is no platform balance; costs are provider-reported.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from backend.domain.errors import ValidationError
from backend.domain.identity import utcnow
from backend.domain.usage import (
    Allowance,
    UsageLimit,
    UsageLimitExceeded,
    UsageRecord,
    UsageTotals,
)
from backend.ports import ProviderUsagePort

# Bounds on what an owner may choose: enough for one draft, and not absurd.
MIN_ALLOWANCE = 20_000
MAX_ALLOWANCE = 50_000_000


class ProviderUsageService:
    def __init__(
        self, store: ProviderUsagePort, *, limit: UsageLimit, allowances: Any = None
    ) -> None:
        self._store = store
        self._limit = limit
        self._allowances = allowances

    async def _period_token_limit(self, workspace_id: str) -> tuple[int | None, int | None]:
        """(the limit in force, what the workspace chose). The lower one wins."""
        site = self._limit.period_token_limit or None
        chosen = await self._allowances.get(workspace_id) if self._allowances else None
        limits = [value for value in (site, chosen) if value and value > 0]
        return (min(limits) if limits else None), chosen

    async def allowance(self, *, workspace_id: str, actor_user_id: str) -> Allowance:
        """What this person has spent in the period, against what they may."""
        limit, chosen = await self._period_token_limit(workspace_id)
        since = utcnow() - timedelta(seconds=self._limit.period_seconds)
        totals = await self._store.totals(
            workspace_id=workspace_id, actor_user_id=actor_user_id, since=since
        )
        return Allowance(
            period_seconds=self._limit.period_seconds,
            limit_tokens=limit,
            used_tokens=totals.total_tokens,
            requests=totals.request_count,
            workspace_limit_tokens=chosen,
            site_limit_tokens=self._limit.period_token_limit or None,
            cost_usd=totals.provider_cost_usd if totals.provider_cost_usd > 0 else None,
        )

    async def set_allowance(
        self, *, workspace_id: str, daily_tokens: int | None, actor_user_id: str | None
    ) -> None:
        if self._allowances is None:
            raise ValidationError("An allowance cannot be set on this site.")
        if daily_tokens is not None and not MIN_ALLOWANCE <= daily_tokens <= MAX_ALLOWANCE:
            raise ValidationError(
                f"Choose an allowance between {MIN_ALLOWANCE:,} and {MAX_ALLOWANCE:,} tokens."
            )
        await self._allowances.set(
            workspace_id=workspace_id, daily_tokens=daily_tokens, actor_user_id=actor_user_id
        )

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
        job_token_limit = limit.per_job_token_limit
        if (
            job_token_limit
            and job_token_limit > 0
            and (job.total_tokens + estimated_tokens > job_token_limit)
        ):
            raise UsageLimitExceeded("Per-job token limit exceeded.")
        job_cost_limit = limit.per_job_cost_limit
        if (
            job_cost_limit
            and job_cost_limit > 0
            and (job.provider_cost_usd + estimated_cost_usd > job_cost_limit)
        ):
            raise UsageLimitExceeded("Per-job cost limit exceeded.")

        since = utcnow() - timedelta(seconds=limit.period_seconds)
        period = await self._store.totals(
            workspace_id=workspace_id, actor_user_id=actor_user_id, since=since
        )
        period_token_limit, _chosen = await self._period_token_limit(workspace_id)
        if period_token_limit and (period.total_tokens + estimated_tokens > period_token_limit):
            raise UsageLimitExceeded(
                "You have used up your assistant allowance for now. It frees up again over "
                "the next day; everything can still be done by hand."
            )
        period_cost_limit = limit.period_cost_limit
        if (
            period_cost_limit
            and period_cost_limit > 0
            and (period.provider_cost_usd + estimated_cost_usd > period_cost_limit)
        ):
            raise UsageLimitExceeded("Rolling-period cost limit exceeded.")

    async def would_duplicate(self, idempotency_key: str) -> bool:
        return await self._store.has_idempotency_key(idempotency_key=idempotency_key)

    async def record(self, record: UsageRecord) -> UsageRecord:
        return await self._store.record(record)
