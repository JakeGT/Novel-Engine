"""A check against the scene's pictures is kept with the pictures it read (CON-001).

The run keeps each picture and version it was given and how (never the picture or its
words); read back, each says what has changed since, and each note of the pictures pass
names the picture it was compared with and is out of date once that picture changed.
"""

import pytest

from backend.services.consistency_runs import PictureRead, compared_with, picture_changes
from backend.services.scene_checks import PictureNote
from tests.contract.test_picture_context import _check, _choose, _scene_with_picture

pytestmark = pytest.mark.contract


def _runs(client, w, scene) -> list[dict]:
    got = client.get(f"{w['base']}/plan/{scene['id']}/checks/runs")
    assert got.status_code == 200, got.text
    return got.json()["items"]


def test_a_pictures_check_keeps_the_pictures_it_read_and_says_when_they_change(
    auth_client, fake_container
):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)
    _choose(auth_client, w, asset_id, "send")
    fake_container.ai.sees_images = True
    job = _check(
        auth_client,
        fake_container,
        w,
        scene,
        [
            PictureNote(
                quote="Mara stood in her red coat",
                problem="Her picture shows a blue coat.",
                picture="Picture 1",
            )
        ],
    )
    (run,) = [item for item in _runs(auth_client, w, scene) if item["job_id"] == job.id]
    assert run["kind"] == "assistant" and "pictures" in run["passes"]
    (part,) = run["scenes"]
    (picture,) = part["pictures"]
    assert picture["asset_id"] == asset_id and picture["choice"] == "send"
    assert picture["label"] == "“Mara on the quay”, version 1"
    assert picture["changed"] is None
    assert not run["stale"]
    (note,) = [item for item in run["findings"] if item["source"] == "note"]
    assert note["check_pass"] == "pictures" and note["picture"]["asset_id"] == asset_id
    assert note["stale"] is False

    # The author now gives it as its description instead: the run says so.
    _choose(auth_client, w, asset_id, "describe")
    (run,) = [item for item in _runs(auth_client, w, scene) if item["job_id"] == job.id]
    (picture,) = run["scenes"][0]["pictures"]
    assert picture["changed"] == (
        "It is now given another way (the picture itself or its description)."
    )
    (note,) = [item for item in run["findings"] if item["source"] == "note"]
    assert note["status"] == "open" and note["stale"] is True
    assert run["stale"] is True
    # Fetched on its own, the run says the same.
    alone = auth_client.get(f"{w['base']}/checks/runs/{run['id']}").json()
    assert alone["scenes"][0]["pictures"][0]["changed"] == picture["changed"]

    # And left out, it is no longer read at all.
    _choose(auth_client, w, asset_id, "leave_out")
    (run,) = [item for item in _runs(auth_client, w, scene) if item["job_id"] == job.id]
    assert run["scenes"][0]["pictures"][0]["changed"] == (
        "It is no longer given with the scene, or is now left out."
    )


def test_a_check_without_the_pictures_pass_keeps_no_pictures(auth_client, fake_container):
    w, scene, _asset_id = _scene_with_picture(auth_client, fake_container)
    auth_client.put(f"{w['base']}/plan/{scene['id']}/studio/text", json={"content": "Gulls."})
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews", json={"passes": ["continuity"]}
    )
    assert started.status_code == 202, started.text
    from tests.contract.test_picture_context import _run

    fake_container.ai.queue({"notes": []})
    _run(fake_container)
    assistant = [item for item in _runs(auth_client, w, scene) if item["kind"] == "assistant"]
    assert assistant and all(not part["pictures"] for part in assistant[0]["scenes"])


def _read(asset: str, version: str = "v1", described: str = "d1", choice: str = "send"):
    return PictureRead(asset, version, f"“{asset}”, version 1", choice, described)


def test_what_changed_about_a_picture_is_said_in_plain_words() -> None:
    then = [_read("a"), _read("b"), _read("c"), _read("d")]
    now = [_read("a"), _read("b", version="v2"), _read("c", described="d2")]
    changed = {item.asset_id: item.changed for item in picture_changes(then, now)}
    assert changed == {
        "a": None,
        "b": "A newer version of it has been added since.",
        "c": "Its accepted description has changed since.",
        "d": "It is no longer given with the scene, or is now left out.",
    }


def test_a_note_names_the_picture_it_was_compared_with() -> None:
    pictures = [_read("a"), _read("b")]
    message = "Her coat is blue there. Compared with the picture “b”, version 1."
    found = compared_with(message, pictures)
    assert found is not None and found.asset_id == "b"
    assert compared_with("No picture named.", pictures) is None
