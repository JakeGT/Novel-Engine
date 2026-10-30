"""The scheduled catch-up of the search index (CTX-012): off by default, a few stories a
round, only into the index in use when it is the configured model's."""

from __future__ import annotations

import pytest

from backend.adapters.embedding_fake import DeterministicEmbedding
from tests.application.test_embedding_index import P, Rig, W, _built, _png
from tests.embedding_doubles import Picture

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _Runner:
    def __init__(self) -> None:
        self.periodic: list[tuple[float, object]] = []

    def every(self, seconds: float, work: object) -> None:
        self.periodic.append((seconds, work))


async def test_it_is_off_unless_minutes_are_set() -> None:
    rig = Rig()
    runner = _Runner()
    assert rig.service.schedule_catch_up(runner, 0) is False
    assert runner.periodic == []
    assert rig.service.schedule_catch_up(runner, 15) is True
    assert runner.periodic == [(900.0, rig.service.catch_up)]


async def test_a_picture_whose_quick_update_was_missed_is_caught_up() -> None:
    rig = Rig()
    rig.picture("ast_1", "a lighthouse at night")
    active = await _built(rig)
    # A second picture is prepared while the quick update fails (nothing embeds it).
    rig.picture("ast_2", "a harbor in fog")
    generation = rig.index.get(active)
    assert generation is not None
    assert (await rig.service.project_lag(generation, W, P)).pictures == 1

    report = await rig.service.catch_up()
    assert report.pictures == 1
    assert (await rig.service.project_lag(generation, W, P)).total == 0
    # Nothing left: the next round does nothing.
    again = await rig.service.catch_up()
    assert (again.pictures, again.captions, again.documents) == (0, 0, 0)


async def test_it_never_writes_into_an_index_of_another_model() -> None:
    rig = Rig(DeterministicEmbedding(dimension=256, model="old"))
    rig.picture("ast_1", "a lighthouse at night")
    await _built(rig)
    rig.use(DeterministicEmbedding(dimension=512, model="new"))
    rig.picture("ast_2", "a harbor in fog")
    report = await rig.service.catch_up()
    assert report.pictures == 0


async def test_each_round_is_bounded_and_takes_turns() -> None:
    rig = Rig()
    await _built(rig)
    for n in range(4):
        data = _png(40 + n)
        rig.world.add_picture(
            f"ws_{n}", f"prj_{n}", Picture(asset_id=f"ast_{n}", version_id=f"ver_{n}", data=data)
        )
        rig.embedder.describe(data, f"picture {n}")
    seen: list[tuple[str, str]] = []
    real = rig.service.build_project

    async def counted(generation_id, workspace_id, project_id, **kwargs):  # type: ignore[no-untyped-def]
        seen.append((workspace_id, project_id))
        return await real(generation_id, workspace_id, project_id, **kwargs)

    rig.service.build_project = counted  # type: ignore[method-assign]
    await rig.service.catch_up(stories=2)
    assert len(seen) == 2
    await rig.service.catch_up(stories=2)
    # The second round starts where the first stopped: other stories get their turn.
    assert len(seen) == 4 and len(set(seen)) == 4


async def test_a_round_embeds_a_bounded_number_of_things_however_many_one_story_misses() -> None:
    # Bounded by things embedded, not stories: one story missing many pictures is caught
    # up over several rounds, each starting with it again until it is done.
    rig = Rig()
    await _built(rig)
    for n in range(7):
        rig.picture(f"ast_{n}", f"picture {n}")
    generation = rig.index.get((await rig.service.status()).active["id"])  # type: ignore[index]
    assert generation is not None
    first = await rig.service.catch_up(items=3)
    assert first.pictures == 3
    assert (await rig.service.project_lag(generation, W, P)).pictures == 4
    second = await rig.service.catch_up(items=3)
    assert second.pictures == 3
    third = await rig.service.catch_up(items=3)
    assert third.pictures == 1
    assert (await rig.service.project_lag(generation, W, P)).total == 0


async def test_a_round_is_skipped_while_another_server_runs_one() -> None:
    rig = Rig()
    await _built(rig)
    rig.picture("ast_1", "a lighthouse at night")
    held = rig.index.hold_lock("embedding-catch-up")
    assert held is not None
    skipped = await rig.service.catch_up()
    assert skipped.pictures == 0
    rig.index.release_lock(held)
    caught = await rig.service.catch_up()
    assert caught.pictures == 1
    # The round let go of the lock when it finished.
    assert rig.index.locks == set()
