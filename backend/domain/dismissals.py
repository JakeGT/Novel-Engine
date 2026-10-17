"""Dismissing a finding in the scene studio, with a reason (CON-010, CON-016).

A dismissal belongs to one scene in one line of the story. It points at the words the
finding was about by a fingerprint, never by storing them: the kind of finding, what it
is about, and the sentence it points at. While that sentence is unchanged the finding
stays dismissed; when the words change the fingerprint no longer matches and the finding
comes back (a review comment goes "outdated" the same way when the line it is on
changes). A dismissal can also run out after a set time. Undoing one keeps the record
and marks it withdrawn, so a dismissal is never changed, only replaced.

Only advisory findings in the studio can be dismissed. Nothing here touches the checks
that guard the story itself, which can never be dismissed or switched off (ADR-013).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
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

    def applies(self, now: datetime) -> bool:
        if self.withdrawn_at is not None:
            return False
        return self.expires_at is None or self.expires_at > now


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
    "DismissReason",
    "FindingDismissal",
    "FindingKind",
    "anchor",
    "expiry",
    "fingerprint",
]
