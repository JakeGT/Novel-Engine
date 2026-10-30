"""Story time in the scene's context, summaries of whole arcs and beats, and the ends of
other recent scenes (CTX-004, CTX-006, CTX-006A, CTX-007)."""

import asyncio
from dataclasses import replace

import pytest

from backend.domain.context import SCENE_DRAFT_POLICY, LaterInTime
from backend.services.scene_summaries import PartSummaryDraft
from tests.contract.test_context import _head
from tests.contract.test_god_hand import _world
from tests.contract.test_studio import _save

pytestmark = pytest.mark.contract


def _scene(client, w, title: str, *, parent: str | None = None, pov: str = "tobin", **fields):
    made = client.post(
        f"{w['base']}/plan",
        json={
            "kind": "scene",
            "title": title,
            "summary": f"{title} happens.",
            "parent_id": parent,
            "refs": [
                {"entity_id": w[pov], "role": "point_of_view"},
                {"entity_id": w["harbor"], "role": "place"},
                {"entity_id": w["mara"], "role": "participant"},
            ],
            "expected_revision": _head(client, w),
            **fields,
        },
    )
    assert made.status_code == 201, made.text
    return made.json()


def _at(client, w, scene, at: float) -> None:
    timed = client.put(
        f"{w['base']}/plan/{scene['id']}/when",
        json={"when": {"kind": "at", "at": at}, "expected_revision": _head(client, w)},
    )
    assert timed.status_code == 200, timed.text


def _part(client, w, kind: str, title: str) -> dict:
    made = client.post(
        f"{w['base']}/plan",
        json={"kind": kind, "title": title, "expected_revision": _head(client, w)},
    )
    assert made.status_code == 201, made.text
    return made.json()


def _context(client, w, scene, headers=None) -> dict:
    got = client.get(
        f"{w['base']}/plan/{scene['id']}/context", params={"budget": 12000}, headers=headers or {}
    )
    assert got.status_code == 200, got.text
    return got.json()


def _items(context) -> dict[str, dict]:
    return {item["key"]: item for item in context["items"]}


def _left(context) -> dict[str, dict]:
    return {omission["item"]["key"]: omission for omission in context["left_out"]}


# --------------------------------------------------------- story time (CTX-004, CTX-007)


def _timed_story(client, w):
    """Reading order A, B, X, C. In the story's time A is year 1, B year 9 (a jump
    forward the reader meets early), X has no prose, and C, the scene to write, year 5."""
    a = _scene(client, w, "A: the arrival")
    b = _scene(client, w, "B: years later")
    x = _scene(client, w, "X: the quiet")
    c = _scene(client, w, "C: the bell")
    for scene, at in ((a, 1), (b, 9), (x, 4), (c, 5)):
        _at(client, w, scene, at)
    assert _save(client, w, a, "Mara tied up her boat at the harbor.", None).status_code == 200
    assert (
        _save(client, w, b, "Mara was old now, and the harbor was gone.", None).status_code == 200
    )
    return a, b, x, c


