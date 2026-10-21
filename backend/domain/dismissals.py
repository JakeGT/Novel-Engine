"""Dismissing a finding in the scene studio, with a reason (CON-010, CON-016).

A dismissal belongs to one scene in one line of the story. It points at the words the
finding was about by a fingerprint, never by storing them: the kind of finding, what it
is about, and the sentence it points at. While that sentence is unchanged the finding
stays dismissed; when the words change the fingerprint no longer matches and the finding
comes back (a review comment goes "outdated" the same way when the line it is on
changes). A dismissal can also run out after a set time. Undoing one keeps the record
and marks it withdrawn, so a dismissal is never changed, only replaced.

A dismissal also keeps a digest of what the finding said when it was dismissed
(``depends_on``): the message is worked out from the facts, times, places and people
the check read, so when those change in a way that matters to it, the finding comes back
even though its sentence is the same (CON-016). An assistant's note has none: what it
was worked out from cannot be told without the assistant.

Advisory findings on a proposal in review can be dismissed too (``ReviewDismissal``):
keyed by the change as it stands (an edit is a new revision of it) and holding what the
finding said, so an edited change or a story that changed what it says brings it back.

Only advisory findings can be dismissed. Nothing here touches the checks that guard the
story itself, which can never be dismissed or switched off (ADR-013); a story rule set
to block is never dismissed either.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

MAX_NOTE = 200
# How long a dismissal may last, besides "until the words change".
EXPIRY_DAYS = (7, 30)
# A dismissal stays in the record, so this bounds what one scene can hold.
MAX_DISMISSALS_PER_SCENE = 500


class DismissReason(StrEnum):
    # Kept as written on purpose (a code checker's "accept" / "won't fix").
    INTENDED = "intended"
    # The check is wrong here (a code checker's "false positive").
    NOT_A_PROBLEM = "not_a_problem"
    # The author's own words.
    OTHER = "other"


class FindingKind(StrEnum):
    CHECK = "check"
    NOTE = "note"


@dataclass(frozen=True)
class FindingDismissal:
    workspace_id: str
    project_id: str
    branch_id: str
    id: str
    plan_id: str
    fingerprint: str
    kind: FindingKind
    # The check's code or the note's kind, for the record; never the author's words.
    code: str
    reason: DismissReason
    note: str
    expires_at: datetime | None
    created_by: str | None
    created_at: datetime
    withdrawn_at: datetime | None = None
    # A digest of what the finding said when dismissed; None for a note, or one dismissed
    # before this was kept.
    depends_on: str | None = None
    # For a finding dismissed by a wider dismissal (``ScopedDismissal``): its id and what
    # it applies to, in words. Never stored on a scene's own dismissal.
    scope_id: str | None = None
    applies_to: str = ""

    def applies(self, now: datetime) -> bool:
        if self.withdrawn_at is not None:
            return False
        return self.expires_at is None or self.expires_at > now


@dataclass(frozen=True)
class ReviewDismissal:
    """An advisory finding on a proposal in review, dismissed by the author."""

    workspace_id: str
    project_id: str
    id: str
    mutation_id: str
    # The change as it stands and the kind of finding (``review_fingerprint``).
    fingerprint: str
    # What the finding said (``finding_dependency`` of its message and, for a story rule,
    # the rule's version).
    depends_on: str
    code: str
    reason: DismissReason
    note: str
    created_by: str | None
    created_at: datetime
    withdrawn_at: datetime | None = None

    @property
    def standing(self) -> bool:
        return self.withdrawn_at is None


class DismissalScope(StrEnum):
    # Everywhere the finding is about one character.
    CHARACTER = "character"
    # In every scene from one to another, in reading order.
    RANGE = "range"


# How many wider dismissals one line of the story may hold, standing or not.
MAX_SCOPED_DISMISSALS = 300


@dataclass(frozen=True)
class ScopedDismissal:
    """One kind of finding dismissed wider than one scene (CON-016): wherever it is about
    one character, or in a stretch of scenes. It holds for findings found later too, until
    it runs out or is withdrawn. A finding is about a character when what it says names
    them (by any of their names here); a scene is in a stretch by reading order now, so
    a scene moved into it is covered and one moved out is not."""

    workspace_id: str
    project_id: str
    branch_id: str
    id: str
    kind: FindingKind
    # The check's code or the note's pass.
    code: str
    scope: DismissalScope
    reason: DismissReason
    note: str
    created_at: datetime
    entity_id: str | None = None
    from_plan_id: str | None = None
    to_plan_id: str | None = None
    expires_at: datetime | None = None
    created_by: str | None = None
    withdrawn_at: datetime | None = None

    def applies(self, now: datetime) -> bool:
        if self.withdrawn_at is not None:
            return False
        return self.expires_at is None or self.expires_at > now


def names_someone(words: str, names: tuple[str, ...] | list[str]) -> bool:
    """Whether some words name someone, by any of their names, as whole words."""
    folded = unicodedata.normalize("NFC", words).casefold()
    for name in names:
        name = " ".join(name.split()).casefold()
        if name and re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", folded):
            return True
    return False


def scope_covers(
    dismissal: ScopedDismissal,
    *,
    kind: FindingKind,
    code: str,
    about: Collection[str] = (),
    position: int | None = None,
    span: tuple[int, int] | None = None,
) -> bool:
    """Whether a wider dismissal covers one finding: the same kind and code, and either
    the finding is about the character (``about``: the entry ids of whom it is about,
    never merely whom it mentions), or the scene's ``position`` in reading order falls
    in the stretch (``span``: the positions of its first and last scene, now)."""
    if dismissal.kind is not kind or dismissal.code != code:
        return False
    if dismissal.scope is DismissalScope.CHARACTER:
        return bool(dismissal.entity_id) and dismissal.entity_id in about
    if position is None or span is None:
        return False
    low, high = min(span), max(span)
    return low <= position <= high


def review_fingerprint(mutation_id: str, operation: str, code: str, occurrence: int = 0) -> str:
    """One finding on one change of a proposal: ``operation`` identifies the change as it
    stands (its id, revision and a digest of its content), or "" for the proposal as a
    whole. ``occurrence`` tells apart findings of the same kind on the same change (two
    possible duplicates, say), in the order they are given; the first keeps the key it
    always had."""
    parts = ["review", mutation_id, operation, code]
    if occurrence:
        parts.append(str(occurrence))
    raw = "\x1f".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_SENTENCE_END = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s)|\n\s*\n")


def _normal(words: str) -> str:
    return " ".join(unicodedata.normalize("NFC", words).split())


def anchor(text: str, start: int | None, end: int | None) -> str:
    """The words a finding points at: the whole sentence its passage sits in.

    A passage longer than one sentence (a paragraph) is its own anchor. A finding
    about the scene as a whole has none.
    """
    if start is None or end is None or not (0 <= start < end <= len(text)):
        return ""
    sentence_start = 0
    for match in _SENTENCE_END.finditer(text):
        if match.end() > start:
            break
        sentence_start = match.end()
    sentence_end = len(text)
    for match in _SENTENCE_END.finditer(text, start):
        if match.end() >= end:
            sentence_end = match.end()
            break
        if match.start() >= start:
            # The passage runs on past the end of a sentence: it is its own anchor.
            return _normal(text[start:end])
    return _normal(text[sentence_start:sentence_end])


def fingerprint(kind: FindingKind, code: str, subject: str, passage: str) -> str:
    """Same finding, same words: same fingerprint. Nothing of the words can be read back."""
    raw = "\x1f".join((kind.value, code, _normal(subject).casefold(), passage))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def voice_fingerprint(code: str, subject: str, message: str, passage: str) -> str:
    """A voice note's fingerprint. A note on a passage is known by that sentence. A note
    on the whole scene has no passage, so a digest of what it says stands in for one, and
    two such notes of one kind and subject never share a dismissal; its numbers are left
    out, so a dismissal holds while only they move."""
    if not passage:
        said = re.sub(r"\d[\d,.]*", "#", _normal(message).casefold())
        passage = "scene:" + hashlib.sha256(said.encode("utf-8")).hexdigest()
    return fingerprint(FindingKind.CHECK, code, subject, passage)


def expiry(now: datetime, days: int | None) -> datetime | None:
    if days is None:
        return None
    if days not in EXPIRY_DAYS:
        raise ValueError("unsupported expiry")
    return now + timedelta(days=days)


__all__ = [
    "EXPIRY_DAYS",
    "MAX_DISMISSALS_PER_SCENE",
    "MAX_NOTE",
    "MAX_SCOPED_DISMISSALS",
    "DismissReason",
    "DismissalScope",
    "FindingDismissal",
    "FindingKind",
    "ReviewDismissal",
    "ScopedDismissal",
    "names_someone",
    "scope_covers",
    "anchor",
    "expiry",
    "fingerprint",
    "review_fingerprint",
    "voice_fingerprint",
]
