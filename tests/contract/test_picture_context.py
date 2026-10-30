"""Pictures in a scene's context: the picture itself, its description, or left out; the
route decides what can actually be sent, and the kept context says exactly what went
(CTX-013, AST-004, AST-007, CON-014).

A secret picture never reaches a point of view that does not know what it shows, and
never goes as a picture at all; a picture whose link applies from a later story time is
not given.
"""

import asyncio

import pytest

from backend.ports import AIUsage, StructuredResponse
from backend.services.scene_checks import PictureNote, PictureNotes
from tests.contract.test_context import _context, _head, _setup
from tests.contract.test_god_hand import _world
from tests.contract.test_media import PNG
from tests.contract.test_picture_observations import _answer, _describe, _review, _view

pytestmark = pytest.mark.contract

PROSE = "Tobin waited at Lantern Harbor while Mara stood in her red coat."


def _run(container) -> list:
    return asyncio.run(container.job_runner.run_pending())


def _scene_with_picture(client, container, *, describe: bool = True) -> tuple[dict, dict, str]:
    """Tobin's scene at the harbour, with Mara in it; a picture of Mara, described and
    its caption and one detail accepted."""
    w = _world(client, container)
    scene = _setup(client, w)
    added = client.post(
        f"{w['base']}/entities/{w['mara']}/pictures",
        params={"expected_revision": _head(client, w), "name": "Mara on the quay"},
        content=PNG,
    )
    assert added.status_code == 201, added.text
    asset_id = added.json()["link"]["asset_id"]
    if describe:
        container.ai.sees_images = True
        container.ai.queue(_answer())
        assert _describe(client, w, asset_id).status_code == 202
        _run(container)
        container.ai.sees_images = False
        container.ai.calls.clear()
        found = {item["text"]: item for item in _view(client, w, asset_id)["observations"]}
        _review(
            client,
            w,
            found["A woman in a long blue coat stands on a quay at dusk."]["id"],
            "accept",
        )
        _review(client, w, found["She wears a long blue coat."]["id"], "accept")
    else:
        _run(container)
    return w, scene, asset_id


def _item(context: dict, key: str) -> dict | None:
    return next((item for item in context["items"] if item["key"] == key), None)


def _left_out(context: dict, key: str) -> dict | None:
    return next((o for o in context["left_out"] if o["item"]["key"] == key), None)


def _choose(client, w, asset_id: str, choice: str) -> None:
    chosen = client.put(f"{w['base']}/pictures/{asset_id}/context", json={"choice": choice})
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["choice"] == choice


def _confirmed(client, url: str, body: dict):
    """Send, and when asked first (pictures going to a company's servers, CTX-013),
    agree and send again, as the studio does once the author agrees."""
    started = client.post(url, json=body)
    if started.status_code == 409 and started.json().get("code") == "needs_confirmation":
        asked = started.json()
        assert asked["sending"]["pictures"], asked
        estimate = asked["field_errors"]["estimated_tokens"]
        started = client.post(
            url,
            json=body,
            headers={"X-Confirm-Tokens": estimate, "X-Confirm-Sending": asked["sending"]["digest"]},
        )
    return started


def _draft(client, container, w, scene) -> dict:
    container.ai.queue("Tobin waited at Lantern Harbor. Mara came down the quay in her blue coat.")
    started = _confirmed(
        client, f"{w['base']}/plan/{scene['id']}/studio/drafts", {"variants": 1, "words": 200}
    )
    assert started.status_code == 202, started.text
    (job,) = _run(container)
    assert job.status.value == "succeeded", job.error_message
    return container.ai.calls[-1]


def _delivery(container) -> dict:
    manifests = list(container.manifests.saved.values())
    return manifests[-1].manifest["delivery"]


def test_a_picture_is_given_by_its_accepted_description_by_default(auth_client, fake_container):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)
    context = _context(auth_client, w, scene)
    item = _item(context, f"picture:{asset_id}")
    assert item is not None and item["section"] == "pictures"
    assert item["text"] == (
        "A picture of Mara (“Mara on the quay”): A woman in a long blue coat stands on a quay "
        "at dusk. She wears a long blue coat."
    )
    assert item["picture"]["choice"] == "describe"
    assert item["picture"]["representation"] == "description"
    assert "accepted description is given in words" in item["reason"]
    assert "She wears a long blue coat." in context["rendered"]

    # A draft is written from the description; no picture goes.
    call = _draft(auth_client, fake_container, w, scene)
    assert call["images"] == []
    assert "She wears a long blue coat." in call["messages"][1]["content"]
    (part,) = _delivery(fake_container)["parts"]
    assert part["representation"] == "description" and part["degraded"] is False


