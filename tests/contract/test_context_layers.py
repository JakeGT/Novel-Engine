"""Earlier scenes, the scene before as written, and why each item ranks where it does
(CTX-006, CTX-006A, CTX-007)."""

import asyncio

import pytest

from backend.services.scene_summaries import SceneSummaryDraft
from tests.contract.test_bible_listing import _commit
from tests.contract.test_context import _head
from tests.contract.test_god_hand import _world
from tests.contract.test_studio import _save

pytestmark = pytest.mark.contract

OPENING = (
    "Tobin waited at Lantern Harbor while the tide came in.\n\n"
    "Mara tied up her boat and did not look at him. The guild bell rang twice."
)
MIDDLE = (
    "The fog came down over the quay.\n\n"
    "Tobin counted the lamps and lost count, and in the end he went home without her."
)


def _scene(client, w, title: str, *, summary: str = "", **fields) -> dict:
    made = client.post(
        f"{w['base']}/plan",
        json={
            "kind": "scene",
            "title": title,
            "summary": summary,
            "refs": [
                {"entity_id": w["tobin"], "role": "point_of_view"},
                {"entity_id": w["harbor"], "role": "place"},
                {"entity_id": w["mara"], "role": "participant"},
            ],
            "expected_revision": _head(client, w),
            **fields,
        },
    )
    assert made.status_code == 201, made.text
    return made.json()


def _thread(client, w, title: str) -> dict:
    made = client.post(
        f"{w['base']}/plan",
        json={"kind": "thread", "title": title, "expected_revision": _head(client, w)},
    )
    assert made.status_code == 201, made.text
    return made.json()


def _context(client, w, scene) -> dict:
    got = client.get(f"{w['base']}/plan/{scene['id']}/context", params={"budget": 12000})
    assert got.status_code == 200, got.text
    return got.json()


def _items(context) -> dict[str, dict]:
    return {item["key"]: item for item in context["items"]}


def _three(client, w) -> tuple[dict, dict, dict]:
    first = _scene(client, w, "A: the arrival", summary="Mara comes home to the harbor.")
    second = _scene(client, w, "B: the fog", summary="Tobin waits in the fog.")
    third = _scene(client, w, "C: the bell", summary="The guild calls Mara back.")
    assert _save(client, w, first, OPENING, None).status_code == 200
    assert _save(client, w, second, MIDDLE, None).status_code == 200
    return first, second, third


