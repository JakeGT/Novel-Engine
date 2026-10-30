"""What part of a large story bible each assistant request is shown (CTX-002).

The God Hand, missing connections and reading a text used to list the story bible up to
fixed counts (200 entries, 300 facts) in name order, so in a larger story the entries a
request was about could be left off. Each now follows a versioned listing policy: what
the request names first, then their neighbours, then the rest by importance, within a
word budget, with what was left out counted, said and recorded with the proposal.
"""

import pytest

from backend.services.canon_change_workflow import ChangePlan, RecordRef
from backend.services.canon_revise_workflow import LinkPlan, SuggestedLink
from backend.services.intake_workflow import PassageDraft, ReadFact
from tests.contract.test_canon_entities import _project_id, _publish_schema

pytestmark = pytest.mark.contract

FILLERS = 400
# Enough words per entry that the filler alone is over every policy's budget.
NOTE = (
    "a quiet figure of the lower town who keeps to the market lanes and is seldom "
    "remembered by anyone for long"
)


def _commit(client, base: str, operations: list[dict], head: int) -> int:
    proposed = client.post(
        f"{base}/mutations",
        json={"origin": "author", "expected_revision": head, "operations": operations},
    )
    assert proposed.status_code == 201, proposed.text
    mutation_id = proposed.json()["id"]
    if proposed.json()["requires_review"]:
        approved = client.post(f"{base}/mutations/{mutation_id}/approve", json={"note": "ok"})
        assert approved.status_code == 200, approved.text
    committed = client.post(f"{base}/mutations/{mutation_id}/commit")
    assert committed.status_code == 200, committed.text
    return int(committed.json()["revision"])


def _ref(entity_id: str, project_id: str) -> dict:
    return {"kind": "entity_ref", "entity_id": entity_id, "project_id": project_id}


