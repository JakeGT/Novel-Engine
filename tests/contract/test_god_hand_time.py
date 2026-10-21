"""The God Hand and story time: dating events, values that follow each other, secrets."""

import pytest

from backend.services.assist_time import StoryTime
from backend.services.canon_change_workflow import (
    ChangePlan,
    EventTime,
    FactTime,
    NewEvent,
    RecordRef,
    Secrecy,
    TimedFact,
)
from tests.contract.test_god_hand import _world

pytestmark = pytest.mark.contract


def _ask(client, w, instruction: str):
    response = client.post(
        f"{w['base']}/assist/changes",
        json={"instruction": instruction, "expected_revision": int(w["head"])},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _ops(client, w, body) -> list[dict]:
    review = client.get(f"{w['base']}/mutations/{body['mutation_id']}").json()
    return [op["payload"] for op in review["operations"]]


def test_three_years_before_the_war_becomes_a_relative_time(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    war = auth_client.post(
        f"{w['base']}/events",
        json={"kind": "battle", "name": "The War", "expected_revision": head},
    ).json()
    w["head"] = str(head + 1)
    fake_container.ai.queue(
        ChangePlan(
            understood="Mara left the guild three years before the war.",
            new_events=[
                NewEvent(
                    ref="leaving",
                    name="Mara leaves the guild",
                    kind="departure",
                    participants=[w["mara"], "nobody"],
                    when=StoryTime(
                        kind="relative",
                        anchor=war["id"],
                        offset=3,
                        direction="before",
                        words="three years before the war",
                    ),
                )
            ],
            event_times=[EventTime(event=war["id"], when=StoryTime(kind="at", at=100))],
        )
    )
    body = _ask(auth_client, w, "Mara left the guild three years before the war, in year 100.")
    assert body["validation_ok"] is True, body
    assert [d["kind"] for d in body["discarded"]] == ["event"]  # "nobody" is not an entry
    created, amended = _ops(auth_client, w, body)
    assert created["op"] == "create_event"
    assert created["when"]["anchor_event_id"] == war["id"]
    assert created["when"]["label"] == "three years before the war"
    assert created["participants"] == [{"entity_id": w["mara"], "role": "participant"}]
    assert amended == {
        "op": "amend_event",
        "id": war["id"],
        "name": None,
        "world_position": None,
        "when": {"kind": "at", "at": 100.0, "label": None},
        # Who takes part, where, and what causes or follows it are left as they are (a
        # merge sets them).
        "participants": None,
        "location_ids": None,
        "cause_ids": None,
        "outcome_ids": None,
        "evidence": amended["evidence"],
    }
    # The model was told the unit and shown the events.
    prompt = fake_container.ai.calls[-1]["messages"][1]["content"]
    assert "Story time is counted in years." in prompt and war["id"] in prompt


def test_a_move_at_a_stated_time_keeps_both_places_each_for_its_time(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    fake_container.ai.queue(
        ChangePlan(
            understood="Mara moved from the harbor to Saltmere in year 10.",
            add_facts=[
                TimedFact(
                    subject_ref=w["mara"],
                    predicate="LOCATED_AT",
                    object_ref=w["saltmere"],
                    starts=StoryTime(kind="at", at=10),
                )
            ],
            fact_times=[FactTime(fact=w["at_harbor"], ends=StoryTime(kind="at", at=10))],
        )
    )
    body = _ask(auth_client, w, "She moved to Saltmere in year 10.")
    assert body["validation_ok"] is True, body
    ops = _ops(auth_client, w, body)
    assert [op["op"] for op in ops] == ["amend_fact", "assert_fact"]
    assert "Both values are kept" in " ".join(body["notes"])
    assert ops[1]["starts"]["at"] == 10

    auth_client.post(f"{w['base']}/mutations/{body['mutation_id']}/approve", json={})
    assert (
        auth_client.post(f"{w['base']}/mutations/{body['mutation_id']}/commit").status_code == 200
    )
    located = auth_client.get(
        f"{w['base']}/facts", params={"subject_id": w["mara"], "predicate": "LOCATED_AT"}
    ).json()["items"]
    assert len(located) == 2


def test_without_a_time_a_new_value_still_replaces_the_old(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    fake_container.ai.queue(
        ChangePlan(
            understood="Mara is in Saltmere.",
            add_facts=[
                TimedFact(subject_ref=w["mara"], predicate="LOCATED_AT", object_ref=w["saltmere"])
            ],
        )
    )
    body = _ask(auth_client, w, "Mara is in Saltmere now.")
    assert [op["op"] for op in _ops(auth_client, w, body)] == ["close_fact", "assert_fact"]
    # Offered by need: the author can keep both, each with when it was true.
    assert "only one thing at a time" in " ".join(body["notes"])


def test_tobin_doesnt_know_this_makes_it_a_secret_and_ends_his_belief(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    head = int(w["head"])
    belief = auth_client.post(
        f"{w['base']}/beliefs",
        json={
            "holder_entity_id": w["tobin"],
            "proposition": {
                "subject_id": w["mara"],
                "predicate": "MEMBER_OF",
                "object": {
                    "kind": "entity_ref",
                    "entity_id": w["guild"],
                    "project_id": w["base"].rsplit("/", 1)[1],
                },
            },
            "source_ref": "author",
            "expected_revision": head,
        },
    ).json()
    w["head"] = str(head + 1)
    fake_container.ai.queue(
        ChangePlan(
            understood="Mara's place in the guild is a secret Tobin does not know.",
            secrecy=[
                Secrecy(fact=w["in_guild"], visibility="secret"),
                Secrecy(fact="fact_missing", visibility="secret"),
                Secrecy(fact=w["at_harbor"], visibility="public"),
            ],
            end_beliefs=[RecordRef(id=belief["id"])],
        )
    )
    body = _ask(auth_client, w, "Tobin doesn't know Mara is in the guild. It's a secret.")
    assert body["validation_ok"] is True, body
    assert sorted(d["kind"] for d in body["discarded"]) == ["fact", "fact"]
    ops = _ops(auth_client, w, body)
    assert {(op["op"], op["id"]) for op in ops} == {
        ("close_belief", belief["id"]),
        ("amend_fact", w["in_guild"]),
    }
    secret = next(op for op in ops if op["op"] == "amend_fact")
    assert secret["visibility"] == "secret" and secret["starts"] is None
