"""Checks on a scene's prose that need no model (ADR-022).

Each finding says what it noticed in plain words, how sure it can be ("warning" or
"note"), and where in the text, so it can sit beside the words as a margin note. These
are deterministic and cheap; they run on every draft and every version, and none of
them ever changes the text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from backend.domain.sources import Mention, MentionStatus

# Phrases readers increasingly recognise as machine-written, from writing-tool checklists
# (2025). Matched as whole words, case-insensitively.
STOCK_PHRASES: tuple[str, ...] = (
    "delve",
    "tapestry",
    "testament to",
    "a sense of",
    "couldn't help but",
    "could not help but",
    "sent shivers down",
    "a wave of",
    "barely above a whisper",
    "in that moment",
    "the weight of",
    "palpable",
    "unspoken",
    "it was as if",
    "little did",
    "breath she didn't know",
    "breath he didn't know",
)

_STOCK = re.compile(
    r"(?<![\w])(" + "|".join(re.escape(phrase) for phrase in STOCK_PHRASES) + r")(?![\w])",
    re.IGNORECASE,
)
_PARAGRAPH = re.compile(r"\n\s*\n")
_FIRST_PERSON = re.compile(r"(?<![\w])(I|me|my)(?![\w])")


class Severity(StrEnum):
    WARNING = "warning"
    NOTE = "note"


@dataclass(frozen=True)
class DraftFinding:
    code: str
    severity: Severity
    message: str
    start: int | None = None
    end: int | None = None
    # What the finding is about (a name, a secret, a place), so the same finding can be
    # recognised again when the author has dismissed it. Never shown.
    subject: str = ""


@dataclass(frozen=True)
class SecretToGuard:
    """A secret the viewpoint character does not know, as the names that would give it away."""

    sentence: str
    names: tuple[str, ...]


@dataclass(frozen=True)
class PlannedReveal:
    sentence: str
    names: tuple[str, ...]


@dataclass
class DraftBrief:
    """What the checks know about the scene the prose is meant to be."""

    point_of_view: str | None = None
    point_of_view_names: tuple[str, ...] = ()
    place: str | None = None
    place_names: tuple[str, ...] = ()
    secrets: list[SecretToGuard] = field(default_factory=list)
    reveals: list[PlannedReveal] = field(default_factory=list)
    words_asked: int | None = None


def _paragraphs(text: str) -> list[tuple[int, int]]:
    spans, cursor = [], 0
    for match in _PARAGRAPH.finditer(text):
        if text[cursor : match.start()].strip():
            spans.append((cursor, match.start()))
        cursor = match.end()
    if text[cursor:].strip():
        spans.append((cursor, len(text)))
    return spans


def _mentions(text: str, names: Iterable[str]) -> bool:
    for name in names:
        if name and re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text, re.IGNORECASE):
            return True
    return False


def word_count(text: str) -> int:
    return len(text.split())


def check_draft(text: str, brief: DraftBrief, mentions: Sequence[Mention]) -> list[DraftFinding]:
    findings: list[DraftFinding] = []

    # New names: marked by the writer, but nothing in the story bible answers to them.
    seen: set[str] = set()
    for mention in mentions:
        if mention.marked and mention.status is MentionStatus.UNKNOWN:
            key = mention.phrase.lower()
            if key in seen:
                continue
            seen.add(key)
            findings.append(
                DraftFinding(
                    "new_name",
                    Severity.NOTE,
                    f"“{mention.phrase}” is a new name. Add it to the story bible, or say who it is.",
                    mention.start,
                    mention.end,
                    subject=key,
                )
            )

    # A secret the viewpoint character does not know, all of whose names share a paragraph.
    for secret in brief.secrets:
        names = [name for name in secret.names if name]
        if len(names) < 2:
            continue
        for start, end in _paragraphs(text):
            paragraph = text[start:end]
            if all(_mentions(paragraph, [name]) for name in names):
                findings.append(
                    DraftFinding(
                        "secret",
                        Severity.WARNING,
                        f"This may give away a secret {brief.point_of_view or 'the viewpoint character'} "
                        f"does not know: {secret.sentence}.",
                        start,
                        end,
                        subject=secret.sentence,
                    )
                )
                break

    # A reveal the plan promised, with none of its names on the page.
    for reveal in brief.reveals:
        names = [name for name in reveal.names if name]
        if names and not all(_mentions(text, [name]) for name in names):
            findings.append(
                DraftFinding(
                    "reveal_missing",
                    Severity.NOTE,
                    f"The plan says the reader learns this here, and it may be missing: {reveal.sentence}.",
                    subject=reveal.sentence,
                )
            )

    # The place and the viewpoint, never mentioned.
    if brief.place and brief.place_names and not _mentions(text, brief.place_names):
        findings.append(
            DraftFinding(
                "place_missing",
                Severity.NOTE,
                f"The scene happens at {brief.place}, which is never named.",
                subject=brief.place,
            )
        )
    if (
        brief.point_of_view
        and brief.point_of_view_names
        and not _mentions(text, brief.point_of_view_names)
        and not _FIRST_PERSON.search(text)
    ):
        findings.append(
            DraftFinding(
                "viewpoint_missing",
                Severity.NOTE,
                f"The scene is seen through {brief.point_of_view}, who is never named.",
                subject=brief.point_of_view,
            )
        )

    # Stock phrasing, each place it occurs (at most a few of each).
    counts: dict[str, int] = {}
    for match in _STOCK.finditer(text):
        phrase = match.group(1).lower()
        counts[phrase] = counts.get(phrase, 0) + 1
        if counts[phrase] > 3:
            continue
        findings.append(
            DraftFinding(
                "stock_phrase",
                Severity.NOTE,
                f"“{match.group(1)}” is stock phrasing that readers often notice.",
                match.start(),
                match.end(),
                subject=phrase,
            )
        )

    # Length, when a length was asked for.
    if brief.words_asked:
        words = word_count(text)
        if words < brief.words_asked * 0.5:
            findings.append(
                DraftFinding(
                    "short",
                    Severity.NOTE,
                    f"About {words} words, against the {brief.words_asked} asked for.",
                    subject=str(brief.words_asked),
                )
            )
        elif words > brief.words_asked * 2:
            findings.append(
                DraftFinding(
                    "long",
                    Severity.NOTE,
                    f"About {words} words, against the {brief.words_asked} asked for.",
                    subject=str(brief.words_asked),
                )
            )

    order = {Severity.WARNING: 0, Severity.NOTE: 1}
    findings.sort(key=lambda f: (order[f.severity], f.start if f.start is not None else 10**9))
    return findings


__all__ = [
    "STOCK_PHRASES",
    "DraftBrief",
    "DraftFinding",
    "PlannedReveal",
    "SecretToGuard",
    "Severity",
    "check_draft",
    "word_count",
]
