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


@dataclass(frozen=True)
class LaterKnowledge:
    """Something a person in the scene only learns after it (CON-006), with the signals
    that say the scene may use it already."""

    sentence: str
    names: tuple[str, ...]
    # Who learns it later, and where ("in “The harbour”", or "later in the story").
    who: str
    where: str
    # The scene is seen through them: their thoughts and actions are on the page.
    viewpoint: bool = True
    planned: bool = False
    in_context: bool = False
    said: bool = False


@dataclass
class DraftBrief:
    """What the checks know about the scene the prose is meant to be."""

    point_of_view: str | None = None
    point_of_view_names: tuple[str, ...] = ()
    place: str | None = None
    place_names: tuple[str, ...] = ()
    secrets: list[SecretToGuard] = field(default_factory=list)
    reveals: list[PlannedReveal] = field(default_factory=list)
    learned_later: list[LaterKnowledge] = field(default_factory=list)
    words_asked: int | None = None
    # Continuity across story time and place for this scene (CON-003, CON-004), found
    # from the story bible and the plan, not the prose; shown with the other checks.
    continuity: list[DraftFinding] = field(default_factory=list)
    # A name shown above → the entry it stands for, and → other phrases that mean that entry
    # in this scene (its aliases, and the author's answers that hold here; CTX-016).
    ids: dict[str, str] = field(default_factory=dict)
    also: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def names_for(self, name: str) -> list[str]:
        return [name, *self.also.get(name, ())]


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
            if all(_mentions(paragraph, brief.names_for(name)) for name in names):
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

    # Something a person in the scene only learns later (CON-006). The words on the page
    # name everyone in it, the plan shows it through them, drafting would give it as true,
    # or they say it: each is only a sign, so each is a warning and never more.
    # What drafting would give as true is said once, for every such fact together, and not
    # again for a fact a warning above already points at: one problem, one note.
    covered: set[str] = set()
    in_context: list = []
    for later in brief.learned_later:
        lead = f"{later.who} only learns this {later.where}: {later.sentence}."
        if later.viewpoint:
            names = [name for name in later.names if name]
            if len(names) >= 2:
                for start, end in _paragraphs(text):
                    paragraph = text[start:end]
                    if all(_mentions(paragraph, brief.names_for(name)) for name in names):
                        covered.add(later.sentence)
                        findings.append(
                            DraftFinding(
                                "learned_later",
                                Severity.WARNING,
                                f"This may use something too early. {lead}",
                                start,
                                end,
                                subject=later.sentence,
                            )
                        )
                        break
        if later.planned:
            covered.add(later.sentence)
            findings.append(
                DraftFinding(
                    "learned_later_planned",
                    Severity.WARNING,
                    f"The plan shows the reader this through {later.who} here. {lead}",
                    subject=later.sentence,
                )
            )
        if later.said:
            covered.add(later.sentence)
            findings.append(
                DraftFinding(
                    "learned_later_said",
                    Severity.WARNING,
                    f"{later.who} says this in this scene. {lead}",
                    subject=f"{later.who}\x1e{later.sentence}",
                )
            )
        if later.in_context:
            in_context.append(later)
    shown = [later for later in in_context if later.sentence not in covered]
    if shown:
        who = sorted({later.who for later in shown})
        whom = who[0] if len(who) == 1 else "the people in this scene"
        facts = "; ".join(f"{later.sentence} ({later.where})" for later in shown)
        findings.append(
            DraftFinding(
                "learned_later_context",
                Severity.WARNING,
                f"Drafting would give the assistant {'this' if len(shown) == 1 else 'these'} as "
                f"true, so {'it' if len(shown) == 1 else 'they'} may end up in {whom}'s thoughts "
                f"before they learn {'it' if len(shown) == 1 else 'them'}: {facts}.",
                subject="\x1e".join(sorted(later.sentence for later in shown)),
            )
        )

    # A reveal the plan promised, with none of its names on the page.
    for reveal in brief.reveals:
        names = [name for name in reveal.names if name]
        if names and not all(_mentions(text, brief.names_for(name)) for name in names):
            findings.append(
                DraftFinding(
                    "reveal_missing",
                    Severity.NOTE,
                    f"The plan says the reader learns this here, and it may be missing: {reveal.sentence}.",
                    subject=reveal.sentence,
                )
            )

    # The place and the viewpoint, never mentioned.
    if (
        brief.place
        and brief.place_names
        and not _mentions(text, [*brief.place_names, *brief.also.get(brief.place, ())])
    ):
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
        and not _mentions(
            text, [*brief.point_of_view_names, *brief.also.get(brief.point_of_view, ())]
        )
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

    findings.extend(brief.continuity)

    order = {Severity.WARNING: 0, Severity.NOTE: 1}
    findings.sort(key=lambda f: (order[f.severity], f.start if f.start is not None else 10**9))
    return findings


__all__ = [
    "STOCK_PHRASES",
    "DraftBrief",
    "DraftFinding",
    "LaterKnowledge",
    "PlannedReveal",
    "SecretToGuard",
    "Severity",
    "check_draft",
    "word_count",
]
