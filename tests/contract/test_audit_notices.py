"""Telling the author when a scheduled audit finishes, and an assistant audit over only the
scenes a what-if wrote differently (CON-011)."""

from __future__ import annotations

import pytest

from backend.services.scene_checks import CheckNote
from tests.contract.test_studio import _save, _studio
from tests.contract.test_voice_and_audits import (
    FIRST,
    _book,
    _note_answer,
    _prose,
    _run,
    _tick,
)

pytestmark = pytest.mark.contract


def _schedule(client, w, beat, passes: list[str], most: int) -> dict:
    saved = client.put(
        f"{w['base']}/audit-schedules",
        json={"plan_id": beat["id"], "passes": passes, "every_days": 7, "max_tokens": most},
    )
    assert saved.status_code == 200, saved.text
    return saved.json()


def test_a_finished_scheduled_audit_leaves_a_notice_until_it_is_marked_read(
    auth_client, fake_container
):
    w, beat, _scenes = _book(auth_client, fake_container, drifting=False)
    # An audit the author starts themselves is on its page already: no notice.
    started = auth_client.post(f"{w['base']}/audits", json={"plan_id": beat["id"], "passes": []})
    assert started.status_code == 202
    _run(fake_container)
    assert auth_client.get(f"{w['base']}/notices").json()["items"] == []

    _schedule(auth_client, w, beat, [], 0)
    _tick(fake_container, days=7)
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded"
    (notice,) = auth_client.get(f"{w['base']}/notices").json()["items"]
    assert notice["job_id"] == done.id and notice["status"] == "succeeded"
    assert notice["scenes"] == 4 and notice["notes"] == 0 and notice["total_tokens"] == 0
    assert notice["part"] == "“The wait”" and notice["what_if"] is None
    assert notice["sentence"].startswith("Your scheduled audit of “The wait” finished.")
    assert "It read 4 scenes" in notice["sentence"] and "It used no tokens." in notice["sentence"]
    assert notice["seen"] is False
    # One audit's notice, for its own page.
    only = auth_client.get(f"{w['base']}/notices", params={"job_id": done.id}).json()["items"]
    assert [item["id"] for item in only] == [notice["id"]]

    # Told once, even if the end is seen again.
    import asyncio

    asyncio.run(fake_container.audit_notices.job_finished(done))
    assert len(auth_client.get(f"{w['base']}/notices").json()["items"]) == 1

    marked = auth_client.post(f"{w['base']}/notices/{notice['id']}/seen")
    assert marked.status_code == 204
    assert auth_client.get(f"{w['base']}/notices", params={"unseen": True}).json()["items"] == []
    (kept,) = auth_client.get(f"{w['base']}/notices").json()["items"]
    assert kept["seen"] is True
    assert auth_client.post(f"{w['base']}/notices/ntc_missing/seen").status_code == 404


def test_a_notice_says_what_the_assistant_found_and_what_it_used(auth_client, fake_container):
    w, beat, _scenes = _book(auth_client, fake_container, drifting=False)
    _schedule(auth_client, w, beat, ["clarity"], 500_000)
    for index in range(4):
        fake_container.ai.queue(
            _note_answer(CheckNote(quote="the gulls", problem=f"Gulls again ({index})."))
        )
    _tick(fake_container, days=7)
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done.error_message
    (notice,) = auth_client.get(f"{w['base']}/notices").json()["items"]
    assert notice["notes"] == 4 and notice["total_tokens"] == done.prompt_tokens + (
        done.completion_tokens
    )
    assert notice["total_tokens"] > 0
    assert "found 4 notes from the assistant" in notice["sentence"]
    assert f"It used {notice['total_tokens']:,} tokens" in notice["sentence"]
    # No words of the story are kept on it.
    stored = fake_container.audit_notices._store.notices  # noqa: SLF001
    assert "gulls" not in repr(stored).lower() and "the wait" not in repr(stored).lower()


