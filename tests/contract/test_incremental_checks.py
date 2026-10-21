"""Incremental checks (CON-007): a scene's checks that need no model are worked out again
only where what they read changed, and give exactly what the studio's checks give."""

import pytest

from tests.contract.test_context import _head, _prop
from tests.contract.test_studio import _save, _scene_world, _studio

pytestmark = pytest.mark.contract

PROSE = (
    "Tobin waited at Lantern Harbor. The salt air was palpable.\n\n"
    "Mara of the Harbor Guild did not come."
)
# Every named check that reads the words.
READS_WORDS = {
    "names",
    "secrets",
    "knowledge",
    "reveals",
    "presence",
    "looks",
    "stock",
    "length",
    "other",
}


def _record(client, w, scene) -> dict:
    made = client.post(f"{w['base']}/plan/{scene['id']}/checks/runs")
    assert made.status_code == 201, made.text
    return made.json()


def _reran(run: dict) -> set[str]:
    return {check["name"] for check in run["scenes"][0]["checks"] if not check["reused"]}


def _last(container) -> set[str]:
    work = container.consistency_runs.checks.last
    assert work is not None
    return set(work.ran)


def test_incremental_checks_give_what_the_studio_gives(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    studio = _studio(auth_client, w, scene)
    run = _record(auth_client, w, scene)
    seen = [(c["code"], c["start"], c["end"], c["message"]) for c in studio["checks"]]
    kept = [
        (f["code"], (f["quoted"] and PROSE.index(f["quoted"])) or None, f["message"])
        for f in run["findings"]
    ]
    assert [(code, message) for code, _, _, message in seen] == [
        (code, message) for code, _, message in kept
    ]
    assert {"stock_phrase", "secret"} <= {code for code, *_ in seen}


def test_an_unchanged_scene_runs_no_check_and_a_change_runs_exactly_what_reads_it(
    auth_client, fake_container
):
    w, scene = _scene_world(auth_client, fake_container)
    first = _save(auth_client, w, scene, PROSE, None).json()
    run = _record(auth_client, w, scene)
    assert {c["name"] for c in run["scenes"][0]["checks"]} >= READS_WORDS | {"continuity"}

    # Nothing changed: reading the history works every check out from what is kept.
    auth_client.get(f"{w['base']}/plan/{scene['id']}/checks/runs")
    assert _last(fake_container) == set()
    again = _record(auth_client, w, scene)
    assert again["id"] == run["id"]

    # The words change: every check that reads them, and not continuity.
    _save(auth_client, w, scene, PROSE.replace("palpable", "cold"), first["version_id"])
    assert _last(fake_container) == READS_WORDS
    newest = auth_client.get(f"{w['base']}/plan/{scene['id']}/checks/runs").json()["items"][0]
    assert _reran(newest) == READS_WORDS

    # A fact no check of this scene reads besides the whole of story time: continuity only.
    fact = auth_client.post(
        f"{w['base']}/facts",
        json={
            "proposition": _prop(w, "guild", "LOCATED_AT", "saltmere"),
            "expected_revision": _head(auth_client, w),
        },
    )
    assert fact.status_code == 201, fact.text
    assert _reran(_record(auth_client, w, scene)) == {"continuity"}

    # The travel table changes: continuity only.
    table = {
        "travel": [{"from_id": w["harbor"], "to_id": w["saltmere"], "amount": 3}],
        "lasting": [],
        "expected_version": 0,
    }
    assert auth_client.put(f"{w['base']}/timeline/how-long", json=table).status_code == 200
    assert _reran(_record(auth_client, w, scene)) == {"continuity"}

    # A new secret the viewpoint character does not know: the secrets check, continuity
    # (the story changed), and the catch-all that reads the whole brief.
    secret = auth_client.post(
        f"{w['base']}/facts/{w['knows']}/amend",
        json={"expected_revision": _head(auth_client, w), "visibility": "secret"},
    )
    assert secret.status_code == 200, secret.text
    assert _reran(_record(auth_client, w, scene)) == {"secrets", "continuity", "other"}


def test_a_dismissal_comes_back_when_what_the_finding_says_changes(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    studio = _studio(auth_client, w, scene)
    secret = next(c for c in studio["checks"] if c["code"] == "secret")
    done = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/dismissals",
        json={"key": secret["key"], "reason": "not_a_problem"},
    )
    assert done.status_code == 200, done.text
    assert next(c for c in done.json()["checks"] if c["key"] == secret["key"])["dismissal"]

    # The viewpoint character is renamed: the finding says something else now, so the
    # dismissal no longer holds and the finding is back, said to be back.
    renamed = auth_client.patch(
        f"{w['base']}/entities/{w['tobin']}",
        json={"name": "Tobin Vale", "expected_revision": _head(auth_client, w)},
    )
    assert renamed.status_code == 200, renamed.text
    back = _studio(auth_client, w, scene)
    found = next(c for c in back["checks"] if c["code"] == "secret")
    assert found["dismissal"] is None and found["came_back"] is True


@pytest.mark.asyncio
async def test_runs_and_what_findings_depend_on_travel_with_the_archive(
    auth_client, fake_container
):
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    studio = _studio(auth_client, w, scene)
    secret = next(c for c in studio["checks"] if c["code"] == "secret")
    auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/dismissals",
        json={"key": secret["key"], "reason": "not_a_problem"},
    )
    (run,) = auth_client.get(f"{w['base']}/plan/{scene['id']}/checks/runs").json()["items"]

    archive = auth_client.post(f"{w['base']}/archive/export").json()
    (carried,) = archive["check_runs"]
    assert carried["id"] == run["id"] and carried["line"] == "main"
    assert carried["fingerprints"]["scenes"][scene["id"]]["inputs"]["prose"]
    # Digests only: none of the words are in what the run keeps besides its findings.
    assert "Lantern Harbor" not in str(carried["fingerprints"])
    (dismissal,) = archive["writing"]["dismissals"]
    assert len(dismissal["depends_on"]) == 64

    workspace_id = archive["project"]["workspace_id"]
    await fake_container.archive_service.import_project(workspace_id, "restored", archive)
    root = await fake_container.journal.ensure_root_branch(
        workspace_id=workspace_id, project_id="restored"
    )
    restored = await fake_container.consistency_run_store.recent(
        workspace_id=workspace_id, project_id="restored", branch_id=root.id
    )
    assert [item.id for item in restored] == [run["id"]]
    assert restored[0].job_id is None and restored[0].fingerprints == carried["fingerprints"]
    (back,) = await fake_container.studio_store.dismissals_in(
        workspace_id=workspace_id, project_id="restored", branch_id=root.id
    )
    assert back.depends_on == dismissal["depends_on"]

    # A malformed history is refused before anything is written.
    broken = {**archive, "check_runs": [{**carried, "kind": "guess"}]}
    from backend.services.canon_archive import _checksum

    broken["checksum"] = _checksum(broken)
    with pytest.raises(Exception, match="malformed"):
        await fake_container.archive_service.import_project(workspace_id, "again", broken)


