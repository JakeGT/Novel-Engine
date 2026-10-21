"""Checks on a scene's prose that need no model (ADR-022).

Each finding says what it noticed in plain words, how sure it can be ("warning" or
"note"), and where in the text, so it can sit beside the words as a margin note. These
are deterministic and cheap; they run on every draft and every version, and none of
them ever changes the text.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from backend.domain.appearance import (
    NUMBER_PATTERN,
    AgeKnown,
    AppearanceDetail,
    clashes,
    identity_clashes,
    number_of,
)
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
# What someone says aloud, between straight or curly double quotes or curly single quotes:
# an "I" there is the speaker's, not the narrator's. A closing ’ is also an apostrophe
# ("Wren’s"), so a single-quoted span must end where no letter follows.
_SPOKEN = re.compile(r"\"[^\"]*\"|“[^”]*”|‘(?:[^’]|’(?=\w))*’(?!\w)")
_SENTENCE = re.compile(r"[^.!?]+(?:[.!?]+|$)")
_NEGATION = re.compile(r"(?<![\w])(not|never|no|nothing)(?![\w])|n[’']t(?![\w])", re.IGNORECASE)


def _first_person(text: str) -> bool:
    """The narration says I, me or my (outside anything a character says aloud)."""
    return bool(_FIRST_PERSON.search(_SPOKEN.sub(" ", text)))


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


@dataclass(frozen=True)
class AfterShown:
    """An "after" condition that names two things the prose can be read for (STY-005):
    an entry and the other end of its fact, or the value it should hold."""

    reading: str
    names: tuple[str, ...]


@dataclass(frozen=True)
class TimeNeighbour:
    """The scene before this one in reading order, and how far before it is in story time.

    ``days`` is the least and the most it can be, in days, from the two scenes' times
    and the story's unit; ``same_day`` is True when both fall in the same whole day of a
    story counted in days, False when certainly not, None when that cannot be told.
    """

    title: str
    # None in a story whose unit has no length in days ("chapter", "cycle", "turn"):
    # then only ``apart`` is known, and only "this comes before it" can be said.
    days: tuple[float, float] | None
    same_day: bool | None = None
    # In the author's terms, for the note: "day 5", "between day 3 and 4" (the
    # scene before, and this one).
    this_when: str = ""
    then_when: str = ""
    # The least and the most this scene comes after the one before, in the story's own
    # units (negative: before it).
    apart: tuple[float, float] | None = None


@dataclass(frozen=True)
class Holding:
    """Something a person could use in the scene, and who has it at the scene's time.

    ``holder`` is who has it then (None when no one does); ``gone`` says, in a few words,
    that it is used up, lost or destroyed by then ("its status is used up")."""

    thing: str
    names: tuple[str, ...]
    holder: str | None = None
    holder_names: tuple[str, ...] = ()
    gone: str = ""


@dataclass(frozen=True)
class Supply:
    """How many of a thing there are at the scene's time, from a fact of the story bible
    (a kind of fact such as "has quantity" or "count" with a number), in the words the
    prose would count it in: the last word of each of its names ("arrows", "coins")."""

    thing: str
    names: tuple[str, ...]
    amount: float
    # Where the number comes from, said plainly: "the story bible gives 12 from day 3".
    source: str = "the story bible"
    key: str = ""


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
    # Checks against the prose itself (STY-005, SCN-007, CON-004, CON-014): who is in
    # the scene, what it should leave true, when it comes after the scene before it,
    # who has what then, and how its people look in their accepted details.
    people: tuple[str, ...] = ()
    after: list[AfterShown] = field(default_factory=list)
    previous: TimeNeighbour | None = None
    holdings: list[Holding] = field(default_factory=list)
    # How many of a thing there are at the scene's time, from the story bible (SCN-007).
    supplies: list[Supply] = field(default_factory=list)
    appearance: list[AppearanceDetail] = field(default_factory=list)
    # How old the scene's people can be at its time, from the story bible (CON-004).
    ages: list[AgeKnown] = field(default_factory=list)

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


def _first_end(text: str, names: Iterable[str]) -> int | None:
    """Where the first mention of any of the names ends, if one is there."""
    ends = [
        match.end()
        for name in names
        if name
        and (match := re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text, re.IGNORECASE))
    ]
    return min(ends) if ends else None


def _only_denied(paragraph: str, names: list[list[str]]) -> bool:
    """Every sentence naming all sides of the secret says not, never, no, nothing or n't
    before the last of them is named ("Osric had never spoken to the Tidewardens"). A
    negation after them is about something else ("…and thought she had not seen")."""
    denied = False
    for sentence in _SENTENCE.findall(paragraph):
        ends = [_first_end(sentence, group) for group in names]
        if any(end is None for end in ends):
            continue
        if not _NEGATION.search(sentence[: max(end for end in ends if end is not None)]):
            return False
        denied = True
    return denied


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
    # Named together only to be denied ("Osric had never spoken to the Tidewardens") it
    # still puts the idea in the reader's head, so it is noted, not warned about.
    for secret in brief.secrets:
        names = [name for name in secret.names if name]
        if len(names) < 2:
            continue
        for start, end in _paragraphs(text):
            paragraph = text[start:end]
            if all(_mentions(paragraph, brief.names_for(name)) for name in names):
                if _only_denied(paragraph, [brief.names_for(name) for name in names]):
                    findings.append(
                        DraftFinding(
                            "secret_denied",
                            Severity.NOTE,
                            "This names both sides of a secret "
                            f"{brief.point_of_view or 'the viewpoint character'} does not know, "
                            f"even to deny it: {secret.sentence}.",
                            start,
                            end,
                            subject=secret.sentence,
                        )
                    )
                else:
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
        and not _first_person(text)
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

    findings.extend(prose_findings(text, brief))
    findings.extend(brief.continuity)

    order = {Severity.WARNING: 0, Severity.NOTE: 1}
    findings.sort(key=lambda f: (order[f.severity], f.start if f.start is not None else 10**9))
    return findings


# ------------------------------------------------------------------ against the prose
#
# Checks that read the prose against what the plan and the story bible say about this
# scene (STY-005, SCN-007, CON-004, CON-014). Each looks only for a signal it can read
# for certain (two names together, a word for when, a colour word), so each is a note
# or, where nothing could make both true, a warning. What they cannot read (a pronoun,
# a paraphrase) is left to the assistant's check of the scene.

# How many days one of a story's units is, for comparing "the next day" with story time.
DAYS_PER_UNIT: dict[str, float] = {
    "minute": 1 / 1440,
    "min": 1 / 1440,
    "hour": 1 / 24,
    "hr": 1 / 24,
    "day": 1.0,
    "week": 7.0,
    "month": 30.0,
    "second": 1 / 86400,
    "sec": 1 / 86400,
    "night": 1.0,
    "fortnight": 14.0,
    "season": 91.0,
    "year": 365.0,
    "decade": 3650.0,
    "century": 36500.0,
}
# Only the opening of a scene says how it follows the one before.
_OPENING_SENTENCES = 2
_OPENING_LIMIT = 400

_NEXT_DAY = re.compile(
    r"(?<![\w])(?:(?:the\s+)?(?:next|following)\s+(?:day|morning|afternoon|evening|night|dawn)"
    r"|the\s+(?:day|morning|night)\s+after(?!\s+(?:the|a|an|that|his|her|their)\b)"
    r"|a\s+day\s+later)(?![\w])",
    re.IGNORECASE,
)
_SAME_DAY = re.compile(
    r"(?<![\w])(?:(?:later\s+that|that\s+same|the\s+same)\s+(?:day|morning|afternoon|evening|night)"
    r"|(?:an?|one|two|three|a\s+few|some)\s+hours?\s+later"
    r"|(?:minutes|moments)\s+later)(?![\w])",
    re.IGNORECASE,
)
_NUMBER_WORDS: dict[str, tuple[float, float]] = {
    "a": (1, 1),
    "an": (1, 1),
    "one": (1, 1),
    "two": (2, 2),
    "three": (3, 3),
    "four": (4, 4),
    "five": (5, 5),
    "six": (6, 6),
    "seven": (7, 7),
    "eight": (8, 8),
    "nine": (9, 9),
    "ten": (10, 10),
    "eleven": (11, 11),
    "twelve": (12, 12),
    "a few": (2, 5),
    "several": (3, 7),
}
_AMOUNT = re.compile(
    r"(?<![\w])(?P<n>a\s+few|several|an?|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|\d{1,3})\s+(?P<unit>days?|weeks?|months?|years?)\s+"
    # "Three days on, ..." but not "three days on the road".
    r"(?:later|passed|on(?=\s*[,;:—–]))"
    r"(?![\w])",
    re.IGNORECASE,
)
# Words before a phrase for when that say it is not what happens: "did not wait for
# the next morning", "promised to be gone by the next morning".
_NOT_SO = re.compile(
    r"(?<![\w])(?:not|never|no|n[’']t|without|would|could|should|might|must|"
    r"promised|promises|promise|hoped|hopes|planned|plans|meant|intended|wanted|wants|"
    r"expected|until|till|wait|waited|waiting|unless|if)(?![\w])",
    re.IGNORECASE,
)
_USES = re.compile(
    r"(?<![\w])(?:drew|draws|held|holds|holding|carried|carries|carrying|wielded|wields|"
    r"wielding|gripped|grips|clutched|clutches|raised|raises|swung|swings|lifted|lifts|"
    r"opened|opens|used|uses|consulted|consults|wore|wears|pocketed|unwrapped|unrolled|"
    r"took\s+out|pulls?\s+out|pulled\s+out|handed|hands|polished|aimed|fired|"
    r"drank|drinks|ate|eats|spent|spends|lit|lights)(?![\w])",
    re.IGNORECASE,
)
# The thing must come soon after the word for using it: "lifted the old sextant", not
# "held her breath and stared at the sextant".
_USE_REACH_WORDS = 3
_NOT_USED = re.compile(
    r"(?<![\w])(?:not|never|no|n[’']t|without|remembered|remembers|thought\s+of|wished|"
    r"missed|missing|lost|gone|had\s+given|once|used\s+to|dreamed|dreamt|imagined)(?![\w])",
    re.IGNORECASE,
)


def _unit_days(unit: str) -> float | None:
    word = " ".join(unit.replace("_", " ").replace("-", " ").lower().split())
    return DAYS_PER_UNIT.get(word, DAYS_PER_UNIT.get(word.removesuffix("s")))


def days_between(
    before: tuple[float, float], after: tuple[float, float], unit: str, *, whole: bool
) -> tuple[float, float] | None:
    """The least and the most days from one time to the other, or None for a unit with
    no length in days. ``whole``: a whole number means some time in that unit
    ("day 5" is any time in day 5), so each end reaches to the next."""
    per = _unit_days(unit)
    if per is None:
        return None
    reach = 1.0 if whole else 0.0
    b_lo, b_hi = before[0], before[1] + (reach if whole and float(before[1]).is_integer() else 0)
    a_lo, a_hi = after[0], after[1] + (reach if whole and float(after[1]).is_integer() else 0)
    return ((a_lo - b_hi) * per, (a_hi - b_lo) * per)


def _without_speech(text: str) -> str:
    """The text with what characters say aloud blanked, so offsets still match."""
    return _SPOKEN.sub(lambda match: " " * len(match.group(0)), text)


def _opening(text: str) -> str:
    spans = _paragraphs(text)
    if not spans:
        return ""
    start, end = spans[0]
    first = text[:end]
    sentences = list(_SENTENCE.finditer(first, start))
    if sentences:
        end = sentences[min(len(sentences), _OPENING_SENTENCES) - 1].end()
    return _without_speech(text[: min(end, start + _OPENING_LIMIT)])


def _days_words(days: float) -> str:
    if days < 1:
        hours = max(1, round(days * 24))
        return f"{hours} hour{'s' if hours != 1 else ''}"
    whole = round(days)
    return f"{whole} day{'s' if whole != 1 else ''}"


def _days_range(lo: float, hi: float) -> str:
    if lo >= 1 and round(hi) != round(lo):
        return f"{round(lo)} to {_days_words(hi)}"
    return _days_words(lo)


def time_words(text: str, previous: TimeNeighbour) -> list[DraftFinding]:
    """The opening's words for when ("the next morning", "three days later", "that
    evening") against the story time between this scene and the one before (SCN-007)."""
    opening = _opening(text)
    if not opening.strip():
        return []
    if previous.days is None:
        return _order_words(opening, previous)
    lo, hi = previous.days
    whole = previous.same_day
    then = (
        f" (“{previous.title}” is {previous.then_when}; this one is {previous.this_when})"
        if previous.this_when and previous.then_when
        else ""
    )
    found: list[DraftFinding] = []

    def add(code: str, severity: Severity, message: str, match: re.Match[str]) -> None:
        found.append(
            DraftFinding(
                code, severity, message, match.start(), match.end(), subject=match.group(0).lower()
            )
        )

    def said(pattern: re.Pattern[str]) -> list[re.Match[str]]:
        """Each match that says this is when the scene comes, not hedged or denied."""
        kept = []
        for match in pattern.finditer(opening):
            sentence = next(
                (s for s in _SENTENCE.finditer(opening) if s.start() <= match.start() < s.end()),
                None,
            )
            before = opening[sentence.start() if sentence else 0 : match.start()]
            if not _NOT_SO.search(before):
                kept.append(match)
        return kept

    nxt = next(iter(said(_NEXT_DAY)), None)
    if nxt is not None:
        words = nxt.group(0)
        if hi < 0:
            add(
                "time_words_before",
                Severity.WARNING if whole is False and hi <= -1 else Severity.NOTE,
                f"This opens “{words}”, but in story time it comes before “{previous.title}”{then}.",
                nxt,
            )
        elif whole is True:
            add(
                "time_words_next_day",
                Severity.WARNING,
                f"This opens “{words}”, but it happens on the same day as “{previous.title}”{then}.",
                nxt,
            )
        elif lo >= 2:
            add(
                "time_words_next_day",
                Severity.NOTE,
                f"This opens “{words}”, but it comes at least {_days_words(lo)} after "
                f"“{previous.title}”{then}.",
                nxt,
            )
    same = next(iter(said(_SAME_DAY)), None)
    if same is not None:
        words = same.group(0)
        if hi < 0:
            add(
                "time_words_before",
                Severity.NOTE,
                f"This opens “{words}”, but in story time it comes before “{previous.title}”{then}.",
                same,
            )
        elif lo >= 1.5:
            add(
                "time_words_same_day",
                Severity.WARNING if lo >= 2 else Severity.NOTE,
                f"This opens “{words}”, but it comes at least {_days_words(lo)} after "
                f"“{previous.title}”{then}.",
                same,
            )
    read = [match.span() for match in (nxt, same) if match is not None]
    for amount in said(_AMOUNT):
        # "A day later" is read once, as the next day.
        if any(amount.start() < end and start < amount.end() for start, end in read):
            continue
        number = amount.group("n").lower()
        count = (
            (float(number), float(number))
            if number.isdigit()
            else _NUMBER_WORDS.get(" ".join(number.split()), (1, 1))
        )
        per = _unit_days(amount.group("unit"))
        if per is None:
            continue
        said_lo, said_hi = count[0] * per, count[1] * per
        # Generous on purpose: "three days later" is not wrong for two and a half or four.
        slack = 1.0 if per <= 7 else per * 0.25
        if said_hi * 1.5 + slack < lo or said_lo * 0.5 - slack > hi:
            add(
                "time_words_amount",
                Severity.NOTE,
                f"This opens “{amount.group(0)}”, but in story time it comes "
                + (
                    f"before “{previous.title}”"
                    if hi < 0
                    else _days_range(lo, hi) + f" after “{previous.title}”"
                )
                + f"{then}.",
                amount,
            )
        break
    return found