def test_a_picture_to_send_goes_to_a_route_that_can_see_it_and_is_recorded(
    auth_client, fake_container
):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)
    _choose(auth_client, w, asset_id, "send")
    context = _context(auth_client, w, scene)
    item = _item(context, f"picture:{asset_id}")
    # Kept like a pin, and costed as the picture it would be.
    assert item["pinned"] is True and item["picture"]["representation"] == "picture"
    assert item["words"] > len(item["text"].split())

    fake_container.ai.sees_images = True
    call = _draft(auth_client, fake_container, w, scene)
    (image,) = call["images"]
    assert image.asset_id == asset_id and image.ref == "picture 1"
    assert "Picture 1: “Mara on the quay”, version 1." in call["messages"][-1]["content"]
    delivery = _delivery(fake_container)
    assert delivery["degraded"] is False
    (part,) = delivery["parts"]
    assert part["representation"] == "picture" and part["asset_id"] == asset_id
    assert part["checksum"] and part["byte_length"] > 0 and part["rendition"] in ("web", "original")


def test_a_text_only_route_gets_the_description_instead_and_the_record_says_so(
    auth_client, fake_container
):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)
    _choose(auth_client, w, asset_id, "send")
    call = _draft(auth_client, fake_container, w, scene)
    assert call["images"] == [] and fake_container.ai.refused == []
    # Never told it saw a picture.
    assert "Picture 1" not in call["messages"][-1]["content"]
    assert "She wears a long blue coat." in call["messages"][1]["content"]
    delivery = _delivery(fake_container)
    (part,) = delivery["parts"]
    assert delivery["degraded"] is True
    assert part["representation"] == "fallback" and part["degraded"] is True
    assert "cannot see pictures" in part["reason"]


def test_a_picture_with_no_description_is_only_named(auth_client, fake_container):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container, describe=False)
    item = _item(_context(auth_client, w, scene), f"picture:{asset_id}")
    assert "no accepted description yet" in item["text"]
    assert item["picture"]["representation"] == "named"
    _choose(auth_client, w, asset_id, "send")
    _draft(auth_client, fake_container, w, scene)
    (part,) = _delivery(fake_container)["parts"]
    assert part["representation"] == "named" and part["degraded"] is True


def test_a_picture_left_out_is_listed_as_the_authors_choice(auth_client, fake_container):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)
    _choose(auth_client, w, asset_id, "leave_out")
    context = _context(auth_client, w, scene)
    assert _item(context, f"picture:{asset_id}") is None
    assert _left_out(context, f"picture:{asset_id}")["reason"] == "excluded"
    call = _draft(auth_client, fake_container, w, scene)
    assert "blue coat" not in call["messages"][1]["content"]


def _make_secret(client, w, asset_id: str, known_by: list[str]) -> dict:
    view = _view(client, w, asset_id)
    (link,) = [item for item in view["links"] if item["target_record_id"] == w["mara"]]
    changed = client.put(
        f"{w['base']}/pictures/{asset_id}/links/{link['id']}",
        json={"secret": True, "known_by": known_by, "expected_revision": view["revision"]},
    )
    assert changed.status_code == 200, changed.text
    return changed.json()


def test_a_secret_picture_never_reaches_a_viewpoint_that_does_not_know_it(
    auth_client, fake_container
):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)
    _choose(auth_client, w, asset_id, "send")
    view = _make_secret(auth_client, w, asset_id, [w["mara"]])
    (link,) = view["links"]
    assert link["secret"] is True and link["known_by_names"] == ["Mara"]

    context = _context(auth_client, w, scene)
    assert _item(context, f"picture:{asset_id}") is None
    secret = _left_out(context, f"picture:{asset_id}")
    assert secret["reason"] == "secret" and secret["item"]["section"] == "secrets"
    assert "never sent as a picture" in secret["item"]["reason"]
    assert secret["item"].get("picture") is None

    # Even with a route that can see pictures and the picture chosen to send.
    fake_container.ai.sees_images = True
    call = _draft(auth_client, fake_container, w, scene)
    assert call["images"] == []
    assert all("blue coat" not in str(message["content"]) for message in call["messages"])
    assert "delivery" not in list(fake_container.manifests.saved.values())[-1].manifest

    # Tobin is told: now it is his to see.
    _make_secret(auth_client, w, asset_id, [w["tobin"]])
    item = _item(_context(auth_client, w, scene), f"picture:{asset_id}")
    assert item is not None and item["section"] == "pictures"
    call = _draft(auth_client, fake_container, w, scene)
    assert len(call["images"]) == 1


