"""The story's ready views: built from the outbox, verified by replay, read with a
fallback when they lag (QRY-001)."""

import asyncio
from dataclasses import replace

import pytest

from tests.contract.test_god_hand import _world
from tests.contract.test_knowledge import _scene, _secret

pytestmark = pytest.mark.contract


def _build(container) -> int:
    return asyncio.run(container.read_model_consumer.run_once())


def _verify(container, **kwargs):
    project = container.settings.AUTH_LOCAL_PROJECT_ID
    workspace = container.settings.AUTH_LOCAL_WORKSPACE_ID
    return asyncio.run(container.read_models.verify(workspace, project, **kwargs))


def _status(client, w, headers=None) -> dict[str, dict]:
    views = client.get(f"{w['base']}/story-views", headers=headers or {}).json()["views"]
    return {view["view"]: view for view in views}


def _where(client, w, who: str, **params):
    return client.get(f"{w['base']}/story-views/where", params={"entity": w[who], **params}).json()


def _event(client, w, head: int, name: str, when: dict, people: list[str]) -> dict:
    made = client.post(
        f"{w['base']}/events",
        json={
            "kind": "happening",
            "name": name,
            "when": when,
            "participants": [{"entity_id": w[who], "role": "present"} for who in people],
            "expected_revision": head,
        },
    )
    assert made.status_code == 201, made.text
    return made.json()


def _story(client, container) -> tuple[dict, int]:
    """Mara at the harbor until year 10, then in Saltmere; a dated event; a scene that
    shows a secret; Mara owns a lantern."""
    w = _world(client, container)
    head = int(w["head"])
    ended = client.post(
        f"{w['base']}/facts/{w['at_harbor']}/amend",
        json={"expected_revision": head, "ends": {"kind": "at", "at": 10}},
    )
    assert ended.status_code == 200, ended.text
    project_id = w["base"].rsplit("/", 1)[1]
    later = client.post(
        f"{w['base']}/facts",
        json={
            "proposition": {
                "subject_id": w["mara"],
                "predicate": "LOCATED_AT",
                "object": {
                    "kind": "entity_ref",
                    "entity_id": w["saltmere"],
                    "project_id": project_id,
                },
            },
            "starts": {"kind": "at", "at": 10},
            "expected_revision": head + 1,
        },
    )
    assert later.status_code == 201, later.text
    w["in_saltmere"] = later.json()["id"]
    lantern = client.post(
        f"{w['base']}/entities",
        json={"type": "Object", "name": "The lantern", "expected_revision": head + 2},
    )
    w["lantern"] = lantern.json()["id"]
    owns = client.post(
        f"{w['base']}/facts",
        json={
            "proposition": {
                "subject_id": w["mara"],
                "predicate": "OWNS",
                "object": {
                    "kind": "entity_ref",
                    "entity_id": w["lantern"],
                    "project_id": project_id,
                },
            },
            "expected_revision": head + 3,
        },
    )
    assert owns.status_code == 201, owns.text
    w["owns"] = owns.json()["id"]
    storm = _event(client, w, head + 4, "The storm", {"kind": "at", "at": 4}, ["mara"])
    w["storm"] = storm["id"]
    _secret(client, w, "in_guild", head + 5)
    scene = _scene(client, w, head + 6, "The Docks", pov="tobin")
    w["docks"] = scene["id"]
    shown = client.put(
        f"{w['base']}/plan/{scene['id']}/reveals",
        json={
            "reveals": [{"fact_id": w["in_guild"], "how": "shown"}],
            "expected_revision": head + 7,
        },
    )
    assert shown.status_code == 200, shown.text
    return w, head + 8


def test_views_are_worked_out_until_built_and_verified_then_answer_reads(
    auth_client, fake_container
):
    w, head = _story(auth_client, fake_container)
    before = _where(auth_client, w, "mara", at=5)
    assert before["answered_from"] == "worked_out" and before["version"] == head
    places = {row["place_id"] for row in before["rows"]}
    assert places == {w["harbor"]}
    assert _status(auth_client, w)["location"]["version"] is None

    assert _build(fake_container) == 1
    status = _status(auth_client, w)
    assert all(view["version"] == head and not view["answers_reads"] for view in status.values())
    # Built but not yet verified: still worked out.
    assert _where(auth_client, w, "mara", at=5)["answered_from"] == "worked_out"

    results = _verify(fake_container)
    assert {result.view for result in results} == set(status)
    assert all(result.matched and result.differences == 0 for result in results)
    status = _status(auth_client, w)
    assert all(view["answers_reads"] and view["outcome"] == "matched" for view in status.values())

    after = _where(auth_client, w, "mara", at=5)
    assert after["answered_from"] == "view" and after["rows"] == before["rows"]
    later = _where(auth_client, w, "mara", at=12)
    assert {row["place_id"] for row in later["rows"]} == {w["saltmere"]}
    # The storm at year 4 puts no place on Mara: an event without one place says nothing.
    assert all(row["source"] == "fact" for row in later["rows"])


