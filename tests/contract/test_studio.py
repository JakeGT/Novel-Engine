"""The scene studio: prose, drafts from the assistant, checks, and the work behind them (ADR-022)."""

import asyncio

import pytest

from tests.contract.test_context import _setup
from tests.contract.test_god_hand import _world

pytestmark = pytest.mark.contract


def _run(container) -> list:
    return asyncio.run(container.job_runner.run_pending())


def test_write_draft_keep_and_trace_a_scene(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _setup(auth_client, w)
    base = f"{w['base']}/plan/{scene['id']}/studio"

    empty = auth_client.get(base).json()
    assert empty["text_id"] is None and empty["content"] == "" and empty["drafts"] == []

    saved = auth_client.put(f"{base}/text", json={"content": "Tobin waited at Lantern Harbor."})
    assert saved.status_code == 200, saved.text
    first = saved.json()
    assert first["versions"][0]["origin"] == "author"

    fake_container.ai.queue("Tobin watched the water at Lantern Harbor. Mara did not come.")
    started = auth_client.post(f"{base}/drafts", json={"variants": 1, "words": 200})
    assert started.status_code == 202, started.text
    assert started.json()["status"] == "queued"
    done = _run(fake_container)
    assert [job.status.value for job in done] == ["succeeded"], done
    job = auth_client.get(f"{w['base']}/jobs/{started.json()['id']}").json()
    assert job["progress"] == 100 and job["completion_tokens"] > 0

    state = auth_client.get(base).json()
    draft = state["drafts"][0]
    assert draft["content"].startswith("Tobin watched")
    # The secret Tobin does not know was not given to the model.
    sent = fake_container.ai.calls[-1]["messages"]
    assert "Harbor Guild" not in sent[1]["content"]

    kept = auth_client.post(
        f"{w['base']}/drafts/{draft['id']}/keep", json={"base_version_id": first["version_id"]}
    )
    assert kept.status_code == 200, kept.text
    after = kept.json()
    assert after["drafts"] == []
    newest = after["versions"][0]
    assert newest["origin"] == "assistant" and newest["draft_id"] == draft["id"]
    assert newest["manifest_id"] and newest["parent_version_id"] == first["version_id"]


# --------------------------------------------------------------------------- helpers


def _studio(client, w, scene, branch: str | None = None) -> dict:
    got = client.get(f"{w['base']}/plan/{scene['id']}/studio", headers=_in(branch))
    assert got.status_code == 200, got.text
    return got.json()


def _in(branch: str | None) -> dict:
    return {"X-Story-Branch": branch} if branch else {}


def _save(client, w, scene, content: str, base: str | None, branch: str | None = None):
    return client.put(
        f"{w['base']}/plan/{scene['id']}/studio/text",
        json={"content": content, "base_version_id": base},
        headers=_in(branch),
    )


def _draft(client, w, scene, branch: str | None = None, **body):
    return client.post(
        f"{w['base']}/plan/{scene['id']}/studio/drafts",
        json={"variants": 1, "words": 200, **body},
        headers=_in(branch),
    )


def _job(client, w, job_id: str) -> dict:
    got = client.get(f"{w['base']}/jobs/{job_id}")
    assert got.status_code == 200, got.text
    return got.json()


def _scene_world(client, container):
    w = _world(client, container)
    return w, _setup(client, w)


PROSE = "Tobin waited at Lantern Harbor while the tide came in."


# ---------------------------------------------------------------- saving and keeping


def test_stale_words_are_refused_and_nothing_is_kept(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    first = _save(auth_client, w, scene, PROSE, None).json()
    second = _save(auth_client, w, scene, PROSE + " Mara did not come.", first["version_id"])
    assert second.status_code == 200, second.text
    assert len(second.json()["versions"]) == 2

    # Written from the first version, after the second was saved.
    stale = _save(auth_client, w, scene, PROSE + " Gulls.", first["version_id"])
    assert stale.status_code == 409, stale.text
    assert "changed while you were writing" in stale.json()["detail"]
    # Written from nothing, when there is now something, is just as stale.
    blind = _save(auth_client, w, scene, "Something else entirely.", None)
    assert blind.status_code == 409, blind.text
    state = _studio(auth_client, w, scene)
    assert len(state["versions"]) == 2 and state["content"].endswith("Mara did not come.")

    # Keeping a draft over a version the author has not seen is refused too.
    fake_container.ai.queue("Tobin watched the water at Lantern Harbor for a long while.")
    _draft(auth_client, w, scene)
    _run(fake_container)
    draft = _studio(auth_client, w, scene)["drafts"][0]
    kept = auth_client.post(
        f"{w['base']}/drafts/{draft['id']}/keep", json={"base_version_id": first["version_id"]}
    )
    assert kept.status_code == 409, kept.text
    after = _studio(auth_client, w, scene)
    assert len(after["versions"]) == 2 and [d["id"] for d in after["drafts"]] == [draft["id"]]


def test_saving_the_same_words_again_is_refused(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    first = _save(auth_client, w, scene, PROSE, None).json()
    again = _save(auth_client, w, scene, PROSE, first["version_id"])
    assert again.status_code == 400, again.text
    assert again.json()["detail"] == "Nothing has changed, so there is nothing to save."
    blank = _save(auth_client, w, scene, "   \n", first["version_id"])
    assert blank.status_code == 400
    assert len(_studio(auth_client, w, scene)["versions"]) == 1


def test_keeping_a_draft_into_an_empty_scene_makes_one_version_from_the_assistant(
    auth_client, fake_container
):
    w, scene = _scene_world(auth_client, fake_container)
    fake_container.ai.queue("Tobin watched the water at Lantern Harbor. Mara did not come.")
    _draft(auth_client, w, scene)
    _run(fake_container)
    draft = _studio(auth_client, w, scene)["drafts"][0]
    kept = auth_client.post(f"{w['base']}/drafts/{draft['id']}/keep", json={})
    assert kept.status_code == 200, kept.text
    (only,) = kept.json()["versions"]
    assert only["origin"] == "assistant" and only["draft_id"] == draft["id"]
    assert only["manifest_id"] == draft["manifest_id"] and only["parent_version_id"] is None
    # A kept draft cannot be kept again.
    again = auth_client.post(
        f"{w['base']}/drafts/{draft['id']}/keep", json={"base_version_id": only["id"]}
    )
    assert again.status_code == 404


def test_several_variants_make_several_drafts_and_discard_removes_one(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    for word in ("first", "second", "third"):
        fake_container.ai.queue(f"Tobin waited at Lantern Harbor, the {word} way.")
    started = _draft(auth_client, w, scene, variants=3, direction="Keep it quiet.")
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded" and done.result["drafts"] == 3
    drafts = _studio(auth_client, w, scene)["drafts"]
    assert [d["number"] for d in drafts] == [1, 2, 3]
    assert [d["content"].split()[-2] for d in drafts] == ["first", "second", "third"]
    assert all(d["job_id"] == started.json()["id"] for d in drafts)
    assert "Keep it quiet." in fake_container.ai.calls[0]["messages"][1]["content"]

    gone = auth_client.delete(f"{w['base']}/drafts/{drafts[1]['id']}")
    assert gone.status_code == 204
    left = _studio(auth_client, w, scene)["drafts"]
    assert [d["number"] for d in left] == [1, 3]
    assert auth_client.delete(f"{w['base']}/drafts/{drafts[1]['id']}").status_code == 404


def test_draft_requests_outside_the_limits_are_refused(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    for body in ({"variants": 4}, {"variants": 0}, {"words": 100}, {"words": 3001}):
        assert _draft(auth_client, w, scene, **body).status_code == 422, body
    assert _draft(auth_client, w, scene, direction="x" * 2001).status_code == 422
    # Nothing to check yet.
    review = auth_client.post(f"{w['base']}/plan/{scene['id']}/studio/reviews", json={})
    assert review.status_code == 400, review.text
    assert fake_container.jobs.jobs == {}
    assert auth_client.get(f"{w['base']}/jobs/job_not_here").status_code == 404
    assert auth_client.post(f"{w['base']}/jobs/job_not_here/cancel").status_code == 404


# --------------------------------------------------------------------- the jobs


def test_cancelling_a_waiting_job_means_nothing_is_drafted(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    started = _draft(auth_client, w, scene).json()
    cancelled = auth_client.post(f"{w['base']}/jobs/{started['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    body = cancelled.json()
    assert body["status"] == "cancelled" and body["cancel_requested"] is True
    assert body["finished_at"] is not None
    assert _run(fake_container) == []
    assert fake_container.ai.calls == []
    assert _studio(auth_client, w, scene)["drafts"] == []


def test_cancelling_while_drafting_stops_at_the_next_step(auth_client, fake_container, monkeypatch):
    w, scene = _scene_world(auth_client, fake_container)
    started = _draft(auth_client, w, scene, variants=3).json()
    fake_container.ai.queue("Tobin waited at Lantern Harbor, the first way.")
    fake_container.ai.queue("Tobin waited at Lantern Harbor, the second way.")
    ai = fake_container.ai
    original = ai.generate_text

    async def then_cancel(**kwargs):
        answer = await original(**kwargs)
        # The author presses stop while the first draft is being written.
        await fake_container.studio_service.cancel(
            _scope_of(fake_container), w["workspace_id"], w["project_id"], started["id"]
        )
        return answer

    w["workspace_id"] = next(iter(fake_container.jobs.jobs))[0]
    w["project_id"] = next(iter(fake_container.jobs.jobs))[1]
    monkeypatch.setattr(ai, "generate_text", then_cancel)
    (done,) = _run(fake_container)
    assert done.status.value == "cancelled" and done.error_code is None
    assert len(ai.calls) == 1
    # The draft that was finished is kept for the author; nothing after it was written.
    drafts = _studio(auth_client, w, scene)["drafts"]
    assert [d["number"] for d in drafts] == [1]
    job = _job(auth_client, w, started["id"])
    assert job["status"] == "cancelled" and job["completion_tokens"] > 0

    # Cancelling a job that has ended leaves it as it ended.
    fake_container.ai.queue("Tobin waited at Lantern Harbor once more, and the tide rose.")
    other = _draft(auth_client, w, scene).json()
    _run(fake_container)
    late = auth_client.post(f"{w['base']}/jobs/{other['id']}/cancel").json()
    assert late["status"] == "succeeded" and late["cancel_requested"] is False


def _scope_of(container):
    from backend.domain.identity import AccessScope, AuthMode

    (job,) = list(container.jobs.jobs.values())[:1]
    return AccessScope(
        actor_user_id=job.actor_user_id,
        auth_mode=AuthMode.LOCAL,
        workspace_id=job.workspace_id,
        project_id=job.project_id,
    )


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        ("provider", "provider"),
        ("output", "provider_output"),
        ("empty", "provider_output"),
        ("no_provider", "no_provider"),
    ],
)
def test_failures_are_told_in_plain_words_and_keep_only_finished_drafts(
    auth_client, fake_container, failure, code
):
    from backend.domain.errors import CapabilityUnavailableError
    from backend.ports import AIInvalidOutputError, AIProviderError

    w, scene = _scene_world(auth_client, fake_container)
    fake_container.ai.queue("Tobin waited at Lantern Harbor, the only way that worked.")
    fake_container.ai.queue(
        {
            "provider": AIProviderError("upstream 529 overloaded sk-live-secret"),
            "output": AIInvalidOutputError("bad json sk-live-secret"),
            "empty": "```\n```",
            "no_provider": CapabilityUnavailableError("no key sk-live-secret"),
        }[failure]
    )
    started = _draft(auth_client, w, scene, variants=2).json()
    (done,) = _run(fake_container)
    assert done.status.value == "failed" and done.error_code == code
    job = _job(auth_client, w, started["id"])
    assert job["status"] == "failed" and job["error_code"] == code
    assert job["error_message"] and "sk-live-secret" not in job["error_message"]
    assert "529" not in job["error_message"] and "json" not in job["error_message"]
    # The first draft finished before the failure and is the only one there.
    drafts = _studio(auth_client, w, scene)["drafts"]
    assert [d["content"] for d in drafts] == [
        "Tobin waited at Lantern Harbor, the only way that worked."
    ]


def test_the_same_request_key_returns_the_same_job(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    url = f"{w['base']}/plan/{scene['id']}/studio/drafts"
    body = {"variants": 1, "words": 300}
    first = auth_client.post(url, json=body, headers={"Idempotency-Key": "click-1"})
    again = auth_client.post(url, json=body, headers={"Idempotency-Key": "click-1"})
    other = auth_client.post(url, json=body, headers={"Idempotency-Key": "click-2"})
    assert first.status_code == again.status_code == other.status_code == 202
    assert first.json()["id"] == again.json()["id"] != other.json()["id"]
    assert len(fake_container.jobs.jobs) == 2


def test_the_estimate_is_positive_and_asks_no_model(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    url = f"{w['base']}/plan/{scene['id']}/studio/estimate"
    one = auth_client.post(url, json={"variants": 1, "words": 600})
    assert one.status_code == 200, one.text
    single = one.json()
    assert single["prompt_tokens"] > 0 and single["context_words"] > 0
    assert single["completion_tokens"] == 800  # 600 words at four tokens to three words
    assert single["total_tokens"] == single["prompt_tokens"] + single["completion_tokens"]
    three = auth_client.post(url, json={"variants": 3, "words": 600}).json()
    assert three["prompt_tokens"] == 3 * single["prompt_tokens"]
    assert three["completion_tokens"] == 3 * single["completion_tokens"]
    assert fake_container.ai.calls == [] and fake_container.jobs.jobs == {}
    assert auth_client.post(url, json={"variants": 9}).status_code == 422


# ---------------------------------------------------------------------- checking


def test_a_check_keeps_only_notes_that_quote_the_text(auth_client, fake_container):
    from backend.services.scene_checks import CheckNote, CheckNotes

    w, scene = _scene_world(auth_client, fake_container)
    saved = _save(auth_client, w, scene, PROSE, None).json()
    fake_container.ai.queue(
        CheckNotes(
            notes=[
                CheckNote(
                    quote="while the tide came in",
                    problem="The tide went out in the last scene.",
                    suggestion="Let it go out.",
                ),
                CheckNote(quote="a quote that is not there", problem="X."),
                CheckNote(quote="Lantern Harbor", problem="   "),
                CheckNote(quote="", problem="No quote at all."),
            ]
        )
    )
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews", json={"passes": ["continuity"]}
    )
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded"
    # How long the pass took is kept too (CON-001); it varies, so it is only checked to be there.
    assert done.result["passes"]["continuity"].pop("ms") >= 0
    assert done.result == {
        "findings": 1,
        "dropped": 3,
        "passes": {"continuity": {"findings": 1, "dropped": 3, "calls": 1}},
    }
    state = _studio(auth_client, w, scene)
    (note,) = state["assistant"]
    assert note["category"] == "continuity" and note["suggestion"] == "Let it go out."
    assert note["check_pass"] == "continuity"
    assert PROSE[note["start"] : note["end"]] == "while the tide came in"
    # Stored against the version that was checked: a newer version starts clean.
    stored = asyncio.run(
        fake_container.studio_store.findings(
            workspace_id=done.workspace_id,
            project_id=done.project_id,
            target_kind="version",
            target_id=saved["version_id"],
        )
    )
    assert [f.job_id for f in stored] == [done.id]
    newer = _save(auth_client, w, scene, PROSE + " Gulls cried.", saved["version_id"]).json()
    assert newer["assistant"] == []


def test_a_check_of_a_draft_is_kept_with_that_draft_and_goes_with_it(auth_client, fake_container):
    from backend.services.scene_checks import CheckNote, CheckNotes

    w, scene = _scene_world(auth_client, fake_container)
    fake_container.ai.queue("Tobin watched the grey water. Mara did not come.")
    fake_container.ai.queue("Tobin counted the boats at Lantern Harbor until dark.")
    _draft(auth_client, w, scene, variants=2)
    _run(fake_container)
    first, second = _studio(auth_client, w, scene)["drafts"]
    fake_container.ai.queue(CheckNotes(notes=[CheckNote(quote="grey water", problem="Too plain.")]))
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews",
        json={"draft_id": first["id"], "passes": ["voice"]},
    )
    assert started.status_code == 202, started.text
    _run(fake_container)
    first, second = _studio(auth_client, w, scene)["drafts"]
    assert [n["category"] for n in first["assistant"]] == ["voice"]
    assert first["content"][first["assistant"][0]["start"] : first["assistant"][0]["end"]] == (
        "grey water"
    )
    assert second["assistant"] == []
    # The draft that was checked was sent, not another.
    assert "grey water" in fake_container.ai.calls[-1]["messages"][1]["content"]

    auth_client.delete(f"{w['base']}/drafts/{first['id']}")
    assert fake_container.studio_store.assistant_findings == []
    missing = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews", json={"draft_id": first["id"]}
    )
    assert missing.status_code == 404


def test_a_draft_is_checked_or_estimated_only_through_its_own_scene(auth_client, fake_container):
    from tests.contract.test_plan_checks import _add

    w, scene = _scene_world(auth_client, fake_container)
    other = _add(auth_client, w, "scene", "Elsewhere")
    fake_container.ai.queue("Tobin watched the grey water. Mara did not come.")
    _draft(auth_client, w, scene)
    _run(fake_container)
    [draft] = _studio(auth_client, w, scene)["drafts"]
    calls = len(fake_container.ai.calls)

    wrong_review = auth_client.post(
        f"{w['base']}/plan/{other['id']}/studio/reviews", json={"draft_id": draft["id"]}
    )
    assert wrong_review.status_code == 404, wrong_review.text
    wrong_estimate = auth_client.get(
        f"{w['base']}/plan/{other['id']}/studio/check-estimate", params={"draft_id": draft["id"]}
    )
    assert wrong_estimate.status_code == 404, wrong_estimate.text
    assert len(fake_container.ai.calls) == calls

    right = auth_client.get(
        f"{w['base']}/plan/{scene['id']}/studio/check-estimate", params={"draft_id": draft["id"]}
    )
    assert right.status_code == 200, right.text


# ------------------------------------------------------------------------- voice


def test_the_authors_voice_is_kept_trimmed_limited_and_given_to_the_assistant(
    auth_client, fake_container
):
    w, scene = _scene_world(auth_client, fake_container)
    assert auth_client.get(f"{w['base']}/voice").json() == {"notes": ""}
    put = auth_client.put(f"{w['base']}/voice", json={"notes": "  Short sentences. No adverbs. "})
    assert put.status_code == 200, put.text
    assert put.json() == {"notes": "Short sentences. No adverbs."}
    assert auth_client.get(f"{w['base']}/voice").json() == {"notes": "Short sentences. No adverbs."}
    too_long = auth_client.put(f"{w['base']}/voice", json={"notes": "x" * 4001})
    assert too_long.status_code == 422
    assert auth_client.put(f"{w['base']}/voice", json={"notes": "x", "extra": 1}).status_code == 422
    assert auth_client.get(f"{w['base']}/voice").json()["notes"] == "Short sentences. No adverbs."

    fake_container.ai.queue("Tobin waited. The tide came. Mara did not.")
    _draft(auth_client, w, scene)
    _run(fake_container)
    # The notes are material in the request, not part of the rules the assistant follows.
    system, user = fake_container.ai.calls[-1]["messages"]
    assert "Short sentences. No adverbs." in user["content"]
    assert "Short sentences. No adverbs." not in system["content"]


# ---------------------------------------------------------------------- what-ifs


def test_in_a_what_if_the_scene_is_copied_on_first_change_and_drafts_stay_there(
    auth_client, fake_container
):
    w, scene = _scene_world(auth_client, fake_container)
    main = _save(auth_client, w, scene, PROSE, None).json()
    what_if = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Mara comes"}).json()
    branch = what_if["id"]

    # At first the what-if reads the main story's words.
    inside = _studio(auth_client, w, scene, branch)
    assert inside["text_id"] == main["text_id"] and inside["content"] == PROSE
    # Saving nothing new there is refused and copies nothing.
    same = _save(auth_client, w, scene, PROSE, main["version_id"], branch)
    assert same.status_code == 400
    assert _studio(auth_client, w, scene, branch)["text_id"] == main["text_id"]

    changed = _save(auth_client, w, scene, PROSE + " Mara came.", main["version_id"], branch)
    assert changed.status_code == 200, changed.text
    copy = changed.json()
    assert copy["text_id"] != main["text_id"] and copy["content"].endswith("Mara came.")
    assert [v["origin"] for v in copy["versions"]] == ["author", "author"]
    # The main story's prose is untouched.
    still = _studio(auth_client, w, scene)
    assert still["text_id"] == main["text_id"] and still["content"] == PROSE
    assert len(still["versions"]) == 1

    # A draft asked for in the what-if is seen only there.
    fake_container.ai.queue("Mara came along the quay to Lantern Harbor, and Tobin stood.")
    started = _draft(auth_client, w, scene, branch)
    assert started.status_code == 202, started.text
    # The work shows where it was asked for, not on the main story.
    assert started.json()["id"] in {j["id"] for j in _studio(auth_client, w, scene, branch)["jobs"]}
    assert started.json()["id"] not in {j["id"] for j in _studio(auth_client, w, scene)["jobs"]}
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    (draft,) = _studio(auth_client, w, scene, branch)["drafts"]
    assert _studio(auth_client, w, scene)["drafts"] == []
    # It cannot be kept or thrown away from the main story.
    wrong = auth_client.post(
        f"{w['base']}/drafts/{draft['id']}/keep", json={"base_version_id": main["version_id"]}
    )
    assert wrong.status_code == 404
    assert auth_client.delete(f"{w['base']}/drafts/{draft['id']}").status_code == 404
    kept = auth_client.post(
        f"{w['base']}/drafts/{draft['id']}/keep",
        json={"base_version_id": copy["version_id"]},
        headers=_in(branch),
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["content"].startswith("Mara came along")
    assert _studio(auth_client, w, scene)["content"] == PROSE


# -------------------------------------------------------------------- the archive


@pytest.mark.asyncio
async def test_scene_links_voice_and_where_words_came_from_travel_with_the_story(
    auth_client, fake_container
):
    w, scene = _scene_world(auth_client, fake_container)
    first = _save(auth_client, w, scene, PROSE, None).json()
    auth_client.put(f"{w['base']}/voice", json={"notes": "Short sentences."})
    fake_container.ai.queue("Tobin watched the water at Lantern Harbor. Mara did not come.")
    _draft(auth_client, w, scene)
    await fake_container.job_runner.run_pending()
    draft = _studio(auth_client, w, scene)["drafts"][0]
    kept = auth_client.post(
        f"{w['base']}/drafts/{draft['id']}/keep", json={"base_version_id": first["version_id"]}
    ).json()

    archive = auth_client.post(f"{w['base']}/archive/export").json()
    writing = archive["writing"]
    assert writing["scenes"] == [{"plan_id": scene["id"], "text_id": kept["text_id"]}]
    assert writing["voice"] == "Short sentences."
    (text,) = [t for t in writing["texts"] if t["id"] == kept["text_id"]]
    assert text["kind"] == "scene"
    assert [v.get("origin", "author") for v in text["versions"]] == ["author", "assistant"]

    service = fake_container.archive_service
    workspace_id = archive["project"]["workspace_id"]
    await service.import_project(workspace_id, "restored", archive)
    root = await fake_container.journal.ensure_root_branch(
        workspace_id=workspace_id, project_id="restored"
    )
    studio = fake_container.studio_store
    assert (
        await studio.manuscript(
            workspace_id=workspace_id,
            project_id="restored",
            branch_id=root.id,
            plan_id=scene["id"],
        )
        == kept["text_id"]
    )
    assert await studio.voice(workspace_id=workspace_id, project_id="restored") == (
        "Short sentences."
    )
    versions = await fake_container.sources.list_versions(
        workspace_id=workspace_id, project_id="restored", source_id=kept["text_id"]
    )
    assert [v.origin.value for v in versions] == ["author", "assistant"]
    assert versions[1].parent_version_id == versions[0].id


# ------------------------------------------------------------------ hosted access


def _hosted_scene(client, container):
    from tests.contract.test_workspace_scope import (
        _create_project,
        _create_workspace,
        _hosted_login,
        _publish_schema,
    )

    csrf = _hosted_login(client, container)
    workspace_id = _create_workspace(client, csrf, "studio")
    project_id = _create_project(client, csrf, workspace_id, "novel")
    _publish_schema(client, csrf, workspace_id, project_id)
    headers = {"X-CSRF-Token": csrf, "X-Workspace-Id": workspace_id}
    base = f"/api/v1/projects/{project_id}"
    made = client.post(
        f"{base}/plan",
        json={"kind": "scene", "title": "The Docks", "summary": "", "expected_revision": 0},
        headers=headers,
    )
    assert made.status_code == 201, made.text
    (user_id,) = [uid for (ws, uid) in container.tenancy.memberships if ws == workspace_id]
    return base, made.json(), headers, workspace_id, user_id


def _demote(container, workspace_id: str, user_id: str) -> None:
    from backend.domain.tenancy import Role

    # Another owner first, so the last owner may become a reader.
    asyncio.run(
        container.tenancy.add_membership(
            workspace_id=workspace_id, user_id="usr_other_owner", role=Role.OWNER
        )
    )
    asyncio.run(
        container.tenancy.set_role(workspace_id=workspace_id, user_id=user_id, role=Role.VIEWER)
    )


def test_hosted_without_a_key_the_job_fails_saying_so(hosted_client, hosted_container):
    base, scene, headers, _, _ = _hosted_scene(hosted_client, hosted_container)
    started = hosted_client.post(
        f"{base}/plan/{scene['id']}/studio/drafts", json={"words": 200}, headers=headers
    )
    assert started.status_code == 202, started.text
    (done,) = _run(hosted_container)
    assert done.status.value == "failed" and done.error_code == "no_provider"
    assert "No AI key" in done.error_message
    assert hosted_container.ai.calls == []


def test_hosted_a_reader_can_look_but_not_write_or_draft(hosted_client, hosted_container):
    base, scene, headers, workspace_id, user_id = _hosted_scene(hosted_client, hosted_container)
    studio = f"{base}/plan/{scene['id']}/studio"
    saved = hosted_client.put(f"{studio}/text", json={"content": PROSE}, headers=headers)
    assert saved.status_code == 200, saved.text
    version = saved.json()["version_id"]
    # A job asked for while the author could write...
    waiting = hosted_client.post(f"{studio}/drafts", json={"words": 200}, headers=headers).json()

    _demote(hosted_container, workspace_id, user_id)
    assert hosted_client.get(studio, headers=headers).status_code == 200
    assert hosted_client.get(f"{base}/voice", headers=headers).status_code == 200
    for response in (
        hosted_client.put(
            f"{studio}/text",
            json={"content": PROSE + " More.", "base_version_id": version},
            headers=headers,
        ),
        hosted_client.post(f"{studio}/drafts", json={"words": 200}, headers=headers),
        hosted_client.post(f"{studio}/reviews", json={}, headers=headers),
        hosted_client.put(f"{base}/voice", json={"notes": "Mine now."}, headers=headers),
        hosted_client.post(f"{base}/jobs/{waiting['id']}/cancel", headers=headers),
    ):
        assert response.status_code == 403, response.text

    # ...is checked again when it runs, and stops because the author can no longer write.
    (done,) = _run(hosted_container)
    assert done.id == waiting["id"]
    assert done.status.value == "failed" and done.error_code == "not_allowed"
    assert done.error_message == "You no longer have permission to do this."
    assert hosted_container.ai.calls == []
    assert len(hosted_container.jobs.jobs) == 1


def test_a_scenes_text_is_changed_and_kept_only_through_the_studio(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    first = _save(auth_client, w, scene, PROSE, None).json()
    # The texts page cannot remove a scene's words or change them behind the studio's back.
    removed = auth_client.delete(f"{w['base']}/texts/{first['text_id']}")
    assert removed.status_code == 400 and "scene" in removed.json()["detail"].lower()
    after = _studio(auth_client, w, scene)
    assert after["text_id"] == first["text_id"] and after["content"] == PROSE


# ----------------------------------------------- what the assistant reads, by line


def _writing(client, w, scene, branch: str | None = None) -> list[dict]:
    got = client.get(
        f"{w['base']}/plan/{scene['id']}/context",
        params={"budget": 12000},
        headers=_in(branch),
    )
    assert got.status_code == 200, got.text
    return [item for item in got.json()["items"] if item["section"] == "writing"]


def test_a_what_ifs_scene_words_never_shape_the_main_storys_drafts(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    main = _save(auth_client, w, scene, PROSE, None).json()
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Mara comes"}).json()["id"]
    changed = _save(
        auth_client,
        w,
        scene,
        "Tobin met Mara at Lantern Harbor under the copper lanterns.",
        main["version_id"],
        branch,
    )
    assert changed.status_code == 200, changed.text

    # The main story reads only its own words: not as a passage, not as a voice sample.
    main_writing = _writing(auth_client, w, scene)
    assert main_writing and not any("copper lanterns" in i["text"] for i in main_writing)
    assert any("tide came in" in item["text"] for item in main_writing)
    # The what-if reads its own, and not the main story's.
    inside = _writing(auth_client, w, scene, branch)
    assert any("copper lanterns" in item["text"] for item in inside)
    assert not any("tide came in" in item["text"] for item in inside)

    fake_container.ai.queue("Tobin watched the water at Lantern Harbor. Mara did not come.")
    _draft(auth_client, w, scene)
    _run(fake_container)
    assert "copper lanterns" not in fake_container.ai.calls[-1]["messages"][1]["content"]


def test_the_voice_sample_is_not_the_scenes_own_words_when_there_is_other_writing(
    auth_client, fake_container
):
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)

    def voice() -> list[dict]:
        return [
            item
            for item in _writing(auth_client, w, scene)
            if item["reason"].startswith("A sample of your voice")
        ]

    # With only the scene's own words, they are the sample (or already given).
    alone = voice()
    assert all("tide came in" in item["text"] for item in alone)

    made = auth_client.post(
        f"{w['base']}/texts",
        json={"title": "Notes", "content": "The gulls argued over nothing at all."},
    )
    assert made.status_code == 201, made.text
    _save(
        auth_client,
        w,
        scene,
        PROSE + " The gulls were loud.",
        _studio(auth_client, w, scene)["version_id"],
    )
    (sample,) = voice()
    assert sample["text"] == "From “Notes”: The gulls argued over nothing at all."


# ------------------------------------------------- limits, stops and taking over


def _ids(container) -> tuple[str, str]:
    workspace_id, project_id, _ = next(iter(container.jobs.jobs))
    return workspace_id, project_id


def test_a_scene_holds_only_so_many_waiting_drafts(auth_client, fake_container):
    from backend.services.studio_service import MAX_WAITING_DRAFTS

    w, scene = _scene_world(auth_client, fake_container)
    for n in range(MAX_WAITING_DRAFTS - 1):
        fake_container.ai.queue(f"Tobin waited at Lantern Harbor, version {n}.")
    for variants in (3, 3, 2):
        assert _draft(auth_client, w, scene, variants=variants).status_code == 202
        _run(fake_container)
    assert len(_studio(auth_client, w, scene)["drafts"]) == MAX_WAITING_DRAFTS - 1 == 8
    # One more fits; two more would not, and nothing is queued for them.
    refused = _draft(auth_client, w, scene, variants=2)
    assert refused.status_code == 400, refused.text
    assert "Keep or discard some first" in refused.json()["detail"]
    jobs_before = len(fake_container.jobs.jobs)
    # Drafts on the way count too: a waiting job for one more fills the scene.
    assert _draft(auth_client, w, scene, variants=1).status_code == 202
    on_the_way = _draft(auth_client, w, scene, variants=1)
    assert on_the_way.status_code == 400 and "being written" in on_the_way.json()["detail"]
    assert len(fake_container.jobs.jobs) == jobs_before + 1

    # Discarding makes room again.
    drafts = _studio(auth_client, w, scene)["drafts"]
    for draft in drafts[:2]:
        assert auth_client.delete(f"{w['base']}/drafts/{draft['id']}").status_code == 204
    assert _draft(auth_client, w, scene, variants=1).status_code == 202


def test_a_person_may_have_only_three_jobs_waiting_at_once(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    for n in range(3):
        assert _draft(auth_client, w, scene).status_code == 202, n
    refused = _draft(auth_client, w, scene)
    assert refused.status_code == 429, refused.text
    assert "Wait for one to finish" in refused.json()["detail"]
    assert len(fake_container.jobs.jobs) == 3


def test_asking_again_with_the_same_key_is_never_refused_for_its_own_job(
    auth_client, fake_container
):
    w, scene = _scene_world(auth_client, fake_container)
    url = f"{w['base']}/plan/{scene['id']}/studio/drafts"
    body = {"variants": 3, "words": 300}
    ids = [
        auth_client.post(url, json=body, headers={"Idempotency-Key": f"k{n}"}).json()["id"]
        for n in range(3)
    ]
    # The scene is full of drafts on the way and the person is at the job limit, yet the
    # same request again gets its job back.
    again = auth_client.post(url, json=body, headers={"Idempotency-Key": "k0"})
    assert again.status_code == 202 and again.json()["id"] == ids[0]


def test_a_stop_asked_for_during_the_last_draft_is_honoured(
    auth_client, fake_container, monkeypatch
):
    w, scene = _scene_world(auth_client, fake_container)
    started = _draft(auth_client, w, scene, variants=1).json()
    fake_container.ai.queue("Tobin waited at Lantern Harbor, and nobody came.")
    ai = fake_container.ai
    original = ai.generate_text

    async def then_cancel(**kwargs):
        answer = await original(**kwargs)
        await fake_container.studio_service.cancel(
            _scope_of(fake_container), *_ids(fake_container), started["id"]
        )
        return answer

    monkeypatch.setattr(ai, "generate_text", then_cancel)
    (done,) = _run(fake_container)
    assert done.status.value == "cancelled"
    # The draft that was written is still there for the author to keep or discard.
    assert [d["number"] for d in _studio(auth_client, w, scene)["drafts"]] == [1]


def test_a_check_stopped_while_the_assistant_reads_stores_nothing(
    auth_client, fake_container, monkeypatch
):
    from backend.services.scene_checks import CheckNote, CheckNotes

    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    fake_container.ai.queue(
        CheckNotes(notes=[CheckNote(quote="while the tide came in", problem="Tide.")])
    )
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews", json={"passes": ["continuity"]}
    ).json()
    ai = fake_container.ai
    original = ai.generate_structured

    async def then_cancel(**kwargs):
        answer = await original(**kwargs)
        await fake_container.studio_service.cancel(
            _scope_of(fake_container), *_ids(fake_container), started["id"]
        )
        return answer

    monkeypatch.setattr(ai, "generate_structured", then_cancel)
    (done,) = _run(fake_container)
    assert done.status.value == "cancelled"
    assert _studio(auth_client, w, scene)["assistant"] == []


def test_a_job_put_back_when_the_app_stopped_keeps_the_drafts_it_made(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    started = _draft(auth_client, w, scene, variants=3).json()
    fake_container.ai.queue("Tobin waited at Lantern Harbor, the first way.")
    # The app stops while the second draft is being written.
    fake_container.ai.queue(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        _run(fake_container)
    waiting = _job(auth_client, w, started["id"])
    assert waiting["status"] == "queued"
    assert [d["number"] for d in _studio(auth_client, w, scene)["drafts"]] == [1]

    fake_container.ai.queue("Tobin waited at Lantern Harbor, the second way.")
    fake_container.ai.queue("Tobin waited at Lantern Harbor, the third way.")
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded" and done.result["drafts"] == 3
    drafts = _studio(auth_client, w, scene)["drafts"]
    # The first draft was not written again: three answers were used for three drafts.
    assert [d["number"] for d in drafts] == [1, 2, 3]
    assert [d["content"].split()[-2] for d in drafts] == ["first", "second", "third"]
    assert fake_container.ai.remaining == 0


def test_each_call_is_recorded_once_and_limits_are_checked_before_each_draft(
    auth_client, fake_container
):
    from backend.ports import AIUsage, TextResponse

    w, scene = _scene_world(auth_client, fake_container)
    url = f"{w['base']}/plan/{scene['id']}/studio/estimate"
    estimate = auth_client.post(url, json={"variants": 2, "words": 200}).json()
    limit = fake_container.usage_service._limit
    # Enough for the two drafts as estimated, and no more.
    limit.period_token_limit = estimate["total_tokens"] + 10
    fake_container.ai.queue(
        TextResponse(
            text="Tobin waited at Lantern Harbor, and the first draft ran long.",
            model="fake",
            usage=AIUsage(prompt_tokens=estimate["total_tokens"], completion_tokens=5),
        )
    )
    fake_container.ai.queue("Never written.")
    started = _draft(auth_client, w, scene, variants=2).json()
    (done,) = _run(fake_container)
    # The first draft used more than expected, so the second was refused before sending.
    assert done.status.value == "failed" and done.error_code == "refused"
    assert "allowance" in done.error_message
    assert fake_container.ai.remaining == 1
    keys = [r.idempotency_key for r in fake_container.usage.records]
    assert keys == [f"{started['id']}:1:1"]
    assert [d["number"] for d in _studio(auth_client, w, scene)["drafts"]] == [1]

    # Asked for when the allowance is already spent, a draft is refused at once.
    refused = _draft(auth_client, w, scene, variants=1)
    assert refused.status_code == 429, refused.text