def _not_denied(opening: str, pattern: re.Pattern[str]) -> list[re.Match[str]]:
    kept = []
    for match in pattern.finditer(opening):
        sentence = next(
            (s for s in _SENTENCE.finditer(opening) if s.start() <= match.start() < s.end()),
            None,
        )
        if not _NOT_SO.search(opening[sentence.start() if sentence else 0 : match.start()]):
            kept.append(match)
    return kept


def _order_words(opening: str, previous: TimeNeighbour) -> list[DraftFinding]:
    """In a story whose unit has no length in days, how far apart two scenes are cannot
    be told, so "the next morning" and "three days later" are not measured. Only one
    thing can be said for certain: that this scene comes before the one before it in
    story time, while its opening says it comes after."""
    if previous.apart is None or previous.apart[1] >= 0:
        return []
    then = (
        f" (“{previous.title}” is {previous.then_when}; this one is {previous.this_when})"
        if previous.this_when and previous.then_when
        else ""
    )
    for pattern in (_NEXT_DAY, _SAME_DAY, _AMOUNT):
        match = next(iter(_not_denied(opening, pattern)), None)
        if match is not None:
            return [
                DraftFinding(
                    "time_words_before",
                    Severity.NOTE,
                    f"This opens “{match.group(0)}”, but in story time it comes before "
                    f"“{previous.title}”{then}.",
                    match.start(),
                    match.end(),
                    subject=match.group(0).lower(),
                )
            ]
    return []


