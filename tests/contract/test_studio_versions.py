"""A scene's versions when what-ifs share its words, and the texts page (ADR-022)."""

import asyncio

import pytest

from tests.contract.test_studio import PROSE, _draft, _save, _scene_world, _studio

pytestmark = pytest.mark.contract


def test_the_main_story_keeps_its_history_when_a_what_if_shares_its_words(
    auth_client, fake_container
):
    w, scene = _scene_world(auth_client, fake_container)
    one = _save(auth_client, w, scene, PROSE, None).json()
    two = _save(auth_client, w, scene, PROSE + " Gulls.", one["version_id"]).json()
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Storm"}).json()["id"]

    # The main story changes first: it writes in place and keeps all its versions.
    three = _save(auth_client, w, scene, PROSE + " Rain.", two["version_id"]).json()
    assert three["text_id"] == one["text_id"]
    assert [v["number"] for v in three["versions"]] == [3, 2, 1]

    # The what-if moved to a copy with the full history as it was when it began.
    inside = _studio(auth_client, w, scene, branch)
    assert inside["text_id"] != one["text_id"]
    assert inside["content"] == PROSE + " Gulls."
    assert [v["number"] for v in inside["versions"]] == [2, 1]
    # And the what-if's own change leaves the main story alone.
    mine = _save(auth_client, w, scene, PROSE + " Storm.", inside["version_id"], branch)
    assert mine.status_code == 200, mine.text
    assert _studio(auth_client, w, scene)["content"] == PROSE + " Rain."


def test_the_texts_page_cannot_change_a_scenes_words(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    first = _save(auth_client, w, scene, PROSE, None).json()
    changed = auth_client.patch(f"{w['base']}/texts/{first['text_id']}", json={"content": "Other."})
    assert changed.status_code == 400
    assert "studio" in changed.json()["detail"]
    assert _studio(auth_client, w, scene)["content"] == PROSE


def test_a_draft_far_shorter_than_asked_is_noted(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    fake_container.ai.queue("Tobin waited at Lantern Harbor. Mara did not come.")
    assert _draft(auth_client, w, scene, words=600).status_code == 202
    asyncio.run(fake_container.job_runner.run_pending())
    (draft,) = _studio(auth_client, w, scene)["drafts"]
    short = [c for c in draft["checks"] if c["code"] == "short"]
    assert short and "600 asked for" in short[0]["message"]


@pytest.mark.asyncio
async def test_an_archive_link_to_a_text_it_does_not_carry_is_skipped(fake_container):
    from backend.domain.identity import AccessScope, AuthMode

    archive = fake_container.archive_service
    scope = AccessScope(actor_user_id="usr_x", auth_mode=AuthMode.LOCAL, correlation_id="c")
    restored = await archive._restore_writing(  # noqa: SLF001 - the restore step itself
        "ws_local",
        "pr_restore",
        {"writing": {"texts": [], "names": [], "scenes": [{"plan_id": "p1", "text_id": "gone"}]}},
        actor_user_id=scope.actor_user_id,
    )
    assert restored["texts"] == 0
    assert (
        await fake_container.studio_store.manuscripts_for(
            workspace_id="ws_local", project_id="pr_restore", branch_id="any"
        )
        == []
    )
