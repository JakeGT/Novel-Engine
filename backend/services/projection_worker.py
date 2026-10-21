"""Keeping the graph projection up to date in the background (ADR-012).

Canon commits write an outbox message; this loop, started with the app, delivers them to
the graph projection for every story that has some waiting, so the graph follows the
story without anyone running a command. Canon never waits for it. A story whose delivery
fails is tried again later, waiting longer each time, so one broken story neither stops
the others nor fills the logs, and stories waiting out a failure never take the places of
stories that are ready. Where the journal offers a lock per story (PostgreSQL), a story
another server is delivering right now is left to it.

The same loop also reads texts waiting for the passage index (CTX-005) when one is
given: ``passages`` is the ``PassageIndex``, which keeps its own waiting list, claims
and retries. It also builds the story's ready views (QRY-001) for every line of the story
the outbox says moved on, when ``read_models`` is set: a ``ReadModelConsumer`` with its own
watermarks, so the graph's delivery marks never decide what the views see. Those builds take
the same per-story lock and stop starting new lines after a short budget each poll.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from backend.core.logging import log_event

logger = logging.getLogger(__name__)

POLL_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 300.0
# How many stories that are ready to try are asked for each round.
BATCH = 50
# How long each poll may spend building the story's ready views (QRY-001).
READ_MODEL_BUDGET_SECONDS = 2.0


class ProjectionWorker:
    def __init__(
        self, *, journal: Any, consumer: Any, autostart: bool = True, passages: Any = None
    ) -> None:
        self._journal = journal
        self._consumer = consumer
        self._passages = passages
        # The story's ready views (QRY-001): a ``ReadModelConsumer``, set by the container.
        self.read_models: Any = None
        self.autostart = autostart
        self._task: asyncio.Task[None] | None = None
        # (workspace, project) -> (next attempt at, current wait)
        self._backoff: dict[tuple[str, str], tuple[float, float]] = {}

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def run_once(self, *, now: float | None = None) -> int:
        """Deliver what is waiting for each story with something to deliver. Returns how many."""
        clock = time.monotonic() if now is None else now
        delivered = 0
        # Stories still waiting out a failure come back in the list and are skipped, so ask
        # for that many more: otherwise enough failing stories would fill every place and
        # the healthy ones behind them would never be delivered.
        waiting_now = sum(1 for at, _ in self._backoff.values() if at > clock)
        pending = await self._journal.projects_with_pending_outbox(limit=BATCH + waiting_now)
        lock = getattr(self._journal, "story_lock", None)
        for workspace_id, project_id in pending:
            key = (workspace_id, project_id)
            waiting = self._backoff.get(key)
            if waiting is not None and waiting[0] > clock:
                continue
            branch = await self._journal.ensure_root_branch(
                workspace_id=workspace_id, project_id=project_id
            )
            try:
                if lock is None:
                    result = await self._drain(workspace_id, project_id, branch.id)
                else:
                    async with lock(workspace_id=workspace_id, project_id=project_id) as held:
                        if not held:
                            continue  # another server is delivering this story right now
                        result = await self._drain(workspace_id, project_id, branch.id)
            except Exception as error:  # noqa: BLE001 - one story never stops the others
                result = None
                log_event(
                    logger,
                    "projection.worker.error",
                    level=logging.WARNING,
                    error_type=type(error).__name__,
                )
            if result is None or result.failed:
                wait = min(MAX_BACKOFF_SECONDS, (waiting[1] * 2) if waiting else POLL_SECONDS * 2)
                self._backoff[key] = (clock + wait, wait)
                continue
            self._backoff.pop(key, None)
            delivered += result.published
        return delivered

    async def _drain(self, workspace_id: str, project_id: str, branch_id: str) -> Any:
        return await self._consumer.drain(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
        )

    async def run_read_models(self) -> int:
        """Build the ready views waiting, one story at a time under the same per-story
        lock as graph delivery (so two servers never build one story at once), and for
        at most ``READ_MODEL_BUDGET_SECONDS`` a poll, so graph delivery and the passage
        index are never kept waiting behind a long rebuild."""
        if self.read_models is None:
            return 0
        return int(
            await self.read_models.run_once(
                lock=getattr(self._journal, "story_lock", None),
                budget_seconds=READ_MODEL_BUDGET_SECONDS,
            )
        )

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception as error:  # noqa: BLE001 - the loop must survive a bad poll
                log_event(
                    logger,
                    "projection.worker.poll_error",
                    level=logging.WARNING,
                    error_type=type(error).__name__,
                )
            if self._passages is not None:
                try:
                    await self._passages.deliver_waiting()
                except Exception as error:  # noqa: BLE001 - the loop must survive a bad poll
                    log_event(
                        logger,
                        "passages.worker.poll_error",
                        level=logging.WARNING,
                        error_type=type(error).__name__,
                    )
            if self.read_models is not None:
                try:
                    await self.run_read_models()
                except Exception as error:  # noqa: BLE001 - the loop must survive a bad poll
                    log_event(
                        logger,
                        "read_models.worker.poll_error",
                        level=logging.WARNING,
                        error_type=type(error).__name__,
                    )
            await asyncio.sleep(POLL_SECONDS)

    def start(self) -> None:
        if self._task is None and self.autostart:
            self._task = asyncio.create_task(self._loop(), name="projection-worker")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


__all__ = ["ProjectionWorker"]