def _spans(text: str, names: Iterable[str]) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    for name in names:
        if name:
            found.extend(
                (m.start(), m.end())
                for m in re.finditer(rf"(?<![\w]){re.escape(name)}(?![\w])", text, re.IGNORECASE)
            )
    return sorted(found)


def resource_findings(text: str, brief: DraftBrief) -> list[DraftFinding]:
    """Someone in the scene using a thing that someone else has then, or that is used
    up, lost or destroyed by then (SCN-007, CON-004). Read only where a sentence names
    the person, then a word for using it, then the thing within a few words."""
    if not brief.holdings or not brief.people:
        return []
    found: list[DraftFinding] = []
    plain = _without_speech(text)
    for holding in brief.holdings:
        thing_names = [*holding.names, *brief.also.get(holding.thing, ())]
        holder_names = (
            [*holding.holder_names, *brief.also.get(holding.holder, ())] if holding.holder else []
        )
        for sentence in _SENTENCE.finditer(plain):
            words = sentence.group(0)
            if _NOT_USED.search(words):
                continue
            things = _spans(words, thing_names)
            if not things:
                continue
            user = None
            for person in brief.people:
                if holding.holder is not None and person == holding.holder:
                    continue
                spans = _spans(words, brief.names_for(person))
                if not spans:
                    continue
                if any(
                    0 <= start - verb.end()
                    and len(words[verb.end() : start].split()) <= _USE_REACH_WORDS
                    for verb in _USES.finditer(words, spans[0][1])
                    for start, _ in things
                ):
                    user = person
                    break
            if user is None:
                continue
            if holding.holder is not None and _spans(words, holder_names):
                continue
            start = sentence.start()
            end = sentence.end()
            if holding.gone:
                found.append(
                    DraftFinding(
                        "resource_used_up",
                        Severity.NOTE,
                        f"Here {user} uses {holding.thing}, but by this point in the story "
                        f"{holding.gone}.",
                        start,
                        end,
                        subject=f"{holding.thing}\x1egone",
                    )
                )
            elif holding.holder is not None:
                found.append(
                    DraftFinding(
                        "resource_held_elsewhere",
                        Severity.NOTE,
                        f"Here {user} has {holding.thing}, but at this time {holding.holder} "
                        "has it, and is not named here.",
                        start,
                        end,
                        subject=f"{holding.thing}\x1e{holding.holder}",
                    )
                )
            break
    return found


