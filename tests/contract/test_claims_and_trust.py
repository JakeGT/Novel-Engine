"""What is said is not what is true (KNW-005); how characters take what they hear (KNW-006)."""

import pytest

from tests.contract.test_god_hand import _world
from tests.contract.test_knowledge import _approve, _knows, _prop

pytestmark = pytest.mark.contract


def _character(client, w, name: str, head: int) -> str:
    made = client.post(
        f"{w['base']}/entities",
        json={"type": "Character", "name": name, "expected_revision": head},
    )
    assert made.status_code == 201, made.text
    return made.json()["id"]


def _head(client, w) -> int:
    return int(client.get(f"{w['base']}/projection").json()["head_revision"])


def _claim(client, w, head: int, **body) -> dict:
    made = client.post(f"{w['base']}/knowledge/claims", json={**body, "expected_revision": head})
    assert made.status_code == 201, made.text
    return made.json()


# ------------------------------------------------------------------ trust


def test_trust_is_set_on_the_character_and_shapes_what_a_telling_proposes(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    ines = _character(auth_client, w, "Ines", head)
    base = f"{w['base']}/knowledge/characters/{w['tobin']}/trust"
    assert auth_client.get(base).json() == {
        "entity_id": w["tobin"],
        "default": "believes",
        "speakers": {},
    }
    saved = auth_client.put(
        base,
        json={
            "default": "doubts",
            "speakers": {w["mara"]: "disbelieves"},
            "expected_revision": head + 1,
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["speakers"] == {w["mara"]: "disbelieves"}
    assert auth_client.get(base).json()["default"] == "doubts"
    # Kept beside the details, never shown as one.
    entity = auth_client.get(f"{w['base']}/entities/{w['tobin']}").json()
    assert entity["attributes"]["_trust"] == {
        "default": "doubts",
        "speakers": {w["mara"]: "disbelieves"},
    }

    # A character's trust is about others, and only characters speak.
    assert (
        auth_client.put(
            base, json={"speakers": {w["tobin"]: "doubts"}, "expected_revision": head + 2}
        ).status_code
        == 400
    )
    assert (
        auth_client.put(
            base, json={"speakers": {w["harbor"]: "doubts"}, "expected_revision": head + 2}
        ).status_code
        == 400
    )
    assert (
        auth_client.put(base, json={"default": "maybe", "expected_revision": head + 2}).status_code
        == 422
    )

    told = auth_client.post(
        f"{w['base']}/knowledge/tell",
        json={
            "speaker_id": w["mara"],
            "listener_ids": [w["tobin"], ines],
            "proposition": _prop(w, "mara", "LOCATED_AT", "saltmere"),
            "expected_revision": head + 2,
        },
    ).json()
    tobin, ines_said = told["proposals"]
    assert tobin["confidence"] is None
    assert tobin["why"] == "Tobin Ashe was told but does not believe Mara, so no belief is added."
    assert ines_said["confidence"] == 1.0
    assert told["beliefs"] == 1
    review = auth_client.get(f"{w['base']}/mutations/{told['mutation_id']}").json()
    assert [o["operation_type"] for o in review["operations"]] == ["create_event", "assert_belief"]

    # Someone else doubts: told by Ines, Tobin believes it, less firmly.
    from_ines = auth_client.post(
        f"{w['base']}/knowledge/tell",
        json={
            "speaker_id": ines,
            "listener_ids": [w["tobin"]],
            "proposition": _prop(w, "mara", "LOCATED_AT", "saltmere"),
            "expected_revision": head + 2,
        },
    ).json()
    assert from_ines["proposals"][0]["confidence"] == 0.5
    assert "doubts what people tell them" in from_ines["proposals"][0]["why"]

    # Back to the default: nothing is kept.
    reset = auth_client.put(base, json={"expected_revision": head + 2})
    assert reset.status_code == 200
    entity = auth_client.get(f"{w['base']}/entities/{w['tobin']}").json()
    assert "_trust" not in entity["attributes"]


def test_what_people_see_is_proposed_for_those_who_notice_and_written_only_on_approval(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    ines = _character(auth_client, w, "Ines", head)
    seen = auth_client.post(
        f"{w['base']}/knowledge/witness",
        json={
            "name": "Mara slips into the guild hall",
            "present_ids": [w["tobin"], ines],
            "not_noticed": [ines],
            "fact_id": w["in_guild"],
            "expected_revision": head + 1,
        },
    )
    assert seen.status_code == 201, seen.text
    body = seen.json()
    assert [(p["holder_id"], p["how"], p["confidence"]) for p in body["proposals"]] == [
        (w["tobin"], "saw", 1.0)
    ]
    assert body["proposals"][0]["why"] == "Tobin Ashe was there and saw it happen."
    # Nothing written yet: no belief, no event, the same version.
    assert _head(auth_client, w) == head + 1
    assert (
        auth_client.get(f"{w['base']}/beliefs", params={"holder_entity_id": w["tobin"]}).json()[
            "items"
        ]
        == []
    )
    _approve(auth_client, w, body["mutation_id"])
    event = auth_client.get(f"{w['base']}/events/{body['event_id']}").json()
    assert event["kind"] == "witnessed"
    assert {(p["entity_id"], p["role"]) for p in event["participants"]} == {
        (w["tobin"], "witness"),
        (ines, "present"),
    }
    assert w["in_guild"] in [k["fact"]["id"] for k in _knows(auth_client, w, "tobin")["knows"]]

    for refused in (
        {"present_ids": [w["tobin"]], "not_noticed": [w["tobin"]]},
        {"present_ids": [w["tobin"]], "not_noticed": [ines]},
        {"present_ids": [w["harbor"]]},
    ):
        answer = auth_client.post(
            f"{w['base']}/knowledge/witness",
            json={
                "name": "Something",
                "fact_id": w["in_guild"],
                "expected_revision": _head(auth_client, w),
                **refused,
            },
        )
        assert answer.status_code == 400, refused


# ----------------------------------------------------------------- claims


def test_a_claim_is_said_not_true_and_the_facts_decide_whether_it_holds(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    facts_before = auth_client.get(f"{w['base']}/facts").json()["items"]
    # Tobin says Mara is in Saltmere; the facts say she is at the harbor.
    said = _claim(
        auth_client,
        w,
        head,
        proposition=_prop(w, "mara", "LOCATED_AT", "saltmere"),
        speaker_id=w["tobin"],
        perceiver_ids=[w["mara"]],
    )
    assert said["revision"] == head + 1 and said["beliefs"] is None
    # The narration states Mara is in the guild: borne out.
    _claim(auth_client, w, head + 1, proposition=_prop(w, "mara", "MEMBER_OF", "guild"))
    # Mara, narrating, wonders whether she knows Tobin, and denies she is in Saltmere.
    _claim(
        auth_client,
        w,
        head + 2,
        proposition=_prop(w, "mara", "LOCATED_AT", "saltmere"),
        speaker_id=w["mara"],
        narrates=True,
        modality="denies",
    )
    _claim(
        auth_client,
        w,
        head + 3,
        proposition=_prop(w, "tobin", "KNOWS", "mara"),
        modality="wonders",
    )

    # Nothing said became a fact, and nobody came to believe it.
    assert auth_client.get(f"{w['base']}/facts").json()["items"] == facts_before
    assert _knows(auth_client, w, "mara")["unconfirmed"] == []
    assert _knows(auth_client, w, "mara")["wrong"] == []

    claims = auth_client.get(
        f"{w['base']}/knowledge/claims", params={"entity_id": w["mara"]}
    ).json()["items"]
    assert [
        (c["event"]["claim"]["modality"], c["speaker_role"], c["standing"]) for c in claims
    ] == [
        ("states", "speaker", "contradicted"),
        ("states", None, "borne_out"),
        ("denies", "narrator", "borne_out"),
        ("wonders", None, "unconfirmed"),
    ]
    first = claims[0]
    assert first["event"]["kind"] == "claim"
    assert first["event"]["name"] == "Tobin Ashe says Mara is located at Saltmere"
    assert first["speaker_id"] == w["tobin"] and first["perceiver_ids"] == [w["mara"]]
    # About Tobin: what he says, and what is said about him.
    about_tobin = auth_client.get(
        f"{w['base']}/knowledge/claims", params={"entity_id": w["tobin"]}
    ).json()["items"]
    assert len(about_tobin) == 2


def test_a_claim_becomes_a_fact_only_through_review(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    said = _claim(
        auth_client,
        w,
        head,
        proposition=_prop(w, "tobin", "LOCATED_AT", "saltmere"),
        speaker_id=w["mara"],
    )
    promoted = auth_client.post(
        f"{w['base']}/knowledge/claims/{said['event_id']}/promote",
        json={"expected_revision": head + 1},
    )
    assert promoted.status_code == 201, promoted.text
    mutation_id = promoted.json()["mutation_id"]
    review = auth_client.get(f"{w['base']}/mutations/{mutation_id}").json()
    assert review["status"] == "needs_review"
    (operation,) = review["operations"]
    assert operation["operation_type"] == "assert_fact"
    assert operation["payload"]["why"].startswith("Made a fact from what Mara says")
    facts = auth_client.get(f"{w['base']}/facts", params={"subject_id": w["tobin"]}).json()
    assert facts["items"] == []

    _approve(auth_client, w, mutation_id)
    facts = auth_client.get(f"{w['base']}/facts", params={"subject_id": w["tobin"]}).json()
    assert [f["predicate"] for f in facts["items"]] == ["LOCATED_AT"]
    claims = auth_client.get(f"{w['base']}/knowledge/claims").json()["items"]
    assert claims[0]["standing"] == "borne_out"
    # Once the facts say it, there is nothing to promote; a denial never promotes.
    again = auth_client.post(
        f"{w['base']}/knowledge/claims/{said['event_id']}/promote",
        json={"expected_revision": _head(auth_client, w)},
    )
    assert again.status_code == 400
    denied = _claim(
        auth_client,
        w,
        _head(auth_client, w),
        proposition=_prop(w, "mara", "MEMBER_OF", "guild"),
        modality="denies",
    )
    refused = auth_client.post(
        f"{w['base']}/knowledge/claims/{denied['event_id']}/promote",
        json={"expected_revision": _head(auth_client, w)},
    )
    assert refused.status_code == 400
    missing = auth_client.post(
        f"{w['base']}/knowledge/claims/event:nope/promote",
        json={"expected_revision": _head(auth_client, w)},
    )
    assert missing.status_code == 404


def test_hearers_of_a_claim_can_be_proposed_beliefs_by_their_trust(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    ines = _character(auth_client, w, "Ines", head)
    assert (
        auth_client.put(
            f"{w['base']}/knowledge/characters/{ines}/trust",
            json={"speakers": {w["mara"]: "disbelieves"}, "expected_revision": head + 1},
        ).status_code
        == 200
    )
    said = _claim(
        auth_client,
        w,
        head + 2,
        proposition=_prop(w, "mara", "LOCATED_AT", "saltmere"),
        speaker_id=w["mara"],
        perceiver_ids=[w["tobin"], ines],
        modality="suggests",
        hearers_believe=True,
    )
    beliefs = said["beliefs"]
    assert [(p["holder_id"], p["confidence"]) for p in beliefs["proposals"]] == [
        (w["tobin"], 0.5),
        (ines, None),
    ]
    assert beliefs["proposals"][0]["why"].endswith("It was only suggested, so less firmly again.")
    review = auth_client.get(f"{w['base']}/mutations/{beliefs['mutation_id']}").json()
    assert review["origin"] == "derived" and review["status"] == "needs_review"
    (operation,) = review["operations"]
    assert operation["payload"]["source_ref"] == said["event_id"]
    assert _knows(auth_client, w, "tobin")["wrong"] == []
    _approve(auth_client, w, beliefs["mutation_id"])
    assert [b["belief"]["confidence"] for b in _knows(auth_client, w, "tobin")["wrong"]] == [0.5]

    # Only a character speaking to others, saying or suggesting something, has believers.
    for body in (
        {"modality": "wonders", "speaker_id": w["mara"], "perceiver_ids": [w["tobin"]]},
        {"modality": "states", "perceiver_ids": [w["tobin"]]},
        {"modality": "states", "speaker_id": w["mara"]},
        {"modality": "states", "speaker_id": w["mara"], "narrates": True, "perceiver_ids": [ines]},
    ):
        answer = auth_client.post(
            f"{w['base']}/knowledge/claims",
            json={
                "proposition": _prop(w, "mara", "LOCATED_AT", "harbor"),
                "hearers_believe": True,
                "expected_revision": _head(auth_client, w),
                **body,
            },
        )
        assert answer.status_code == 400, body


def test_a_claim_said_in_a_scene_is_listed_with_that_scene(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    scene = auth_client.post(
        f"{w['base']}/plan",
        json={"kind": "scene", "title": "At the docks", "expected_revision": head},
    ).json()
    chapter = auth_client.post(
        f"{w['base']}/plan",
        json={"kind": "thread", "title": "One", "expected_revision": _head(auth_client, w)},
    ).json()
    _claim(
        auth_client,
        w,
        _head(auth_client, w),
        proposition=_prop(w, "mara", "LOCATED_AT", "saltmere"),
        scene_id=scene["id"],
    )
    in_scene = auth_client.get(
        f"{w['base']}/knowledge/claims", params={"scene_id": scene["id"]}
    ).json()["items"]
    assert len(in_scene) == 1 and in_scene[0]["event"]["claim"]["scene_id"] == scene["id"]
    for scene_id in (chapter["id"], "plan:missing"):
        answer = auth_client.post(
            f"{w['base']}/knowledge/claims",
            json={
                "proposition": _prop(w, "mara", "LOCATED_AT", "saltmere"),
                "scene_id": scene_id,
                "expected_revision": _head(auth_client, w),
            },
        )
        assert answer.status_code in (400, 409, 422), answer.text


def test_an_event_cannot_pass_itself_off_as_a_claim(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    made = auth_client.post(
        f"{w['base']}/events",
        json={"kind": "claim", "name": "A bare claim", "expected_revision": int(w["head"])},
    )
    assert made.status_code in (400, 409, 422), made.text


def test_a_claim_made_a_fact_replaces_what_can_be_only_one_thing(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    # The facts say Mara is at the harbor; Tobin says she is in Saltmere, and he is right.
    said = _claim(
        auth_client,
        w,
        head,
        proposition=_prop(w, "mara", "LOCATED_AT", "saltmere"),
        speaker_id=w["tobin"],
    )
    promoted = auth_client.post(
        f"{w['base']}/knowledge/claims/{said['event_id']}/promote",
        json={"expected_revision": head + 1},
    ).json()
    review = auth_client.get(f"{w['base']}/mutations/{promoted['mutation_id']}").json()
    assert [(o["operation_type"], o["payload"]["id"]) for o in review["operations"]] == [
        ("close_fact", w["at_harbor"]),
        ("assert_fact", "tmp:fact-from-claim"),
    ]
    assert review["operations"][0]["payload"]["why"] == (
        "Ended so what Tobin Ashe says can be true instead."
    )
    _approve(auth_client, w, promoted["mutation_id"])
    located = auth_client.get(
        f"{w['base']}/facts", params={"subject_id": w["mara"], "predicate": "LOCATED_AT"}
    ).json()["items"]
    current = [f for f in located if f["closed_revision"] is None]
    assert [f["object"]["entity_id"] for f in current] == [w["saltmere"]]
