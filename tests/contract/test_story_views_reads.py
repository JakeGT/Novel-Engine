"""Reads of the story's ready views while the story moves (QRY-001): an answer never claims
a version it may not reflect, the timeline and the reader's journey never mix a view
with newer records, a read that hit its limit says so, and building views shares the
background loop fairly and one story at a time."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

import backend.services.read_models as read_models
from tests.contract.test_story_views import _build, _story, _verify

pytestmark = pytest.mark.contract


def _ids(container) -> tuple[str, str]:
    return container.settings.AUTH_LOCAL_WORKSPACE_ID, container.settings.AUTH_LOCAL_PROJECT_ID


def _moving(service, monkeypatch, *, settles_after: int | None) -> list[int]:
    """Make the line's head move on after the first look, ``settles_after`` times (or
    for ever when None)."""
    real = service._line
    calls: list[int] = []

    async def line(workspace_id, project_id):
        branch, head = await real(workspace_id, project_id)
        calls.append(head)
        moved = len(calls) - 1
        if settles_after is not None:
            moved = min(moved, settles_after)
        return branch, head + moved

    monkeypatch.setattr(service, "_line", line)
    return calls


def test_an_answer_worked_out_while_a_commit_lands_reads_again(
    auth_client, fake_container, monkeypatch
):
    _story(auth_client, fake_container)
    service = fake_container.read_models
    workspace, project = _ids(fake_container)
    head = asyncio.run(service._line(workspace, project))[1]
    _moving(service, monkeypatch, settles_after=1)
    answer = asyncio.run(service.rows(workspace, project, "location"))
    # Read again once the head stopped moving: the newer version, not the older one.
    assert answer.source == "worked_out"
    assert (answer.version, answer.latest) == (head + 1, head + 1)


def test_an_answer_while_commits_keep_landing_says_it_is_behind(
    auth_client, fake_container, monkeypatch
):
    _story(auth_client, fake_container)
    service = fake_container.read_models
    workspace, project = _ids(fake_container)
    _moving(service, monkeypatch, settles_after=None)
    answer = asyncio.run(service.rows(workspace, project, "location"))
    assert answer.version < answer.latest


def test_the_timeline_and_journey_are_worked_out_when_the_story_moved_while_reading(
    auth_client, fake_container, monkeypatch
):
    _story(auth_client, fake_container)
    _build(fake_container)
    _verify(fake_container)
    service = fake_container.read_models
    workspace, project = _ids(fake_container)
    assert asyncio.run(service.timeline(workspace, project, "year")) is not None
    assert asyncio.run(service.journey(workspace, project)) is not None

    _moving(service, monkeypatch, settles_after=1)
    assert asyncio.run(service.timeline(workspace, project, "year")) is None
    _moving(service, monkeypatch, settles_after=1)
    assert asyncio.run(service.journey(workspace, project)) is None


def test_a_read_that_hit_its_limit_says_it_was_cut(auth_client, fake_container, monkeypatch):
    w, _ = _story(auth_client, fake_container)
    whole = auth_client.get(f"{w['base']}/story-views/where", params={"entity": w["mara"]})
    assert whole.json()["cut"] is False
    monkeypatch.setattr(read_models, "MAX_RECORDS", 2)
    cut = auth_client.get(f"{w['base']}/story-views/where", params={"entity": w["mara"]})
    assert cut.json()["cut"] is True
    leaning = auth_client.get(
        f"{w['base']}/story-views/dependents", params={"record": f"entity:{w['mara']}"}
    )
    assert leaning.json()["cut"] is True


def test_building_views_leaves_a_story_another_server_holds(auth_client, fake_container):
    _story(auth_client, fake_container)
    consumer = fake_container.read_model_consumer
    asked: list[str] = []

    @asynccontextmanager
    async def busy(*, workspace_id, project_id):
        asked.append(project_id)
        yield False

    assert asyncio.run(consumer.run_once(lock=busy)) == 0
    assert asked
    workspace, project = _ids(fake_container)
    status = asyncio.run(fake_container.read_models.status(None, workspace, project))
    assert all(view.version is None for view in status)

    @asynccontextmanager
    async def free(*, workspace_id, project_id):
        yield True

    assert asyncio.run(consumer.run_once(lock=free)) == 1


def test_one_poll_stops_starting_lines_once_its_budget_is_spent(
    auth_client, fake_container, monkeypatch
):
    w, head = _story(auth_client, fake_container)
    made = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Another way"})
    stayed = auth_client.post(
        f"{w['base']}/facts/{w['in_saltmere']}/close",
        json={"expected_revision": head},
        headers={"X-Story-Branch": made.json()["id"]},
    )
    assert stayed.status_code in (200, 201), stayed.text
    consumer = fake_container.read_model_consumer
    # Both lines wait; with no time to spare, each poll builds just one.
    assert asyncio.run(consumer.run_once(budget_seconds=0)) == 1
    assert asyncio.run(consumer.run_once(budget_seconds=0)) == 1
    assert asyncio.run(consumer.run_once(budget_seconds=0)) == 0
