"""Readiness reports how far behind the passage index and the media in the search index
are (CTX-005, OPS-007), in the same shape as every other optional part."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app

pytestmark = pytest.mark.contract


def test_ready_reports_the_passage_waiting_list(fake_container) -> None:
    app = create_app(container=fake_container)
    with TestClient(app) as client:
        body = client.get("/health/ready").json()
    passages = body["checks"]["passage_index"]
    assert passages == {
        "ok": True,
        "optional": True,
        "waiting": 0,
        "oldest_waiting_seconds": None,
        "failing": 0,
    }
    assert body["status"] == "ready"


def test_writing_waiting_too_long_degrades_with_counts_only(fake_container) -> None:
    queue = fake_container.passage_index._queue  # noqa: SLF001 - the waiting list itself
    asyncio.run(
        queue.request(workspace_id="ws_secret", project_id="prj_secret", source_id="src_mara")
    )
    queue.rows[("ws_secret", "prj_secret", "src_mara")]["first"] -= 20 * 60
    queue.rows[("ws_secret", "prj_secret", "src_mara")]["attempts"] = 2
    app = create_app(container=fake_container)
    with TestClient(app) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    passages = body["checks"]["passage_index"]
    assert body["status"] == "degraded" and passages["ok"] is False
    assert passages["waiting"] == 1 and passages["failing"] == 1
    assert passages["oldest_waiting_seconds"] >= 20 * 60
    assert (
        "20 minutes" in passages["detail"] and "The writing itself is safe." in passages["detail"]
    )
    # Never which story or text.
    assert "secret" not in response.text and "src_mara" not in response.text


def test_a_hosted_caller_without_the_token_sees_only_whether_it_is_up(hosted_container) -> None:
    app = create_app(container=hosted_container)
    with TestClient(app) as client:
        body = client.get("/health/ready").json()
    assert body["checks"]["passage_index"] == {"ok": True, "optional": True}


def test_the_search_index_reports_media_still_to_reach_it(fake_container) -> None:
    from types import SimpleNamespace

    from backend.services.embedding_index import IndexStatus

    async def status(*, with_lag: bool = True) -> IndexStatus:
        return IndexStatus(
            configured={},
            active={"id": "gen_1", "model": "stand-in", "dimension": 8, "rows": 4},
            building=None,
            searchable=True,
            reason=None,
            lag={"documents": 0, "captions": 2, "pictures": 3, "projects": 1, "total": 5},
        )

    fake_container.embedding_index_service = SimpleNamespace(status=status, embedder=object())
    app = create_app(container=fake_container)
    with TestClient(app) as client:
        check = client.get("/health/ready").json()["checks"]["search_index"]
    assert check["lag"] == 5 and check["caption_lag"] == 2 and check["picture_lag"] == 3
