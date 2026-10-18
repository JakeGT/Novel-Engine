"""Keep the safe ones for me: which of the assistant's suggestions may be kept without asking
(AST-EXP-015, OSS-007; ADR-014 amendment).

Pure rules, no storage. Delegation is off by default and opt-in per kind of change. A kind
can be switched on only after the author has approved that kind by hand
``HAND_APPROVALS_NEEDED`` times, and only purely additive kinds exist:

* a new entry,
* details for an entry that has none,
* a new fact that contradicts nothing, replaces nothing and says nothing about who may know.

Everything else always waits: removals, replacements, ending facts, beliefs, events, the
plan, rules, anything a check warns about or stops, imports and readings of text, undoing,
more than ``SIZE_LIMIT`` changes at once, suggestions from connected programs (always: they
only ever suggest, and a story cannot allow them), anything that looks like an entry the story
already has, changes to an entry's marks (any detail under a reserved key), and anything that
touches a secret. A suggestion is kept whole or not at all: one change that does not qualify
makes the whole suggestion wait.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from backend.domain.operations import AssertFactOp, CanonOperation, CreateEntityOp, UpdateEntityOp

# How many suggestions of a kind the author must have approved by hand first.
HAND_APPROVALS_NEEDED = 3

# A suggestion with more changes than this always waits.
SIZE_LIMIT = 12

# The review decision's note says this, so history and the review screen show it.
KEPT_NOTE_PREFIX = "Kept for you under your rule"

# At most this many suggestions are kept for the author in one story in a day; past it,
# everything waits (a cap on what can happen unseen, whatever the rules say).
DAILY_LIMIT = 50

# How a suggestion reached the story. ``session``: the author asked the assistant in the
# app. ``client``: another assistant connected with an access key (QRY-003). ``text``: facts
# read from a text the author added. Only ``session`` can be kept; ``client`` never is.
VIA_SESSION = "session"
VIA_CLIENT = "client"
VIA_TEXT = "text"

# How a suggestion was decided, as ``delegation_records.how`` says it.
HOW_BY_HAND = "by_hand"
HOW_KEPT = "kept"
HOW_WAITED = "waited"


class DelegationKind(StrEnum):
    NEW_ENTRY = "new_entry"
    FIRST_DETAILS = "first_details"
    NEW_FACT = "new_fact"


KIND_NAMES: dict[str, str] = {
    DelegationKind.NEW_ENTRY.value: "New entries",
    DelegationKind.FIRST_DETAILS.value: "Details for entries that have none",
    DelegationKind.NEW_FACT.value: "New facts that change nothing else",
}

KIND_DESCRIPTIONS: dict[str, str] = {
    DelegationKind.NEW_ENTRY.value: (
        "A new person, place or thing in the story bible. Never one that looks like an entry "
        "you already have."
    ),
    DelegationKind.FIRST_DETAILS.value: (
        "Details on an entry that has none yet. Never changes details an entry already has, "
        "or its name."
    ),
    DelegationKind.NEW_FACT.value: (
        "A new fact that contradicts nothing and replaces nothing. Never a secret, and never "
        "one that says who may know something."
    ),
}

# Plain words for the changes that are never kept, by kind of change.
NEVER_KEPT_WORDS: dict[str, str] = {
    "update_entity": "a change to an entry's name or to details it already has",
    "retire_entity": "archiving an entry",
    "close_fact": "ending a fact",
    "amend_fact": "changing when a fact is true or who may know it",
    "assert_belief": "what a character believes",
    "close_belief": "ending a belief",
    "create_event": "an event",
    "amend_event": "changing an event",
    "retire_event": "removing an event",
    "link_asset": "a picture",
    "retire_asset_link": "removing a picture",
    "create_plan_item": "a change to the plan",
    "update_plan_item": "a change to the plan",
    "retire_plan_item": "a change to the plan",
}

# Shown to the author as the list of what always waits.
ALWAYS_WAITS: tuple[str, ...] = (
    "Removing, archiving or ending anything",
    "Replacing or changing what is already there",
    "Changes to the plan or to story rules",
    "Anything about secrets or who knows what",
    "Anything a check warns about or a story rule stops",
    "Facts read from a text you added, and anything imported",
    "Undoing earlier changes",
    "Anything in a what-if",
    "Anything suggested before the story last changed",
    f"More than {SIZE_LIMIT} changes at once, or more than {DAILY_LIMIT} kept in a day",
    "Suggestions from other assistants you connected",
)


def kind_name(kind: str) -> str:
    return KIND_NAMES.get(kind, kind)


def kept_note(kinds: list[str] | tuple[str, ...]) -> str:
    names = " and ".join(f"“{kind_name(kind)}”" for kind in kinds)
    plural = "s" if len(kinds) > 1 else ""
    return f"{KEPT_NOTE_PREFIX}{plural} {names}."


def is_kept_note(note: str | None) -> bool:
    return bool(note) and str(note).startswith(KEPT_NOTE_PREFIX)


def _only_attributes(operation: UpdateEntityOp) -> bool:
    return (
        operation.name is None
        and operation.slug is None
        and operation.aliases is None
        and operation.external_ids is None
        and bool(operation.attributes)
    )


def structural_kind(operation: CanonOperation) -> str | None:
    """The kind a change looks like on its own, for counting approvals by hand.

    Whether an entry had details before is only known when deciding; for counting, a
    change that only sets details counts as approving "details".
    """
    if isinstance(operation, CreateEntityOp):
        return DelegationKind.NEW_ENTRY.value
    if isinstance(operation, UpdateEntityOp) and _only_attributes(operation):
        return DelegationKind.FIRST_DETAILS.value
    if isinstance(operation, AssertFactOp) and operation.visibility is None:
        return DelegationKind.NEW_FACT.value
    return None


def is_reserved_key(key: str) -> bool:
    """A key the app keeps beside an entry's details (tags, importance, trust and the like).

    A project-defined field name cannot start with an underscore, so every such key is the
    app's own (``backend.domain.canon.marks``)."""
    return key.startswith("_")