# Words for spending some of a thing: "fired three arrows", "paid ten coins".
_SPENDS = re.compile(
    r"(?<![\w])(?:fired|fires|shot|shoots|loosed|looses|spent|spends|paid|pays|drank|drinks|"
    r"ate|eats|used|uses|threw|throws|burned|burnt|burns|lit|lights|broke|breaks|tossed|"
    r"tosses|emptied|empties|swallowed|swallows|downed|gave\s+away|gives\s+away|"
    r"handed\s+over|hands\s+over)(?![\w])",
    re.IGNORECASE,
)
# Words for having some: "had nine arrows", "counted twelve coins".
_HAS_SOME = re.compile(
    r"(?<![\w])(?:had|has|carried|carries|counted|counts|held|holds|kept|keeps)(?![\w])",
    re.IGNORECASE,
)
_LEFT_AFTER = re.compile(r"^\s+(?:left|remaining|remained|to\s+spare)(?![\w])", re.IGNORECASE)
_SUPPLY_HEDGE = re.compile(
    r"(?<![\w])(?:not|never|no|n[’']t|without|would|could|should|might|must|if|unless|"
    r"wished|wanted|meant|planned|hoped|remembered|thought|dreamed|dreamt|imagined|once|"
    r"used\s+to|before|last\s+time|other|others|another|more\s+than|nearly|almost|"
    r"about|some|over|under)(?![\w])",
    re.IGNORECASE,
)
_ONE = re.compile(r"^(?:a|an|one|single|a\s+single)$", re.IGNORECASE)
# The verb and the number must be close: "fired three arrows", "drank two of the skins".
_SPEND_REACH_WORDS = 3


