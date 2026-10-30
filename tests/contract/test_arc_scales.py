"""Shared scales and whether an arc's fact still holds until its next stage (STY-006)."""

import pytest

from tests.contract.test_arc_paths import _arc
from tests.contract.test_context import _head
from tests.contract.test_god_hand import _world
from tests.contract.test_plan_checks import _add, _outline, _put

pytestmark = pytest.mark.contract

TRUST = {
    "id": "trust",
    "name": "Trust",
    "levels": ["None", "Wary", "Some", "Complete"],
    "ordered": True,
    "one_step": True,
    "about": "How far they trust the crew.",
}


def _scales(client, w, scales: list[dict], version: int):
    return client.put(
        f"{w['base']}/plan/scales", json={"scales": scales, "expected_version": version}
    )


def test_scales_are_saved_versioned_and_shared(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    empty = auth_client.get(f"{w['base']}/plan/scales").json()
    assert empty == {"scales": [], "version": 0}
    saved = _scales(auth_client, w, [TRUST], 0)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    # Saving against an old version is refused, so nobody overwrites another unnoticed.
    stale = _scales(auth_client, w, [TRUST], 0)
    assert stale.status_code == 409
    assert "Someone else changed the scales" in stale.text
    assert _scales(auth_client, w, [TRUST, {**TRUST}], 1).status_code == 422
    assert _scales(auth_client, w, [{**TRUST, "levels": ["Only"]}], 1).status_code == 422


def test_stages_on_a_scale_show_progress_and_long_jumps(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    assert _scales(auth_client, w, [TRUST], 0).status_code == 200
    one = _add(auth_client, w, "scene", "One")
    two = _add(auth_client, w, "scene", "Two")
    arc = _arc(auth_client, w)
    _put(
        auth_client,
        w,
        arc["id"],
        "path",
        {
            "stages": [
                {"name": "Alone", "scale_id": "trust", "level": "None"},
                {"name": "Warming", "scene_id": one["id"], "scale_id": "trust", "level": "Wary"},
                {"name": "Trusts", "scene_id": two["id"], "scale_id": "trust", "level": "Complete"},
            ]
        },
    )
    outline = _outline(auth_client, w)
    [progress] = outline["scales"]
    assert progress["scale"]["name"] == "Trust"
    [row] = progress["rows"]
    assert row["start"] == "None" and row["target"] == "Complete"
    assert [(s["scene_number"], s["level"], s["toward"]) for s in row["steps"]] == [
        (1, "Wary", "toward"),
        (2, "Complete", "reached"),
    ]
    [jump] = [o for o in outline["open"] if o["code"] == "arc_level_jump"]
    assert "moves 2 levels at once" in jump["message"]

    # A level the scale does not have is refused; so is removing a scale in use.
    bad = auth_client.put(
        f"{w['base']}/plan/{arc['id']}/path",
        json={
            "stages": [{"name": "X", "scale_id": "trust", "level": "Total"}],
            "expected_revision": _head(auth_client, w),
        },
    )
    assert bad.status_code == 400 and "no level called" in bad.text
    removing = _scales(auth_client, w, [], 1)
    assert removing.status_code == 400 and "used by" in removing.text
    assert fake_container.ai.calls == []


def test_a_fact_that_stops_before_the_next_stage_is_flagged(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    first = _add(auth_client, w, "scene", "Arrival")
    middle = _add(auth_client, w, "scene", "Storm")
    last = _add(auth_client, w, "scene", "Departure")
    arc = _arc(auth_client, w)
    _put(
        auth_client,
        w,
        arc["id"],
        "path",
        {
            "stages": [
                {"name": "Settled", "scene_id": first["id"], "fact_id": w["at_harbor"]},
                {"name": "Leaves", "scene_id": last["id"]},
            ]
        },
    )
    _put(auth_client, w, first["id"], "when", {"when": {"kind": "at", "at": 1}})
    _put(auth_client, w, middle["id"], "when", {"when": {"kind": "at", "at": 5}})
    ends = auth_client.post(
        f"{w['base']}/facts/{w['at_harbor']}/amend",
        json={"expected_revision": _head(auth_client, w), "ends": {"kind": "at", "at": 3}},
    )
    assert ends.status_code == 200, ends.text
    outline = _outline(auth_client, w)
    [lapse] = [o for o in outline["open"] if o["code"] == "arc_fact_lapses"]
    assert lapse["plan_id"] == arc["id"]
    assert "no longer true when “Storm” happens" in lapse["message"]
    assert "before the next stage is reached in “Departure”" in lapse["message"]


@pytest.mark.asyncio
async def test_the_scales_travel_in_an_archive_and_never_overwrite_ones_there(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    assert _scales(auth_client, w, [TRUST], 0).status_code == 200
    archive = auth_client.post(f"{w['base']}/archive/export").json()
    assert archive["story_time"]["scales"]["scales"][0]["levels"][-1] == "Complete"

    workspace_id = archive["project"]["workspace_id"]
    await fake_container.archive_service.import_project(workspace_id, "restored", archive)
    restored, version = await fake_container.story_clocks.get_scales(
        workspace_id=workspace_id, project_id="restored"
    )
    assert version == 1 and restored["scales"][0]["name"] == "Trust"

    # A story that already has its own scales keeps them.
    own = {"scales": [{**TRUST, "name": "Standing"}]}
    await fake_container.story_clocks.set_scales(
        workspace_id=workspace_id, project_id="kept", scales=own, expected_version=0
    )
    await fake_container.archive_service.import_project(workspace_id, "kept", archive)
    kept, version = await fake_container.story_clocks.get_scales(
        workspace_id=workspace_id, project_id="kept"
    )
    assert version == 1 and kept["scales"][0]["name"] == "Standing"


def test_a_scale_or_level_in_use_on_any_line_cannot_be_removed(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    assert _scales(auth_client, w, [TRUST], 0).status_code == 200
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Mara stays"}).json()["id"]
    inside = {"X-Story-Branch": branch}
    head = int(auth_client.get(f"{w['base']}/projection", headers=inside).json()["head_revision"])
    arc = auth_client.post(
        f"{w['base']}/plan",
        headers=inside,
        json={"kind": "arc", "title": "Mara warms", "expected_revision": head},
    )
    assert arc.status_code == 201, arc.text
    head = int(auth_client.get(f"{w['base']}/projection", headers=inside).json()["head_revision"])
    path = auth_client.put(
        f"{w['base']}/plan/{arc.json()['id']}/path",
        headers=inside,
        json={
            "stages": [{"name": "Wary of all", "scale_id": "trust", "level": "Wary"}],
            "expected_revision": head,
        },
    )
    assert path.status_code == 200, path.text
    # The arc is only on the what-if, and the scales are shared: removing is refused.
    gone = _scales(auth_client, w, [], 1)
    assert gone.status_code == 400 and "used by “Mara warms”" in gone.text
    # So is removing the level it stands on.
    fewer = _scales(auth_client, w, [{**TRUST, "levels": ["None", "Some", "Complete"]}], 1)
    assert fewer.status_code == 400 and "level “Wary”" in fewer.text
    # A level nobody stands on can go.
    kept = _scales(auth_client, w, [{**TRUST, "levels": ["None", "Wary", "Some"]}], 1)
    assert kept.status_code == 200, kept.text