def test_checking_again_after_the_rules_change_records_a_new_run(auth_client, fake_container):
    from tests.contract.test_rules import NEEDS_MEMBER

    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    run = _record(auth_client, w, scene)
    assert _record(auth_client, w, scene)["id"] == run["id"]

    # A rule written since: the run is out of date, and checking again gives a new one,
    # not the same out-of-date run however often it is asked for.
    made = auth_client.post(
        f"{w['base']}/rules", json={"title": "Guilds have members", "body": NEEDS_MEMBER}
    )
    assert made.status_code == 201, made.text
    (old,) = [
        item
        for item in auth_client.get(f"{w['base']}/plan/{scene['id']}/checks/runs").json()["items"]
        if item["id"] == run["id"]
    ]
    assert old["stale"] is True
    again = _record(auth_client, w, scene)
    assert again["id"] != run["id"] and again["stale"] is False
    assert _record(auth_client, w, scene)["id"] == again["id"]


def test_continuity_read_while_the_story_moves_on_is_not_kept(
    auth_client, fake_container, monkeypatch
):
    import backend.services.story_time_service as story_time

    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    # A fact changes the story, so the next run works continuity out again.
    fact = auth_client.post(
        f"{w['base']}/facts",
        json={
            "proposition": _prop(w, "guild", "LOCATED_AT", "saltmere"),
            "expected_revision": _head(auth_client, w),
        },
    )
    assert fact.status_code == 201, fact.text
    reader = fake_container.consistency_runs.checks._studio._reader
    real_head, real_read = reader.get_head, story_time.read_story
    moved = {"on": False}

    async def head(**kwargs):
        found = await real_head(**kwargs)
        return found + 1 if moved["on"] else found

    async def read(*args, **kwargs):
        # Something is committed between reading the story's version and the story.
        moved["on"] = True
        return await real_read(*args, **kwargs)

    monkeypatch.setattr(reader, "get_head", head)
    monkeypatch.setattr(story_time, "read_story", read)
    assert "continuity" in _reran(_record(auth_client, w, scene))
    monkeypatch.setattr(reader, "get_head", real_head)
    monkeypatch.setattr(story_time, "read_story", real_read)

    # Nothing was kept under the version it was not read at: it is worked out again,
    # and kept this time.
    auth_client.get(f"{w['base']}/plan/{scene['id']}/checks/runs")
    assert _last(fake_container) == {"continuity"}
    auth_client.get(f"{w['base']}/plan/{scene['id']}/checks/runs")
    assert _last(fake_container) == set()