def test_the_timeline_and_the_readers_journey_read_the_same_from_their_views(
    auth_client, fake_container, monkeypatch
):
    w, _ = _story(auth_client, fake_container)
    worked_out = auth_client.get(f"{w['base']}/timeline").json()
    journey = auth_client.get(f"{w['base']}/reader").json()
    _build(fake_container)
    _verify(fake_container)

    import backend.services.knowledge_service as knowledge
    import backend.services.story_time_service as story_time

    def refuse(*_args, **_kwargs):
        raise AssertionError("worked out instead of read from the view")

    monkeypatch.setattr(story_time, "read_story", refuse)
    monkeypatch.setattr(knowledge, "_journey_of", refuse)
    assert auth_client.get(f"{w['base']}/timeline").json() == worked_out
    assert auth_client.get(f"{w['base']}/reader").json() == journey


def test_a_view_that_lags_falls_back_and_catches_up(auth_client, fake_container):
    w, head = _story(auth_client, fake_container)
    _build(fake_container)
    _verify(fake_container)
    moved = auth_client.post(
        f"{w['base']}/facts/{w['in_saltmere']}/amend",
        json={"expected_revision": head, "starts": {"kind": "at", "at": 20}},
    )
    assert moved.status_code == 200, moved.text
    lagging = _where(auth_client, w, "mara", at=15)
    assert lagging["answered_from"] == "worked_out" and lagging["version"] == head + 1
    assert lagging["rows"] == []
    assert _status(auth_client, w)["location"]["behind"] == 1
    assert _build(fake_container) == 1
    caught_up = _where(auth_client, w, "mara", at=15)
    # The same builder was verified: the rebuilt view answers again at once.
    assert caught_up["answered_from"] == "view" and caught_up["version"] == head + 1
    assert caught_up["rows"] == []
    assert _build(fake_container) == 0


def test_a_view_that_differs_from_the_replay_is_not_trusted_and_can_be_rebuilt(
    auth_client, fake_container
):
    w, head = _story(auth_client, fake_container)
    _build(fake_container)
    store = fake_container.read_model_store
    key = next(key for key in store.views if key[3] == "location")
    stored = store.views[key]
    store.views[key] = replace(stored, rows=stored.rows[1:], checksum="tampered")
    results = {result.view: result for result in _verify(fake_container)}
    assert not results["location"].matched and results["location"].differences == 1
    assert results["possession"].matched
    status = _status(auth_client, w)
    assert status["location"]["outcome"] == "differed" and not status["location"]["answers_reads"]
    assert status["possession"]["answers_reads"]
    assert _where(auth_client, w, "mara", at=5)["answered_from"] == "worked_out"

    _verify(fake_container, repair=True)
    assert _status(auth_client, w)["location"]["version"] is None
    _build(fake_container)
    assert all(result.matched for result in _verify(fake_container))
    assert _where(auth_client, w, "mara", at=5)["answered_from"] == "view"


def test_holders_relationships_dependents_and_disclosures(auth_client, fake_container):
    w, _ = _story(auth_client, fake_container)
    _build(fake_container)
    _verify(fake_container)
    base = w["base"]
    held = auth_client.get(f"{base}/story-views/holders", params={"item": w["lantern"]}).json()
    assert [(row["holder_id"], row["predicate"]) for row in held["rows"]] == [(w["mara"], "OWNS")]
    assert held["answered_from"] == "view"
    nobody = auth_client.get(f"{base}/story-views/holders")
    assert nobody.status_code == 400

    related = auth_client.get(
        f"{base}/story-views/relationships", params={"entity": w["tobin"]}
    ).json()
    assert [(row["a"], row["predicate"], row["b"]) for row in related["rows"]] == [
        (w["mara"], "KNOWS", w["tobin"])
    ]

    leaning = auth_client.get(
        f"{base}/story-views/dependents", params={"record": f"entity:{w['harbor']}"}
    ).json()
    assert {(item["record"], item["why"], item["depth"]) for item in leaning["dependents"]} == {
        (f"fact:{w['at_harbor']}", "object", 1)
    }
    on_storm = auth_client.get(
        f"{base}/story-views/dependents", params={"record": f"entity:{w['mara']}"}
    ).json()["dependents"]
    assert (f"event:{w['storm']}", "takes part", 1) in {
        (item["record"], item["why"], item["depth"]) for item in on_storm
    }
    # The scene leans on the secret it shows, two steps from Mara.
    assert any(
        item["record"] == f"plan_item:{w['docks']}" and item["depth"] == 2 for item in on_storm
    )
    bad = auth_client.get(f"{base}/story-views/dependents", params={"record": "thing:x"})
    assert bad.status_code == 400

    shown = auth_client.get(
        f"{base}/story-views/disclosures", params={"fact": w["in_guild"]}
    ).json()
    assert shown["first_shown"] == 1
    assert shown["rows"] == [{"scene_id": w["docks"], "number": 1, "how": "shown"}]