def test_a_picture_applies_only_from_its_story_time(auth_client, fake_container):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)
    view = _view(auth_client, w, asset_id)
    (link,) = view["links"]
    changed = auth_client.put(
        f"{w['base']}/pictures/{asset_id}/links/{link['id']}",
        json={"applies_from": {"kind": "at", "at": 10}, "expected_revision": view["revision"]},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["links"][0]["applies_from"] == "year 10"
    # The scene has no time yet: given, with a note.
    item = _item(_context(auth_client, w, scene), f"picture:{asset_id}")
    assert "may not apply yet" in item["text"]

    def timed(at: int) -> None:
        got = auth_client.put(
            f"{w['base']}/plan/{scene['id']}/when",
            json={"when": {"kind": "at", "at": at}, "expected_revision": _head(auth_client, w)},
        )
        assert got.status_code == 200, got.text

    timed(5)
    context = _context(auth_client, w, scene)
    assert _item(context, f"picture:{asset_id}") is None
    assert _left_out(context, f"picture:{asset_id}") is None
    timed(12)
    item = _item(_context(auth_client, w, scene), f"picture:{asset_id}")
    assert item is not None and "may not apply" not in item["text"]


def test_a_picture_on_the_scene_itself_is_named_in_its_context(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    scene = _setup(auth_client, w)
    added = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/pictures",
        params={"expected_revision": _head(auth_client, w), "name": "The docks at night"},
        content=PNG,
    )
    assert added.status_code == 201, added.text
    asset_id = added.json()["link"]["asset_id"]
    item = _item(_context(auth_client, w, scene), f"picture:{asset_id}")
    assert item["text"] == (
        "There is a picture of this scene (“The docks at night”); it has no accepted "
        "description yet."
    )
    assert item["reason"].startswith("You put it on this scene.")
    assert item["record_type"] == "asset" and item["record_id"] == asset_id


def _check(client, container, w, scene, notes: list[PictureNote]):
    client.put(f"{w['base']}/plan/{scene['id']}/studio/text", json={"content": PROSE})
    container.ai.queue(
        StructuredResponse(
            parsed=PictureNotes(notes=notes),
            model="fake",
            usage=AIUsage(prompt_tokens=300, completion_tokens=20),
        )
    )
    started = _confirmed(
        client, f"{w['base']}/plan/{scene['id']}/studio/reviews", {"passes": ["pictures"]}
    )
    assert started.status_code == 202, started.text
    (job,) = _run(container)
    assert job.status.value == "succeeded", job.error_message
    return job


def test_the_pictures_check_sees_the_pictures_and_cites_which(auth_client, fake_container):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)
    _choose(auth_client, w, asset_id, "send")
    fake_container.ai.sees_images = True
    estimate = auth_client.get(
        f"{w['base']}/plan/{scene['id']}/studio/check-estimate", params={"passes": ["pictures"]}
    )
    assert estimate.status_code == 200 and estimate.json()["total_tokens"] > 0
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
    call = fake_container.ai.calls[-1]
    assert call["response_format"] is PictureNotes and len(call["images"]) == 1
    assert "for one thing only: the scene against its pictures" in call["messages"][0]["content"]
    assert "Picture 1: “Mara on the quay”, version 1." in call["messages"][1]["content"]
    pass_result = job.result["passes"]["pictures"]
    assert pass_result["findings"] == 1
    assert pass_result["delivery"]["parts"][0]["representation"] == "picture"
    studio = auth_client.get(f"{w['base']}/plan/{scene['id']}/studio").json()
    (note,) = studio["assistant"]
    assert note["check_pass"] == "pictures"
    assert note["message"] == (
        "Her picture shows a blue coat. Compared with the picture “Mara on the quay”, version 1."
    )
    assert PROSE[note["start"] : note["end"]] == "Mara stood in her red coat"


