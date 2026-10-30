"""Prompt tokens a provider read from its cache are kept on the job and the usage record
(CTX-015)."""

import pytest

from backend.ports import AIUsage, StructuredResponse, TextResponse
from tests.contract.test_studio import PROSE, _draft, _job, _run, _save, _scene_world

pytestmark = pytest.mark.contract


def test_a_draft_job_and_its_usage_record_show_the_cached_prompt_tokens(
    auth_client, fake_container
):
    w, scene = _scene_world(auth_client, fake_container)
    for cached in (0, 1024):
        fake_container.ai.queue(
            TextResponse(
                text="Tobin waited at Lantern Harbor until the tide turned.",
                model="fake",
                usage=AIUsage(
                    prompt_tokens=1500, completion_tokens=40, cached_prompt_tokens=cached
                ),
            )
        )
    started = _draft(auth_client, w, scene, variants=2).json()
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done.error_message

    job = _job(auth_client, w, started["id"])
    assert job["prompt_tokens"] == 3000
    assert job["cached_prompt_tokens"] == 1024
    records = fake_container.usage.records
    assert [r.cached_prompt_tokens for r in records] == [0, 1024]


def test_a_provider_that_reports_no_cache_leaves_zero(auth_client, fake_container):
    from backend.services.scene_checks import CheckNotes

    w, scene = _scene_world(auth_client, fake_container)
    assert _save(auth_client, w, scene, PROSE, None).status_code == 200
    fake_container.ai.queue(
        StructuredResponse(
            parsed=CheckNotes(notes=[]),
            model="fake",
            usage=AIUsage(prompt_tokens=800, completion_tokens=12),
        )
    )
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews", json={"passes": ["clarity"]}
    )
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done.error_message
    job = _job(auth_client, w, started.json()["id"])
    assert job["prompt_tokens"] == 800 and job["cached_prompt_tokens"] == 0
    assert fake_container.usage.records[-1].cached_prompt_tokens == 0