def test_each_line_of_the_story_has_its_own_views(auth_client, fake_container):
    w, head = _story(auth_client, fake_container)
    made = auth_client.post(f"{w['base']}/what-ifs", json={"name": "What if she stayed?"})
    what_if = made.json()["id"]
    inside = {"X-Story-Branch": what_if}
    stayed = auth_client.post(
        f"{w['base']}/facts/{w['in_saltmere']}/close",
        json={"expected_revision": head},
        headers=inside,
    )
    assert stayed.status_code in (200, 201), stayed.text
    assert _build(fake_container) == 2
    _verify(fake_container)
    there = auth_client.get(
        f"{w['base']}/story-views/where",
        params={"entity": w["mara"], "at": 12},
        headers=inside,
    ).json()
    assert there["answered_from"] == "view" and there["rows"] == []
    main = _where(auth_client, w, "mara", at=12)
    assert {row["place_id"] for row in main["rows"]} == {w["saltmere"]}
    assert _status(auth_client, w, inside)["location"]["version"] == head + 1


def test_an_archive_says_the_views_are_left_out_and_built_again(auth_client, fake_container):
    w, _ = _story(auth_client, fake_container)
    _build(fake_container)
    workspace = fake_container.settings.AUTH_LOCAL_WORKSPACE_ID
    project = fake_container.settings.AUTH_LOCAL_PROJECT_ID
    archive = asyncio.run(fake_container.archive_service.export_project(workspace, project))
    assert archive["derived"]["story_views"]["included"] is False
    assert "read_models" not in str(archive["sections"])


def test_the_views_screen_says_how_far_each_is_and_an_owner_can_check_them(
    auth_client, fake_container
):
    # QRY-001's screen: words for each view's state, how long a lagging one has waited, and
    # a check against the story's history on request.
    w, _head = _story(auth_client, fake_container)
    body = auth_client.get(f"{w['base']}/story-views").json()
    assert body["main_story"] is True and "built again in full" in body["how_built"]
    assert {view["state"] for view in body["views"]} == {"Not built yet"}
    _build(fake_container)
    states = _status(auth_client, w)
    assert {view["state"] for view in states.values()} == {"Not checked yet"}
    assert all(view["built_at"] and view["lag_seconds"] is None for view in states.values())

    checked = auth_client.post(f"{w['base']}/story-views/check")
    assert checked.status_code == 200, checked.text
    assert checked.json() == {"checked": 6, "matched": 6, "differed": 0, "skipped": 0}
    states = _status(auth_client, w)
    assert {view["state"] for view in states.values()} == {"Ready"}
    assert all(view["answers_reads"] for view in states.values())

    # A change: each view is behind until built again, and says since when.
    head = int(auth_client.get(f"{w['base']}/projection").json()["head_revision"])
    added = auth_client.post(
        f"{w['base']}/entities",
        json={"type": "Character", "name": "Odile Fenn", "expected_revision": head},
    )
    assert added.status_code == 201, added.text
    states = _status(auth_client, w)
    assert {view["state"] for view in states.values()} == {"Catching up"}
    assert all(view["behind"] == 1 and view["lag_seconds"] is not None for view in states.values())


def test_one_check_of_a_line_runs_at_a_time_and_its_replay_runs_off_the_loop(
    auth_client, fake_container, monkeypatch
):
    # Checking replays the whole history: a second check while one runs is refused (409),
    # here or on another server, and the replay itself runs in a thread so other requests
    # are answered meanwhile.
    import threading

    from backend.domain.errors import ConflictError
    from backend.domain.identity import AccessScope, AuthMode
    from backend.services import story_at_version

    w, _head = _story(auth_client, fake_container)
    _build(fake_container)
    service = fake_container.read_models
    workspace = fake_container.settings.AUTH_LOCAL_WORKSPACE_ID
    project = fake_container.settings.AUTH_LOCAL_PROJECT_ID
    scope = AccessScope(actor_user_id="usr_x", auth_mode=AuthMode.LOCAL, workspace_id=workspace)

    threads: list[int] = []
    real_replay = story_at_version.replay

    def replay(versions):
        threads.append(threading.get_ident())
        return real_replay(versions)

    monkeypatch.setattr(story_at_version, "replay", replay)
    real_verify = service.verify

    async def slow_verify(*args, **kwargs):
        await asyncio.sleep(0.05)
        return await real_verify(*args, **kwargs)

    monkeypatch.setattr(service, "verify", slow_verify)

    async def both():
        return await asyncio.gather(
            service.check_now(scope, workspace, project),
            service.check_now(scope, workspace, project),
            return_exceptions=True,
        )

    first, second = asyncio.run(both())
    assert isinstance(first, list) and len(first) == 6
    assert isinstance(second, ConflictError)
    assert threads and threading.get_ident() not in threads
    # Once the first finished, the line can be checked again.
    assert len(asyncio.run(service.check_now(scope, workspace, project))) == 6

    # Another server holding the line's check: answered at once with 409.
    branch_id, _ = asyncio.run(service._line(workspace, project))
    fake_container.read_model_store.held_elsewhere.add((workspace, project, branch_id))
    refused = auth_client.post(f"{w['base']}/story-views/check")
    assert refused.status_code == 409, refused.text
    assert "being checked now" in refused.text
