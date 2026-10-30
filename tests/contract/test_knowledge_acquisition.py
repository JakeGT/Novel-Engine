"""How a character came to know something (KNW-002), and knowledge used before it was
learned (CON-006): warnings in the studio and the plan, never a block."""

import pytest

from tests.contract.test_context import _head, _scene
from tests.contract.test_god_hand import _world
from tests.contract.test_knowledge import _approve, _knows, _prop
from tests.contract.test_studio import _save, _studio

pytestmark = pytest.mark.contract


def _tell(client, w, speaker: str, listener: str, prop: dict, **extra) -> dict:
    told = client.post(
        f"{w['base']}/knowledge/tell",
        json={
            "speaker_id": w[speaker],
            "listener_ids": [w[listener]],
            "proposition": prop,
            "expected_revision": _head(client, w),
            **extra,
        },
    )
    assert told.status_code == 201, told.text
    _approve(client, w, told.json()["mutation_id"])
    return told.json()


def _learned(client, w, who: str) -> list[dict]:
    return _knows(client, w, who)["learned"]


def test_each_belief_says_how_and_when_it_was_learned(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    market = _scene(auth_client, w, [("tobin", "point_of_view")], title="The Market")
    # Told in a scene.
    _tell(
        auth_client,
        w,
        "mara",
        "tobin",
        _prop(w, "mara", "MEMBER_OF", "guild"),
        scene_id=market["id"],
    )
    # Seen at a time.
    seen = auth_client.post(
        f"{w['base']}/knowledge/witness",
        json={
            "name": "Mara steps off the boat",
            "present_ids": [w["tobin"]],
            "proposition": _prop(w, "mara", "LOCATED_AT", "harbor"),
            "when": {"kind": "at", "at": 12},
            "expected_revision": _head(auth_client, w),
        },
    )
    assert seen.status_code == 201, seen.text
    _approve(auth_client, w, seen.json()["mutation_id"])
    beliefs = auth_client.get(
        f"{w['base']}/beliefs", params={"holder_entity_id": w["tobin"]}
    ).json()["items"]
    in_guild = next(b for b in beliefs if b["predicate"] == "MEMBER_OF")

    # Worked out from a fact and his own belief.
    worked = auth_client.post(
        f"{w['base']}/knowledge/infer",
        json={
            "holder_id": w["tobin"],
            "proposition": _prop(w, "mara", "KNOWS", "tobin"),
            "premises": [w["at_harbor"], in_guild["id"]],
            "scene_id": market["id"],
            "expected_revision": _head(auth_client, w),
        },
    )
    assert worked.status_code == 201, worked.text
    # Saved at once: it is the author's own change, like recording a claim.
    event = auth_client.get(f"{w['base']}/events/{worked.json()['event_id']}").json()
    assert event["kind"] == "inferred"
    assert event["premises"] == [w["at_harbor"], in_guild["id"]]
    assert event["scene_id"] == market["id"]

    learned = {item["how"]: item for item in _learned(auth_client, w, "tobin")}
    assert learned["told"]["words"] == "Told by Mara"
    assert learned["told"]["scene_title"] == "The Market"
    assert learned["told"]["source_id"] == w["mara"]
    assert learned["saw"]["words"] == "Saw it: Mara steps off the boat"
    assert learned["saw"]["when"] == "year 12"
    assert learned["worked_out"]["words"] == "Worked it out"
    assert [(p["id"], p["kind"]) for p in learned["worked_out"]["premises"]] == [
        (w["at_harbor"], "fact"),
        (in_guild["id"], "belief"),
    ]
    assert learned["worked_out"]["premises"][0]["sentence"] == "Mara is located at Lantern Harbor."
    # And what he worked out is something he now knows.
    assert w["knows"] in {k["fact"]["id"] for k in _knows(auth_client, w, "tobin")["knows"]}


def test_an_inference_is_refused_in_plain_words_when_it_cannot_stand(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = _head(auth_client, w)
    mara_belief = auth_client.post(
        f"{w['base']}/beliefs",
        json={
            "holder_entity_id": w["mara"],
            "proposition": _prop(w, "tobin", "LOCATED_AT", "saltmere"),
            "source_ref": "author",
            "expected_revision": head,
        },
    ).json()

    def infer(**body):
        return auth_client.post(
            f"{w['base']}/knowledge/infer",
            json={
                "holder_id": w["tobin"],
                "proposition": _prop(w, "mara", "KNOWS", "tobin"),
                "expected_revision": _head(auth_client, w),
                **body,
            },
        )

    assert infer(premises=[]).status_code == 422
    other = infer(premises=[mara_belief["id"]])
    assert other.status_code == 400
    assert "not from what someone else believes" in other.json()["detail"]
    assert infer(premises=["fact:nope"]).status_code == 400
    itself = infer(premises=[w["knows"]])
    assert itself.status_code == 400 and "from itself" in itself.json()["detail"]
    assert infer(premises=[w["at_harbor"]], scene_id="plan:nope").status_code == 400
    assert infer(premises=[w["at_harbor"]]).status_code == 201
    again = infer(premises=[w["in_guild"]])
    assert again.status_code == 400 and "already believes" in again.json()["detail"]


def _codes(state: dict) -> set[str]:
    return {check["code"] for check in state["checks"]}


def test_knowledge_used_before_it_is_learned_is_a_warning_in_the_studio_and_the_plan(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    docks = _scene(
        auth_client,
        w,
        [("tobin", "point_of_view"), ("harbor", "place"), ("mara", "participant")],
        title="The Docks",
    )
    market = _scene(auth_client, w, [("tobin", "point_of_view")], title="The Market")
    # The plan shows the reader Mara's guild through Tobin at the docks...
    shown = auth_client.put(
        f"{w['base']}/plan/{docks['id']}/reveals",
        json={"reveals": [{"fact_id": w["in_guild"]}], "expected_revision": _head(auth_client, w)},
    )
    assert shown.status_code == 200, shown.text
    # ...and Tobin says so there...
    said = auth_client.post(
        f"{w['base']}/knowledge/claims",
        json={
            "proposition": _prop(w, "mara", "MEMBER_OF", "guild"),
            "speaker_id": w["tobin"],
            "scene_id": docks["id"],
            "expected_revision": _head(auth_client, w),
        },
    )
    assert said.status_code == 201, said.text
    # ...but Mara only tells him at the market, later in the plan.
    _tell(
        auth_client,
        w,
        "mara",
        "tobin",
        _prop(w, "mara", "MEMBER_OF", "guild"),
        scene_id=market["id"],
    )

    outline = auth_client.get(f"{w['base']}/plan").json()
    flagged = {(o["plan_id"], o["code"]) for o in outline["open"]}
    assert (docks["id"], "shown_before_learned") in flagged
    assert (docks["id"], "said_before_learned") in flagged
    assert not any(
        plan_id == market["id"] and code.endswith("before_learned") for plan_id, code in flagged
    )
    early = next(o for o in outline["open"] if o["code"] == "shown_before_learned")
    assert early["message"] == (
        "This shows the reader “Mara is a member of Harbor Guild” through Tobin Ashe, "
        "who only learns it in “The Market”."
    )

    # The words name everyone in it: a warning beside them. Saving is never refused.
    prose = "Tobin watched the water.\n\nHe knew Mara answered to the Harbor Guild now."
    saved = _save(auth_client, w, docks, prose, None)
    assert saved.status_code == 200, saved.text
    state = saved.json()
    assert {"learned_later", "learned_later_planned", "learned_later_said"} <= _codes(state)
    # The same fact is not said a fourth time for what drafting would give: one problem, one note.
    assert "learned_later_context" not in _codes(state)
    text_check = next(c for c in state["checks"] if c["code"] == "learned_later")
    assert text_check["severity"] == "warning"
    assert prose[text_check["start"] : text_check["end"]].startswith("He knew Mara")
    assert "Tobin Ashe only learns this in “The Market”" in text_check["message"]

    # At the market, where he learns it, nothing is flagged.
    there = _save(auth_client, w, market, prose, None).json()
    assert not {code for code in _codes(there) if code.startswith("learned_later")}


def test_a_secret_learned_later_stays_out_of_what_the_viewpoint_knows_in_earlier_scenes(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    amended = auth_client.post(
        f"{w['base']}/facts/{w['in_guild']}/amend",
        json={"visibility": "secret", "expected_revision": _head(auth_client, w)},
    )
    assert amended.status_code == 200, amended.text
    docks = _scene(
        auth_client, w, [("tobin", "point_of_view"), ("mara", "participant")], title="The Docks"
    )
    market = _scene(auth_client, w, [("tobin", "point_of_view")], title="The Market")
    for scene, at in ((docks, 5), (market, 9)):
        timed = auth_client.put(
            f"{w['base']}/plan/{scene['id']}/when",
            json={"when": {"kind": "at", "at": at}, "expected_revision": _head(auth_client, w)},
        )
        assert timed.status_code == 200, timed.text
    # Told in year eight: after the docks, before the market (by story time, no scene).
    _tell(
        auth_client,
        w,
        "mara",
        "tobin",
        _prop(w, "mara", "MEMBER_OF", "guild"),
        when={"kind": "at", "at": 8},
    )

    def context(scene) -> dict:
        got = auth_client.get(f"{w['base']}/plan/{scene['id']}/context")
        assert got.status_code == 200, got.text
        return got.json()

    def sections(found: dict) -> dict[str, str]:
        placed = {item["key"]: item["section"] for item in found["items"]}
        placed |= {o["item"]["key"]: o["item"]["section"] for o in found["left_out"]}
        return placed

    # At the docks the secret is the author's alone; at the market Tobin holds it.
    assert sections(context(docks))[f"fact:{w['in_guild']}"] == "secrets"
    assert sections(context(market)).get(f"knows:{w['in_guild']}") == "knows"
    assert f"knows:{w['in_guild']}" not in sections(context(docks))

    # The studio warns once, with the plainer check, not as an unknown secret too.
    state = _save(auth_client, w, docks, "Tobin saw Mara wear the Harbor Guild's pin.", None).json()
    codes = [check["code"] for check in state["checks"]]
    assert "learned_later" in codes and "secret" not in codes
    assert "learned_later_context" not in codes  # a secret is not given as true
    assert _studio(auth_client, w, docks)["checks"] == state["checks"]
