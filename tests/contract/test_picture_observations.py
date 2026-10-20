"""Describing a picture, reviewing what a model saw, and making a fact of it only through a
reviewed proposal (AST-005, AST-007, CTX-013).

The model is scripted. A route is text only unless the test says it can see pictures; a
route that cannot is refused before anything is queued, sent or spent.
"""

import asyncio

import pytest

from backend.ports import AIUsage, ImageInput, StructuredResponse
from backend.services.media_observations import (
    PictureDescription,
    SeenDetail,
    SeenRegion,
)
from tests.contract.test_context import _head
from tests.contract.test_god_hand import _world
from tests.contract.test_media import PNG

pytestmark = pytest.mark.contract


def _run(container) -> list:
    return asyncio.run(container.job_runner.run_pending())


def _picture(client, w, entity: str = "mara", name: str = "Mara on the quay") -> dict:
    added = client.post(
        f"{w['base']}/entities/{w[entity]}/pictures",
        params={"expected_revision": _head(client, w), "name": name},
        content=PNG,
    )
    assert added.status_code == 201, added.text
    return added.json()


def _answer(**overrides) -> StructuredResponse:
    description = PictureDescription(
        caption="A woman in a long blue coat stands on a quay at dusk.",
        confidence=0.9,
        details=[
            SeenDetail(
                detail="She wears a long blue coat.",
                about="Mara",
                region=SeenRegion(x=0.2, y=0.1, width=0.5, height=0.8),
                confidence=0.8,
            ),
            # Named by an alias: still Mara.
            SeenDetail(detail="Her hair is short and grey.", about="the fox", confidence=7),
            # Someone the picture is not shown with: kept, about no one.
            SeenDetail(detail="A man waits behind her.", about="Captain Rook", confidence=0.4),
            # A region that runs off the picture is dropped; the detail stays.
            SeenDetail(
                detail="Gulls circle overhead.",
                region=SeenRegion(x=0.8, y=0.0, width=0.5, height=0.2),
            ),
            SeenDetail(detail="   ", about="Mara"),
        ],
    )
    return StructuredResponse(
        parsed=description.model_copy(update=overrides),
        model="vision-model",
        usage=AIUsage(prompt_tokens=900, completion_tokens=120),
    )


def _view(client, w, asset_id: str) -> dict:
    got = client.get(f"{w['base']}/pictures/{asset_id}")
    assert got.status_code == 200, got.text
    return got.json()


def _describe(client, w, asset_id: str, **headers):
    return client.post(f"{w['base']}/pictures/{asset_id}/describe", headers=headers)


def test_a_picture_is_described_by_a_model_that_can_see_it(auth_client, fake_container):
    fake_container.ai.sees_images = True
    w = _world(auth_client, fake_container)
    added = _picture(auth_client, w)
    asset_id, version_id = added["link"]["asset_id"], added["version"]["id"]

    estimate = auth_client.get(f"{w['base']}/pictures/{asset_id}/describe/estimate").json()
    assert estimate["sees_pictures"] is True and estimate["reason"] is None
    assert estimate["total_tokens"] == estimate["prompt_tokens"] + estimate["completion_tokens"]
    assert estimate["prompt_tokens"] > 85
    assert fake_container.ai.calls == []

    fake_container.ai.queue(_answer())
    started = _describe(auth_client, w, asset_id)
    assert started.status_code == 202, started.text
    assert started.json()["kind"] == "media_describe"
    done = {job.kind: job for job in _run(fake_container)}
    job = done["media_describe"]
    assert job.status.value == "succeeded", job.error_message
    assert job.result["observations"] == 5 and job.result["dropped"] == 1

    (call,) = fake_container.ai.calls
    (image,) = call["images"]
    assert isinstance(image, ImageInput)
    assert image.asset_id == asset_id and image.version_id == version_id
    assert image.ref == "picture 1" and image.content_type.startswith("image/")
    assert call["response_format"] is PictureDescription
    # The model is told which entries the picture is shown with, as material.
    assert "- Mara (character)" in call["messages"][1]["content"]
    # Exactly what went, by version and copy, is kept with the job.
    (part,) = job.result["delivery"]["parts"]
    assert part["representation"] == "picture" and part["version_id"] == version_id
    assert part["rendition"] in ("web", "original") and part["degraded"] is False
    # Spending was recorded once, under the job's own key.
    records = [r for r in fake_container.usage.records if r.idempotency_key]
    assert [r.idempotency_key for r in records] == [f"{job.id}:{job.attempts}:describe"]

    view = _view(auth_client, w, asset_id)
    assert view["context_choice"] == "describe" and view["description"] == ""
    observations = {item["text"]: item for item in view["observations"]}
    caption = observations["A woman in a long blue coat stands on a quay at dusk."]
    assert caption["kind"] == "caption" and caption["state"] == "proposed"
    assert caption["model"] == "vision-model" and caption["prompt_version"] == "describe-picture/1"
    assert caption["job_id"] == job.id and caption["current"] is True
    coat = observations["She wears a long blue coat."]
    assert coat["subject_record_id"] == w["mara"] and coat["subject_name"] == "Mara"
    assert coat["region"] == {"x": 0.2, "y": 0.1, "width": 0.5, "height": 0.8}
    assert observations["Her hair is short and grey."]["subject_record_id"] == w["mara"]
    assert observations["Her hair is short and grey."]["confidence"] == 1.0
    assert observations["A man waits behind her."]["subject_record_id"] is None
    assert observations["Gulls circle overhead."]["region"] is None
    # Nothing in the story changed.
    facts = auth_client.get(f"{w['base']}/facts").json()["items"]
    assert all(fact["predicate"] != "HAS_DETAIL" for fact in facts)