def test_the_pictures_check_uses_descriptions_for_a_text_only_route(auth_client, fake_container):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)
    job = _check(auth_client, fake_container, w, scene, [])
    call = fake_container.ai.calls[-1]
    assert call["images"] == []
    assert "She wears a long blue coat." in call["messages"][1]["content"]
    assert job.result["passes"]["pictures"]["delivery"]["parts"][0]["representation"] == (
        "description"
    )


def test_the_pictures_check_makes_no_call_with_nothing_to_look_at(auth_client, fake_container):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container, describe=False)
    estimate = auth_client.get(
        f"{w['base']}/plan/{scene['id']}/studio/check-estimate", params={"passes": ["pictures"]}
    ).json()
    assert estimate["total_tokens"] == 0
    auth_client.put(f"{w['base']}/plan/{scene['id']}/studio/text", json={"content": PROSE})
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews", json={"passes": ["pictures"]}
    )
    assert started.status_code == 202, started.text
    (job,) = _run(fake_container)
    assert job.status.value == "succeeded" and job.result["findings"] == 0
    assert [call for call in fake_container.ai.calls if "images" in call] == []


def test_an_accepted_appearance_fact_flows_into_the_scene(auth_client, fake_container):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)
    found = {item["text"]: item for item in _view(auth_client, w, asset_id)["observations"]}
    offered = auth_client.post(
        f"{w['base']}/observations/{found['She wears a long blue coat.']['id']}/propose", json={}
    ).json()["mutation_id"]
    auth_client.post(f"{w['base']}/mutations/{offered}/approve", json={})
    assert auth_client.post(f"{w['base']}/mutations/{offered}/commit").status_code == 200
    context = _context(auth_client, w, scene)
    facts = [item for item in context["items"] if item["section"] == "true"]
    assert any("She wears a long blue coat" in item["text"] for item in facts)


class _Search:
    """Area B's search, standing in: it answers the given pictures as found by meaning."""

    def __init__(self, asset_ids: list[str]) -> None:
        self.asset_ids = asset_ids
        self.asked: list[dict] = []

    async def pictures_for(self, workspace_id, project_id, **kwargs):
        from backend.services.media_search import MediaSearchResult, PictureCandidate

        self.asked.append(kwargs)
        return MediaSearchResult(
            pictures=[
                PictureCandidate(
                    asset_id=asset_id,
                    asset_version_id=None,
                    segment_id=None,
                    reason="similar",
                    score=0.9,
                )
                for asset_id in self.asset_ids
            ],
            semantic=True,
        )


def test_a_picture_found_by_meaning_comes_after_links_and_is_only_described(
    auth_client, fake_container
):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)
    # A second picture, of Saltmere (not in the scene), described and accepted.
    other = auth_client.post(
        f"{w['base']}/entities/{w['saltmere']}/pictures",
        params={"expected_revision": _head(auth_client, w), "name": "Saltmere at noon"},
        content=PNG,
    ).json()["link"]["asset_id"]
    fake_container.ai.sees_images = True
    fake_container.ai.queue(_answer(caption="A white town above a harbour.", details=[]))
    assert _describe(auth_client, w, other).status_code == 202
    _run(fake_container)
    (caption,) = _view(auth_client, w, other)["observations"]
    _review(auth_client, w, caption["id"], "accept")
    auth_client.put(f"{w['base']}/pictures/{other}/context", json={"choice": "send"})
    search = _Search([asset_id, other])
    fake_container.context_service.search = search

    context = _context(auth_client, w, scene)
    linked, found = _item(context, f"picture:{asset_id}"), _item(context, f"picture:{other}")
    assert linked["authority"] == "picture" and linked["score"] > found["score"]
    assert found["authority"] == "similar picture"
    assert (
        found["text"]
        == "A picture like this scene (“Saltmere at noon”): A white town above a harbour."
    )
    # Never sent, whatever its own choice: only a linked picture may go as itself.
    assert found["picture"]["choice"] == "describe" and found["pinned"] is False
    assert search.asked[0]["gate"].point_of_view is True

    # A secret picture Tobin does not know is never suggested by meaning either.
    view = _view(auth_client, w, other)
    (link,) = view["links"]
    auth_client.put(
        f"{w['base']}/pictures/{other}/links/{link['id']}",
        json={"secret": True, "known_by": [w["mara"]], "expected_revision": view["revision"]},
    )
    assert _item(_context(auth_client, w, scene), f"picture:{other}") is None


