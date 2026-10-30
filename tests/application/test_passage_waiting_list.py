"""The passage index's waiting list: texts are read in by the worker, with lag (CTX-005)."""

import asyncio

import pytest

from backend.services.passage_index import PassageIndex, retry_after
from backend.services.projection_worker import ProjectionWorker
from tests.doubles import InMemoryPassageQueue, InMemoryPassageStore

pytestmark = pytest.mark.application

W, P = "ws", "pj"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _Text:
    def __init__(self, source_id: str, content: str) -> None:
        self.id = source_id
        self.title = source_id
        self.latest_version_id = f"{source_id}:v1"
        self.content = content
        self.kind = None


class _Version:
    def __init__(self, text: _Text) -> None:
        self.id = text.latest_version_id
        self.content = text.content


class _Sources:
    def __init__(self) -> None:
        self.texts: dict[str, _Text] = {}
        self.fail: set[str] = set()
        self.reads = 0

    def write(self, source_id: str, content: str) -> None:
        number = 1
        if source_id in self.texts:
            number = int(self.texts[source_id].latest_version_id.rsplit("v", 1)[1]) + 1
        text = _Text(source_id, content)
        text.latest_version_id = f"{source_id}:v{number}"
        self.texts[source_id] = text

    async def version(self, workspace_id, project_id, source_id, version_id=None):
        from backend.domain.errors import NotFoundError

        self.reads += 1
        if source_id in self.fail:
            raise RuntimeError("the store is down")
        text = self.texts.get(source_id)
        if text is None:
            raise NotFoundError("gone")
        return text, _Version(text), []

    async def index_for(self, workspace_id, project_id, source_id, own_line=False):
        from backend.domain.sources import build_index

        return build_index([], []), [], None

    async def all_texts(self, workspace_id, project_id):
        return list(self.texts.values())


class _Branch:
    id = "main"


class _Journal:
    async def ensure_root_branch(self, **_):
        return _Branch()


class _Reader:
    async def get_head(self, **_):
        return 7


def _index(sources, queue, *, deliver_now=False) -> tuple[PassageIndex, InMemoryPassageStore]:
    store = InMemoryPassageStore()
    index = PassageIndex(
        store=store,
        sources=sources,
        reader=_Reader(),
        journal=_Journal(),
        queue=queue,
        deliver_now=deliver_now,
    )
    return index, store


def test_a_change_waits_with_its_lag_until_the_worker_reads_it_in():
    clock = _Clock()
    sources, queue = _Sources(), InMemoryPassageQueue(clock)
    index, store = _index(sources, queue)
    sources.write("src_a", "Mara walks.\n\nTobin waits.")
    asyncio.run(index.request(W, P, "src_a"))
    # Saving did not read the text: it waits.
    assert sources.reads == 0
    clock.now += 12
    status = asyncio.run(index.status(W, P))
    assert status.waiting == 1 and status.waiting_seconds == pytest.approx(12)
    assert status.behind == ["src_a"] and status.indexed == 0

    done = asyncio.run(index.deliver_waiting())
    assert done.read == 1 and done.failed == 0
    status = asyncio.run(index.status(W, P))
    assert status.waiting == 0 and status.waiting_seconds is None and status.indexed == 1
    passages = asyncio.run(store.for_source(workspace_id=W, project_id=P, source_id="src_a"))
    assert [p.version_id for p in passages] == ["src_a:v1"]


def test_asking_again_while_a_text_is_being_read_is_never_lost():
    clock = _Clock()
    sources, queue = _Sources(), InMemoryPassageQueue(clock)
    index, store = _index(sources, queue)
    sources.write("src_a", "First words.")
    asyncio.run(index.request(W, P, "src_a"))
    (claimed,) = asyncio.run(queue.claim(workspace_id=W, project_id=P, limit=5, lease_seconds=60))
    # The author saves again while the first version is being read.
    sources.write("src_a", "Second words.\n\nAnd more.")
    asyncio.run(index.request(W, P, "src_a"))
    # Still claimed: no second reader picks it up meanwhile.
    assert asyncio.run(queue.claim(workspace_id=W, project_id=P, limit=5, lease_seconds=60)) == []
    finished = asyncio.run(
        queue.done(workspace_id=W, project_id=P, source_id="src_a", generation=claimed.generation)
    )
    assert finished is False  # a newer change is waiting
    asyncio.run(index.deliver(W, P))
    passages = asyncio.run(store.for_source(workspace_id=W, project_id=P, source_id="src_a"))
    assert {p.version_id for p in passages} == {"src_a:v2"}
    assert asyncio.run(index.waiting(W, P)).count == 0


def test_a_failure_is_tried_again_later_and_never_stops_other_texts():
    clock = _Clock()
    sources, queue = _Sources(), InMemoryPassageQueue(clock)
    index, _ = _index(sources, queue)
    sources.write("src_bad", "Words.")
    sources.write("src_good", "Words too.")
    sources.fail.add("src_bad")
    asyncio.run(index.request(W, P, "src_bad"))
    asyncio.run(index.request(W, P, "src_good"))
    done = asyncio.run(index.deliver_waiting())
    assert done.read == 1 and done.failed == 1
    waiting = asyncio.run(index.waiting(W, P))
    assert waiting.count == 1 and waiting.failing == 1
    # Not tried again before its wait is over, then tried and read.
    assert asyncio.run(index.deliver_waiting()).read == 0
    sources.fail.clear()
    clock.now += retry_after(0) + 1
    assert asyncio.run(index.deliver_waiting()).read == 1
    assert retry_after(0) < retry_after(3) <= retry_after(30) == 600.0


