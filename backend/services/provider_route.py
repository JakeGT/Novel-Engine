"""Which AI provider a request uses, within the person's limits, and how its use is recorded.

Shared by requests answered at once and by stored jobs (ADR-022). Hosted deployments use
the workspace's own key (no platform-funded inference); a self-hosted install uses the
operator's server key.
"""

from __future__ import annotations

from typing import Any

from backend.adapters.system import uuid7
from backend.core.config import Settings
from backend.domain.errors import CapabilityUnavailableError
from backend.domain.identity import AccessScope
from backend.domain.providers import set_request_model
from backend.domain.usage import UsageOutcome, UsageRecord
from backend.ports import AIUsage


async def resolve_provider(
    *,
    settings: Settings,
    credentials: Any,
    usage: Any,
    scope: AccessScope,
    estimated_tokens: int = 0,
    idempotency_key: str | None = None,
) -> tuple[str | None, str | None, str | None, Any]:
    """Return (credential id, key, base url, usage sink) after checking the person's limits.

    ``estimated_tokens`` is what the work is expected to use, so a request that would run
    past a limit is refused before anything is sent. The sink takes an optional
    ``idempotency_key`` naming the one call it records, so the same call is never
    counted twice."""
    workspace_id = scope.workspace_id or ""
    project_id = scope.project_id or ""
    credential_id: str | None = None
    api_key: str | None = None
    provider_base_url: str | None = None
    provider = settings.AI_PROVIDER
    if settings.is_hosted:
        route = await credentials.resolve_route_for_use(scope, workspace_id)
        if route is None:
            raise CapabilityUnavailableError(
                "No provider credential is configured for this workspace."
            )
        credential_id, api_key = route.credential_id, route.api_key
        provider_base_url = route.provider.base_url
        provider = route.provider.id
        # The workspace's own choice of model, read by the gateway for this request.
        set_request_model(route.model)

    await usage.ensure_within_limits(
        workspace_id=workspace_id,
        actor_user_id=scope.actor_user_id,
        estimated_tokens=estimated_tokens,
        idempotency_key=idempotency_key,
    )

    async def sink(ai_usage: AIUsage, model: str, idempotency_key: str | None = None) -> None:
        await usage.record(
            UsageRecord(
                id=str(uuid7()),
                workspace_id=workspace_id,
                project_id=project_id or None,
                actor_user_id=scope.actor_user_id,
                provider=provider,
                model=model or "",
                requested_route=model or "",
                prompt_tokens=ai_usage.prompt_tokens,
                completion_tokens=ai_usage.completion_tokens,
                cached_prompt_tokens=ai_usage.cached_prompt_tokens,
                outcome=UsageOutcome.SUCCESS,
                idempotency_key=idempotency_key,
            )
        )

    return credential_id, api_key, provider_base_url, sink


__all__ = ["resolve_provider"]
