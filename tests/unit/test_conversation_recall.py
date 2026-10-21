"""What the assistant reads again of a kept thread, which turns a thread never lets go,
and what counts as the author's own words (AST-EXP-005, -013, -015, -017)."""

from __future__ import annotations

import pytest

from backend.domain.conversations import (
    RECALLED_CHARS,
    ThreadKind,
    Turn,
    TurnAnswer,
    TurnRole,
    pinned,
    recalled,
)
from backend.domain.delegation import VIA_PREMISE, DelegationSettings, Proposal, RuleState, decide
from backend.domain.operations import CreateEntityOp

pytestmark = pytest.mark.unit


def _size(turns: list[Turn]) -> int:
    return sum(
        len(turn.body)
        + sum(len(q) for q in turn.questions)
        + sum(len(a.question) + len(a.answer) for a in turn.answers)
        for turn in turns
    )


def test_the_newest_turn_is_cut_down_to_the_bound_when_it_is_longer_on_its_own() -> None:
    small = Turn(role=TurnRole.AUTHOR, body="Earlier.")
    huge = Turn(
        role=TurnRole.AUTHOR,
        body="b" * 5000,
        answers=[TurnAnswer(question="Who?", answer="a" * 8000) for _ in range(12)],
    )
    picked = recalled([small, huge])
    assert len(picked) == 1
    assert _size(picked) <= RECALLED_CHARS
    assert picked[0].body == "b" * 5000  # its own words first
    assert picked[0].answers[0].question == "Who?"
    # A turn within the bound is read as it is.
    assert recalled([small]) == [small]


def test_a_premise_thread_keeps_the_turns_that_tell_the_story() -> None:
    told = Turn(role=TurnRole.AUTHOR, body="Wren lights the lamps.")
    answered = Turn(role=TurnRole.AUTHOR, answers=[TurnAnswer(question="Q?", answer="A.")])
    reply = Turn(role=TurnRole.ASSISTANT, body="Understood.")
    assert pinned(ThreadKind.PREMISE, told)
    assert not pinned(ThreadKind.PREMISE, answered)
    assert not pinned(ThreadKind.PREMISE, reply)
    assert not pinned(ThreadKind.CHANGE, told)


def test_suggestions_from_a_premise_always_wait_for_the_author() -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 10, 18, tzinfo=UTC)
    refusal = decide(
        Proposal(
            origin="model",
            intent="edit",
            status="needs_review",
            via=VIA_PREMISE,
            stale=False,
            operations=(CreateEntityOp(id="tmp:0", type="Character", name="Wren"),),
            facts=(),
        ),
        story=DelegationSettings(rules={"new_entry": RuleState(since=now)}),
        workspace_forbidden=False,
        hand_approvals={"new_entry": 99},
    )
    assert getattr(refusal, "code", None) == "premise"
