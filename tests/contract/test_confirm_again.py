"""Work planned again as it runs is not sent when it would now need more than the author
confirmed (OPS-002, CON-013): a check or an audit stops before any call and says the size
needs confirming again."""

import asyncio
from dataclasses import replace

import pytest

from backend.domain.check_passes import PassEstimate
from tests.contract.test_story_audit import _story
from tests.contract.test_studio import PROSE, _save, _scene_world

pytestmark = pytest.mark.contract


def _run(container) -> list:
    return asyncio.run(container.job_runner.run_pending())


def _confirmed(client, url, body) -> dict:
    asked = client.post(url, json=body)
    assert asked.status_code == 409 and asked.json()["code"] == "needs_confirmation", asked.text
    tokens = asked.json()["field_errors"]["estimated_tokens"]
    sent = client.post(url, json=body, headers={"X-Confirm-Tokens": tokens})
    assert sent.status_code == 202, sent.text
    return sent.json()


def test_a_check_that_grew_after_it_was_confirmed_stops_before_sending(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    first = _save(auth_client, w, scene, PROSE, None).json()
    estimate = auth_client.get(
        f"{w['base']}/plan/{scene['id']}/studio/check-estimate",
        params={"passes": ["continuity", "clarity"]},
    ).json()["total_tokens"]
    auth_client.put(f"{w['base']}/assistant-settings", json={"confirm_above_tokens": estimate - 1})
    url = f"{w['base']}/plan/{scene['id']}/studio/reviews"
    _confirmed(auth_client, url, {"passes": ["continuity", "clarity"]})
    # The scene is much longer by the time the check runs.
    _save(auth_client, w, scene, "\n\n".join([PROSE] * 20), first["version_id"])

    (done,) = _run(fake_container)
    assert done.status.value == "failed" and done.error_code == "refused"
    assert "needs confirmation again" in done.error_message
    assert "Nothing was sent" in done.error_message
    assert fake_container.ai.calls == [] and fake_container.usage.records == []


def test_a_check_that_did_not_need_confirming_stops_when_it_would_now(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    first = _save(auth_client, w, scene, PROSE, None).json()
    estimate = auth_client.get(
        f"{w['base']}/plan/{scene['id']}/studio/check-estimate", params={"passes": "clarity"}
    ).json()["total_tokens"]
    auth_client.put(f"{w['base']}/assistant-settings", json={"confirm_above_tokens": estimate + 50})
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews", json={"passes": ["clarity"]}
    )
    assert started.status_code == 202, started.text
    _save(auth_client, w, scene, "\n\n".join([PROSE] * 20), first["version_id"])
    (done,) = _run(fake_container)
    assert done.error_code == "refused" and "needs confirmation again" in done.error_message
    assert fake_container.ai.calls == []


def test_an_audit_that_grew_after_it_was_confirmed_stops_before_sending(
    auth_client, fake_container, monkeypatch
):
    w, beat, _ = _story(auth_client, fake_container)
    estimate = auth_client.get(
        f"{w['base']}/audits/estimate", params={"plan_id": beat["id"], "passes": "clarity"}
    ).json()["total_tokens"]
    auth_client.put(f"{w['base']}/assistant-settings", json={"confirm_above_tokens": estimate - 1})
    started = _confirmed(
        auth_client, f"{w['base']}/audits", {"plan_id": beat["id"], "passes": ["clarity"]}
    )
    assert started["id"]
    # Planned again as it runs, the scene now reads far more (the story grew meanwhile).
    studio = fake_container.studio_service
    planned = studio.check_plan

    async def bigger(*args, **kwargs):
        plan = await planned(*args, **kwargs)
        return replace(
            plan,
            estimates=tuple(
                PassEstimate(item.check, item.prompt_tokens * 10, item.completion_tokens)
                for item in plan.estimates
            ),
        )

    monkeypatch.setattr(studio, "check_plan", bigger)
    (done,) = _run(fake_container)
    assert done.status.value == "failed" and done.error_code == "refused"
    assert done.error_message.startswith("Auditing this part of the story needs confirmation")
    assert fake_container.ai.calls == [] and fake_container.usage.records == []
