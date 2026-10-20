"""How much the assistant does and spends on its own (CON-009, OPS-002, AST-EXP-011).

Checking profiles start checks through the job queue only after the author saves or keeps
a draft, within an allowance per scene and every other limit; a request above the size the
author set is sent only once confirmed; a conversation stops at its budget.
"""

import asyncio
from dataclasses import replace

import pytest

from backend.domain.check_passes import PASS_ORDER, CheckPass
from backend.domain.jobs import JobStatus
from backend.domain.usage import UsageRecord
from backend.ports import AIUsage, StructuredResponse
from backend.services.canon_change_workflow import ChangePlan
from backend.services.scene_checks import CheckNotes
from tests.contract.test_god_hand import _world
from tests.contract.test_studio import PROSE, _draft, _save, _scene_world, _studio

pytestmark = pytest.mark.contract


def _run(container) -> list:
    return asyncio.run(container.job_runner.run_pending())


def _settings(client, w, **body):
    response = client.put(f"{w['base']}/assistant-settings", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _keep_a_draft(client, container, w, scene, base: str | None) -> dict:
    container.ai.queue("Tobin watched the water at Lantern Harbor. Mara did not come.")
    assert _draft(client, w, scene).status_code == 202
    _run(container)
    draft = _studio(client, w, scene)["drafts"][0]
    kept = client.post(f"{w['base']}/drafts/{draft['id']}/keep", json={"base_version_id": base})
    assert kept.status_code == 200, kept.text
    return kept.json()


def _queue_check(container, tokens: int = 100) -> None:
    """Answers for one check with every pass, one call each, using ``tokens`` in all.
    The pictures pass makes no call on a scene without pictures."""
    calls = [item for item in PASS_ORDER if item is not CheckPass.PICTURES]
    share, rest = divmod(tokens, len(calls))
    for index, _ in enumerate(calls):
        container.ai.queue(
            StructuredResponse(
                parsed=CheckNotes(notes=[]),
                model="fake",
                usage=AIUsage(
                    prompt_tokens=share + (rest if index == 0 else 0), completion_tokens=0
                ),
            )
        )


# ------------------------------------------------------------------ the settings


def test_a_story_checks_only_when_asked_until_the_author_chooses(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    got = auth_client.get(f"{w['base']}/assistant-settings").json()
    assert got["check_profile"] == "on_request"
    assert got["confirm_above_tokens"] is None and got["conversation_tokens"] is None

    first = _save(auth_client, w, scene, PROSE, None).json()
    assert first["checking"]["profile"] == "on_request"
    assert first["automatic"] is None
    kept = _keep_a_draft(auth_client, fake_container, w, scene, first["version_id"])
    # Nothing was started on the author's behalf; the checks without a model still ran.
    assert kept["automatic"] is None
    assert all(not job["automatic"] for job in kept["jobs"])
    assert isinstance(kept["checks"], list)

    chosen = _settings(auth_client, w, check_profile="balanced", scene_tokens=50_000)
    assert chosen["check_profile"] == "balanced" and chosen["scene_tokens"] == 50_000
    refused = auth_client.put(
        f"{w['base']}/assistant-settings", json={"check_profile": "always", "scene_tokens": 10}
    )
    assert refused.status_code == 422


def test_balanced_checks_a_kept_draft_and_not_a_save(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _settings(auth_client, w, check_profile="balanced")
    first = _save(auth_client, w, scene, PROSE, None).json()
    assert first["automatic"] is None

    kept = _keep_a_draft(auth_client, fake_container, w, scene, first["version_id"])
    assert kept["automatic"] == "started"
    check = next(job for job in kept["jobs"] if job["kind"] == "scene_review")
    assert check["automatic"] is True and check["status"] == "queued"

    _queue_check(fake_container, 900)
    done = _run(fake_container)
    assert [job.status for job in done] == [JobStatus.SUCCEEDED]
    # The check read the kept draft, now the scene's text.
    sent = fake_container.ai.calls[-1]["messages"][1]["content"]
    assert "Mara did not come." in sent
    state = _studio(auth_client, w, scene)
    assert state["checking"]["spent_tokens"] == 900


def test_thorough_checks_after_saving_at_most_once_in_a_while(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _settings(auth_client, w, check_profile="thorough", check_minutes=15)
    first = _save(auth_client, w, scene, PROSE, None).json()
    assert first["automatic"] == "started"

    # Still running: the next save waits rather than queueing a second check.
    second = _save(auth_client, w, scene, PROSE + " Gulls.", first["version_id"]).json()
    assert second["automatic"] == "busy"
    _queue_check(fake_container)
    _run(fake_container)

    third = _save(auth_client, w, scene, PROSE + " Gulls cried.", second["version_id"]).json()
    assert third["automatic"] == "too_soon"
    assert third["checking"]["next_after_save"] is not None


def test_a_scene_stops_checking_on_its_own_at_its_allowance(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _settings(auth_client, w, check_profile="balanced", scene_tokens=15_000)
    first = _save(auth_client, w, scene, PROSE, None).json()
    kept = _keep_a_draft(auth_client, fake_container, w, scene, first["version_id"])
    assert kept["automatic"] == "started"
    _queue_check(fake_container, 14_900)
    _run(fake_container)

    again = _keep_a_draft(auth_client, fake_container, w, scene, kept["version_id"])
    assert again["automatic"] == "scene_allowance"
    assert again["checking"]["spent_tokens"] == 14_900
    # Asking by hand is still the author's choice.
    asked = auth_client.post(f"{w['base']}/plan/{scene['id']}/studio/reviews", json={})
    assert asked.status_code == 202, asked.text


def test_automatic_checks_respect_the_allowance_and_the_job_cap(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _settings(auth_client, w, check_profile="thorough", check_minutes=5)
    first = _save(auth_client, w, scene, PROSE, None).json()
    started = next(job for job in first["jobs"] if job["automatic"])
    stored = asyncio.run(
        fake_container.jobs.get(
            workspace_id=_workspace(fake_container),
            project_id=w["base"].rsplit("/", 1)[1],
            job_id=started["id"],
        )
    )
    assert stored is not None
    # Three pieces of work under way elsewhere in the story fill the person's cap.
    for number in range(3):
        asyncio.run(
            fake_container.jobs.create(
                replace(
                    stored,
                    id=f"job_elsewhere_{number}",
                    payload={"plan_id": "pln_other"},
                    idempotency_key="",
                )
            )
        )
    asyncio.run(
        fake_container.jobs.request_cancel(
            workspace_id=stored.workspace_id, project_id=stored.project_id, job_id=stored.id
        )
    )
    capped = _save(auth_client, w, scene, PROSE + " Gulls.", first["version_id"]).json()
    assert capped["automatic"] == "job_cap"

    for number in range(3):
        asyncio.run(
            fake_container.jobs.request_cancel(
                workspace_id=stored.workspace_id,
                project_id=stored.project_id,
                job_id=f"job_elsewhere_{number}",
            )
        )
    # The day's allowance is used up: nothing is started, and the save still succeeds.
    asyncio.run(
        fake_container.usage.record(
            UsageRecord(
                id="use_big",
                workspace_id=stored.workspace_id,
                actor_user_id=stored.actor_user_id,
                provider="openai",
                model="fake",
                requested_route="fake",
                prompt_tokens=2_000_000,
            )
        )
    )
    spent = _save(auth_client, w, scene, PROSE + " Gulls cried.", capped["version_id"]).json()
    assert spent["automatic"] == "allowance"
    assert spent["versions"][0]["number"] == 3


def test_automatic_checks_need_a_model(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _settings(auth_client, w, check_profile="thorough")
    fake_container.ai._available = False
    saved = _save(auth_client, w, scene, PROSE, None)
    assert saved.status_code == 200
    assert saved.json()["automatic"] == "no_provider"
    assert all(not job["automatic"] for job in saved.json()["jobs"])


# ------------------------------------------------------------- asking first


def test_a_large_draft_is_sent_only_once_confirmed(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _settings(auth_client, w, confirm_above_tokens=1_000)
    refused = _draft(auth_client, w, scene, words=1_500)
    assert refused.status_code == 409, refused.text
    problem = refused.json()
    assert problem["code"] == "needs_confirmation"
    estimated = int(problem["field_errors"]["estimated_tokens"])
    assert estimated > 1_000
    assert problem["detail"].startswith("Writing this draft will use about")
    # Nothing was queued or sent.
    assert fake_container.jobs.jobs == {}

    # A smaller confirmation than the estimate does not count.
    short = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/drafts",
        json={"variants": 1, "words": 1_500},
        headers={"X-Confirm-Tokens": str(estimated - 1)},
    )
    assert short.status_code == 409
    sent = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/drafts",
        json={"variants": 1, "words": 1_500},
        headers={"X-Confirm-Tokens": str(estimated)},
    )
    assert sent.status_code == 202, sent.text


def test_a_check_and_a_reading_ask_first_too(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    _settings(auth_client, w, confirm_above_tokens=1_000)
    review = auth_client.post(f"{w['base']}/plan/{scene['id']}/studio/reviews", json={})
    assert review.status_code == 409 and review.json()["code"] == "needs_confirmation"
    assert review.json()["detail"].startswith("Checking this scene")
    estimate = auth_client.get(f"{w['base']}/plan/{scene['id']}/studio/check-estimate").json()
    assert estimate["total_tokens"] == int(review.json()["field_errors"]["estimated_tokens"])

    # Reading the scene into the story reads its words with the story bible's names.
    reading = auth_client.post(f"{w['base']}/plan/{scene['id']}/studio/readings")
    assert reading.status_code == 409, reading.text
    assert reading.json()["detail"].startswith("Reading this scene into the story")
    tokens = reading.json()["field_errors"]["estimated_tokens"]
    confirmed = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/readings", headers={"X-Confirm-Tokens": tokens}
    )
    assert confirmed.status_code == 202, confirmed.text


def test_an_automatic_check_above_the_threshold_waits_for_the_author(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _settings(auth_client, w, check_profile="thorough", confirm_above_tokens=1_000)
    saved = _save(auth_client, w, scene, PROSE, None).json()
    assert saved["automatic"] == "needs_confirmation"
    assert saved["jobs"] == []


def test_the_lower_of_the_story_and_workspace_threshold_applies(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    got = auth_client.get(f"{w['base']}/assistant-settings").json()
    assert got["effective_confirm_above_tokens"] is None
    asyncio.run(
        fake_container.assistant_settings.set_workspace(
            _workspace(fake_container),
            confirm_above_tokens=2_000,
            conversation_tokens=None,
            actor_user_id=None,
        )
    )
    _settings(auth_client, w, confirm_above_tokens=50_000)
    got = auth_client.get(f"{w['base']}/assistant-settings").json()
    assert got["workspace_confirm_above_tokens"] == 2_000
    assert got["effective_confirm_above_tokens"] == 2_000
    # A story may choose lower than the workspace; the lower always wins.
    _settings(auth_client, w, confirm_above_tokens=1_500)
    got = auth_client.get(f"{w['base']}/assistant-settings").json()
    assert got["effective_confirm_above_tokens"] == 1_500


def _workspace(container) -> str:
    """The workspace a local test story lives in."""
    return str(container.settings.AUTH_LOCAL_WORKSPACE_ID)


# ---------------------------------------------------------------- conversations


def _change(client, w, **body):
    return client.post(
        f"{w['base']}/assist/changes",
        json={"instruction": "Burn the harbor down.", "expected_revision": int(w["head"]), **body},
    )


def test_a_conversation_stops_at_its_budget(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    _settings(auth_client, w, conversation_tokens=6_000)
    fake_container.ai.queue(
        StructuredResponse(
            parsed=ChangePlan(understood="Unclear.", questions=["Which harbor do you mean?"]),
            model="fake",
            usage=AIUsage(prompt_tokens=3_500, completion_tokens=200),
        )
    )
    first = _change(auth_client, w)
    assert first.status_code == 200, first.text
    session_id = first.json()["session_id"]

    estimate = auth_client.get(
        f"{w['base']}/assist/estimate", params={"session_id": session_id}
    ).json()
    assert estimate["conversation_tokens"] == 6_000
    assert estimate["conversation_used_tokens"] == 3_700
    assert estimate["reply_tokens"] > 0

    calls = len(fake_container.ai.calls)
    refused = _change(auth_client, w, session_id=session_id)
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "conversation_budget"
    assert "3,700 of the 6,000" in refused.json()["detail"]
    assert len(fake_container.ai.calls) == calls

    # A new conversation starts afresh.
    fake_container.ai.queue(ChangePlan(understood="Unclear.", questions=["Which one?"]))
    assert _change(auth_client, w).status_code == 200


def test_an_assistant_reply_above_the_threshold_asks_first(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    _settings(auth_client, w, confirm_above_tokens=1_000)
    refused = _change(auth_client, w)
    assert refused.status_code == 409
    problem = refused.json()
    assert problem["code"] == "needs_confirmation"
    assert problem["detail"].startswith("This change will use about")
    assert fake_container.ai.calls == []
    fake_container.ai.queue(ChangePlan(understood="Unclear.", questions=["Which harbor?"]))
    sent = _change_confirmed(auth_client, w, problem["field_errors"]["estimated_tokens"])
    assert sent.status_code == 200, sent.text


def _change_confirmed(client, w, tokens: str):
    return client.post(
        f"{w['base']}/assist/changes",
        json={"instruction": "Burn the harbor down.", "expected_revision": int(w["head"])},
        headers={"X-Confirm-Tokens": tokens},
    )


# ------------------------------------------------------------------- money


def test_a_reported_cost_is_kept_on_the_job_and_the_allowance(auth_client, fake_container):
    from backend.ports import TextResponse

    w, scene = _scene_world(auth_client, fake_container)
    fake_container.ai.queue(
        TextResponse(
            text="Tobin watched the water at Lantern Harbor.",
            model="fake",
            usage=AIUsage(prompt_tokens=300, completion_tokens=40, cost_usd=0.0125),
        )
    )
    started = _draft(auth_client, w, scene)
    _run(fake_container)
    job = auth_client.get(f"{w['base']}/jobs/{started.json()['id']}").json()
    assert job["provider_cost_usd"] == pytest.approx(0.0125)
    usage = auth_client.get("/api/v1/ai/usage")
    if usage.status_code == 200:
        assert usage.json()["cost_usd"] == pytest.approx(0.0125)
    # No price is ever made up when the provider says nothing.
    fake_container.ai.queue("Tobin counted the boats.")
    again = _draft(auth_client, w, scene)
    _run(fake_container)
    assert (
        auth_client.get(f"{w['base']}/jobs/{again.json()['id']}").json()["provider_cost_usd"]
        is None
    )


# ------------------------------------------- every model call asks first


def test_every_assistant_request_asks_first_above_the_threshold(auth_client, fake_container):
    """Drafting a rule and asking about a character (both ways) are held to the threshold
    like every other request: refused before any call, sent once confirmed."""
    w = _world(auth_client, fake_container)
    _settings(auth_client, w, confirm_above_tokens=1_000)
    legacy = "/api/v1/projects/local-project/assistant-settings"
    assert auth_client.put(legacy, json={"confirm_above_tokens": 1_000}).status_code == 200
    auth_client.post("/api/v1/characters/init", json={"name": "Arin", "bio": "A scout."})
    requests = [
        (f"{w['base']}/assist/rules/draft", {"sentence": "Every pilot belongs to a guild."}),
        (f"{w['base']}/characters/{w['mara']}/questions", None),
        (
            f"{w['base']}/characters/{w['mara']}/proposals",
            {
                "answers": [{"question_id": "q1", "question": "Why?", "answer": "Debt."}],
                "expected_revision": int(w["head"]),
            },
        ),
        ("/api/v1/characters/generate-questions", {"id": "char_arin"}),
        (
            "/api/v1/characters/process-answers",
            {"id": "char_arin", "chapter": 1, "answers": {"Why?": "Debt."}},
        ),
    ]
    for path, body in requests:
        refused = auth_client.post(path, json=body)
        assert refused.status_code == 409, f"{path}: {refused.text}"
        assert refused.json()["code"] == "needs_confirmation", path
    assert fake_container.ai.calls == []

    from backend.services.rule_workflow import RuleDraft

    fake_container.ai.queue(RuleDraft(title="Pilots", understood="Pilots join guilds."))
    path, body = requests[0]
    refused = auth_client.post(path, json=body)
    sent = auth_client.post(
        path,
        json=body,
        headers={"X-Confirm-Tokens": refused.json()["field_errors"]["estimated_tokens"]},
    )
    assert sent.status_code == 200, sent.text


def test_a_blanket_confirmation_sent_up_front_is_not_a_confirmation(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    _settings(auth_client, w, confirm_above_tokens=1_000)
    refused = _change(auth_client, w)
    estimated = int(refused.json()["field_errors"]["estimated_tokens"])
    huge = _change_confirmed(auth_client, w, str(estimated * 100))
    assert huge.status_code == 409 and huge.json()["code"] == "needs_confirmation"
    assert fake_container.ai.calls == []


def test_revising_suggested_changes_counts_toward_their_conversation(auth_client, fake_container):
    from backend.services.canon_revise_workflow import RevisionPlan
    from tests.contract.test_assist_revisions import _proposal

    w = _world(auth_client, fake_container)
    _settings(auth_client, w, conversation_tokens=20_000)
    fake_container.ai.queue(
        StructuredResponse(
            parsed=ChangePlan(understood="Unclear.", questions=["Which harbor?"]),
            model="fake",
            usage=AIUsage(prompt_tokens=3_500, completion_tokens=200),
        )
    )
    session_id = _change(auth_client, w).json()["session_id"]
    mutation_id = _proposal(auth_client, w)
    store = fake_container.assist_sessions
    store.sessions[session_id] = store.sessions[session_id].model_copy(
        update={"mutation_id": mutation_id}
    )

    def revise():
        return auth_client.post(
            f"{w['base']}/assist/revisions",
            json={"mutation_id": mutation_id, "instruction": "Name him Hale."},
        )

    fake_container.ai.queue(
        StructuredResponse(
            parsed=RevisionPlan(understood="Unclear.", questions=["Which professor?"]),
            model="fake",
            usage=AIUsage(prompt_tokens=1_000, completion_tokens=0),
        )
    )
    assert revise().status_code == 200
    used = auth_client.get(
        f"{w['base']}/assist/estimate", params={"session_id": session_id}
    ).json()["conversation_used_tokens"]
    assert used == 4_700

    _settings(auth_client, w, conversation_tokens=6_000)
    calls = len(fake_container.ai.calls)
    refused = revise()
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "conversation_budget"
    assert len(fake_container.ai.calls) == calls


# ------------------------------------------------ checks on their own, at once


def test_two_saves_at_once_start_one_automatic_check(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _settings(auth_client, w, check_profile="thorough", check_minutes=5)
    service = fake_container.studio_service
    project_id = w["base"].rsplit("/", 1)[1]
    workspace_id = _workspace(fake_container)
    first = _save(auth_client, w, scene, PROSE, None).json()
    assert first["automatic"] == "started"
    started = next(job for job in first["jobs"] if job["automatic"])
    asyncio.run(
        fake_container.jobs.request_cancel(
            workspace_id=workspace_id, project_id=project_id, job_id=started["id"]
        )
    )
    # Both saves read the scene's checks before either started one.
    seen = asyncio.run(service._automatic_jobs(workspace_id, project_id, scene["id"]))

    async def stale(*_args):
        return seen

    service._automatic_jobs = stale
    second = _save(auth_client, w, scene, PROSE + " Gulls.", first["version_id"]).json()
    third = _save(auth_client, w, scene, PROSE + " Gulls cried.", second["version_id"]).json()
    assert second["automatic"] == "started"
    automatic = [
        job
        for job in fake_container.jobs.jobs.values()
        if job.payload.get("automatic") and job.id != started["id"]
    ]
    assert len(automatic) == 1, third["automatic"]
    assert automatic[0].payload["estimated_tokens"] > 0


def test_a_waiting_automatic_check_holds_its_estimate_of_the_allowance():
    from datetime import UTC, datetime

    from backend.domain.identity import AuthMode
    from backend.domain.jobs import Job
    from backend.services.studio_service import _held

    def job(status, used, estimate):
        return Job(
            workspace_id="w",
            project_id="p",
            id=f"job_{status.value}",
            kind="scene_review",
            status=status,
            actor_user_id="u",
            auth_mode=AuthMode.LOCAL,
            policy_version="1",
            payload={"automatic": True, "estimated_tokens": estimate},
            prompt_tokens=used,
            created_at=datetime.now(UTC),
        )

    assert _held([job(JobStatus.SUCCEEDED, 900, 5_000)]) == 900
    assert _held([job(JobStatus.QUEUED, 0, 5_000)]) == 5_000
    assert _held([job(JobStatus.RUNNING, 6_000, 5_000)]) == 6_000


def test_an_unexpected_error_starting_a_check_never_fails_the_save(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _settings(auth_client, w, check_profile="thorough")

    async def broken(*_args, **_kwargs):
        raise RuntimeError("database went away")

    fake_container.studio_service.review_estimate = broken
    saved = _save(auth_client, w, scene, PROSE, None)
    assert saved.status_code == 200, saved.text
    assert saved.json()["automatic"] == "not_started"
    assert saved.json()["versions"][0]["number"] == 1