def _heads(names: Iterable[str]) -> list[str]:
    """The words the prose would count a thing in: the last word of each name, with and
    without a plural ending ("the arrows" → arrows, arrow)."""
    found: list[str] = []
    for name in names:
        words = re.findall(r"[\w-]+", name.lower())
        if not words:
            continue
        head = words[-1]
        found.append(head)
        if head.endswith("es") and len(head) > 3:
            found.extend([head[:-2], head[:-1]])
        elif head.endswith("s") and len(head) > 2:
            found.append(head[:-1])
        else:
            found.extend([head + "s", head + "es"])
    return list(dict.fromkeys(found))


def _amount_words(value: float) -> str:
    return f"{value:g}"


# Where a clause begins: a stop inside the sentence, or a word that starts another
# clause ("Wren watched as the guards fired"). The spender is read from that clause.
_CLAUSE_START = re.compile(
    r"[,;:—–()]|(?<![\w])(?:as|while|when|whereas|because|that|until|after|who|which|"
    r"where|whom|whose|so)(?![\w])",
    re.IGNORECASE,
)
_SUBJECT_PRONOUNS = frozenset({"she", "he", "they", "i", "we"})
_NOT_A_PERSON = frozenset(
    {
        "the",
        "a",
        "an",
        "his",
        "her",
        "their",
        "its",
        "my",
        "our",
        "your",
        "some",
        "this",
        "these",
        "those",
        "every",
        "each",
    }
)
# A sentence longer than this is not a sentence a writer meant; it is not read for counts.
_LONGEST_COUNTED_SENTENCE = 2_000


