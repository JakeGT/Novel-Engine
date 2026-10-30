"""The graph projection kept up to date in the background (ADR-012)."""

from contextlib import asynccontextmanager
from dataclasses import dataclass

import pytest

from backend.services.projection_worker import POLL_SECONDS, ProjectionWorker

pytestmark = pytest.mark.unit


@dataclass
class _Branch:
    id: str


@dataclass
class _Result:
    published: int
    failed: int


class _Journal:
    def __init__(self, pending):
        self.pending = pending

    async def projects_with_pending_outbox(self, *, limit=50):
        return list(self.pending)[:limit]

    async def ensure_root_branch(self, *, workspace_id, project_id):
        return _Branch(f"main-{project_id}")


class _Consumer:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.calls = []

    async def drain(self, *, workspace_id, project_id, branch_id):
        self.calls.append((project_id, branch_id))
        outcome = self.outcomes[project_id].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.asyncio
async def test_every_story_with_waiting_changes_is_delivered_on_the_main_line() -> None:
    consumer = _Consumer({"a": [_Result(2, 0)], "b": [_Result(1, 0)]})
    worker = ProjectionWorker(journal=_Journal([("w", "a"), ("w", "b")]), consumer=consumer)
    assert await worker.run_once(now=0) == 3
    assert consumer.calls == [("a", "main-a"), ("b", "main-b")]


@pytest.mark.asyncio
async def test_a_failing_story_waits_longer_each_time_and_never_stops_the_others() -> None:
    consumer = _Consumer(
        {
            "bad": [RuntimeError("down"), _Result(0, 1), _Result(1, 0)],
            "good": [_Result(1, 0) for _ in range(4)],
        }
    )
    worker = ProjectionWorker(journal=_Journal([("w", "bad"), ("w", "good")]), consumer=consumer)
    assert await worker.run_once(now=0) == 1  # bad raised, good delivered
    first_wait = POLL_SECONDS * 2
    assert await worker.run_once(now=first_wait - 0.1) == 1  # bad is still waiting
    assert [c[0] for c in consumer.calls].count("bad") == 1
    assert await worker.run_once(now=first_wait) == 1  # bad tried again, fails again
    assert await worker.run_once(now=first_wait + first_wait * 2) == 2  # and recovers


@pytest.mark.asyncio
async def test_a_worker_switched_off_never_starts() -> None:
    worker = ProjectionWorker(journal=_Journal([]), consumer=_Consumer({}), autostart=False)
    worker.start()
    assert worker.running is False
    await worker.stop()


@pytest.mark.asyncio
async def test_many_failing_stories_never_crowd_out_a_healthy_one() -> None:
    failing = [("w", f"bad{n:02d}") for n in range(60)]
    consumer = _Consumer(
        {
            **{project: [_Result(0, 1) for _ in range(5)] for _, project in failing},
            "good": [_Result(1, 0)],
        }
    )
    # The failing stories are older, so they come first in the list.
    worker = ProjectionWorker(journal=_Journal([*failing, ("w", "good")]), consumer=consumer)
    assert await worker.run_once(now=0) == 0  # the first fifty fail
    # They are all waiting now; the healthy story behind them still gets its turn.
    assert await worker.run_once(now=1) == 1
    assert ("good", "main-good") in consumer.calls


class _LockingJournal(_Journal):
    def __init__(self, pending, busy):
        super().__init__(pending)
        self.busy = busy

    @asynccontextmanager
    async def story_lock(self, *, workspace_id, project_id):
        yield project_id not in self.busy


@pytest.mark.asyncio
async def test_a_story_another_server_is_delivering_is_left_to_it() -> None:
    consumer = _Consumer({"a": [_Result(1, 0)], "b": [_Result(2, 0)]})
    journal = _LockingJournal([("w", "a"), ("w", "b")], busy={"a"})
    worker = ProjectionWorker(journal=journal, consumer=consumer)
    assert await worker.run_once(now=0) == 2
    assert consumer.calls == [("b", "main-b")]
    # Skipped, not failed: it is tried again straight away next round.
    journal.busy.clear()
    assert await worker.run_once(now=0) == 1


@pytest.mark.asyncio
async def test_views_are_built_under_the_story_lock_and_within_a_budget() -> None:
    from backend.services.projection_worker import READ_MODEL_BUDGET_SECONDS

    class _Views:
        def __init__(self):
            self.calls = []

        async def run_once(self, **kwargs):
            self.calls.append(kwargs)
            return 1

    journal = _LockingJournal([], busy=set())
    worker = ProjectionWorker(journal=journal, consumer=_Consumer({}))
    views = _Views()
    worker.read_models = views
    assert await worker.run_read_models() == 1
    (call,) = views.calls
    assert call["budget_seconds"] == READ_MODEL_BUDGET_SECONDS
    assert call["lock"] == journal.story_lock
