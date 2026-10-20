"""A check with the assistant is made of passes the author chooses (CON-008).

One model call per pass, each with its own prompt and only the material it needs; the
viewpoint pass gets the viewpoint character's knowledge and, apart and labelled, the
secrets kept from them. Every note records the pass that found it; notes from the older
all-in-one check still show.
"""

import asyncio

import pytest

from backend.domain.check_passes import PASS_ORDER, CheckPass
from backend.domain.studio import AssistantFinding
from backend.ports import AIUsage, StructuredResponse
from backend.services.scene_checks import CheckNote, CheckNotes
from tests.contract.test_studio import PROSE, _ids, _save, _scene_world, _scope_of, _studio

pytestmark = pytest.mark.contract

ALL = [item.value for item in PASS_ORDER]
# The passes that read the scene's words; the pictures pass makes no call on a scene with
# no pictures it may use (tests/contract/test_picture_context.py).
TEXT = [item.value for item in PASS_ORDER if item is not CheckPass.PICTURES]


def _run(container) -> list:
    return asyncio.run(container.job_runner.run_pending())


def _check(client, w, scene, **body):
    return client.post(f"{w['base']}/plan/{scene['id']}/studio/reviews", json=body)


def _answer(*notes: CheckNote, tokens: int = 100) -> StructuredResponse:
    return StructuredResponse(
        parsed=CheckNotes(notes=list(notes)),
        model="fake",
        usage=AIUsage(prompt_tokens=tokens, completion_tokens=10),
    )


