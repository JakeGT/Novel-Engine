"""Remembered name answers follow a merge (AST-EXP-017), and stop following on undo."""

import pytest

from tests.contract.test_assistant_conversations import _accept, _twin, _world_with_project
from tests.contract.test_context import _head

pytestmark = pytest.mark.contract


def _named(client, base: str, text_id: str, phrase: str) -> list[list[str]]:
    got = client.get(f"{base}/texts/{text_id}")
    assert got.status_code == 200, got.text
    return [
        m["entity_ids"] for m in got.json()["mentions"] if m["phrase"].lower() == phrase.lower()
    ]


def test_a_name_answer_for_the_merged_entry_names_the_kept_one_until_undone(
    auth_client, fake_container
) -> None:
    w = _world_with_project(auth_client, fake_container)
    base = w["base"]
    twin = _twin(auth_client, w)
    said = auth_client.post(
        f"{base}/names", json={"phrase": "the skipper", "verdict": "is", "entity_id": twin}
    )
    assert said.status_code in (200, 201), said.text
    text = auth_client.post(
        f"{base}/texts", json={"title": "Notes", "content": "The skipper owes money."}
    )
    assert text.status_code == 201, text.text
    text_id = text.json()["id"]
    assert _named(auth_client, base, text_id, "the skipper") == [[twin]]

    proposed = auth_client.post(
        f"{base}/merges",
        json={"entity_id": twin, "into": w["mara"], "expected_revision": _head(auth_client, w)},
    )
    assert proposed.status_code == 201, proposed.text
    merged_at = _accept(auth_client, base, proposed.json()["mutation_id"])
    # The answer is read as naming Mara now, in texts and in the name memory.
    assert _named(auth_client, base, text_id, "the skipper") == [[w["mara"]]]
    memory = auth_client.get(f"{base}/names").json()
    assert [b["entity_id"] for b in memory["items"] if b["phrase"] == "the skipper"] == [w["mara"]]
    assert memory["names"][w["mara"]] == "Mara"

    reverted = auth_client.post(
        f"{base}/reverts",
        json={
            "from_revision": merged_at - 1,
            "expected_revision": merged_at,
            "acknowledge_high_impact": True,
        },
    )
    assert reverted.status_code == 200, reverted.text
    assert _named(auth_client, base, text_id, "the skipper") == [[twin]]