def test_a_text_only_assistant_is_refused_before_anything_is_spent(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    asset_id = _picture(auth_client, w)["link"]["asset_id"]
    _run(fake_container)

    estimate = auth_client.get(f"{w['base']}/pictures/{asset_id}/describe/estimate").json()
    assert estimate["sees_pictures"] is False
    assert "cannot see pictures" in estimate["reason"]
    refused = _describe(auth_client, w, asset_id)
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "cannot_see_pictures"
    assert "nothing was sent" in refused.json()["detail"]
    assert [job for job in fake_container.jobs.jobs.values() if job.kind == "media_describe"] == []
    assert fake_container.ai.calls == [] and fake_container.ai.refused == []

    # Queued while the route could see pictures, run after it no longer can: the job
    # stops before the call, and nothing is spent.
    fake_container.ai.sees_images = True
    assert _describe(auth_client, w, asset_id).status_code == 202
    fake_container.ai.sees_images = False
    (job,) = _run(fake_container)
    assert job.status.value == "failed" and job.error_code == "refused"
    assert "cannot see pictures" in job.error_message
    assert fake_container.ai.calls == []
    assert [r for r in fake_container.usage.records if r.prompt_tokens] == []


def test_the_gateway_itself_refuses_a_picture_it_cannot_take(fake_container):
    """Even a caller that skipped negotiation cannot get a picture past a text-only route."""
    from backend.ports import AIUnsupportedInputError

    image = ImageInput(data=PNG, content_type="image/png", ref="picture 1")
    with pytest.raises(AIUnsupportedInputError):
        asyncio.run(
            fake_container.ai.generate_structured(
                messages=[{"role": "user", "content": "hi"}],
                response_format=PictureDescription,
                images=[image],
            )
        )
    fake_container.ai.sees_images = True
    fake_container.ai.max_image_bytes = 10
    with pytest.raises(AIUnsupportedInputError):
        asyncio.run(
            fake_container.ai.generate_structured(
                messages=[{"role": "user", "content": "hi"}],
                response_format=PictureDescription,
                images=[image],
            )
        )
    assert fake_container.ai.calls == [] and len(fake_container.ai.refused) == 2


def test_describing_asks_first_above_the_threshold(auth_client, fake_container):
    fake_container.ai.sees_images = True
    w = _world(auth_client, fake_container)
    asset_id = _picture(auth_client, w)["link"]["asset_id"]
    settings = auth_client.put(
        f"{w['base']}/assistant-settings", json={"confirm_above_tokens": 1_000}
    )
    assert settings.status_code == 200, settings.text
    asked = _describe(auth_client, w, asset_id)
    assert asked.status_code == 409 and asked.json()["code"] == "needs_confirmation"
    tokens = asked.json()["field_errors"]["estimated_tokens"]
    estimate = auth_client.get(f"{w['base']}/pictures/{asset_id}/describe/estimate").json()
    assert int(tokens) == estimate["total_tokens"]
    sent = _describe(auth_client, w, asset_id, **{"X-Confirm-Tokens": tokens})
    assert sent.status_code == 202, sent.text


def _described(client, container, w) -> tuple[str, dict]:
    container.ai.sees_images = True
    asset_id = _picture(client, w)["link"]["asset_id"]
    container.ai.queue(_answer())
    assert _describe(client, w, asset_id).status_code == 202
    _run(container)
    view = _view(client, w, asset_id)
    return asset_id, {item["text"]: item for item in view["observations"]}


def _review(client, w, observation_id: str, decision: str, words: str | None = None):
    return client.post(
        f"{w['base']}/observations/{observation_id}/review",
        json={"decision": decision, "corrected_text": words},
    )


def test_the_author_accepts_corrects_and_rejects_and_their_words_win(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    asset_id, found = _described(auth_client, fake_container, w)
    caption = found["A woman in a long blue coat stands on a quay at dusk."]
    coat = found["She wears a long blue coat."]
    man = found["A man waits behind her."]

    accepted = _review(auth_client, w, caption["id"], "accept")
    assert accepted.status_code == 200 and accepted.json()["state"] == "accepted"
    assert accepted.json()["reviewed_at"] is not None
    corrected = _review(auth_client, w, coat["id"], "correct", "  She wears a  long green coat. ")
    assert corrected.json()["state"] == "corrected"
    assert corrected.json()["words"] == "She wears a long green coat."
    assert corrected.json()["text"] == "She wears a long blue coat."
    assert _review(auth_client, w, man["id"], "reject").json()["state"] == "rejected"

    # A rejected one stays set aside; an empty correction is refused.
    assert _review(auth_client, w, man["id"], "accept").status_code == 400
    assert _review(auth_client, w, caption["id"], "correct", "   ").status_code == 400
    assert _review(auth_client, w, caption["id"], "accept").status_code == 400
    assert _review(auth_client, w, "obs_missing", "accept").status_code == 404

    view = _view(auth_client, w, asset_id)
    # What a model that cannot see the picture is given: the caption, then the details.
    assert view["description"] == (
        "A woman in a long blue coat stands on a quay at dusk. She wears a long green coat."
    )
    # Reviewing changed nothing in the story.
    assert all(
        fact["predicate"] != "HAS_DETAIL"
        for fact in auth_client.get(f"{w['base']}/facts").json()["items"]
    )


def test_describing_again_supersedes_only_what_nobody_reviewed(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    asset_id, found = _described(auth_client, fake_container, w)
    kept = found["She wears a long blue coat."]
    _review(auth_client, w, kept["id"], "accept")
    fake_container.ai.queue(_answer(caption="A quay at dusk.", details=[]))
    assert _describe(auth_client, w, asset_id).status_code == 202
    (job,) = [job for job in _run(fake_container) if job.kind == "media_describe"]
    assert job.result["superseded"] == 4
    states = {
        item["text"]: item["state"] for item in _view(auth_client, w, asset_id)["observations"]
    }
    assert states["She wears a long blue coat."] == "accepted"
    assert states["A woman in a long blue coat stands on a quay at dusk."] == "superseded"
    assert states["A quay at dusk."] == "proposed"


def test_an_accepted_detail_becomes_a_fact_only_through_a_reviewed_proposal(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    asset_id, found = _described(auth_client, fake_container, w)
    coat, hair = found["She wears a long blue coat."], found["Her hair is short and grey."]
    caption = found["A woman in a long blue coat stands on a quay at dusk."]
    propose = f"{w['base']}/observations/{{}}/propose"

    # Only what the author accepted, and only a detail of how someone looks.
    unreviewed = auth_client.post(propose.format(coat["id"]), json={})
    assert unreviewed.status_code == 400 and "Accept" in unreviewed.json()["detail"]
    _review(auth_client, w, caption["id"], "accept")
    assert auth_client.post(propose.format(caption["id"]), json={}).status_code == 400

    _review(auth_client, w, coat["id"], "correct", "She wears a long green coat.")
    head = _head(auth_client, w)
    offered = auth_client.post(propose.format(coat["id"]), json={})
    assert offered.status_code == 201, offered.text
    mutation_id = offered.json()["mutation_id"]
    assert offered.json()["observation"]["proposal_mutation_set_id"] == mutation_id
    # It waits for review: nothing in the story changed.
    assert _head(auth_client, w) == head
    mutation = auth_client.get(f"{w['base']}/mutations/{mutation_id}").json()
    assert mutation["status"] == "needs_review" and mutation["origin"] == "model"
    (operation,) = [item["payload"] for item in mutation["operations"]]
    assert operation["proposition"] == {
        "subject_id": w["mara"],
        "predicate": "HAS_DETAIL",
        "object": {"kind": "structured", "value": {"appearance": "She wears a long green coat."}},
    }
    # The evidence points at the picture version and region, never at the words.
    assert operation["evidence"] == [
        {
            "source_artefact_id": coat["asset_version_id"],
            "location": "region:0.200,0.100,0.500,0.800",
        }
    ]
    assert auth_client.post(propose.format(coat["id"]), json={}).status_code == 409
    # Once offered, it is decided in the review queue, not by rejecting it here.
    assert _review(auth_client, w, coat["id"], "reject").status_code == 400

    blocked = auth_client.post(f"{w['base']}/mutations/{mutation_id}/commit")
    assert blocked.status_code == 409 and blocked.json()["code"] == "review_required"
    assert (
        auth_client.post(f"{w['base']}/mutations/{mutation_id}/approve", json={}).status_code == 200
    )
    assert auth_client.post(f"{w['base']}/mutations/{mutation_id}/commit").status_code == 200

    # A second detail joins the first: the old fact is closed in the same proposal.
    _review(auth_client, w, hair["id"], "accept")
    second = auth_client.post(propose.format(hair["id"]), json={}).json()["mutation_id"]
    ops = auth_client.get(f"{w['base']}/mutations/{second}").json()["operations"]
    assert [item["payload"]["op"] for item in ops] == ["close_fact", "assert_fact"]
    assert ops[1]["payload"]["proposition"]["object"]["value"] == {
        "appearance": "She wears a long green coat. Her hair is short and grey."
    }
    auth_client.post(f"{w['base']}/mutations/{second}/approve", json={})
    assert auth_client.post(f"{w['base']}/mutations/{second}/commit").status_code == 200
    details = [
        fact
        for fact in auth_client.get(f"{w['base']}/facts").json()["items"]
        if fact["predicate"] == "HAS_DETAIL"
    ]
    assert len(details) == 1
    assert details[0]["object"]["value"]["appearance"].endswith("short and grey.")

    # A detail about no listed entry needs one chosen, and only a person, place or thing.
    man = found["A man waits behind her."]
    _review(auth_client, w, man["id"], "accept")
    assert auth_client.post(propose.format(man["id"]), json={}).status_code == 400
    guild = auth_client.post(propose.format(man["id"]), json={"entity_id": w["guild"]})
    assert guild.status_code == 400
    tobin = auth_client.post(propose.format(man["id"]), json={"entity_id": w["tobin"]})
    assert tobin.status_code == 201, tobin.text
    assert asset_id


def test_pictures_and_observations_are_for_members_only(api_client, fake_container):
    base = f"/api/v1/projects/{fake_container.settings.AUTH_LOCAL_PROJECT_ID}"
    assert api_client.get(f"{base}/pictures/ast_x").status_code == 401
    assert api_client.post(f"{base}/pictures/ast_x/describe").status_code in (401, 403)
    assert api_client.post(
        f"{base}/observations/obs_x/review", json={"decision": "accept"}
    ).status_code in (401, 403)


def test_an_archive_carries_what_the_author_decided_and_the_choice(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    asset_id, found = _described(auth_client, fake_container, w)
    _review(auth_client, w, found["She wears a long blue coat."]["id"], "accept")
    _review(auth_client, w, found["A man waits behind her."]["id"], "reject")
    auth_client.put(f"{w['base']}/pictures/{asset_id}/context", json={"choice": "send"})
    notes = asyncio.run(
        fake_container.media_observations.export_notes(
            fake_container.settings.AUTH_LOCAL_WORKSPACE_ID,
            fake_container.settings.AUTH_LOCAL_PROJECT_ID,
        )
    )
    assert sorted(item["state"] for item in notes["observations"]) == ["accepted", "rejected"]
    assert notes["choices"] == {asset_id: "send"}
    assert all("workspace_id" not in item for item in notes["observations"])


def _secret_link(client, w, asset_id: str, known_by: list[str]) -> None:
    view = _view(client, w, asset_id)
    (link,) = view["links"]
    changed = client.put(
        f"{w['base']}/pictures/{asset_id}/links/{link['id']}",
        json={"secret": True, "known_by": known_by, "expected_revision": view["revision"]},
    )
    assert changed.status_code == 200, changed.text


def _offer(client, w, observation_id: str, **body) -> tuple[list[dict], str]:
    offered = client.post(f"{w['base']}/observations/{observation_id}/propose", json=body)
    assert offered.status_code == 201, offered.text
    mutation_id = offered.json()["mutation_id"]
    mutation = client.get(f"{w['base']}/mutations/{mutation_id}").json()
    assert mutation["status"] == "needs_review", mutation
    return [item["payload"] for item in mutation["operations"]], mutation_id


def _approve(client, w, mutation_id: str) -> None:
    assert client.post(f"{w['base']}/mutations/{mutation_id}/approve", json={}).status_code == 200
    committed = client.post(f"{w['base']}/mutations/{mutation_id}/commit")
    assert committed.status_code == 200, committed.text


def _knows(client, w, character: str) -> set[str]:
    got = client.get(f"{w['base']}/knowledge/characters/{w[character]}")
    assert got.status_code == 200, got.text
    return {item["fact"]["id"] for item in got.json()["knows"]}


def test_a_detail_seen_in_a_secret_picture_is_a_secret_its_knowers_know(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    asset_id, found = _described(auth_client, fake_container, w)
    _secret_link(auth_client, w, asset_id, [w["tobin"]])
    coat = found["She wears a long blue coat."]
    _review(auth_client, w, coat["id"], "accept")
    ops, mutation_id = _offer(auth_client, w, coat["id"])
    fact, belief = ops
    assert fact["op"] == "assert_fact" and fact["visibility"] == "secret"
    # The reviewer is told it is a secret, and who knows it.
    assert "Kept a secret, known only to Tobin" in fact["why"]
    assert belief["op"] == "assert_belief" and belief["holder_entity_id"] == w["tobin"]
    assert belief["proposition"] == fact["proposition"]
    _approve(auth_client, w, mutation_id)
    (detail,) = [
        item
        for item in auth_client.get(f"{w['base']}/facts").json()["items"]
        if item["predicate"] == "HAS_DETAIL"
    ]
    assert detail["visibility"] == "secret"
    assert detail["id"] in _knows(auth_client, w, "tobin")

    # A detail from an open picture never makes the secret words public by joining them.
    fake_container.ai.queue(_answer())
    second = _picture(auth_client, w, name="Mara at the market")["link"]["asset_id"]
    assert _describe(auth_client, w, second).status_code == 202
    _run(fake_container)
    hair = next(
        item
        for item in _view(auth_client, w, second)["observations"]
        if item["text"] == "Her hair is short and grey."
    )
    _review(auth_client, w, hair["id"], "accept")
    ops, mutation_id = _offer(auth_client, w, hair["id"])
    assert [op["op"] for op in ops] == [
        "close_fact",
        "assert_fact",
        "close_belief",
        "assert_belief",
    ]
    joined = ops[1]
    assert joined["visibility"] == "secret"
    assert joined["proposition"]["object"]["value"]["appearance"] == (
        "She wears a long blue coat. Her hair is short and grey."
    )
    assert ops[3]["holder_entity_id"] == w["tobin"]
    _approve(auth_client, w, mutation_id)
    (detail,) = [
        item
        for item in auth_client.get(f"{w['base']}/facts").json()["items"]
        if item["predicate"] == "HAS_DETAIL"
    ]
    assert detail["visibility"] == "secret"
    assert detail["id"] in _knows(auth_client, w, "tobin")
    assert detail["id"] not in _knows(auth_client, w, "mara")


def test_a_detail_seen_in_a_secret_picture_of_a_scene_is_a_secret(auth_client, fake_container):
    from tests.contract.test_context import _setup

    w = _world(auth_client, fake_container)
    scene = _setup(auth_client, w)
    added = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/pictures",
        params={"expected_revision": _head(auth_client, w), "name": "The quay at dusk"},
        content=PNG,
    )
    assert added.status_code == 201, added.text
    asset_id = added.json()["link"]["asset_id"]
    _secret_link(auth_client, w, asset_id, [])
    fake_container.ai.sees_images = True
    fake_container.ai.queue(_answer())
    assert _describe(auth_client, w, asset_id).status_code == 202
    _run(fake_container)
    coat = next(
        item
        for item in _view(auth_client, w, asset_id)["observations"]
        if item["text"] == "She wears a long blue coat."
    )
    _review(auth_client, w, coat["id"], "accept")
    ops, _ = _offer(auth_client, w, coat["id"], entity_id=w["mara"])
    (fact,) = ops
    assert fact["visibility"] == "secret"
    assert "known to no one in the story yet" in fact["why"]


def test_a_detail_seen_in_an_open_picture_says_it_is_ordinary(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    _, found = _described(auth_client, fake_container, w)
    coat = found["She wears a long blue coat."]
    _review(auth_client, w, coat["id"], "accept")
    ops, _ = _offer(auth_client, w, coat["id"])
    (fact,) = ops
    assert fact["visibility"] is None and "not a secret" in fact["why"]
