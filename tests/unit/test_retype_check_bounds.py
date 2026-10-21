"""A new type is checked against every current fact and belief, however many there are
up to what is read at once; past that the change is refused in plain words rather than
checked in part (AST-EXP-017)."""

from types import SimpleNamespace

import pytest

import backend.domain.validation as validation
from backend.domain.canon.assertions import Fact
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.validation import AuthoritativeValidator

pytestmark = pytest.mark.unit


def _member(number: int, group: str) -> Fact:
    return Fact(
        id=f"fct_{number:06d}",
        workspace_id="ws",
        project_id="pr",
        branch_id="main",
        proposition=Proposition(
            subject_id=f"person_{number}",
            predicate="MEMBER_OF",
            object=EntityRefObject(entity_id=group, project_id="pr"),
        ),
        provenance_id="p",
        created_revision=1,
    )


class _Reader:
    def __init__(self, facts: list[Fact]) -> None:
        self.facts = facts

    async def asserted_facts(self, limit: int) -> list[Fact]:
        return self.facts[:limit]

    async def current_beliefs(self, limit: int) -> list:
        return []


async def _found(facts: list[Fact]) -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    await AuthoritativeValidator._check_retyped(
        {"guild": (3, "Character")},
        {},
        {},
        SimpleNamespace(reader=_Reader(facts), predicate_specs={}),  # type: ignore[arg-type]
        lambda index, code, message: found.append((index, code, message)),
    )
    return found


@pytest.mark.asyncio
async def test_a_fact_past_the_first_ten_thousand_still_counts() -> None:
    # Ten thousand facts about someone else, then one that a character cannot be pointed at.
    facts = [_member(n, "club") for n in range(10_000)] + [_member(10_000, "guild")]
    found = await _found(facts)
    assert [(index, code) for index, code, _ in found] == [(3, "type_does_not_fit")]


@pytest.mark.asyncio
async def test_a_story_larger_than_is_read_at_once_is_refused_not_checked_in_part(
    monkeypatch,
) -> None:
    monkeypatch.setattr(validation, "MAX_RETYPE_RECORDS", 5)
    found = await _found([_member(n, "club") for n in range(6)])
    assert found == [
        (
            3,
            "story_too_large",
            "the story has more than 5 current facts or beliefs, too many to check a new "
            "type against at once; keep the type",
        )
    ]
    assert await _found([_member(n, "club") for n in range(5)]) == []