def test_a_removed_text_is_forgotten_and_a_text_behind_is_asked_for_again():
    clock = _Clock()
    sources, queue = _Sources(), InMemoryPassageQueue(clock)
    index, store = _index(sources, queue, deliver_now=True)
    sources.write("src_a", "Words.")
    asyncio.run(index.request(W, P, "src_a"))
    assert asyncio.run(store.for_source(workspace_id=W, project_id=P, source_id="src_a"))
    del sources.texts["src_a"]
    asyncio.run(index.request(W, P, "src_a"))  # removed since: nothing of it stays
    assert asyncio.run(store.for_source(workspace_id=W, project_id=P, source_id="src_a")) == []

    # A text changed with nothing asked for it (a server stopped in between).
    later, later_queue = _Sources(), InMemoryPassageQueue(clock)
    healing, _ = _index(later, later_queue)
    later.write("src_b", "Never asked for.")
    status = asyncio.run(healing.status(W, P))
    assert status.behind == ["src_b"] and status.waiting == 1


def test_the_graph_worker_also_reads_waiting_texts():
    clock = _Clock()
    sources, queue = _Sources(), InMemoryPassageQueue(clock)
    index, _ = _index(sources, queue)
    sources.write("src_a", "Words.")
    asyncio.run(index.request(W, P, "src_a"))

    class _NoJournal:
        async def projects_with_pending_outbox(self, *, limit):
            return []

    worker = ProjectionWorker(journal=_NoJournal(), consumer=None, passages=index)

    async def one_round():
        task = asyncio.create_task(worker._loop())  # noqa: SLF001 - one poll of the loop
        for _ in range(50):
            await asyncio.sleep(0)
            if (await index.waiting(W, P)).count == 0:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(one_round())
    assert asyncio.run(index.waiting(W, P)).count == 0


def test_each_text_is_claimed_just_before_it_is_read_so_a_slow_batch_keeps_its_leases():
    from backend.services.passage_index import LEASE_SECONDS

    clock = _Clock()
    queue = InMemoryPassageQueue(clock)
    left: dict[str, float] = {}

    class _Slow(_Sources):
        async def version(self, workspace_id, project_id, source_id, version_id=None):
            row = queue.rows.get((W, P, source_id))
            if row is not None and source_id not in left:
                left[source_id] = row["claimed_until"] - clock.now
            clock.now += 100  # each text takes most of a lease to read
            return await super().version(workspace_id, project_id, source_id, version_id)

    sources = _Slow()
    index, _ = _index(sources, queue)
    for name in ("src_a", "src_b", "src_c"):
        sources.write(name, "Words.")
        asyncio.run(index.request(W, P, name))
    assert asyncio.run(index.deliver(W, P)).read == 3
    # Every text still had its whole lease when its reading began.
    assert left == {name: LEASE_SECONDS for name in ("src_a", "src_b", "src_c")}


def test_a_fresh_change_is_read_at_once_even_after_a_failure():
    clock = _Clock()
    sources, queue = _Sources(), InMemoryPassageQueue(clock)
    index, store = _index(sources, queue)
    sources.write("src_a", "First words.")
    sources.fail.add("src_a")
    asyncio.run(index.request(W, P, "src_a"))
    assert asyncio.run(index.deliver(W, P)).failed == 1
    # The store is back and the author saves again: no waiting out the old failure.
    sources.fail.clear()
    sources.write("src_a", "Second words.")
    asyncio.run(index.request(W, P, "src_a"))
    assert asyncio.run(index.deliver(W, P)).read == 1
    passages = asyncio.run(store.for_source(workspace_id=W, project_id=P, source_id="src_a"))
    assert {p.version_id for p in passages} == {"src_a:v2"}


def test_a_failure_reading_an_old_version_does_not_hold_back_a_newer_change():
    clock = _Clock()
    queue = InMemoryPassageQueue(clock)
    asyncio.run(queue.request(workspace_id=W, project_id=P, source_id="src_a"))
    (claimed,) = asyncio.run(queue.claim(workspace_id=W, project_id=P, limit=5, lease_seconds=60))
    asyncio.run(queue.request(workspace_id=W, project_id=P, source_id="src_a"))
    counted = asyncio.run(
        queue.failed(
            workspace_id=W,
            project_id=P,
            source_id="src_a",
            generation=claimed.generation,
            error="Boom",
            retry_after_seconds=600,
        )
    )
    assert counted is False
    # The newer change is ready now, with no failure counted against it.
    (again,) = asyncio.run(queue.claim(workspace_id=W, project_id=P, limit=5, lease_seconds=60))
    assert again.generation == claimed.generation + 1 and again.attempts == 0
    assert asyncio.run(queue.waiting(workspace_id=W, project_id=P)).failing == 0


def test_every_text_a_claim_returns_is_read_even_past_the_one_asked_for():
    # A claim that holds more texts than asked for (as PostgreSQL could, before the pick
    # was materialised) must not leave the extra ones held and unread.
    class _GreedyQueue(InMemoryPassageQueue):
        async def claim(self, *, workspace_id, project_id, limit, lease_seconds):
            return await super().claim(
                workspace_id=workspace_id,
                project_id=project_id,
                limit=limit + 1,
                lease_seconds=lease_seconds,
            )

    clock = _Clock()
    sources, queue = _Sources(), _GreedyQueue(clock)
    index, _ = _index(sources, queue)
    sources.write("src_a", "Mara walks.")
    sources.write("src_b", "Tobin waits.")
    asyncio.run(index.request(W, P, "src_a"))
    asyncio.run(index.request(W, P, "src_b"))
    done = asyncio.run(index.deliver_waiting())
    assert done.read == 2
    assert asyncio.run(index.status(W, P)).waiting == 0