def _saltmere_picture(client, container, w) -> str:
    """A second picture, of Saltmere (not in the scene), its caption accepted."""
    other = client.post(
        f"{w['base']}/entities/{w['saltmere']}/pictures",
        params={"expected_revision": _head(client, w), "name": "Saltmere at noon"},
        content=PNG,
    ).json()["link"]["asset_id"]
    container.ai.sees_images = True
    container.ai.queue(_answer(caption="A white town above a harbour.", details=[]))
    assert _describe(client, w, other).status_code == 202
    _run(container)
    container.ai.sees_images = False
    (caption,) = _view(client, w, other)["observations"]
    _review(client, w, caption["id"], "accept")
    return other


def test_a_picture_found_by_meaning_that_the_author_left_out_stays_out(auth_client, fake_container):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)
    other = _saltmere_picture(auth_client, fake_container, w)
    _choose(auth_client, w, other, "leave_out")
    fake_container.context_service.search = _Search([other])

    context = _context(auth_client, w, scene)
    assert _item(context, f"picture:{other}") is None
    assert "white town" not in context["rendered"]
    call = _draft(auth_client, fake_container, w, scene)
    assert all("white town" not in str(message["content"]) for message in call["messages"])


def test_a_picture_left_out_by_its_story_time_is_not_brought_back_by_meaning(
    auth_client, fake_container
):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)
    view = _view(auth_client, w, asset_id)
    (link,) = view["links"]
    auth_client.put(
        f"{w['base']}/pictures/{asset_id}/links/{link['id']}",
        json={"applies_from": {"kind": "at", "at": 10}, "expected_revision": view["revision"]},
    )
    auth_client.put(
        f"{w['base']}/plan/{scene['id']}/when",
        json={"when": {"kind": "at", "at": 5}, "expected_revision": _head(auth_client, w)},
    )
    fake_container.context_service.search = _Search([asset_id])

    context = _context(auth_client, w, scene)
    assert _item(context, f"picture:{asset_id}") is None
    assert "blue coat" not in context["rendered"]


def test_a_secret_version_of_a_linked_picture_is_kept_with_the_secrets(auth_client, fake_container):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)
    _choose(auth_client, w, asset_id, "send")
    versions = fake_container.media_store.versions
    for key, version in list(versions.items()):
        if version.asset_id == asset_id:
            versions[key] = version.model_copy(update={"visibility": "secret"})

    context = _context(auth_client, w, scene)
    assert _item(context, f"picture:{asset_id}") is None
    secret = _left_out(context, f"picture:{asset_id}")
    assert secret["reason"] == "secret" and secret["item"]["section"] == "secrets"
    assert "marked secret" in secret["item"]["reason"]
    fake_container.ai.sees_images = True
    call = _draft(auth_client, fake_container, w, scene)
    assert call["images"] == []
    assert all("blue coat" not in str(message["content"]) for message in call["messages"])


def test_the_names_of_the_pictures_sent_go_inside_the_material_markers(auth_client, fake_container):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)
    _choose(auth_client, w, asset_id, "send")
    fake_container.ai.sees_images = True
    call = _draft(auth_client, fake_container, w, scene)
    user = call["messages"][-1]["content"]
    legend = user.index("Picture 1: “Mara on the quay”, version 1.")
    assert user.index("<<<MATERIAL-") < legend < user.index("<<<END MATERIAL-")


def test_drafting_is_estimated_with_the_pictures_it_sends(auth_client, fake_container):
    w, scene, asset_id = _scene_with_picture(auth_client, fake_container)

    def estimate() -> int:
        got = auth_client.post(
            f"{w['base']}/plan/{scene['id']}/studio/estimate", json={"variants": 2, "words": 200}
        )
        assert got.status_code == 200, got.text
        return got.json()["prompt_tokens"]

    described = estimate()
    _choose(auth_client, w, asset_id, "send")
    # Each of the two drafts reads the picture: at least a picture's cost more each.
    from backend.domain.context_parts import MIN_IMAGE_TOKENS

    assert estimate() - described >= 2 * MIN_IMAGE_TOKENS