def _supply_holder(supply: Supply, brief: DraftBrief) -> str | None:
    """Whose the supply is: the holder the story bible gives for the thing, or a scene
    person named in its own name ("Wren's arrows")."""
    for holding in brief.holdings:
        if holding.holder and holding.thing == supply.thing:
            return holding.holder
    for person in brief.people:
        for name in brief.names_for(person):
            if name and re.match(rf"{re.escape(name)}[’']s(?![\w])", supply.thing, re.IGNORECASE):
                return person
    return None


def _holds(who: str | None, holder: str | None) -> bool:
    """Whether the one spending is someone in the scene, and the holder when one is known."""
    return who is not None and (holder is None or who == holder)


def supply_findings(text: str, brief: DraftBrief) -> list[DraftFinding]:
    """Counting what is spent (SCN-007): where someone in the scene spends a stated
    number of a thing ("Wren fired three arrows"), the numbers are added up through the
    scene, and a note says when they come to more than the story bible gives at this
    time; and a stated count left or had ("nine arrows left") that is more than could be
    left is a note too. Only numbers next to the thing's own word are read, never "it",
    "the rest", or what is said aloud. The one spending or having must be the subject of
    the clause, named or a pronoun for the last person named, and the thing's holder when
    the story bible says who that is: "the guards fired twenty arrows" is not counted."""
    if not brief.supplies or not brief.people:
        return []
    plain = _without_speech(text)
    found: list[DraftFinding] = []
    person_of: dict[str, str] = {}
    for person in brief.people:
        for name in brief.names_for(person):
            if name:
                person_of.setdefault(name.lower(), person)
    people = list(person_of)
    if not people:
        return []
    named = sorted(
        (start, person_of[plain[start:end].lower()])
        for start, end in _spans(plain, people)
        if plain[start:end].lower() in person_of
    )
    named_at = [start for start, _ in named]
    reference = re.compile(
        r"(?<![\w])(?:(?P<owner>[\w-]+[’']s)|"
        rf"(?P<name>{'|'.join(re.escape(n) for n in sorted(people, key=len, reverse=True))})|"
        r"(?P<word>[\w-]+))(?![\w])",
        re.IGNORECASE,
    )

    def first_person(head: str, at: int) -> tuple[bool, str | None]:
        """Whether the words name a subject, and the scene person it is if one."""
        for ref in reference.finditer(head):
            if ref.group("owner"):
                return True, None
            if ref.group("name"):
                return True, person_of[ref.group("name").lower()]
            word = ref.group("word").lower()
            if word in _SUBJECT_PRONOUNS:
                index = bisect_left(named_at, at + ref.start()) - 1
                return True, named[index][1] if index >= 0 else None
            if word in _NOT_A_PERSON:
                return True, None
        return False, None

    def subject(words: str, offset: int, verb: int, starts: list[re.Match[str]]) -> str | None:
        ends = [clause.end() for clause in starts]
        index = bisect_right(ends, verb) - 1
        begin = ends[index] if index >= 0 else 0
        found_one, who = first_person(words[begin:verb], offset + begin)
        if found_one or index < 0:
            return who
        # "Wren turned, fired three arrows": the sentence's own subject.
        head_end = starts[0].start()
        return first_person(words[:head_end], offset)[1]

    for supply in brief.supplies:
        heads = _heads([*supply.names, *brief.also.get(supply.thing, ())])
        if not heads:
            continue
        holder = _supply_holder(supply, brief)
        counted = re.compile(
            rf"(?<![\w-])(?P<n>{NUMBER_PATTERN}|a\s+single|single|an?)\s+"
            rf"(?:(?:more|of\s+(?:the|her|his|their|its|my))\s+)?(?:[\w-]+\s+){{0,2}}?"
            rf"(?P<noun>{'|'.join(re.escape(head) for head in heads)})(?![\w-])",
            re.IGNORECASE,
        )

        spent = 0.0
        said = False
        for sentence in _SENTENCE.finditer(plain):
            words = sentence.group(0)
            if len(words) > _LONGEST_COUNTED_SENTENCE or _SUPPLY_HEDGE.search(words):
                continue
            matches = list(counted.finditer(words))
            if not matches:
                continue
            offset = sentence.start()
            spends = list(_SPENDS.finditer(words))
            spend_ends = [verb.end() for verb in spends]
            haves = list(_HAS_SOME.finditer(words))
            have_ends = [verb.end() for verb in haves]
            starts = list(_CLAUSE_START.finditer(words))
            names_someone = bool(_spans(words, people))
            for match in matches:
                raw = " ".join(match.group("n").split())
                value = 1 if _ONE.match(raw) else number_of(raw)
                if value is None:
                    continue
                start, end = offset + match.start(), offset + match.end()
                # The nearest word for spending before the number, if close enough.
                index = bisect_right(spend_ends, match.start()) - 1
                verb = spends[index] if index >= 0 else None
                if (
                    verb is not None
                    and len(words[verb.end() : match.start()].split()) <= _SPEND_REACH_WORDS
                ):
                    if not _holds(subject(words, offset, verb.start(), starts), holder):
                        continue
                    spent += value
                    if spent > supply.amount and not said:
                        said = True
                        found.append(
                            DraftFinding(
                                "supply_overspent",
                                Severity.NOTE,
                                f"By here this scene has used {_amount_words(spent)} of "
                                f"{supply.thing}, but {supply.source} gives "
                                f"{_amount_words(supply.amount)} at this time.",
                                start,
                                end,
                                subject=f"{supply.thing}\x1espent",
                            )
                        )
                    continue
                index = bisect_right(have_ends, match.start()) - 1
                has = haves[index] if index >= 0 else None
                if (
                    has is not None
                    and len(words[has.end() : match.start()].split()) > _SPEND_REACH_WORDS
                ):
                    has = None
                if has is not None:
                    if not _holds(subject(words, offset, has.start(), starts), holder):
                        continue
                elif not (names_someone and _LEFT_AFTER.match(words[match.end() :])):
                    continue
                if value > supply.amount - spent and not said:
                    said = True
                    used = (
                        f", and {_amount_words(spent)} were used earlier in the scene"
                        if spent
                        else ""
                    )
                    found.append(
                        DraftFinding(
                            "supply_count_differs",
                            Severity.NOTE,
                            f"Here there are {value} of {supply.thing}, but {supply.source} "
                            f"gives {_amount_words(supply.amount)} at this time{used}.",
                            start,
                            end,
                            subject=f"{supply.thing}\x1ecount",
                        )
                    )
    return found