def has_no_details(attributes: dict[str, Any] | None) -> bool:
    """No detail of the entry's own has a value. Marks under reserved keys do not count."""
    return not any(
        value not in (None, "", [], {})
        for key, value in (attributes or {}).items()
        if not is_reserved_key(key)
    )


def changes_reserved(new: dict[str, Any] | None, current: dict[str, Any] | None) -> bool:
    """Whether setting ``new`` as an entry's details would add, change or drop a mark.

    Details are replaced whole, so a key the entry has and ``new`` leaves out is dropped."""
    new, current = new or {}, current or {}
    keys = {key for key in (*new, *current) if is_reserved_key(key)}
    return any(new.get(key) != current.get(key) for key in keys)


@dataclass(frozen=True)
class Refusal:
    """Why a suggestion waited for the author instead of being kept, in plain words."""

    code: str
    message: str


@dataclass(frozen=True)
class RuleState:
    since: datetime
    by: str | None = None


@dataclass(frozen=True)
class DelegationSettings:
    rules: dict[str, RuleState] = field(default_factory=dict)
    allow_clients: bool = False
    # Only meaningful on a workspace's own row.
    forbidden: bool = False


RULE_OFF_CODE = "kept_rule_off"
RULE_OFF_MESSAGE = (
    "This was going to be kept for you, but that has been switched off, so it waits for you."
)


def rule_still_on(
    kinds: list[str] | tuple[str, ...], story: DelegationSettings, workspace_forbidden: bool
) -> bool:
    """Checked again inside the commit transaction for a suggestion being kept."""
    return not workspace_forbidden and all(kind in story.rules for kind in kinds)


@dataclass(frozen=True)
class DelegationRecord:
    """How one suggestion was decided.

    ``how`` is ``by_hand`` (a person approved it; ``state`` ``counted``), ``kept`` (kept
    under a rule: ``pending`` while it commits, then ``kept``) or ``waited`` (it was
    considered and left for the author; ``state`` ``waiting``, with the reason).
    """

    workspace_id: str
    project_id: str
    mutation_id: str
    how: str
    state: str
    kinds: tuple[str, ...]
    branch_id: str
    actor_user_id: str | None
    revision: int | None
    undo_mutation_id: str | None
    created_at: datetime
    via: str = VIA_SESSION
    reason_code: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class OperationFacts:
    """What the service looked up about one change before deciding."""

    # For details: whether the entry exists and has no details now.
    target_exists: bool = True
    target_empty: bool = True
    # For details: whether it would add, change or drop a mark (a reserved key).
    changes_marks: bool = False
    # For a fact: whether a secret is already held about anyone or anything it names.
    secret_overlap: bool = False
    # For a new entry: whether its name or a nickname is already an entry's name or nickname.
    looks_like_existing: bool = False


def classify(operation: CanonOperation, facts: OperationFacts) -> str | Refusal:
    """The kind of one change, or why it can never be kept."""
    if isinstance(operation, CreateEntityOp):
        if facts.looks_like_existing:
            return Refusal(
                "looks_like_existing",
                "It adds an entry with the same name or nickname as one you already have.",
            )
        return DelegationKind.NEW_ENTRY.value
    if isinstance(operation, UpdateEntityOp):
        if not _only_attributes(operation):
            return Refusal("not_additive", f"It includes {NEVER_KEPT_WORDS['update_entity']}.")
        if facts.changes_marks:
            return Refusal(
                "not_additive",
                "It changes an entry's tags, importance or other marks, so it waits for you.",
            )
        if operation.id.startswith("tmp:") or not facts.target_exists:
            return Refusal("not_additive", "It adds details to an entry that is not saved yet.")
        if not facts.target_empty:
            return Refusal(
                "not_additive", "It changes details an entry already has, so it waits for you."
            )
        return DelegationKind.FIRST_DETAILS.value
    if isinstance(operation, AssertFactOp):
        if operation.visibility is not None or facts.secret_overlap:
            return Refusal("secret", "It touches a secret or who may know something.")
        return DelegationKind.NEW_FACT.value
    words = NEVER_KEPT_WORDS.get(operation.op, "a kind of change that is never kept")
    return Refusal("not_additive", f"It includes {words}.")