def test_the_assistant_checks_only_the_scenes_a_what_if_wrote_differently(
    auth_client, fake_container
):
    w, _beat, scenes = _book(auth_client, fake_container, drifting=False)
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Mara stays away"}).json()[
        "id"
    ]
    url = f"{w['base']}/what-ifs/{branch}/audit/changed"
    same = auth_client.get(f"{url}/estimate", params={"passes": ["clarity"]}).json()
    assert same["written"] == 0
    refused = auth_client.post(url, json={"passes": ["clarity"]})
    assert refused.status_code == 400 and "nothing to audit" in refused.text

    current = _studio(auth_client, w, scenes[1], branch)["version_id"]
    assert _save(auth_client, w, scenes[1], _prose(FIRST), current, branch).status_code == 200
    estimate = auth_client.get(f"{url}/estimate", params={"passes": ["clarity"]})
    assert estimate.status_code == 200, estimate.text
    found = estimate.json()
    assert [s["title"] for s in found["scenes"]] == ["Two"] and found["written"] == 1
    assert found["total_tokens"] > 0
    assert found["title"] == "The scenes written differently in this what-if"
    # The checks without the assistant are on the comparison page already.
    assert auth_client.post(url, json={"passes": []}).status_code == 422

    fake_container.ai.queue(_note_answer(CheckNote(quote="I walk", problem="Told with I.")))
    started = auth_client.post(url, json={"passes": ["clarity"]})
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done.error_message
    assert len(fake_container.ai.calls) == 1
    # Read in the what-if, from the main story's side too.
    report = auth_client.get(f"{w['base']}/audits/{done.id}").json()
    assert report["title"] == "The scenes written differently in this what-if"
    assert [scene["title"] for scene in report["scenes"]] == ["Two"]
    assert report["scenes"][0]["current"] is True
    assert [note["message"] for note in report["scenes"][0]["notes"]] == ["Told with I."]
    listed = auth_client.get(
        f"{w['base']}/audits",
        params={"plan_id": "what-if-changes"},
        headers={"X-Story-Branch": branch},
    ).json()["items"]
    assert [job["id"] for job in listed] == [done.id]
    assert (
        auth_client.get(f"{w['base']}/what-ifs/br_missing/audit/changed/estimate").status_code
        == 404
    )


def test_unread_notices_older_than_the_newest_twenty_read_ones_still_show(
    auth_client, fake_container
):
    from dataclasses import replace

    from backend.domain.identity import utcnow
    from backend.domain.notices import MAX_LISTED

    w, beat, _scenes = _book(auth_client, fake_container, drifting=False)
    _schedule(auth_client, w, beat, [], 0)
    _tick(fake_container, days=7)
    _run(fake_container)
    store = fake_container.audit_notices._store  # noqa: SLF001
    (unread,) = store.notices
    # Twenty newer notices, all read, after the one still unread.
    for n in range(MAX_LISTED):
        store.notices.append(
            replace(unread, id=f"ntc_{n}", job_id=f"job_{n}", created_at=utcnow(), seen_at=utcnow())
        )
    found = auth_client.get(f"{w['base']}/notices", params={"unseen": True}).json()["items"]
    assert [item["id"] for item in found] == [unread.id]


def test_a_scheduled_audit_stopped_while_waiting_leaves_a_notice(auth_client, fake_container):
    w, beat, _scenes = _book(auth_client, fake_container, drifting=False)
    _schedule(auth_client, w, beat, [], 0)
    _tick(fake_container, days=7)
    (waiting,) = [
        job
        for job in fake_container.job_runner._store.jobs.values()  # noqa: SLF001
        if job.payload.get("schedule_id")
    ]
    stopped = auth_client.post(f"{w['base']}/jobs/{waiting.id}/cancel")
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["status"] == "cancelled"
    assert _run(fake_container) == []  # the runner never sees it
    (notice,) = auth_client.get(f"{w['base']}/notices").json()["items"]
    assert notice["job_id"] == waiting.id and notice["status"] == "cancelled"
    # Stopped again: still one notice.
    auth_client.post(f"{w['base']}/jobs/{waiting.id}/cancel")
    assert len(auth_client.get(f"{w['base']}/notices").json()["items"]) == 1
