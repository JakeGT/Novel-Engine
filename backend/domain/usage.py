"""Provider usage records and limit policy.

A usage record captures what a model call requested and what actually happened.
There is no platform balance: costs are provider-reported and belong to the
user's own account. Limits bound a single job and a rolling period.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from backend.domain.errors import DomainError
from backend.domain.identity import utcnow


class UsageOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class UsageRecord(BaseModel):
    id: str
    workspace_id: str
    project_id: str | None = None
    actor_user_id: str
    provider: str
    model: str
    requested_route: str
    actual_route: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Of the prompt tokens, those the provider read from its cache (part of prompt_tokens).
    cached_prompt_tokens: int = 0
    latency_ms: int = 0
    attempt: int = 1
    outcome: UsageOutcome = UsageOutcome.SUCCESS
    provider_cost_usd: float | None = None
    idempotency_key: str | None = None
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class UsageTotals(BaseModel):
    request_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_prompt_tokens: int = 0
    provider_cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class UsageLimit:
    """Per-job and rolling-period token/cost bounds for an author or project."""

    def __init__(
        self,
        *,
        per_job_token_limit: int | None = None,
        per_job_cost_limit: float | None = None,
        period_seconds: int = 86400,
        period_token_limit: int | None = None,
        period_cost_limit: float | None = None,
    ) -> None:
        self.per_job_token_limit = per_job_token_limit
        self.per_job_cost_limit = per_job_cost_limit
        self.period_seconds = period_seconds
        self.period_token_limit = period_token_limit
        self.period_cost_limit = period_cost_limit


class UsageLimitExceeded(DomainError):
    """A per-job or rolling-period token/cost limit would be exceeded."""

    status_code = 429
    code = "usage_limit"


class Allowance(BaseModel):
    """What one person may spend through a workspace's key in a period, and has."""

    period_seconds: int
    # The limit in force: the lower of the site's and the workspace's. None = no limit.
    limit_tokens: int | None
    used_tokens: int
    requests: int
    # What a workspace owner chose, if anything, and the site's own ceiling.
    workspace_limit_tokens: int | None
    site_limit_tokens: int | None
    # What the provider reported this person's calls in the period cost, in US dollars;
    # None when no provider reported a cost (prices are never worked out here).
    cost_usd: float | None = None


class UsageError(RuntimeError):
    """Base class for usage-store failures."""