@dataclass(frozen=True)
class Proposal:
    """The suggestion being decided, as the service read it."""

    origin: str
    intent: str
    status: str
    via: str
    stale: bool
    operations: tuple[CanonOperation, ...]
    facts: tuple[OperationFacts, ...]
    # Every check result on it: (code, message, blocking).
    issues: tuple[tuple[str, str, bool], ...] = ()
    # False when it was made in a what-if rather than the story itself.
    main_line: bool = True
    # How many suggestions were kept in this story in the last day.
    kept_today: int = 0


def decide(
    proposal: Proposal,
    *,
    story: DelegationSettings,
    workspace_forbidden: bool,
    hand_approvals: dict[str, int],
) -> tuple[str, ...] | Refusal:
    """The kinds a suggestion is kept under, or why it waits. Pure and total.

    Checks run from the broadest reason to the narrowest so the author sees the reason
    that matters most: where it came from, then what it changes, then what checks say.
    """
    if workspace_forbidden:
        return Refusal(
            "workspace_forbids",
            "A workspace owner has turned this off for every story in the workspace.",
        )
    if not story.rules:
        return Refusal("off", "Keeping suggestions for you is switched off in this story.")
    if proposal.origin == "import":
        return Refusal("import", "It was brought in from elsewhere, so it waits for you.")
    if proposal.origin != "model":
        return Refusal("not_assistant", "Only the assistant's suggestions can be kept for you.")
    if proposal.intent == "revert":
        return Refusal("revert", "It undoes earlier changes, so it waits for you.")
    if proposal.via == VIA_TEXT:
        return Refusal("text", "It was read from a text you added, so it waits for you.")
    if proposal.via != VIA_SESSION:
        # Connected assistants only ever suggest (QRY-003, ADR-024): never kept for you.
        return Refusal(
            "client",
            "It came from another assistant you connected; their suggestions always wait for you."
            if proposal.via == VIA_CLIENT
            else "It did not come from the assistant in the app, so it waits for you.",
        )
    if not proposal.main_line:
        return Refusal("what_if", "It is in a what-if, so it waits for you.")
    if proposal.status != "needs_review":
        return Refusal("not_waiting", "It is not waiting for review.")
    if not proposal.operations:
        return Refusal("empty", "It has no changes left.")
    if len(proposal.operations) > SIZE_LIMIT:
        return Refusal(
            "too_many",
            f"It has {len(proposal.operations)} changes; more than {SIZE_LIMIT} always wait.",
        )
    if proposal.kept_today >= DAILY_LIMIT:
        return Refusal(
            "daily_limit",
            f"{DAILY_LIMIT} suggestions were already kept for you today; the rest wait.",
        )
    if proposal.stale:
        return Refusal("stale", "The story changed after it was suggested.")
    kinds: list[str] = []
    for operation, facts in zip(proposal.operations, proposal.facts, strict=True):
        kind = classify(operation, facts)
        if isinstance(kind, Refusal):
            return kind
        if kind not in kinds:
            kinds.append(kind)
    for kind in kinds:
        if kind not in story.rules:
            return Refusal("kind_off", f"You have not switched on “{kind_name(kind)}”.")
        count = hand_approvals.get(kind, 0)
        if count < HAND_APPROVALS_NEEDED:
            return Refusal(
                "needs_hand_approvals",
                f"You have approved “{kind_name(kind)}” by hand {count} "
                f"time{'s' if count != 1 else ''}; it takes {HAND_APPROVALS_NEEDED}.",
            )
    stops = [message for _code, message, blocking in proposal.issues if blocking]
    if stops:
        return Refusal("blocked", f"A check stops it: {stops[0]}")
    if proposal.issues:
        return Refusal("warning", f"There is a warning on it: {proposal.issues[0][1]}")
    return tuple(kinds)


__all__ = [
    "DAILY_LIMIT",
    "HOW_BY_HAND",
    "HOW_KEPT",
    "HOW_WAITED",
    "VIA_TEXT",
    "DelegationRecord",
    "RULE_OFF_CODE",
    "RULE_OFF_MESSAGE",
    "ALWAYS_WAITS",
    "HAND_APPROVALS_NEEDED",
    "KEPT_NOTE_PREFIX",
    "KIND_DESCRIPTIONS",
    "KIND_NAMES",
    "SIZE_LIMIT",
    "VIA_CLIENT",
    "VIA_SESSION",
    "DelegationKind",
    "DelegationSettings",
    "OperationFacts",
    "Proposal",
    "Refusal",
    "RuleState",
    "classify",
    "decide",
    "changes_reserved",
    "has_no_details",
    "is_reserved_key",
    "is_kept_note",
    "kept_note",
    "kind_name",
    "rule_still_on",
    "structural_kind",
]