def after_findings(text: str, brief: DraftBrief) -> list[DraftFinding]:
    """An "after" condition whose names never share a paragraph (STY-005). Naming them
    together is not proof the scene shows it, so only the absence is said, as a note."""
    found: list[DraftFinding] = []
    paragraphs = [text[start:end] for start, end in _paragraphs(text)]
    for condition in brief.after:
        names = [name for name in condition.names if name]
        if len(names) < 2:
            continue
        if any(
            all(_mentions(paragraph, brief.names_for(name)) for name in names)
            for paragraph in paragraphs
        ):
            continue
        found.append(
            DraftFinding(
                "after_not_shown",
                Severity.NOTE,
                "The plan says this scene should leave this true, and no paragraph names "
                f"{' and '.join(names)} together: {condition.reading.rstrip('.')}.",
                subject=condition.reading,
            )
        )
    return found


def appearance_findings(text: str, brief: DraftBrief) -> list[DraftFinding]:
    """The prose giving someone eyes, hair or a beard of a colour their accepted
    details say otherwise (CON-004; with pictures' accepted details, CON-014)."""
    if not brief.appearance:
        return []
    details = [
        AppearanceDetail(
            who=detail.who,
            names=tuple(dict.fromkeys([*detail.names, *brief.also.get(detail.who, ())])),
            words=detail.words,
            source=detail.source,
            key=detail.key,
        )
        for detail in brief.appearance
    ]
    others = [
        name
        for person in brief.people
        for name in brief.names_for(person)
        if all(person != detail.who for detail in details)
    ]
    found: list[DraftFinding] = []
    for clash in clashes(_without_speech(text), details, others):
        said = " ".join(clash.detail.words.split())
        if len(said) > 160:
            said = said[:157].rstrip() + "…"
        found.append(
            DraftFinding(
                "appearance_differs",
                Severity.NOTE,
                f"Here {clash.who} has “{text[clash.start : clash.end]}”, but "
                f"{clash.detail.source} says: “{said}”",
                clash.start,
                clash.end,
                subject=f"{clash.who}\x1e{clash.feature}\x1e{clash.prose_colour}",
            )
        )
    return found


