"""The scene's intent and threads in what the assistant is given, and comparing that with
the context kept for a draft (STY-002, STY-003, CTX-009)."""

import asyncio

import pytest

from tests.contract.test_context import _context, _head, _setup
from tests.contract.test_god_hand import _world
from tests.contract.test_studio import _draft, _in, _save, _studio

pytestmark = pytest.mark.contract


def _patch(client, w, plan_id: str, branch: str | None = None, **fields) -> dict:
    head = client.get(f"{w['base']}/projection", headers=_in(branch)).json()["head_revision"]
    got = client.patch(
        f"{w['base']}/plan/{plan_id}",
        json={"expected_revision": head, **fields},
        headers=_in(branch),
    )
    assert got.status_code == 200, got.text
    return got.json()


def _kept_draft(client, container, w, scene, branch: str | None = None) -> dict:
    """Draft the scene with the scripted model and keep it; returns the kept version."""
    base = _studio(client, w, scene, branch)["version_id"]
    if base is None:
        base = _save(client, w, scene, "Tobin waited.", None, branch).json()["version_id"]
    container.ai.queue("Tobin watched the water at Lantern Harbor. Mara did not come.")
    assert _draft(client, w, scene, branch).status_code == 202
    asyncio.run(container.job_runner.run_pending())
    draft = _studio(client, w, scene, branch)["drafts"][0]
    kept = client.post(
        f"{w['base']}/drafts/{draft['id']}/keep",
        json={"base_version_id": base},
        headers=_in(branch),
    )
    assert kept.status_code == 200, kept.text
    return kept.json()["versions"][0]


def test_the_scenes_intent_and_the_threads_it_moves_are_given_to_the_assistant(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    scene = _setup(auth_client, w)
    thread = auth_client.post(
        f"{w['base']}/plan",
        json={
            "kind": "thread",
            "title": "Where is Mara?",
            "expected_revision": _head(auth_client, w),
        },
    ).json()
    _patch(
        auth_client,
        w,
        scene["id"],
        advances=[thread["id"]],
        intent={"goal": "find Mara", "obstacle": "the fog", "outcome": "no_and"},
    )
    items = {item["key"]: item for item in _context(auth_client, w, scene)["items"]}
    intent = items[f"intent:{scene['id']}"]
    assert intent["hard"] is True and intent["section"] == "scene"
    assert intent["text"] == (
        "Tobin Ashe wants: find Mara. In the way: the fog. "
        "How it ends: they do not get it, and things get worse."
    )
    moving = items[f"thread:{scene['id']}:{thread['id']}"]
    assert moving["text"] == "This scene moves the thread “Where is Mara?” along."


def test_a_kept_draft_can_be_compared_with_the_context_now(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _setup(auth_client, w)
    version = _kept_draft(auth_client, fake_container, w, scene)
    base = f"{w['base']}/plan/{scene['id']}/context/kept"

    kept = auth_client.get(base)
    assert kept.status_code == 200, kept.text
    (listed,) = kept.json()
    assert listed["manifest_id"] == version["manifest_id"]
    assert listed["version_id"] == version["id"] and listed["version_number"] == 2

    same = auth_client.get(f"{base}/{listed['manifest_id']}").json()
    assert same["added"] == [] and same["removed"] == [] and same["unchanged"] > 0
    # The kept draft is now the scene's newest writing, so the sample of the author's
    # voice reads differently; nothing else has changed.
    assert [c["item"]["section"] for c in same["changed"]] == ["writing"]
    assert "words" in same["changed"][0]["what"]
    assert same["changed"][0]["text_then"] is None  # no words of the writing were kept

    # Change the story: the scene's summary, an exclusion, and a new intent.
    context = _context(auth_client, w, scene)
    dropped = next(
        item["key"] for item in context["items"] if item["section"] == "true" and not item["hard"]
    )
    head = _head(auth_client, w)
    settings = auth_client.put(
        f"{w['base']}/plan/{scene['id']}/context",
        json={"excludes": [dropped], "expected_revision": head},
    )
    assert settings.status_code == 200, settings.text
    _patch(
        auth_client,
        w,
        scene["id"],
        summary="Tobin gives up on the water.",
        intent={"goal": "find Mara"},
    )

    diff = auth_client.get(f"{base}/{listed['manifest_id']}").json()
    assert diff["same"] is False
    assert [item["key"] for item in diff["added"]] == [f"intent:{scene['id']}"]
    (removed,) = [r for r in diff["removed"] if r["key"] == dropped]
    assert removed["left_out"] == "excluded" and removed["text"]
    changed = {c["item"]["key"]: c for c in diff["changed"]}
    scene_change = changed[f"scene:{scene['id']}"]
    assert scene_change["what"] == ["words"]
    assert scene_change["text_then"].startswith("The Docks. Tobin watches the water.")
    assert "Tobin gives up" in scene_change["item"]["text"]
    assert fake_container.ai.calls  # only the draft itself asked the model


def test_a_kept_context_is_read_only_for_its_scene_and_its_line_of_the_story(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    scene = _setup(auth_client, w)
    other = auth_client.post(
        f"{w['base']}/plan",
        json={"kind": "scene", "title": "Elsewhere", "expected_revision": _head(auth_client, w)},
    ).json()
    version = _kept_draft(auth_client, fake_container, w, scene)
    manifest = version["manifest_id"]

    # Not through another scene.
    assert (
        auth_client.get(f"{w['base']}/plan/{other['id']}/context/kept/{manifest}").status_code
        == 404
    )
    assert auth_client.get(f"{w['base']}/plan/{other['id']}/context/kept").json() == []
    assert (
        auth_client.get(f"{w['base']}/plan/{scene['id']}/context/kept/ctx_unknown").status_code
        == 404
    )

    # A what-if started after the draft was kept carries the version, so it may compare.
    what_if = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Fog"}).json()
    header = _in(what_if["id"])
    carried = auth_client.get(f"{w['base']}/plan/{scene['id']}/context/kept", headers=header)
    assert [k["manifest_id"] for k in carried.json()] == [manifest]
    assert (
        auth_client.get(
            f"{w['base']}/plan/{scene['id']}/context/kept/{manifest}", headers=header
        ).status_code
        == 200
    )

    # A draft kept in the what-if is not readable from the main story.
    inside = _kept_draft(auth_client, fake_container, w, scene, what_if["id"])
    assert inside["manifest_id"] != manifest
    main = auth_client.get(f"{w['base']}/plan/{scene['id']}/context/kept/{inside['manifest_id']}")
    assert main.status_code == 404
    assert manifest in {
        k["manifest_id"]
        for k in auth_client.get(f"{w['base']}/plan/{scene['id']}/context/kept").json()
    }


def test_a_kept_context_keeps_no_words_of_the_authors_writing(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _setup(auth_client, w)
    version = _kept_draft(auth_client, fake_container, w, scene)
    saved = next(
        value
        for (_ws, _pj, manifest_id), value in fake_container.manifests.saved.items()
        if manifest_id == version["manifest_id"]
    )
    items = saved.manifest["items"]
    assert all("digest" in item for item in items)
    assert all("text" not in item for item in items if item["authority"] == "writing")
    assert any("text" in item for item in items if item["authority"] != "writing")
