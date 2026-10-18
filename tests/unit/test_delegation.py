"""The pure rules for keeping the assistant's suggestions (AST-EXP-015)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.domain.canon.propositions import Proposition
from backend.domain.delegation import (
    DAILY_LIMIT,
    HAND_APPROVALS_NEEDED,
    SIZE_LIMIT,
    VIA_CLIENT,
    VIA_SESSION,
    VIA_TEXT,
    DelegationSettings,
    OperationFacts,
    Proposal,
    Refusal,
    RuleState,
    changes_reserved,
    classify,
    decide,
    has_no_details,
    is_kept_note,
    kept_note,
    rule_still_on,
    structural_kind,
)
from backend.domain.operations import (
    AssertFactOp,
    CloseFactOp,
    CreateEntityOp,
    CreatePlanItemOp,
    RetireEntityOp,
    UpdateEntityOp,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 18, tzinfo=UTC)
ALL_ON = DelegationSettings(
    rules={kind: RuleState(since=NOW) for kind in ("new_entry", "first_details", "new_fact")}
)
EARNED = dict.fromkeys(("new_entry", "first_details", "new_fact"), HAND_APPROVALS_NEEDED)


def _entry(n: int = 0) -> CreateEntityOp:
    return CreateEntityOp(id=f"tmp:{n}", type="Character", name=f"Person {n}")


def _fact(**extra) -> AssertFactOp:
    return AssertFactOp(
        id="tmp:f",
        proposition=Proposition.model_validate(
            {
                "subject_id": "ent_a",
                "predicate": "OWNS",
                "object": {"kind": "string", "value": "a boat"},
            }
        ),
        **extra,
    )


def _proposal(*operations, facts=None, **extra) -> Proposal:
    base = {
        "origin": "model",
        "intent": "edit",
        "status": "needs_review",
        "via": VIA_SESSION,
        "stale": False,
        "operations": tuple(operations),
        "facts": tuple(facts or [OperationFacts() for _ in operations]),
    }
    base.update(extra)
    return Proposal(**base)


def _decide(proposal, *, story=ALL_ON, forbidden=False, counts=None):
    return decide(
        proposal,
        story=story,
        workspace_forbidden=forbidden,
        hand_approvals=EARNED if counts is None else counts,
    )


def test_purely_new_suggestions_are_kept_under_the_kinds_they_hold() -> None:
    assert _decide(_proposal(_entry(), _fact())) == ("new_entry", "new_fact")
    details = UpdateEntityOp(id="ent_a", attributes={"role": "cook"})
    assert _decide(_proposal(details)) == ("first_details",)


@pytest.mark.parametrize(
    ("proposal", "kwargs", "code"),
    [
        (_proposal(_entry()), {"forbidden": True}, "workspace_forbids"),
        (_proposal(_entry()), {"story": DelegationSettings()}, "off"),
        (_proposal(_entry(), origin="import"), {}, "import"),
        (_proposal(_entry(), origin="author"), {}, "not_assistant"),
        (_proposal(_entry(), intent="revert"), {}, "revert"),
        (_proposal(_entry(), via=VIA_TEXT), {}, "text"),
        (_proposal(_entry(), via=VIA_CLIENT), {}, "client"),
        (_proposal(_entry(), via="mystery"), {}, "client"),
        (_proposal(_entry(), main_line=False), {}, "what_if"),
        (_proposal(_entry(), status="approved"), {}, "not_waiting"),
        (_proposal(), {}, "empty"),
        (_proposal(*[_entry(n) for n in range(SIZE_LIMIT + 1)]), {}, "too_many"),
        (_proposal(_entry(), kept_today=DAILY_LIMIT), {}, "daily_limit"),
        (_proposal(_entry(), stale=True), {}, "stale"),
        (_proposal(RetireEntityOp(id="ent_a")), {}, "not_additive"),
        (_proposal(CloseFactOp(id="fct_a")), {}, "not_additive"),
        (_proposal(UpdateEntityOp(id="ent_a", name="New")), {}, "not_additive"),
        (
            _proposal(
                UpdateEntityOp(id="ent_a", attributes={"role": "x"}),
                facts=[OperationFacts(target_empty=False)],
            ),
            {},
            "not_additive",
        ),
        (
            _proposal(
                UpdateEntityOp(id="ent_a", attributes={"role": "x"}),
                facts=[OperationFacts(target_exists=False)],
            ),
            {},
            "not_additive",
        ),
        (
            _proposal(CreatePlanItemOp(id="tmp:p", kind="scene", title="A scene")),
            {},
            "not_additive",
        ),
        (
            _proposal(
                UpdateEntityOp(id="ent_a", attributes={"role": "x", "_importance": "main"}),
                facts=[OperationFacts(changes_marks=True)],
            ),
            {},
            "not_additive",
        ),
        (
            _proposal(_entry(), facts=[OperationFacts(looks_like_existing=True)]),
            {},
            "looks_like_existing",
        ),
        (_proposal(_fact(visibility="secret")), {}, "secret"),
        (_proposal(_fact(), facts=[OperationFacts(secret_overlap=True)]), {}, "secret"),
        (
            _proposal(_fact()),
            {"story": DelegationSettings(rules={"new_entry": RuleState(since=NOW)})},
            "kind_off",
        ),
        (_proposal(_entry()), {"counts": {"new_entry": 2}}, "needs_hand_approvals"),
        (_proposal(_entry(), issues=(("rule:x", "Guilds need members.", True),)), {}, "blocked"),
        (_proposal(_entry(), issues=(("dup", "Looks like Mara.", False),)), {}, "warning"),
    ],
)
def test_every_reason_a_suggestion_waits(proposal, kwargs, code) -> None:
    decided = _decide(proposal, **kwargs)
    assert isinstance(decided, Refusal) and decided.code == code, decided
    assert decided.message and "_" not in decided.message


def test_another_assistant_is_never_kept_even_with_an_old_setting() -> None:
    # A stored allowance from before cannot let a connected assistant through (QRY-003).
    story = DelegationSettings(rules=ALL_ON.rules, allow_clients=True)
    assert _decide(_proposal(_entry(), via=VIA_CLIENT), story=story).code == "client"
    assert _decide(_proposal(_entry(), via=VIA_TEXT), story=story).code == "text"


def test_what_a_suggestion_changes_outranks_what_checks_say() -> None:
    proposal = _proposal(RetireEntityOp(id="ent_a"), issues=(("x", "Still referenced.", False),))
    assert _decide(proposal).code == "not_additive"


def test_one_change_that_does_not_qualify_keeps_the_whole_suggestion_waiting() -> None:
    assert _decide(_proposal(_entry(), RetireEntityOp(id="ent_a"))).code == "not_additive"


def test_counting_and_notes() -> None:
    assert structural_kind(_entry()) == "new_entry"
    assert structural_kind(UpdateEntityOp(id="ent_a", attributes={"a": 1})) == "first_details"
    assert structural_kind(UpdateEntityOp(id="ent_a", name="x")) is None
    assert structural_kind(_fact(visibility="secret")) is None
    assert classify(_entry(), OperationFacts()) == "new_entry"
    assert has_no_details({"role": "", "tags": []}) and not has_no_details({"role": "cook"})
    note = kept_note(("new_entry", "new_fact"))
    assert (
        note
        == "Kept for you under your rules “New entries” and “New facts that change nothing else”."
    )
    assert is_kept_note(note) and not is_kept_note("approved")
    assert rule_still_on(("new_entry",), ALL_ON, False)
    assert not rule_still_on(("new_entry",), ALL_ON, True)
    assert not rule_still_on(("new_entry",), DelegationSettings(), False)


def test_marks_are_not_details_and_changing_one_is_noticed() -> None:
    # Reserved keys (tags, importance, needs detail, trust) are not the entry's own details.
    assert has_no_details({"_needs_detail": True, "_tags": ["smith"], "_trust": {"x": 1}})
    assert has_no_details({"_importance": "main", "role": ""})
    assert not has_no_details({"_tags": ["smith"], "role": "cook"})
    # Details are replaced whole: adding, changing or leaving out a mark all change it.
    assert not changes_reserved({"role": "cook"}, {})
    assert not changes_reserved({"role": "cook", "_tags": ["a"]}, {"_tags": ["a"]})
    assert changes_reserved({"role": "cook", "_importance": "main"}, {})
    assert changes_reserved({"role": "cook", "_tags": ["b"]}, {"_tags": ["a"]})
    assert changes_reserved({"role": "cook"}, {"_needs_detail": True})
