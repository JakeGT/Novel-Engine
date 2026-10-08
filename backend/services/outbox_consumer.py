"""Outbox consumer seam (ADR-012).

A bounded runner that consumes committed canonical outbox messages and drives the
derived graph projection. Canonical commits never depend on this; a failure here
leaves the message unpublished with an attempt count and keeps projection lag
visible. A worker can later replace the bounded in-process runner without changing
the contract.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.domain.projection import ProjectionStatus
from backend.ports import CanonJournalPort
from backend.services.projection_service import GraphProjectionService

CANON_COMMIT_TOPIC = "canon.commit"


@dataclass(frozen=True)
class OutboxDrainResult:
    processed: int
    published: int
    failed: int
    lag: int


class OutboxConsumer:
    def __init__(
        self,
        *,
        journal: CanonJournalPort,
        projector: GraphProjectionService,
    ) -> None:
        self._journal = journal
        self._projector = projector

    async def drain(
        self,
        *,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        limit: int = 100,
    ) -> OutboxDrainResult:
        messages = await self._journal.list_outbox(
            workspace_id=workspace_id, project_id=project_id, limit=limit
        )
        pending = sorted(
            (message for message in messages if message.published_at is None),
            key=lambda message: (message.revision, message.created_at),
        )
        published = 0
        failed = 0
        for message in pending:
            if message.topic != CANON_COMMIT_TOPIC:
                await self._journal.mark_outbox_published(message_id=message.id)
                published += 1
                continue
            report = await self._projector.project_pending(
                workspace_id=message.workspace_id,
                project_id=message.project_id,
                branch_id=message.branch_id,
            )
            if report.status is ProjectionStatus.ERROR:
                await self._journal.mark_outbox_failed(
                    message_id=message.id, error=report.error or "projection_error"
                )
                failed += 1
                break
            await self._journal.mark_outbox_published(message_id=message.id)
            published += 1

        lag = (
            await self._projector.lag(
                workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
            )
        ).lag
        return OutboxDrainResult(
            processed=len(pending), published=published, failed=failed, lag=lag
        )
