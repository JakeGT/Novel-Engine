"""Tags, importance and "fill in later" on entries: set by the assistant, kept beside details."""

import pytest

from backend.domain.canon.marks import clean_tags, details, marks
from backend.services.canon_creation_workflow import (
    CreationDraft,
    DraftAttribute,
    DraftEntity,
    DraftFact,
)
from tests.contract.test_canon_assist import _setup

pytestmark = pytest.mark.contract


def test_marks_are_tidied_and_bounded() -> None:
    assert clean_tags(["  Protagonist ", "protagonist", "", "x" * 60, 7, "John’s  professor."]) == [
        "Protagonist",
        "John’s professor",
    ]
    assert len(clean_tags([f"tag {n}" for n in range(20)])) == 6
    assert marks(tags=[], importance="Legendary", needs_detail=False) == {}
    assert marks(tags=["mentor"], importance=" MAIN ", needs_detail=True) == {
        "_tags": ["mentor"],
        "_importance": "main",
        "_needs_detail": True,
    }
    assert details({"role": "pilot", "_tags": ["x"], "_importance": "main"}) == {"role": "pilot"}


def _commit(client, base: str, mutation_id: str) -> None:
    assert client.post(f"{base}/mutations/{mutation_id}/approve", json={}).status_code == 200
    committed = client.post(f"{base}/mutations/{mutation_id}/commit")
    assert committed.status_code == 200, committed.text


def test_the_assistant_tags_ranks_and_marks_what_it_creates(auth_client, fake_container) -> None:
    base, _ = _setup(auth_client, fake_container)
    fake_container.ai.queue(
        CreationDraft(
            entities=[
                DraftEntity(
                    ref="john",
                    type="Character",
                    name="John Williams",
                    attributes=[
                        DraftAttribute(key="role", value="composer"),
                        # A model cannot smuggle a mark in as an ordinary detail.
                        DraftAttribute(key="_importance", value="background"),
                    ],
                    tags=["protagonist", "Protagonist", "young composer"],
                    importance="main",
                ),
                # Only mentioned: created, tagged by its relation, and marked to fill in.
                DraftEntity(
                    ref="prof",
                    type="Character",
                    name="John Williams’s professor",
                    tags=["John’s professor"],
                    importance="nonsense",
                ),
            ],
            facts=[DraftFact(subject_ref="john", predicate="KNOWS", object_ref="prof")],
        )
    )
    body = auth_client.post(
        f"{base}/assist/proposals",
        json={"description": "John, a composer, has a professor.", "expected_revision": 1},
    ).json()
    prompt = fake_container.ai.calls[-1]["messages"][0]["content"]
    assert "needs_more" in prompt and "tags" in prompt and "even in passing" in prompt

    operations = auth_client.get(f"{base}/mutations/{body['mutation_id']}").json()["operations"]
    john, professor = operations[0]["payload"], operations[1]["payload"]
    assert john["attributes"] == {
        "role": "composer",
        "_tags": ["protagonist", "young composer"],
        "_importance": "main",
    }
    assert professor["attributes"] == {"_tags": ["John’s professor"], "_needs_detail": True}

    _commit(auth_client, base, body["mutation_id"])
    todo = auth_client.get(f"{base}/entities/needs-detail").json()["items"]
    # The professor is on the list although a fact mentions him; John is not.
    assert "John Williams’s professor" in {item["name"] for item in todo}
    assert "John Williams" not in {item["name"] for item in todo}


def test_filling_an_entry_in_clears_its_mark_and_keeps_what_the_author_set(
    auth_client, fake_container
) -> None:
    base, _ = _setup(auth_client, fake_container)
    created = auth_client.post(
        f"{base}/entities",
        json={
            "type": "Character",
            "name": "The Professor",
            "attributes": {"_needs_detail": True, "_importance": "supporting", "_tags": ["mentor"]},
            "expected_revision": 1,
        },
    )
    assert created.status_code == 201, created.text
    entity_id = created.json()["id"]
    assert entity_id in {
        item["id"] for item in auth_client.get(f"{base}/entities/needs-detail").json()["items"]
    }

    fake_container.ai.queue(
        CreationDraft(
            entities=[
                DraftEntity(
                    ref="prof",
                    type="Character",
                    name="The Professor",
                    attributes=[DraftAttribute(key="field", value="composition")],
                    tags=["John’s professor"],
                    importance="main",
                )
            ]
        )
    )
    body = auth_client.post(
        f"{base}/assist/proposals",
        json={
            "entity_id": entity_id,
            "description": "He teaches composition.",
            "expected_revision": 2,
        },
    ).json()

    update = auth_client.get(f"{base}/mutations/{body['mutation_id']}").json()["operations"][0]
    assert update["payload"]["attributes"] == {
        "field": "composition",
        # Added to, not replaced; and the author's own ranking stands.
        "_tags": ["mentor", "John’s professor"],
        "_importance": "supporting",
    }
    _commit(auth_client, base, body["mutation_id"])
    assert entity_id not in {
        item["id"] for item in auth_client.get(f"{base}/entities/needs-detail").json()["items"]
    }