_IDENTITY_CODES = {"height": "height_differs", "mark": "mark_differs", "age": "age_differs"}


def identity_findings(text: str, brief: DraftBrief) -> list[DraftFinding]:
    """The prose giving someone a height, a scar or mark on the other side, or an age
    that the story bible says otherwise (CON-004). Notes, quoting both sides."""
    if not brief.appearance and not brief.ages:
        return []

    def names(who: str, given: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys([*given, *brief.also.get(who, ())]))

    details = [
        AppearanceDetail(
            who=detail.who,
            names=names(detail.who, detail.names),
            words=detail.words,
            source=detail.source,
            key=detail.key,
        )
        for detail in brief.appearance
    ]
    ages = [
        AgeKnown(age.who, names(age.who, age.names), age.lo, age.hi, age.because, age.key)
        for age in brief.ages
    ]
    known = {detail.who for detail in details} | {age.who for age in ages}
    others = [
        name for person in brief.people if person not in known for name in brief.names_for(person)
    ]
    found: list[DraftFinding] = []
    for clash in identity_clashes(_without_speech(text), details, ages, others):
        quote = text[clash.start : clash.end]
        if clash.kind == "age":
            message = (
                f"Here {clash.who} is {clash.said} (“{quote}”), but {clash.source}, "
                f"so {clash.who} is {clash.against} here."
            )
        else:
            message = f"Here {clash.who} has “{quote}”, but {clash.source} says: “{clash.against}”"
        found.append(
            DraftFinding(
                _IDENTITY_CODES[clash.kind],
                Severity.NOTE,
                message,
                clash.start,
                clash.end,
                subject=f"{clash.who}\x1e{clash.kind}\x1e{clash.said}",
            )
        )
    return found


def prose_findings(text: str, brief: DraftBrief) -> list[DraftFinding]:
    """Every check against the prose that the brief has something for."""
    found = [*after_findings(text, brief)]
    if brief.previous is not None:
        found.extend(time_words(text, brief.previous))
    found.extend(resource_findings(text, brief))
    found.extend(supply_findings(text, brief))
    found.extend(appearance_findings(text, brief))
    found.extend(identity_findings(text, brief))
    return found


__all__ = [
    "STOCK_PHRASES",
    "AfterShown",
    "DraftBrief",
    "DraftFinding",
    "Holding",
    "LaterKnowledge",
    "PlannedReveal",
    "SecretToGuard",
    "Severity",
    "Supply",
    "TimeNeighbour",
    "after_findings",
    "appearance_findings",
    "check_draft",
    "days_between",
    "identity_findings",
    "prose_findings",
    "resource_findings",
    "supply_findings",
    "time_words",
    "word_count",
]
