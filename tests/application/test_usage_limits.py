import pytest

from backend.domain.usage import UsageLimit, UsageLimitExceeded, UsageRecord
from backend.services.usage_service import ProviderUsageService
from tests.doubles import InMemoryProviderUsageStore

pytestmark = pytest.mark.application


@pytest.mark.asyncio
async def test_per_job_and_period_token_limits() -> None:
    store = InMemoryProviderUsageStore()
    service = ProviderUsageService(
        store,
        limit=UsageLimit(per_job_token_limit=100, period_token_limit=1000, period_seconds=3600),
    )

    await service.ensure_within_limits(
        workspace_id="w", actor_user_id="a", estimated_tokens=50, idempotency_key="job1"
    )
    await service.record(
        UsageRecord(
            id="u1",
            workspace_id="w",
            actor_user_id="a",
            provider="openrouter",
            model="m",
            requested_route="chat",
            prompt_tokens=50,
            completion_tokens=10,
            idempotency_key="job1",
        )
    )

    # 60 used + 40 estimated == 100 limit: allowed.
    await service.ensure_within_limits(
        workspace_id="w", actor_user_id="a", estimated_tokens=40, idempotency_key="job1"
    )
    with pytest.raises(UsageLimitExceeded):
        await service.ensure_within_limits(
            workspace_id="w", actor_user_id="a", estimated_tokens=41, idempotency_key="job1"
        )

    assert await service.would_duplicate("job1") is True
    assert await service.would_duplicate("other") is False


@pytest.mark.asyncio
async def test_cost_limits_are_opt_in() -> None:
    store = InMemoryProviderUsageStore()
    # Zero cost limits are treated as disabled.
    service = ProviderUsageService(store, limit=UsageLimit(per_job_cost_limit=0.0))
    await service.record(
        UsageRecord(
            id="u1",
            workspace_id="w",
            actor_user_id="a",
            provider="openrouter",
            model="m",
            requested_route="chat",
            provider_cost_usd=5.0,
            idempotency_key="job1",
        )
    )
    await service.ensure_within_limits(workspace_id="w", actor_user_id="a", idempotency_key="job1")