def test_material_from_a_scene_later_in_the_storys_time_is_left_out_and_says_why(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    a, b, _x, c = _timed_story(auth_client, w)
    context = _context(auth_client, w, c)
    assert context["policy_version"] == "scene-draft/3"
    items, left = _items(context), _left(context)

    # B's end and its plan card come from later in the story's time: listed, not given.
    later = left[f"nearby:{b['id']}"]
    assert later["reason"] == "later"
    assert "It happens later in the story's time than this scene." in later["item"]["reason"]
    assert left[f"summary:{b['id']}"]["reason"] == "later"
    assert "the harbor was gone" not in context["rendered"]
    assert not [key for key in items if b["id"] in key and not key.startswith("plan:")]

    # A is earlier in both clocks: further back in reading order and in the story's time,
    # each said in its reason, the second weighed on top of the first.
    card = items[f"summary:{a['id']}"]
    names = [signal["name"] for signal in card["signals"]]
    assert names == ["distance", "story time"]
    assert "In the story's time it happens 4 years before this scene." in card["reason"]
    assert card["score"] == pytest.approx(4.0 + sum(s["effect"] for s in card["signals"]))

    # The author can still choose it: a pin brings it back.
    pinned = auth_client.put(
        f"{w['base']}/plan/{c['id']}/context",
        json={
            "pins": [f"nearby:{b['id']}"],
            "excludes": [],
            "budget": 12000,
            "include_secrets": False,
            "expected_revision": _head(auth_client, w),
        },
    )
    assert pinned.status_code == 200, pinned.text
    assert f"nearby:{b['id']}" in _items(pinned.json())
    assert fake_container.ai.calls == []


def test_a_policy_may_lower_later_material_instead_of_leaving_it_out(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    _a, b, _x, c = _timed_story(auth_client, w)
    service = fake_container.context_service
    service.policy = replace(SCENE_DRAFT_POLICY, later_in_story_time=LaterInTime.LOWER)
    try:
        items = _items(_context(auth_client, w, c))
    finally:
        service.policy = SCENE_DRAFT_POLICY
    lowered = items[f"nearby:{b['id']}"]
    signal = next(s for s in lowered["signals"] if s["name"] == "later in time")
    assert signal["effect"] < 0
    assert signal["note"] == "It happens later in the story's time than this scene."


def test_the_end_of_a_flash_forward_just_before_follows_the_story_time_policy(
    auth_client, fake_container
):
    # Reading order A, B, C; B (year 9) is a flash-forward read just before C (year 5).
    # B's end is no different from any other later material: listed with its reason, not
    # given; and B's words found as passages are listed too, not silently dropped.
    w = _world(auth_client, fake_container)
    a = _scene(auth_client, w, "A: the arrival")
    b = _scene(auth_client, w, "B: years later")
    c = _scene(auth_client, w, "C: the bell")
    for scene, at in ((a, 1), (b, 9), (c, 5)):
        _at(auth_client, w, scene, at)
    assert _save(auth_client, w, a, "Mara tied up her boat at the harbor.", None).status_code == 200
    gone = "Mara was old now, and the harbor was gone."
    assert _save(auth_client, w, b, gone, None).status_code == 200
    context = _context(auth_client, w, c)
    items, left = _items(context), _left(context)
    assert f"before:{b['id']}" not in items
    end = left[f"before:{b['id']}"]
    assert end["reason"] == "later"
    assert "It happens later in the story's time than this scene." in end["item"]["reason"]
    assert "the harbor was gone" not in context["rendered"]
    # Listed once: the end, with its own "Use it", not again as a passage of the same words.
    version = end["item"]["record_id"]
    assert not [
        key
        for key, omission in left.items()
        if omission["item"]["section"] == "writing" and omission["item"]["record_id"] == version
    ]

    # A policy that only lowers it gives it, lowered and saying why.
    service = fake_container.context_service
    service.policy = replace(SCENE_DRAFT_POLICY, later_in_story_time=LaterInTime.LOWER)
    try:
        lowered = _items(_context(auth_client, w, c))[f"before:{b['id']}"]
    finally:
        service.policy = SCENE_DRAFT_POLICY
    signal = next(s for s in lowered["signals"] if s["name"] == "later in time")
    assert signal["effect"] < 0 and lowered["score"] < 7.0


def test_search_by_meaning_never_scores_a_text_later_in_the_storys_time(
    auth_client, fake_container, monkeypatch
):
    w = _world(auth_client, fake_container)
    a, b, _x, c = _timed_story(auth_client, w)
    index = fake_container.passage_index
    asked: list[set[str] | None] = []

    async def scores(workspace_id, project_id, query, *, source_ids=None, limit=20):
        asked.append(set(source_ids) if source_ids is not None else None)
        return {}

    monkeypatch.setattr(index, "semantic_scores", scores)
    monkeypatch.setattr(type(index), "semantic", property(lambda self: True))
    _context(auth_client, w, c)
    # Only A's words are searched: B's scene comes later in the story's time.
    assert len(asked[0] or ()) == 1
    service = fake_container.context_service
    service.policy = replace(SCENE_DRAFT_POLICY, later_in_story_time=LaterInTime.LOWER)
    try:
        asked.clear()
        _context(auth_client, w, c)
    finally:
        service.policy = SCENE_DRAFT_POLICY
    # A policy that only lowers it searches both.
    assert len(asked[0] or ()) == 2
    assert a and b


# ------------------------------------------------------ the ends of nearby scenes (6A)


def test_the_end_of_a_recent_scene_on_the_same_thread_is_given_with_a_warning(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    thread = _part(auth_client, w, "thread", "The guild's debt")
    opener = _scene(auth_client, w, "A: the debt", pov="mara", sets_up=[thread["id"]])
    _scene(auth_client, w, "B: elsewhere", pov="mara")
    _scene(auth_client, w, "C: the wait", pov="mara")
    closer = _scene(auth_client, w, "D: the payment", pays_off=[thread["id"]])
    tail = "Mara counted the coins twice and said nothing to anyone."
    assert _save(auth_client, w, opener, tail, None).status_code == 200
    item = _items(_context(auth_client, w, closer))[f"nearby:{opener['id']}"]
    assert item["section"] == "nearby"
    assert item["section_title"] == "How other recent scenes end"
    assert item["text"].startswith("From the end of “A: the debt”: Mara counted")
    # Seen through other eyes: the warning is in the words the assistant reads.
    assert "It is seen through Mara; Tobin Ashe may not know all of it." in item["text"]
    assert "It is on the thread “The guild's debt” too." in item["reason"]
    assert "It is three scenes back." in item["reason"]


def test_no_more_than_two_nearby_scene_ends_and_never_one_of_another_viewpoint_alone(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    scenes = [_scene(auth_client, w, f"{n}: scene", pov="tobin") for n in range(1, 5)]
    other = _scene(auth_client, w, "5: through Mara", pov="mara")
    target = _scene(auth_client, w, "6: now")
    for scene in [*scenes, other]:
        words = f"The end of {scene['title']}, as Tobin waits."
        assert _save(auth_client, w, scene, words, None).status_code == 200
    items = _items(_context(auth_client, w, target))
    nearby = [key for key in items if key.startswith("nearby:")]
    # The scene before is given as such; of the rest, the two nearest seen by Tobin.
    assert set(nearby) == {f"nearby:{scenes[3]['id']}", f"nearby:{scenes[2]['id']}"}
    assert f"before:{other['id']}" in items


# ------------------------------------------------ summaries of arcs and beats (CTX-006)


def _parts_story(client, w):
    """Beat one: two scenes; beat two: five scenes and the scene to write."""
    one = _part(client, w, "beat", "1: the arrival")
    two = _part(client, w, "beat", "2: the fog")
    first = [_scene(client, w, f"1.{n}", parent=one["id"]) for n in (1, 2)]
    second = [_scene(client, w, f"2.{n}", parent=two["id"]) for n in range(1, 6)]
    target = _scene(client, w, "2.6", parent=two["id"])
    for scene, words in zip(
        first, ("Mara comes home.", "Tobin sees her at the quay."), strict=True
    ):
        assert _save(client, w, scene, words + " The tide is in.", None).status_code == 200
        kept = client.put(f"{w['base']}/plan/{scene['id']}/summary", json={"body": words})
        assert kept.status_code == 200, kept.text
    return one, two, first, second, target


def test_an_earlier_beat_is_given_as_a_summary_worked_out_from_its_scenes(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    one, two, first, _second, target = _parts_story(auth_client, w)

    view = auth_client.get(f"{w['base']}/plan/{one['id']}/part-summary").json()
    assert view["kind"] == "beat" and view["scenes"] == 2 and view["unsummarised"] == []
    assert view["derived"] == "Mara comes home. Tobin sees her at the quay."
    assert view["kept"] is None and view["suggested"] == []

    items = _items(_context(auth_client, w, target))
    part = items[f"part:{one['id']}"]
    assert part["section"] == "earlier" and part["authority"] == "summary"
    assert part["text"] == (
        "The beat “1: the arrival”, from its scenes' summaries: "
        "Mara comes home. Tobin sees her at the quay."
    )
    assert "worked out from the summaries you kept" in part["reason"]
    assert any(signal["name"] == "distance" for signal in part["signals"])
    # The beat this scene is in is never summarised for it.
    assert f"part:{two['id']}" not in items
    # Nothing written for the second beat's scenes: nothing to ask the assistant about.
    refused = auth_client.post(f"{w['base']}/plan/{two['id']}/part-summary/suggest", json={})
    assert refused.status_code == 400 and "at least one scene" in refused.json()["detail"]
    # A scene is not a part.
    assert auth_client.get(f"{w['base']}/plan/{first[0]['id']}/part-summary").status_code == 400
    assert fake_container.ai.calls == []


def test_an_earlier_beat_holding_a_flash_forward_is_later_material(auth_client, fake_container):
    # Beat one's first scene is in year 1 and its last in year 2, but its first-read
    # scene jumps to year 9; the scene to write is in year 5. The beat's summary tells of
    # year 9, so the policy treats it as later material, though its last scene is earlier.
    w = _world(auth_client, fake_container)
    one, _two, first, second, target = _parts_story(auth_client, w)
    _at(auth_client, w, first[0], 9)
    _at(auth_client, w, first[1], 2)
    for scene in second:
        _at(auth_client, w, scene, 3)
    _at(auth_client, w, target, 5)
    context = _context(auth_client, w, target)
    assert f"part:{one['id']}" not in _items(context)
    left = _left(context)[f"part:{one['id']}"]
    assert left["reason"] == "later"
    assert "It happens later in the story's time than this scene." in left["item"]["reason"]


def test_a_kept_beat_summary_is_used_and_goes_out_of_date_when_a_scene_summary_changes(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    one, _two, first, _second, target = _parts_story(auth_client, w)
    base = f"{w['base']}/plan/{one['id']}/part-summary"
    written = auth_client.put(base, json={"body": "  Mara comes home, and Tobin sees her. "})
    assert written.status_code == 200, written.text
    assert written.json()["kept"]["body"] == "Mara comes home, and Tobin sees her."
    assert written.json()["kept"]["origin"] == "author" and written.json()["kept"]["scenes"] == 2
    assert written.json()["kept_out_of_date"] is False
    too_long = auth_client.put(base, json={"body": "word " * 201})
    assert too_long.status_code == 400 and "200 words" in too_long.json()["detail"]

    part = _items(_context(auth_client, w, target))[f"part:{one['id']}"]
    assert (
        part["text"] == "The beat “1: the arrival”, in short: Mara comes home, and Tobin sees her."
    )
    assert part["reason"] == (
        "It is your own summary of the beat “1: the arrival”. It is six scenes back."
    )
    assert part["record_type"] == "part_summary"

    # A scene summary in it is kept anew: the beat's summary is out of date, ranked lower.
    again = auth_client.put(
        f"{w['base']}/plan/{first[0]['id']}/summary", json={"body": "Mara comes home, tired."}
    )
    assert again.status_code == 200, again.text
    assert auth_client.get(base).json()["kept_out_of_date"] is True
    stale = _items(_context(auth_client, w, target))[f"part:{one['id']}"]
    assert "out of date" in [signal["name"] for signal in stale["signals"]]
    assert "has changed since this was written" in stale["reason"]
    assert stale["score"] < part["score"]


def test_a_change_to_any_scene_summary_in_a_long_beat_puts_its_summary_out_of_date(
    auth_client, fake_container, monkeypatch
):
    # The assistant reads only the first scenes of a long part (here one), but a kept
    # summary of the part goes out of date when a summary of any scene in it changes.
    from backend.services import scene_summaries

    monkeypatch.setattr(scene_summaries, "MAX_PART_SCENES", 1)
    w = _world(auth_client, fake_container)
    one, _two, first, _second, target = _parts_story(auth_client, w)
    base = f"{w['base']}/plan/{one['id']}/part-summary"
    assert auth_client.put(base, json={"body": "Mara comes home."}).status_code == 200
    assert auth_client.get(base).json()["kept_out_of_date"] is False
    again = auth_client.put(
        f"{w['base']}/plan/{first[1]['id']}/summary", json={"body": "Tobin looks away."}
    )
    assert again.status_code == 200, again.text
    assert auth_client.get(base).json()["kept_out_of_date"] is True
    stale = _items(_context(auth_client, w, target))[f"part:{one['id']}"]
    assert "out of date" in [signal["name"] for signal in stale["signals"]]

    # Asking the assistant sends it only the first scene's summary.
    fake_container.ai.queue(PartSummaryDraft(summary="Mara returns."))
    asked = auth_client.post(f"{base}/suggest", json={})
    assert asked.status_code == 202, asked.text
    (done,) = asyncio.run(fake_container.job_runner.run_pending())
    assert done.status.value == "succeeded", done
    sent = fake_container.ai.calls[-1]["messages"][-1]["content"]
    assert "Mara comes home." in sent and "Tobin looks away." not in sent
    # Its suggestion is on every scene's summary, so it is not out of date at once.
    (suggested,) = auth_client.get(base).json()["suggested"]
    kept = auth_client.post(f"{base}/{suggested['id']}", json={"accept": True})
    assert kept.status_code == 200, kept.text
    assert kept.json()["kept_out_of_date"] is False


def test_a_beat_summary_the_assistant_suggests_is_used_only_once_kept(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    one, _two, _first, _second, target = _parts_story(auth_client, w)
    base = f"{w['base']}/plan/{one['id']}/part-summary"
    fake_container.ai.queue(PartSummaryDraft(summary=" Mara returns.  Tobin notices. "))
    started = auth_client.post(f"{base}/suggest", json={})
    assert started.status_code == 202, started.text
    assert started.json()["kind"] == "part_summary"
    (done,) = asyncio.run(fake_container.job_runner.run_pending())
    assert done.status.value == "succeeded", done
    # The scenes' kept summaries went to the model between marker lines; never the prose.
    sent = fake_container.ai.calls[-1]["messages"][-1]["content"]
    assert "<<<SUMMARIES-1>>>" in sent and "Tobin sees her at the quay." in sent
    assert "The tide is in." not in sent

    (suggested,) = auth_client.get(base).json()["suggested"]
    assert suggested["body"] == "Mara returns. Tobin notices."
    assert suggested["prompt_version"] == "part-summary/1" and suggested["job_id"] == done.id
    # A suggestion is not used: the summary worked out from the scenes is.
    part = _items(_context(auth_client, w, target))[f"part:{one['id']}"]
    assert "from its scenes' summaries" in part["text"]

    kept = auth_client.post(
        f"{base}/{suggested['id']}", json={"accept": True, "body": "Mara returns; Tobin sees."}
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["kept"]["origin"] == "model_corrected"
    assert auth_client.post(f"{base}/{suggested['id']}", json={"accept": False}).status_code == 400
    part = _items(_context(auth_client, w, target))[f"part:{one['id']}"]
    assert part["text"].endswith("in short: Mara returns; Tobin sees.")
    assert "a summary the assistant suggested and you corrected" in part["reason"]


def test_beat_summaries_keep_to_their_line_of_the_story_and_travel_in_the_archive(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    one, _two, _first, _second, target = _parts_story(auth_client, w)
    base = f"{w['base']}/plan/{one['id']}/part-summary"
    assert auth_client.put(base, json={"body": "Mara is home."}).status_code == 200
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Mara stays away"}).json()[
        "id"
    ]
    headers = {"X-Story-Branch": branch}
    # The what-if uses the main story's until it keeps its own.
    assert auth_client.get(base, headers=headers).json()["kept"]["body"] == "Mara is home."
    own = auth_client.put(base, json={"body": "Mara never comes."}, headers=headers)
    assert own.status_code == 200, own.text
    in_what_if = _items(_context(auth_client, w, target, headers))[f"part:{one['id']}"]
    assert in_what_if["text"].endswith("Mara never comes.")
    assert _items(_context(auth_client, w, target))[f"part:{one['id']}"]["text"].endswith(
        "Mara is home."
    )

    summaries = fake_container.scene_summaries
    parts = summaries.parts._store  # noqa: SLF001 - the archive's own view of the rows
    workspace_id, project_id, _ = next(iter(parts.rows))
    exported = asyncio.run(summaries.export_summaries(workspace_id, project_id))
    assert {(row["body"], row["branch_id"]) for row in exported if row.get("part")} == {
        ("Mara is home.", None),
        ("Mara never comes.", branch),
    }
    parts.rows.clear()
    summaries._store.rows.clear()  # noqa: SLF001
    restored = asyncio.run(summaries.restore_summaries(workspace_id, project_id, exported))
    # The two scene summaries and the main story's beat summary; the what-if's is left
    # out without its what-if.
    assert restored == 3
    (only,) = parts.rows.values()
    assert only.body == "Mara is home." and only.branch_id is None