def _big_world(client, container, fillers: int = FILLERS) -> dict[str, str]:
    """Hundreds of minor people who all sort before the ones a request is about."""
    project_id = _project_id(container)
    _publish_schema(client, project_id)
    base = f"/api/v1/projects/{project_id}"
    head = 0
    for batch in range(fillers // 100):
        operations: list[dict] = []
        for number in range(batch * 100, batch * 100 + 100):
            operations.append(
                {
                    "op": "create_entity",
                    "id": f"tmp:p{number}",
                    "type": "Character",
                    "name": f"Aaron {number:03d}",
                    "attributes": {"note": NOTE},
                }
            )
            if number > batch * 100:
                operations.append(
                    {
                        "op": "assert_fact",
                        "id": f"tmp:k{number}",
                        "proposition": {
                            "subject_id": f"tmp:p{number}",
                            "predicate": "KNOWS",
                            "object": _ref(f"tmp:p{number - 1}", project_id),
                        },
                    }
                )
        head = _commit(client, base, operations, head)
    ids = {"base": base, "project_id": project_id}
    for key, body in (
        ("zelda", {"type": "Character", "name": "Zelda Quill", "aliases": ["the Heron"]}),
        ("zephyr", {"type": "Location", "name": "Zephyr Keep"}),
        # Named only in Zelda's details: where a missing connection is written down.
        ("zoran", {"type": "Character", "name": "Zoran Pike"}),
        ("yew", {"type": "Location", "name": "Yew Hollow"}),
    ):
        created = client.post(f"{base}/entities", json={**body, "expected_revision": head})
        assert created.status_code == 201, created.text
        ids[key] = created.json()["id"]
        head += 1
    noted = client.patch(
        f"{base}/entities/{ids['zelda']}",
        json={"attributes": {"family": "Zoran Pike is her brother"}, "expected_revision": head},
    )
    assert noted.status_code == 200, noted.text
    head += 1
    for key, subject, predicate, target in (
        ("zelda_at", "zelda", "LOCATED_AT", "zephyr"),
        # Zoran's place: one fact away from someone a request names.
        ("zoran_at", "zoran", "LOCATED_AT", "yew"),
    ):
        fact = client.post(
            f"{base}/facts",
            json={
                "proposition": {
                    "subject_id": ids[subject],
                    "predicate": predicate,
                    "object": _ref(ids[target], project_id),
                },
                "expected_revision": head,
            },
        )
        assert fact.status_code == 201, fact.text
        ids[key] = fact.json()["id"]
        head += 1
    ids["head"] = str(head)
    return ids


def _prompt(container) -> str:
    return str(container.ai.calls[-1]["messages"][1]["content"])


def test_the_god_hand_finds_what_the_instruction_names_in_a_story_past_the_old_limits(
    auth_client, fake_container
):
    w = _big_world(auth_client, fake_container)
    fake_container.ai.queue(
        ChangePlan(understood="Zelda leaves Zephyr Keep.", end_facts=[RecordRef(id=w["zelda_at"])])
    )
    # Named by an alias, in other case and with punctuation around it.
    asked = auth_client.post(
        f"{w['base']}/assist/changes",
        json={
            "instruction": "THE HERON's gone: she left the keep.",
            "expected_revision": int(w["head"]),
        },
    )
    assert asked.status_code == 200, asked.text
    body = asked.json()

    prompt = _prompt(fake_container)
    # The named entry, its fact, and what that fact points at are listed, ahead of the rest.
    assert f"- {w['zelda']}: Zelda Quill (Character)" in prompt
    assert w["zelda_at"] in prompt and w["zephyr"] in prompt
    assert prompt.index("Zelda Quill") < prompt.index("Aaron ")
    # The rest of a story over the budget is cut, and the cut is said.
    assert "The story is larger than this list:" in prompt
    assert prompt.count("Aaron ") < FILLERS
    context = body["context"]
    assert context["policy_version"] == "change-story/1"
    assert context["left_out"]["entries"] > 0 and context["listed"]["entries"] > 4
    assert any("Not shown:" in note for note in body["notes"])

    # The policy is recorded with the proposal; nothing of the story is.
    detail = auth_client.get(f"{w['base']}/mutations/{body['mutation_id']}").json()
    reasons = " ".join(item["reason"] for item in detail["transitions"])
    assert "context change-story/1:" in reasons and "left out" in reasons
    assert "Zelda" not in reasons


def test_missing_connections_list_entries_whose_details_name_others_first(
    auth_client, fake_container
):
    w = _big_world(auth_client, fake_container)
    fake_container.ai.queue(
        LinkPlan(
            links=[
                SuggestedLink(
                    subject_ref=w["zelda"],
                    predicate="KNOWS",
                    object_ref=w["zoran"],
                    why="Zoran Pike is her brother",
                )
            ]
        )
    )
    found = auth_client.post(
        f"{w['base']}/assist/links", json={"expected_revision": int(w["head"])}
    )
    assert found.status_code == 200, found.text
    prompt = _prompt(fake_container)
    assert f"- {w['zelda']}: Zelda Quill" in prompt and f"- {w['zoran']}: Zoran Pike" in prompt
    assert prompt.index("Zoran Pike (Character)") < prompt.index("Aaron ")
    assert "The story is larger than this list:" in prompt
    assert found.json()["context"]["policy_version"] == "suggest-links/2"
    assert found.json()["operation_count"] == 1


def test_reading_a_text_lists_the_entries_it_names_and_their_neighbours(
    auth_client, fake_container
):
    # Reading lists names only, a few words each: more of them fit.
    w = _big_world(auth_client, fake_container, fillers=700)
    text = auth_client.post(
        f"{w['base']}/texts",
        json={
            "title": "The keep",
            "content": "Zelda Quill climbed to Zephyr Keep at dusk with Zoran Pike.\n",
        },
    )
    assert text.status_code == 201, text.text
    fake_container.ai.queue(
        PassageDraft(
            facts=[
                ReadFact(
                    subject_ref=w["zelda"],
                    predicate="KNOWS",
                    object_ref=w["zoran"],
                    quote="with Zoran Pike",
                )
            ]
        )
    )
    read = auth_client.post(
        f"{w['base']}/assist/texts/{text.json()['id']}/read",
        json={"expected_revision": int(w["head"])},
    )
    assert read.status_code == 200, read.text
    prompt = _prompt(fake_container)
    entries = prompt.split("Entries already in the story bible:\n", 1)[1].splitlines()
    # The three the passage names come first, ahead of seven hundred others.
    assert entries[:3] == [
        f"- {w['zelda']} | Character | Zelda Quill (also called: the Heron)",
        f"- {w['zephyr']} | Location | Zephyr Keep",
        f"- {w['zoran']} | Character | Zoran Pike",
    ]
    # Then their neighbours: Zoran's place, one fact away.
    assert entries[3] == f"- {w['yew']} | Location | Yew Hollow"
    assert f'"Zelda Quill" means {w["zelda"]}' in prompt
    assert "The story is larger than this list:" in prompt
    body = read.json()
    assert body["context"]["policy_version"] == "read-text/1"
    assert body["context"]["left_out"]["entries"] > 0
    detail = auth_client.get(f"{w['base']}/mutations/{body['mutation_id']}").json()
    assert "context read-text/1:" in " ".join(item["reason"] for item in detail["transitions"])


def test_a_small_story_is_listed_whole_and_nothing_is_said_left_out(auth_client, fake_container):
    from tests.contract.test_god_hand import _world

    w = _world(auth_client, fake_container)
    fake_container.ai.queue(ChangePlan(understood="Nothing.", questions=["Which Mara?"]))
    asked = auth_client.post(
        f"{w['base']}/assist/changes",
        json={"instruction": "Change Mara.", "expected_revision": int(w["head"])},
    )
    assert asked.status_code == 200, asked.text
    prompt = _prompt(fake_container)
    assert "The story is larger" not in prompt
    for key in ("mara", "tobin", "harbor", "saltmere", "guild", "at_harbor", "knows"):
        assert w[key] in prompt
    assert asked.json()["context"]["left_out"] == {}
    assert not any("Not shown" in note for note in asked.json()["notes"])


def test_missing_connections_stay_within_the_budget_when_most_details_name_someone(
    auth_client, fake_container
):
    from backend.domain.bible_listing import LINKS_POLICY

    project_id = _project_id(fake_container)
    _publish_schema(auth_client, project_id)
    base = f"/api/v1/projects/{project_id}"
    head = 0
    for batch in range(4):
        operations = [
            {
                "op": "create_entity",
                "id": f"tmp:p{number}",
                "type": "Character",
                "name": f"Aaron {number:03d}",
                # Every one's details name someone else, at length.
                "attributes": {"note": f"cousin of Aaron {(number + 1) % 400:03d}, {NOTE} " * 3},
            }
            for number in range(batch * 100, batch * 100 + 100)
        ]
        head = _commit(auth_client, base, operations, head)
    # Nothing found is said plainly; what matters here is what the assistant was shown.
    fake_container.ai.queue(LinkPlan())
    auth_client.post(f"{base}/assist/links", json={"expected_revision": head})
    listed = [line for line in _prompt(fake_container).splitlines() if line.startswith("- ent_")]
    assert listed
    assert sum(len(line.split()) for line in listed) <= LINKS_POLICY.budget
    # Each entry's details are cut short, so more of the story fits.
    assert all(len(line.split()) <= (LINKS_POLICY.detail_words or 0) + 10 for line in listed)
    assert all(line.endswith("…") for line in listed)
    assert "The story is larger than this list:" in _prompt(fake_container)