def test_earlier_scenes_come_as_summaries_events_and_the_end_of_the_scene_before(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    first, second, third = _three(auth_client, w)
    project_id = w["base"].rsplit("/", 1)[1]
    _commit(
        auth_client,
        w["base"],
        [
            {
                "op": "create_event",
                "id": "tmp:e1",
                "kind": "happening",
                "name": "Mara comes ashore",
                "participants": [{"entity_id": w["mara"], "role": "participant"}],
                "scene_id": first["id"],
            },
            {
                "op": "create_event",
                "id": "tmp:e2",
                "kind": "happening",
                "name": "The guild bell rings for Mara",
                "scene_id": third["id"],
            },
        ],
        _head(auth_client, w),
    )
    assert project_id
    items = _items(_context(auth_client, w, third))

    # Two scenes back: what its plan card says, and the events placed in it.
    planned = items[f"summary:{first['id']}"]
    assert planned["section"] == "earlier" and planned["authority"] == "plan"
    assert planned["text"] == "“A: the arrival”, as planned: Mara comes home to the harbor."
    assert "It is two scenes back." in planned["reason"]
    assert [s["name"] for s in planned["signals"]] == ["distance"]
    assert planned["score"] == pytest.approx(4.0 + planned["signals"][0]["effect"])
    block = items[f"events:{first['id']}"]
    assert block["text"] == "In “A: the arrival”: Mara comes ashore."
    assert block["reason"].startswith("These are the events you placed in “A: the arrival”.")

    # This scene's own events, with the scene.
    own = items[f"events:{third['id']}"]
    assert own["section"] == "scene"
    assert own["text"] == "What you recorded happens in this scene: The guild bell rings for Mara."

    # The scene before, as written, from its end; its plan card is given as before.
    before = items[f"before:{second['id']}"]
    assert before["section"] == "before" and before["section_title"] == "How the scene before ends"
    assert before["text"].endswith("went home without her.")
    assert before["reason"] == "It is how the scene before, “B: the fog”, ends, as you wrote it."
    assert f"plan:{second['id']}" in items
    # Its passages are not given a second time among the author's writing.
    assert not [
        item
        for item in items.values()
        if item["section"] == "writing" and "went home without her" in item["text"]
    ]
    assert f"summary:{second['id']}" not in items

    # Kept contexts never hold the author's words, the scene before's included.
    rendered = _context(auth_client, w, third)["rendered"]
    assert rendered.index("## How the scene before ends") < rendered.index("## The scene")
    # The scene two back is seen through the same eyes: its end comes as written too
    # (CTX-006A), after the earlier scenes' summaries and before the scene before's end.
    assert (
        rendered.index("## What happened in earlier scenes")
        < rendered.index("## How other recent scenes end")
        < rendered.index("## How the scene before ends")
    )
    assert fake_container.ai.calls == []


def test_a_summary_the_assistant_suggests_is_used_only_once_kept(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    first, second, third = _three(auth_client, w)
    base = f"{w['base']}/plan/{second['id']}/summary"

    view = auth_client.get(base).json()
    assert view["has_prose"] is True and view["kept"] is None and view["suggested"] == []
    assert view["plan_card"] == "Tobin waits in the fog."

    fake_container.ai.queue(
        SceneSummaryDraft(summary="  Fog covers the quay.\n Tobin waits for Mara and gives up. ")
    )
    started = auth_client.post(f"{base}/suggest", json={})
    assert started.status_code == 202, started.text
    assert started.json()["kind"] == "scene_summary"
    (done,) = asyncio.run(fake_container.job_runner.run_pending())
    assert done.status.value == "succeeded", done
    # The prose went to the model between marker lines, as material.
    sent = fake_container.ai.calls[-1]["messages"][-1]["content"]
    assert "<<<SCENE-1>>>" in sent and "went home without her" in sent

    view = auth_client.get(base).json()
    (suggested,) = view["suggested"]
    assert suggested["body"] == "Fog covers the quay. Tobin waits for Mara and gives up."
    assert suggested["origin"] == "model" and suggested["state"] == "proposed"
    assert suggested["prompt_version"] == "scene-summary/1" and suggested["job_id"] == done.id

    # A suggestion is not used: one scene back, nothing but the plan card says what happened.
    items = _items(_context(auth_client, w, third))
    assert f"summary:{second['id']}" not in items

    kept = auth_client.post(
        f"{base}/{suggested['id']}",
        json={"accept": True, "body": "Fog covers the quay. Tobin gives up on Mara."},
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["kept"]["origin"] == "model_corrected"
    assert kept.json()["suggested"] == []
    again = auth_client.post(f"{base}/{suggested['id']}", json={"accept": False})
    assert again.status_code == 400

    summary = _items(_context(auth_client, w, third))[f"summary:{second['id']}"]
    assert (
        summary["text"] == "“B: the fog”, as written: Fog covers the quay. Tobin gives up on Mara."
    )
    assert summary["authority"] == "summary"
    assert "a summary the assistant suggested and you corrected" in summary["reason"]

    # The scene is rewritten: the summary is out of date, ranked lower, and says so.
    base_version = auth_client.get(base).json()["version_id"]
    assert (
        _save(auth_client, w, second, MIDDLE + "\n\nHe came back.", base_version).status_code == 200
    )
    assert auth_client.get(base).json()["kept_out_of_date"] is True
    stale = _items(_context(auth_client, w, third))[f"summary:{second['id']}"]
    assert "out of date" in [s["name"] for s in stale["signals"]]
    assert "earlier version" in stale["reason"] and stale["score"] < summary["score"]


def test_the_authors_own_summary_and_turning_a_suggestion_down(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    first, _second, _third = _three(auth_client, w)
    base = f"{w['base']}/plan/{first['id']}/summary"
    written = auth_client.put(base, json={"body": "  Mara is home.  Tobin sees her. "})
    assert written.status_code == 200, written.text
    assert written.json()["kept"]["body"] == "Mara is home. Tobin sees her."
    assert written.json()["kept"]["origin"] == "author"
    too_long = auth_client.put(base, json={"body": "word " * 121})
    assert too_long.status_code == 400 and "120 words" in too_long.json()["detail"]

    fake_container.ai.queue(SceneSummaryDraft(summary="Something else entirely."))
    assert auth_client.post(f"{base}/suggest", json={}).status_code == 202
    asyncio.run(fake_container.job_runner.run_pending())
    (suggested,) = auth_client.get(base).json()["suggested"]
    turned = auth_client.post(f"{base}/{suggested['id']}", json={"accept": False})
    assert turned.status_code == 200
    assert turned.json()["kept"]["body"] == "Mara is home. Tobin sees her."
    assert turned.json()["suggested"] == []

    # Nothing written means nothing to summarise, and nothing is asked of a model.
    empty = _scene(auth_client, w, "D: unwritten")
    refused = auth_client.post(f"{w['base']}/plan/{empty['id']}/summary/suggest", json={})
    assert refused.status_code == 400 and "nothing written" in refused.json()["detail"]


def test_passages_from_a_scene_on_the_same_thread_are_given_and_say_why(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    thread = _thread(auth_client, w, "The guild's debt")
    opener = _scene(auth_client, w, "A: the debt", sets_up=[thread["id"]])
    _scene(auth_client, w, "B: elsewhere")
    closer = _scene(auth_client, w, "C: the payment", pays_off=[thread["id"]])
    # Long enough that its end, given as written (CTX-006A), does not reach the ledger.
    later = " ".join(["The gulls cried over the quay and the tide went out."] * 20)
    assert (
        _save(
            auth_client,
            w,
            opener,
            "The ledger lay open on the counter, unpaid.\n\n" + later,
            None,
        ).status_code
        == 200
    )
    items = _items(_context(auth_client, w, closer))
    passage = next(item for item in items.values() if "ledger lay open" in item["text"])
    assert (
        "It is from “A: the debt”, which opens the thread “The guild's debt”." in passage["reason"]
    )
    names = [signal["name"] for signal in passage["signals"]]
    assert "thread" in names and "distance" in names


def test_importance_continuity_and_near_copies_move_the_ranking_and_say_why(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    scene = _scene(auth_client, w, "The docks")
    # Mara is one of the author's main entries (a mark changed through review).
    entity = auth_client.get(f"{w['base']}/entities/{w['mara']}").json()
    marked = auth_client.patch(
        f"{w['base']}/entities/{w['mara']}",
        json={
            "attributes": {**entity["attributes"], "_importance": "main"},
            "expected_revision": _head(auth_client, w),
        },
    )
    assert marked.status_code == 200, marked.text
    # Two texts with nearly the same paragraph about Mara.
    words = "Mara tied up her boat at the harbor and walked up the hill to the guild house."
    for title in ("Notes one", "Notes two"):
        added = auth_client.post(
            f"{w['base']}/texts", json={"title": title, "content": words + " The end."}
        )
        assert added.status_code == 201, added.text
    items = _items(_context(auth_client, w, scene))

    fact = items[f"fact:{w['at_harbor']}"]
    importance = next(s for s in fact["signals"] if s["name"] == "importance")
    assert importance["effect"] > 0 and importance["note"] == (
        "You marked Mara as one of your main entries."
    )
    assert importance["note"] in fact["reason"]

    copies = [item for item in items.values() if item["text"].startswith("From “Notes")]
    assert len(copies) == 2
    lowered = [item for item in copies if any(s["name"] == "diversity" for s in item["signals"])]
    assert len(lowered) == 1
    assert "Much of it is like another passage given, from “Notes" in lowered[0]["reason"]
    assert lowered[0]["score"] < max(item["score"] for item in copies)
    # Each item's signals add up to how far it moved from where it started.
    for item in items.values():
        if item["signals"]:
            assert all(signal["note"] or signal["name"] == "thread" for signal in item["signals"])


def test_an_open_continuity_warning_near_the_scene_raises_what_it_is_about(
    auth_client, fake_container
):
    from tests.contract.test_continuity import _scene as timed_scene

    w = _world(auth_client, fake_container)
    died = auth_client.post(
        f"{w['base']}/events",
        json={
            "kind": "death",
            "name": "Tobin drowns",
            "participants": [{"entity_id": w["tobin"], "role": "participant"}],
            "when": {"kind": "at", "at": 5},
            "expected_revision": _head(auth_client, w),
        },
    )
    assert died.status_code == 201, died.text
    late = timed_scene(auth_client, w, "The wake", 9, "harbor", ["tobin"])
    items = _items(_context(auth_client, w, late))
    tobin = items[f"entity:{w['tobin']}"]
    signal = next(s for s in tobin["signals"] if s["name"] == "continuity")
    assert signal["effect"] == 2.0
    assert signal["note"].startswith("There is a continuity warning about it near this scene:")


def test_the_index_status_reports_its_waiting_list(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    status = auth_client.get(f"{w['base']}/texts-index").json()
    assert status["waiting"] == 0 and status["waiting_seconds"] is None and status["failing"] == 0


def test_summaries_and_the_scene_before_never_cross_lines_of_the_story(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    first, second, third = _three(auth_client, w)
    kept = auth_client.put(
        f"{w['base']}/plan/{first['id']}/summary", json={"body": "Mara is home at last."}
    )
    assert kept.status_code == 200, kept.text
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Mara stays away"}).json()[
        "id"
    ]
    headers = {"X-Story-Branch": branch}

    def context(in_branch: str | None) -> dict[str, dict]:
        got = auth_client.get(
            f"{w['base']}/plan/{third['id']}/context",
            params={"budget": 12000},
            headers={"X-Story-Branch": in_branch} if in_branch else {},
        )
        assert got.status_code == 200, got.text
        return _items(got.json())

    # Until the what-if changes a scene's words, it shares them, and their summary.
    assert "Mara is home at last." in context(branch)[f"summary:{first['id']}"]["text"]

    # The what-if rewrites both earlier scenes: its own copies, with no summary of their own.
    for scene, words in ((first, "Mara never came."), (second, "Tobin sailed out alone.")):
        state = auth_client.get(f"{w['base']}/plan/{scene['id']}/studio", headers=headers).json()
        saved = _save(auth_client, w, scene, words, state.get("version_id"), branch)
        assert saved.status_code == 200, saved.text

    in_what_if = context(branch)
    assert in_what_if[f"summary:{first['id']}"]["authority"] == "plan"
    assert in_what_if[f"before:{second['id']}"]["text"].endswith("Tobin sailed out alone.")
    main = context(None)
    assert "Mara is home at last." in main[f"summary:{first['id']}"]["text"]
    assert main[f"before:{second['id']}"]["text"].endswith("went home without her.")
    assert not any("sailed out alone" in item["text"] for item in main.values())


def test_a_summary_kept_in_a_what_if_is_its_own_and_never_replaces_the_main_storys(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    first, _second, third = _three(auth_client, w)
    base = f"{w['base']}/plan/{first['id']}/summary"
    assert auth_client.put(base, json={"body": "Mara is home at last."}).status_code == 200
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Mara stays away"}).json()[
        "id"
    ]
    headers = {"X-Story-Branch": branch}

    def summary_in(in_branch: str | None) -> str:
        got = auth_client.get(
            f"{w['base']}/plan/{third['id']}/context",
            params={"budget": 12000},
            headers={"X-Story-Branch": in_branch} if in_branch else {},
        )
        assert got.status_code == 200, got.text
        return str(_items(got.json())[f"summary:{first['id']}"]["text"])

    # The what-if shares the scene's words, and so the main story's summary of them.
    assert auth_client.get(base, headers=headers).json()["kept"]["body"] == (
        "Mara is home at last."
    )
    assert "Mara is home at last." in summary_in(branch)

    # A summary kept in the what-if, of the very same words, is the what-if's alone.
    written = auth_client.put(base, json={"body": "Mara only dreams of home."}, headers=headers)
    assert written.status_code == 200, written.text
    assert "Mara only dreams of home." in summary_in(branch)
    assert "Mara is home at last." in summary_in(None)
    assert auth_client.get(base).json()["kept"]["body"] == "Mara is home at last."

    # And the other way round: a new one in the main story leaves the what-if's alone.
    assert auth_client.put(base, json={"body": "Mara is home, tired."}).status_code == 200
    assert "Mara is home, tired." in summary_in(None)
    assert "Mara only dreams of home." in summary_in(branch)

    # A suggestion asked for in the what-if is decided there, never in the main story.
    fake_container.ai.queue(SceneSummaryDraft(summary="Mara sails past the harbor."))
    assert auth_client.post(f"{base}/suggest", json={}, headers=headers).status_code == 202
    asyncio.run(fake_container.job_runner.run_pending())
    assert auth_client.get(base).json()["suggested"] == []
    (suggested,) = auth_client.get(base, headers=headers).json()["suggested"]
    assert auth_client.post(f"{base}/{suggested['id']}", json={"accept": True}).status_code == 404
    kept = auth_client.post(f"{base}/{suggested['id']}", json={"accept": True}, headers=headers)
    assert kept.status_code == 200, kept.text
    assert "Mara sails past the harbor." in summary_in(branch)
    assert "Mara is home, tired." in summary_in(None)


def _seen_by(client, w, title: str, pov: str) -> dict:
    made = client.post(
        f"{w['base']}/plan",
        json={
            "kind": "scene",
            "title": title,
            "summary": f"{title} happens.",
            "refs": [
                {"entity_id": w[pov], "role": "point_of_view"},
                {"entity_id": w["harbor"], "role": "place"},
            ],
            "expected_revision": _head(client, w),
        },
    )
    assert made.status_code == 201, made.text
    return made.json()


def test_the_assistant_is_told_when_an_earlier_scene_is_seen_through_someone_else(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    first = _seen_by(auth_client, w, "A: Mara alone", "mara")
    second = _seen_by(auth_client, w, "B: Mara at sea", "mara")
    third = _scene(auth_client, w, "C: Tobin waits")
    assert _save(auth_client, w, first, OPENING, None).status_code == 200
    assert _save(auth_client, w, second, MIDDLE, None).status_code == 200
    assert (
        auth_client.put(
            f"{w['base']}/plan/{first['id']}/summary", json={"body": "Mara keeps a secret."}
        ).status_code
        == 200
    )
    context = _context(auth_client, w, third)
    items = _items(context)
    warning = "It is seen through Mara; Tobin Ashe may not know all of it."
    # In the words the assistant reads, not only in the reason shown to the author.
    for key in (f"summary:{first['id']}", f"before:{second['id']}", f"plan:{second['id']}"):
        assert warning in items[key]["text"], key
        assert warning in items[key]["reason"], key
    rendered = context["rendered"]
    assert f"“A: Mara alone”, as written: Mara keeps a secret. ({warning})" in rendered
    assert rendered.count(warning) >= 3
    # A scene seen through the same eyes says nothing of the kind.
    same = _items(_context(auth_client, w, second))
    assert "may not know" not in same[f"summary:{first['id']}"]["text"]


def test_a_later_scene_on_the_same_thread_is_never_given_to_an_earlier_one(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    thread = _thread(auth_client, w, "The guild's debt")
    opener = _scene(auth_client, w, "A: the debt", sets_up=[thread["id"]])
    closer = _scene(auth_client, w, "B: the payment", pays_off=[thread["id"]])
    payoff = "The ledger was paid at last, every coin counted twice."
    assert _save(auth_client, w, closer, payoff, None).status_code == 200
    # The payoff is not what the scene that opens the thread picks up.
    items = _items(_context(auth_client, w, opener))
    assert not [item for item in items.values() if "resolves the thread" in item["reason"]]
    assert not [
        item
        for item in items.values()
        if "ledger was paid" in item["text"]
        and any(signal["name"] == "thread" for signal in item["signals"])
    ]


def _voice(context) -> list[dict]:
    return [
        item for item in context["items"] if item["reason"].startswith("A sample of your voice")
    ]


def test_a_later_scenes_words_are_never_the_voice_sample_for_an_earlier_one(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    # Seen through other eyes, so its end is not given as a nearby scene's (CTX-006A) and
    # its words reach the context only as the voice sample.
    first = _seen_by(auth_client, w, "A: the arrival", "mara")
    _scene(auth_client, w, "B: the wait")
    target = _scene(auth_client, w, "C: the fog")
    last = _scene(auth_client, w, "D: the payment")
    opening = "Rain fell on the slate roofs and the guild bell rang twice."
    payoff = "The ledger was paid at last, every coin counted twice."
    # The payoff is the newest writing seen through the same eyes, and comes later.
    assert _save(auth_client, w, first, opening, None).status_code == 200
    assert _save(auth_client, w, last, payoff, None).status_code == 200
    (sample,) = _voice(_context(auth_client, w, target))
    assert "ledger" not in sample["text"]
    assert "guild bell" in sample["text"]
    # A later scene may sample the ones before it.
    (sample,) = _voice(_context(auth_client, w, last))
    assert "guild bell" in sample["text"]

    # With nothing written before it, the first scene gets no sample from later on.
    assert not [
        item for item in _voice(_context(auth_client, w, first)) if "ledger" in item["text"]
    ]
    # Writing outside the plan is still the author's voice, and comes before the payoff.
    made = auth_client.post(
        f"{w['base']}/texts",
        json={"title": "Notes", "content": "The gulls argued over nothing at all."},
    )
    assert made.status_code == 201, made.text
    (sample,) = _voice(_context(auth_client, w, first))
    assert sample["text"] == "From “Notes”: The gulls argued over nothing at all."


def test_whole_story_continuity_is_worked_out_once_per_version_of_the_story(
    auth_client, fake_container, monkeypatch
):
    from backend.services import continuity_service
    from tests.contract.test_continuity import _scene as timed_scene

    calls: list[int] = []
    real = continuity_service.story_continuity

    async def counted(*args, **kwargs):
        calls.append(1)
        return await real(*args, **kwargs)

    monkeypatch.setattr(continuity_service, "story_continuity", counted)
    w = _world(auth_client, fake_container)
    late = timed_scene(auth_client, w, "The wake", 9, "harbor", ["tobin"])
    assert f"entity:{w['tobin']}" in _items(_context(auth_client, w, late))
    _context(auth_client, w, late)
    other = timed_scene(auth_client, w, "The morning", 10, "harbor", ["tobin"])
    before = len(calls)
    _context(auth_client, w, late)
    _context(auth_client, w, other)
    assert len(calls) == before + 1  # a new version once; then reused for another scene

    # A change to the story is seen: the warning appears with the death that causes it.
    died = auth_client.post(
        f"{w['base']}/events",
        json={
            "kind": "death",
            "name": "Tobin drowns",
            "participants": [{"entity_id": w["tobin"], "role": "participant"}],
            "when": {"kind": "at", "at": 5},
            "expected_revision": _head(auth_client, w),
        },
    )
    assert died.status_code == 201, died.text
    tobin = _items(_context(auth_client, w, late))[f"entity:{w['tobin']}"]
    assert any(s["name"] == "continuity" for s in tobin["signals"])
    assert len(calls) == before + 2


def test_an_archive_keeps_which_line_each_summary_belongs_to(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    first, _second, _third = _three(auth_client, w)
    base = f"{w['base']}/plan/{first['id']}/summary"
    assert auth_client.put(base, json={"body": "Mara is home at last."}).status_code == 200
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Mara stays away"}).json()[
        "id"
    ]
    headers = {"X-Story-Branch": branch}
    kept = auth_client.put(base, json={"body": "Mara dreams."}, headers=headers)
    assert kept.status_code == 200, kept.text
    summaries = fake_container.scene_summaries
    store = summaries._store  # noqa: SLF001 - the archive's own view of the rows
    workspace_id, project_id, _ = next(iter(store.rows))
    exported = asyncio.run(summaries.export_summaries(workspace_id, project_id))
    assert {(row["body"], row["branch_id"]) for row in exported} == {
        ("Mara is home at last.", None),
        ("Mara dreams.", branch),
    }
    store.rows.clear()
    # Without the restored what-if, its summary is left out, never made the main story's.
    assert asyncio.run(summaries.restore_summaries(workspace_id, project_id, exported)) == 1
    (only,) = store.rows.values()
    assert only.body == "Mara is home at last." and only.branch_id is None
    store.rows.clear()
    restored = asyncio.run(
        summaries.restore_summaries(workspace_id, project_id, exported, {branch: "br_restored"})
    )
    assert restored == 2
    assert {(row.body, row.branch_id) for row in store.rows.values()} == {
        ("Mara is home at last.", None),
        ("Mara dreams.", "br_restored"),
    }