def test_every_pass_runs_by_default_one_call_each_and_notes_say_which(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    quotes = {
        "viewpoint": "Tobin waited",
        "continuity": "Lantern Harbor",
        "plan": "while the tide came in",
        "voice": "the tide came in",
        "clarity": "waited at",
    }
    for name in TEXT:
        fake_container.ai.queue(_answer(CheckNote(quote=quotes[name], problem=f"A {name} slip.")))
    started = _check(auth_client, w, scene)
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done.error_message
    assert done.payload["passes"] == ALL
    assert set(done.result["passes"]) == set(ALL) and done.result["findings"] == 5

    calls = fake_container.ai.calls
    assert len(calls) == 5 and all(call["response_format"] is CheckNotes for call in calls)
    notes = {note["check_pass"]: note for note in _studio(auth_client, w, scene)["assistant"]}
    assert set(notes) == set(TEXT)
    for name, note in notes.items():
        assert note["category"] == name and note["message"] == f"A {name} slip."
        assert PROSE[note["start"] : note["end"]] == quotes[name]

    # Each pass is asked about its own thing, with its own part of the story.
    systems = [call["messages"][0]["content"] for call in calls]
    assert "secrets kept from them" in systems[0]
    assert "continuity with the story bible" in systems[1]
    assert "what its plan asks" in systems[2]
    assert "the author's voice" in systems[3]
    assert "for one thing only: clarity" in systems[4]
    viewpoint, continuity = calls[0]["messages"][1]["content"], calls[1]["messages"][1]["content"]
    # The secret Tobin does not know goes only to the viewpoint pass, apart and labelled.
    assert "Harbor Guild" in viewpoint
    secrets_block = viewpoint.split("For the author only", 1)[1].split("The scene:", 1)[0]
    assert "Harbor Guild" in secrets_block
    assert "Harbor Guild" not in viewpoint.split("For the author only", 1)[0]
    assert "Harbor Guild" not in continuity
    assert all("Harbor Guild" not in call["messages"][1]["content"] for call in calls[1:])
    # What Tobin wrongly believes is the viewpoint pass's material; the scene is in every call.
    assert "believes Mara is located at Saltmere" in viewpoint
    assert all(PROSE in call["messages"][1]["content"] for call in calls)


def test_only_the_chosen_passes_run(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    fake_container.ai.queue(_answer())
    fake_container.ai.queue(_answer())
    # Asked in any order, run in the usual one.
    started = _check(auth_client, w, scene, passes=["clarity", "viewpoint", "clarity"])
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert done.payload["passes"] == ["viewpoint", "clarity"]
    assert len(fake_container.ai.calls) == 2

    assert _check(auth_client, w, scene, passes=[]).status_code == 400
    assert _check(auth_client, w, scene, passes=["spelling"]).status_code == 422


def test_the_estimate_is_given_pass_by_pass_and_asks_no_model(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    url = f"{w['base']}/plan/{scene['id']}/studio/check-estimate"
    every = auth_client.get(url).json()
    assert [item["check_pass"] for item in every["passes"]] == ALL
    assert all(item["label"] and item["description"] for item in every["passes"])
    assert every["total_tokens"] == sum(item["total_tokens"] for item in every["passes"])
    assert every["prompt_tokens"] == sum(item["prompt_tokens"] for item in every["passes"])
    by_pass = {item["check_pass"]: item["total_tokens"] for item in every["passes"]}
    # The viewpoint pass reads the secrets as well, so it reads more than clarity does.
    assert by_pass["viewpoint"] > by_pass["clarity"]

    two = auth_client.get(url, params={"passes": ["clarity", "plan"]}).json()
    assert [item["check_pass"] for item in two["passes"]] == ["plan", "clarity"]
    assert two["total_tokens"] == by_pass["plan"] + by_pass["clarity"]
    assert auth_client.get(url, params={"passes": ["nope"]}).status_code == 422
    assert fake_container.ai.calls == [] and fake_container.jobs.jobs == {}


def test_a_check_asks_first_with_the_size_of_the_chosen_passes(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    auth_client.put(f"{w['base']}/assistant-settings", json={"confirm_above_tokens": 1_000})
    asked = _check(auth_client, w, scene, passes=["viewpoint", "voice"])
    assert asked.status_code == 409 and asked.json()["code"] == "needs_confirmation"
    estimate = auth_client.get(
        f"{w['base']}/plan/{scene['id']}/studio/check-estimate",
        params={"passes": ["viewpoint", "voice"]},
    ).json()
    tokens = asked.json()["field_errors"]["estimated_tokens"]
    assert int(tokens) == estimate["total_tokens"]
    sent = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews",
        json={"passes": ["viewpoint", "voice"]},
        headers={"X-Confirm-Tokens": tokens},
    )
    assert sent.status_code == 202, sent.text


def test_each_pass_is_recorded_once_and_limits_are_checked_before_each(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    asked = ["viewpoint", "plan", "clarity"]
    estimate = auth_client.get(
        f"{w['base']}/plan/{scene['id']}/studio/check-estimate", params={"passes": asked}
    ).json()
    # Enough for the three passes as estimated, and no more.
    fake_container.usage_service._limit.period_token_limit = estimate["total_tokens"] + 10
    fake_container.ai.queue(_answer(tokens=50))
    # The second pass uses far more than expected, so the third is refused before sending.
    fake_container.ai.queue(_answer(tokens=estimate["total_tokens"]))
    fake_container.ai.queue(_answer())
    started = _check(auth_client, w, scene, passes=asked).json()
    (done,) = _run(fake_container)
    assert done.status.value == "failed" and done.error_code == "refused"
    assert "allowance" in done.error_message
    assert fake_container.ai.remaining == 1
    keys = [record.idempotency_key for record in fake_container.usage.records]
    assert keys == [
        f"{started['id']}:{done.attempts}:review:viewpoint",
        f"{started['id']}:{done.attempts}:review:plan",
    ]


def test_stopping_between_passes_keeps_the_passes_already_finished(
    auth_client, fake_container, monkeypatch
):
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    fake_container.ai.queue(_answer(CheckNote(quote="Tobin waited", problem="Knows too much.")))
    fake_container.ai.queue(_answer(CheckNote(quote="Lantern Harbor", problem="Wrong harbour.")))
    started = _check(auth_client, w, scene, passes=["viewpoint", "continuity"]).json()
    ai = fake_container.ai
    original = ai.generate_structured
    calls = {"n": 0}

    async def stop_during_the_second(**kwargs):
        calls["n"] += 1
        answer = await original(**kwargs)
        if calls["n"] == 2:
            await fake_container.studio_service.cancel(
                _scope_of(fake_container), *_ids(fake_container), started["id"]
            )
        return answer

    monkeypatch.setattr(ai, "generate_structured", stop_during_the_second)
    (done,) = _run(fake_container)
    assert done.status.value == "cancelled"
    notes = _studio(auth_client, w, scene)["assistant"]
    assert [note["check_pass"] for note in notes] == ["viewpoint"]


def test_notes_from_the_older_all_in_one_check_still_show(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    saved = _save(auth_client, w, scene, PROSE, None).json()
    workspace_id = str(fake_container.settings.AUTH_LOCAL_WORKSPACE_ID)
    project_id = w["base"].rsplit("/", 1)[1]
    start = PROSE.index("Lantern Harbor")
    old = AssistantFinding(
        id="fnd_old",
        target_kind="version",
        target_id=saved["version_id"],
        job_id="job_old",
        category="continuity",
        message="Stored before passes existed.",
        suggestion="",
        start=start,
        end=start + len("Lantern Harbor"),
    )
    stored = fake_container.studio_store
    asyncio.run(
        stored.add_findings(workspace_id=workspace_id, project_id=project_id, findings=[old])
    )
    (note,) = _studio(auth_client, w, scene)["assistant"]
    assert note["check_pass"] is None and note["category"] == "continuity"
    assert note["message"] == "Stored before passes existed."
