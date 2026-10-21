"""Characters, places, a timeline and a recap as separate parts of the reader's guide,
each as spoiler-safe as the guide itself (QRY-002)."""

import asyncio

import pytest

from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import CreateEventOp
from backend.domain.worldtime import At
from tests.contract.test_god_hand import _world
from tests.contract.test_knowledge import _scene, _secret

pytestmark = pytest.mark.contract


def _guide(client, w, scene: str, part: str, **params):
    made = client.get(f"{w['base']}/reader/guide", params={"scene": scene, "part": part, **params})
    assert made.status_code == 200, made.text
    return made.json()


def _event_in(container, w, head: int, name: str, scene_id: str, at: float, who: list[str]):
    settings = container.settings
    scope = AccessScope(
        actor_user_id=settings.AUTH_LOCAL_USER_ID,
        auth_mode=AuthMode.LOCAL,
        workspace_id=settings.AUTH_LOCAL_WORKSPACE_ID,
        project_id=settings.AUTH_LOCAL_PROJECT_ID,
    )
    from backend.domain.canon.events import EventParticipant

    async def commit():
        draft = await container.mutation_service.create_draft(
            scope,
            workspace_id=scope.workspace_id,
            project_id=scope.project_id,
            operations=[
                CreateEventOp(
                    id="tmp:e",
                    kind="happening",
                    name=name,
                    participants=[EventParticipant(entity_id=w[p], role="present") for p in who],
                    when=At(at=at),
                    scene_id=scene_id,
                )
            ],
            expected_head=head,
        )
        return await container.mutation_service.commit(scope, draft.id)

    return asyncio.run(commit())


def _story(client, container) -> tuple[dict, str, str]:
    """Two scenes: the first at the docks (year 5) with Mara and Tobin, showing nothing;
    the second (year 3, a flashback) shows Mara's secret guild membership and hints at
    where she is. A storm happens in the first scene; a duel happens in neither."""
    w = _world(client, container)
    head = int(w["head"])
    _secret(client, w, "in_guild", head)
    first = _scene(client, w, head + 1, "The Docks", pov="mara")
    client.patch(
        f"{w['base']}/plan/{first['id']}",
        json={
            "refs": [
                {"entity_id": w["mara"], "role": "point_of_view"},
                {"entity_id": w["harbor"], "role": "place"},
                {"entity_id": w["tobin"], "role": "participant"},
            ],
            "summary": "Secretly, Tobin is the guild's spy.",
            "expected_revision": head + 2,
        },
    )
    second = _scene(client, w, head + 3, "Before the Docks", pov="mara")
    client.put(
        f"{w['base']}/plan/{second['id']}/reveals",
        json={
            "reveals": [
                {"fact_id": w["in_guild"], "how": "shown"},
                {"fact_id": w["at_harbor"], "how": "hinted"},
            ],
            "expected_revision": head + 4,
        },
    )
    for scene, at, version in ((first, 5, head + 5), (second, 3, head + 6)):
        timed = client.put(
            f"{w['base']}/plan/{scene['id']}/when",
            json={"when": {"kind": "at", "at": at}, "expected_revision": version},
        )
        assert timed.status_code == 200, timed.text
    _event_in(container, w, head + 7, "The storm", first["id"], 5, ["mara", "tobin"])
    duel = client.post(
        f"{w['base']}/events",
        json={
            "kind": "happening",
            "name": "The duel",
            "when": {"kind": "at", "at": 1},
            "participants": [{"entity_id": w["mara"], "role": "fighter"}],
            "expected_revision": head + 8,
        },
    )
    assert duel.status_code == 201, duel.text
    return w, first["id"], second["id"]


def test_characters_and_places_are_the_guides_own_sections(auth_client, fake_container):
    w, first, _ = _story(auth_client, fake_container)
    people = _guide(auth_client, w, first, "characters")
    assert [s["heading"] for s in people["sections"]] == ["People"]
    assert {e["name"] for e in people["sections"][0]["entries"]} == {"Mara", "Tobin Ashe"}
    assert people["title"] == f"Characters so far, after “The Docks” — version {people['version']}"
    assert people["part"] == "characters" and "part: characters\n" in people["markdown"]
    # The secret is not shown by the first scene.
    assert "Harbor Guild" not in people["markdown"]
    places = _guide(auth_client, w, first, "places")
    assert [s["heading"] for s in places["sections"]] == ["Places"]
    assert [e["name"] for e in places["sections"][0]["entries"]] == ["Lantern Harbor"]


def test_the_timeline_holds_scenes_read_so_far_and_what_happened_in_them(
    auth_client, fake_container
):
    w, first, second = _story(auth_client, fake_container)
    after_first = _guide(auth_client, w, first, "timeline")
    (placed,) = after_first["sections"]
    assert placed["heading"] == "In story time"
    assert [e["name"] for e in placed["entries"]] == ["Scene 1: The Docks", "The storm"]
    assert placed["entries"][1]["lines"] == [
        "When: year 5",
        "In scene 1, “The Docks”.",
        "Who: Mara, Tobin Ashe.",
    ]
    after_second = _guide(auth_client, w, second, "timeline")
    names = [e["name"] for e in after_second["sections"][0]["entries"]]
    # In story-time order, the flashback first; the duel is told in no scene read so far.
    assert names == ["Scene 2: Before the Docks", "Scene 1: The Docks", "The storm"]
    assert "duel" not in after_second["markdown"]
    as_tobin = _guide(auth_client, w, second, "timeline", viewpoint=w["tobin"])
    assert [e["name"] for e in as_tobin["sections"][0]["entries"]] == [
        "Scene 1: The Docks",
        "The storm",
    ]


def test_the_recap_shows_what_each_scene_shows_and_never_the_plans_notes(
    auth_client, fake_container
):
    w, first, second = _story(auth_client, fake_container)
    recap = _guide(auth_client, w, second, "recap")
    (section,) = recap["sections"]
    docks, before = section["entries"]
    assert docks["name"] == "Scene 1: The Docks"
    assert docks["lines"] == [
        "Seen through Mara’s eyes.",
        "Where: Lantern Harbor.",
        "Also there: Tobin Ashe.",
        "Shows the reader nothing plainly yet.",
    ]
    assert before["lines"] == [
        "Seen through Mara’s eyes.",
        "Set earlier in story time than the scene before it.",
        "Shows: Mara is a member of Harbor Guild.",
    ]
    assert "spy" not in recap["markdown"] and "located at" not in str(before["lines"])
    assert "Hints, and the plan's own notes" in recap["leaves_out"]
    as_tobin = _guide(auth_client, w, second, "recap", viewpoint=w["tobin"])
    assert all(
        "Shows:" not in line for e in as_tobin["sections"][0]["entries"] for line in e["lines"]
    )


def test_an_unknown_part_is_refused(auth_client, fake_container):
    w, first, _ = _story(auth_client, fake_container)
    refused = auth_client.get(
        f"{w['base']}/reader/guide", params={"scene": first, "part": "spoilers"}
    )
    assert refused.status_code == 422
    everything = _guide(auth_client, w, first, "everything")
    assert everything["title"].startswith("The story so far") and everything["part"] == "everything"
