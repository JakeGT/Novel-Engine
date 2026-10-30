"""Change anything: an entry recorded as the wrong type, and a fact recorded as the wrong
kind, become one reviewed change that is undone in one step (AST-EXP-017)."""

import pytest

from backend.services.canon_change_workflow import ChangePlan, KindChange, NewEvent, TypeChange
from backend.services.canon_creation_workflow import DraftEntity, DraftFact
from tests.contract.test_assistant_conversations import _accept
from tests.contract.test_bible_listing import _commit
from tests.contract.test_context import _head
from tests.contract.test_god_hand import _world
from tests.contract.test_plan_checks import _add, _outline, _put

pytestmark = pytest.mark.contract


def _ask(client, w, plan: ChangePlan, container, instruction: str) -> dict:
    container.ai.queue(plan)
    response = client.post(
        f"{w['base']}/assist/changes",
        json={"instruction": instruction, "expected_revision": int(w["head"])},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _undo(client, base: str, revision: int) -> None:
    preview = client.post(f"{base}/reverts/preview", json={"from_revision": revision - 1})
    assert preview.status_code == 200 and not preview.json()["conflicts"], preview.text
    reverted = client.post(
        f"{base}/reverts",
        json={
            "from_revision": revision - 1,
            "expected_revision": revision,
            "acknowledge_high_impact": True,
        },
    )
    assert reverted.status_code == 200, reverted.text


def test_a_fact_of_the_wrong_kind_is_stated_again_as_the_right_kind(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    base = w["base"]
    body = _ask(
        auth_client,
        w,
        ChangePlan(
            understood="Mara is related to Tobin rather than only knowing him.",
            kind_changes=[
                KindChange(fact=w["knows"], new_kind="RELATED_TO"),
                # A place is not a group one can be a member of.
                KindChange(fact=w["at_harbor"], new_kind="MEMBER_OF"),
                KindChange(fact="fct_missing", new_kind="KNOWS"),
            ],
        ),
        fake_container,
        "Mara and Tobin are family, not just acquainted.",
    )
    assert body["status"] == "needs_review" and body["validation_ok"] is True, body
    reasons = {item["reason"] for item in body["discarded"]}
    assert "“located at” cannot become “member of”: it cannot point to a location" in reasons
    review = auth_client.get(f"{base}/mutations/{body['mutation_id']}").json()
    assert review["requires_review"] is True
    kinds = [(op["operation_type"], op["payload"].get("id")) for op in review["operations"]]
    assert ("close_fact", w["knows"]) in kinds
    (stated,) = [
        op["payload"] for op in review["operations"] if op["operation_type"] == "assert_fact"
    ]
    assert stated["proposition"]["predicate"] == "RELATED_TO"
    assert stated["proposition"]["object"]["entity_id"] == w["tobin"]
    assert "“knows”" in stated["why"]

    revision = _accept(auth_client, base, body["mutation_id"])
    facts = auth_client.get(f"{base}/facts", params={"subject_id": w["mara"]}).json()["items"]
    assert sorted(fact["predicate"] for fact in facts) == ["LOCATED_AT", "MEMBER_OF", "RELATED_TO"]
    _undo(auth_client, base, revision)
    facts = auth_client.get(f"{base}/facts", params={"subject_id": w["mara"]}).json()["items"]
    assert sorted(fact["predicate"] for fact in facts) == ["KNOWS", "LOCATED_AT", "MEMBER_OF"]


def test_an_entry_of_the_wrong_type_changes_type_and_ends_what_no_longer_fits(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    base = w["base"]
    body = _ask(
        auth_client,
        w,
        ChangePlan(
            understood="The Harbor Guild is one person, not a group.",
            type_changes=[
                TypeChange(ref=w["guild"], new_type="Character"),
                TypeChange(ref=w["tobin"], new_type="Spaceship"),
            ],
        ),
        fake_container,
        "The Harbor Guild is actually a single person.",
    )
    assert body["status"] == "needs_review" and body["validation_ok"] is True, body
    assert any("'Spaceship' is not a type of entry" in i["reason"] for i in body["discarded"])
    notes = " ".join(body["notes"])
    assert "Harbor Guild becomes a character. 1 fact a character cannot have is ended." in notes
    review = auth_client.get(f"{base}/mutations/{body['mutation_id']}").json()
    operations = [(op["operation_type"], op["payload"]) for op in review["operations"]]
    assert ("update_entity", {"id": w["guild"], "type": "Character"}) in [
        (kind, {key: payload[key] for key in ("id", "type") if key in payload})
        for kind, payload in operations
    ]
    assert ("close_fact", w["in_guild"]) in [(kind, p.get("id")) for kind, p in operations]

    revision = _accept(auth_client, base, body["mutation_id"])
    assert auth_client.get(f"{base}/entities/{w['guild']}").json()["type"] == "Character"
    _undo(auth_client, base, revision)
    assert auth_client.get(f"{base}/entities/{w['guild']}").json()["type"] == "Faction"
    facts = auth_client.get(f"{base}/facts", params={"subject_id": w["mara"]}).json()["items"]
    assert "MEMBER_OF" in {fact["predicate"] for fact in facts}


def test_a_new_type_that_leaves_a_fact_unfit_is_refused_by_the_validator(
    auth_client, fake_container
):
    # Asked directly, without ending the fact first: refused in plain words.
    w = _world(auth_client, fake_container)
    base = w["base"]
    draft = auth_client.post(
        f"{base}/mutations",
        json={
            "operations": [{"op": "update_entity", "id": w["guild"], "type": "Character"}],
            "expected_revision": int(w["head"]),
        },
    )
    assert draft.status_code in (200, 201), draft.text
    checked = auth_client.post(f"{base}/mutations/{draft.json()['id']}/validate")
    issues = checked.json()["issues"]
    assert checked.json()["ok"] is False
    assert [issue["code"] for issue in issues] == ["type_does_not_fit"]
    assert "“member of” cannot point to a character" in issues[0]["message"]


def _plan_named(client, w) -> tuple[dict, dict]:
    """A scene that reveals the fact that Mara knows Tobin, and an arc stage tied to it."""
    scene = _add(client, w, "scene", "The reunion")
    arc = _add(client, w, "arc", "Finding family", refs=[{"entity_id": w["mara"], "role": "about"}])
    _put(client, w, scene["id"], "reveals", {"reveals": [{"fact_id": w["knows"]}]})
    _put(
        client,
        w,
        arc["id"],
        "path",
        {"stages": [{"name": "Knows him", "scene_id": scene["id"], "fact_id": w["knows"]}]},
    )
    w["head"] = str(_head(client, w))
    return scene, arc


def test_a_fact_of_a_new_kind_is_still_what_the_plan_reveals_and_its_arcs_are_named(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    base = w["base"]
    scene, arc = _plan_named(auth_client, w)
    body = _ask(
        auth_client,
        w,
        ChangePlan(
            understood="Mara is related to Tobin.",
            kind_changes=[KindChange(fact=w["knows"], new_kind="RELATED_TO")],
        ),
        fake_container,
        "Mara and Tobin are family.",
    )
    assert body["validation_ok"] is True, body
    notes = " ".join(body["notes"])
    assert "1 scene that reveals the fact whose kind changes now reveals it" in notes
    assert "The arc “Finding family” still ties a stage to the old record" in notes
    review = auth_client.get(f"{base}/mutations/{body['mutation_id']}").json()
    (stated,) = [
        op["payload"] for op in review["operations"] if op["operation_type"] == "assert_fact"
    ]
    moved = {
        op["payload"]["id"]
        for op in review["operations"]
        if op["operation_type"] == "update_plan_item"
    }
    assert moved == {scene["id"]}

    revision = _accept(auth_client, base, body["mutation_id"])
    new_id = stated["id"]
    assert auth_client.get(f"{base}/facts/{new_id}").json()["predicate"] == "RELATED_TO"
    outline = {item["id"]: item for item in _outline(auth_client, w)["items"]}
    assert [r["fact_id"] for r in outline[scene["id"]]["reveals"]] == [new_id]
    assert [s["fact_id"] for s in outline[arc["id"]]["path"]] == [w["knows"]]

    # Undone in one step: the fact is stated again as a new record, and the scene reveals
    # the old record again, as the note says.
    _undo(auth_client, base, revision)
    outline = {item["id"]: item for item in _outline(auth_client, w)["items"]}
    assert [r["fact_id"] for r in outline[scene["id"]]["reveals"]] == [w["knows"]]
    assert auth_client.get(f"{base}/facts/{w['knows']}").json()["status"] == "closed"
    facts = auth_client.get(f"{base}/facts", params={"subject_id": w["mara"]}).json()["items"]
    assert "KNOWS" in {fact["predicate"] for fact in facts}


def test_a_kind_change_that_does_not_fit_is_set_aside_whole_not_cut_part_way(
    auth_client, fake_container
):
    from backend.services.canon_change_workflow import MAX_CHANGES

    w = _world(auth_client, fake_container)
    base = w["base"]
    body = _ask(
        auth_client,
        w,
        ChangePlan(
            understood="A crowd of new people who know Tobin; and Mara is his family.",
            new_entities=[
                DraftEntity(ref=f"p{n}", type="Character", name=f"Passer-by {n}")
                for n in range(MAX_CHANGES)
            ],
            add_facts=[
                DraftFact(subject_ref=f"p{n}", predicate="KNOWS", object_ref=w["tobin"])
                for n in range(MAX_CHANGES)
            ],
            new_events=[
                NewEvent(ref=f"e{n}", name=f"Meeting {n}", participants=[w["tobin"]])
                for n in range(MAX_CHANGES)
            ],
            kind_changes=[KindChange(fact=w["knows"], new_kind="RELATED_TO")],
        ),
        fake_container,
        "Everyone knows Tobin, and Mara is his family.",
    )
    reasons = [item["reason"] for item in body["discarded"]]
    assert any(
        "Mara: changing “knows” to “related to” did not fit in this change" in r for r in reasons
    ), reasons
    assert any("did not fit in one review" in r for r in reasons), reasons
    review = auth_client.get(f"{base}/mutations/{body['mutation_id']}").json()
    # The fact is not ended without being stated again.
    closed = [
        op["payload"]["id"] for op in review["operations"] if op["operation_type"] == "close_fact"
    ]
    assert w["knows"] not in closed
    assert len(review["operations"]) <= MAX_CHANGES * 2


def test_a_new_type_that_ends_more_than_fits_is_set_aside_whole(auth_client, fake_container):
    from backend.services.canon_change_workflow import MAX_CHANGES

    w = _world(auth_client, fake_container)
    base = w["base"]
    project_id = base.rsplit("/", 1)[1]
    members = MAX_CHANGES * 2 + 10
    operations = []
    for n in range(members):
        operations.append(
            {"op": "create_entity", "id": f"tmp:m{n}", "type": "Character", "name": f"Member {n}"}
        )
        operations.append(
            {
                "op": "assert_fact",
                "id": f"tmp:f{n}",
                "proposition": {
                    "subject_id": f"tmp:m{n}",
                    "predicate": "MEMBER_OF",
                    "object": {
                        "kind": "entity_ref",
                        "entity_id": w["guild"],
                        "project_id": project_id,
                    },
                },
            }
        )
    w["head"] = str(_commit(auth_client, base, operations, int(w["head"])))
    body = _ask(
        auth_client,
        w,
        ChangePlan(
            understood="The Harbor Guild is one person; and Mara is Tobin's family.",
            type_changes=[TypeChange(ref=w["guild"], new_type="Character")],
            kind_changes=[KindChange(fact=w["knows"], new_kind="RELATED_TO")],
        ),
        fake_container,
        "The Harbor Guild is a single person, and Mara is Tobin's family.",
    )
    reasons = [item["reason"] for item in body["discarded"]]
    assert any(
        r.startswith(
            f"Harbor Guild: the new type, with the {members + 1} things it ends, did not fit"
        )
        for r in reasons
    ), reasons
    assert not any("Harbor Guild becomes a character" in note for note in body["notes"])
    review = auth_client.get(f"{base}/mutations/{body['mutation_id']}").json()
    kinds = [(op["operation_type"], op["payload"].get("id")) for op in review["operations"]]
    # Neither the new type nor any of what it would end; the kind change is whole.
    assert all(kind != "update_entity" for kind, _ in kinds)
    assert [i for kind, i in kinds if kind == "close_fact"] == [w["knows"]]
    assert sum(1 for kind, _ in kinds if kind == "assert_fact") == 1
    assert body["validation_ok"] is True


def test_details_the_new_type_does_not_have_go_with_the_old_type(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    base = w["base"]
    project_id = base.rsplit("/", 1)[1]
    draft = auth_client.post(
        f"/api/v1/projects/{project_id}/schemas/drafts",
        json={
            "definition": {
                "label": "ships",
                "description": "",
                "entity_extensions": [
                    {"entity_type": "Ship", "fields": [{"name": "hull", "field_type": "string"}]},
                    {
                        "entity_type": "Station",
                        "fields": [{"name": "orbit", "field_type": "string"}],
                    },
                ],
                "predicate_extensions": [],
            }
        },
    )
    assert draft.status_code == 201, draft.text
    version = draft.json()["version"]["id"]
    published = auth_client.post(f"/api/v1/projects/{project_id}/schemas/{version}/publish")
    assert published.status_code == 200, published.text
    ship = auth_client.post(
        f"{base}/entities",
        json={
            "type": "Ship",
            "name": "The Gull",
            "attributes": {"hull": "oak"},
            "expected_revision": int(w["head"]),
        },
    )
    assert ship.status_code == 201, ship.text
    ship_id = ship.json()["id"]
    w["head"] = str(_head(auth_client, w))

    # By hand, the old detail blocks the new type, in plain words.
    by_hand = auth_client.post(
        f"{base}/mutations",
        json={
            "operations": [{"op": "update_entity", "id": ship_id, "type": "Station"}],
            "expected_revision": int(w["head"]),
        },
    )
    checked = auth_client.post(f"{base}/mutations/{by_hand.json()['id']}/validate").json()
    assert [issue["message"] for issue in checked["issues"]] == [
        "a station has no detail “hull”; remove it in the same change, or keep the type"
    ]

    # Asked of the assistant, the detail goes with the old type and the author is told.
    body = _ask(
        auth_client,
        w,
        ChangePlan(
            understood="The Gull is a station.",
            type_changes=[TypeChange(ref=ship_id, new_type="Station")],
        ),
        fake_container,
        "The Gull is a station, not a ship.",
    )
    assert body["validation_ok"] is True, body
    assert any("A station has no “hull”, so that detail goes." in note for note in body["notes"])
    _accept(auth_client, base, body["mutation_id"])
    entry = auth_client.get(f"{base}/entities/{ship_id}").json()
    assert entry["type"] == "Station" and entry["attributes"] == {}
