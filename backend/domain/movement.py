"""Travel read from a scene's prose, against the plan and the story bible (SCN-007, CON-004).

"Wren rode to Fennick" moves Wren in the scene. That one signal is read for certain: a
person of the scene named in the narration (never a pronoun, never what is said aloud),
a verb of going in the simple past ("rode", "walked", "sailed", "went", "returned",
"arrived at", "reached" and the like), and a place the story bible knows by one of its
names, written as a name (a capital letter). A sentence that says "not", "would",
"wanted to", "if" or the like, or "had" (something from before the scene), is left
alone, as is "towards" or "set out for" (setting out is not arriving), a journey made
only in a dream or in the mind ("in her dreams"), and one told in a clause about
something else ("the horse that Wren rode to Fennick was lame"). People are told apart
by their entries, so two people who share a name are never mixed up: a name both go by
is not read.

What the signal is compared with, each a note, quoting the words:

* **the plan's place**: the scene is planned at one place and the words take someone to
  another (a place inside the other, with a travel time of 0, is the same place);
* **the travel table**: they are somewhere else, in a scene, an event or a "located at"
  fact placed in story time, too soon before or after this scene for the journey the
  author's table gives between the two places;
* **a status that keeps them**: imprisoned, held or captive at the scene's time (and not
  released, freed or escaped by then), so they cannot go anywhere else; or exiled or
  banished from the place they go to.

Everything here is linear in the length of the text: sentences, speech and verbs are
found by single passes, and a place is looked up by its first word, so a 500,000
character scene is read in well under a second.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

# What someone says aloud (as in ``drafting``): never read for travel.
from backend.domain.spoken import blank_spoken

_SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)", re.MULTILINE)
# Verbs of going in the simple past, then (after at most two words: "rode hard",
# "walked slowly back") the word that says where to. Bounded, so it never backtracks far.
_GOING = (
    r"rode|walked|sailed|travell?ed|journeyed|marched|flew|drove|ran|went|hurried|fled|"
    r"returned|galloped|strode|wandered|trudged|raced|headed|moved|crossed|limped|rowed|"
    r"climbed|slipped|stole|came|made\s+(?:her|his|their|my|our)\s+way"
)
_MOVE = re.compile(
    rf"(?<![\w’'])(?:(?:{_GOING})(?:\s+[a-z]+){{0,2}}?\s+(?:back\s+|home\s+|on\s+|up\s+|down\s+)?"
    r"(?:to|into|in\s+to|onto|on\s+to)"
    r"|arrived\s+(?:back\s+)?(?:at|in)|reached|entered)\s+(?:the\s+)?(?=[A-Z])"
)
# A clause that does not say it happened: wished, planned, refused, in question or not.
_HEDGE = re.compile(
    r"(?<![\w])(?:not|never|no|nor|would|could|might|should|must|shall|will|if|unless|"
    r"wanted|wants|planned|plans|meant|hoped|hopes|tried|tries|dreamed|dreamt|imagined|"
    r"remembered|thought|wondered|refused|had|has|have|almost|nearly|perhaps|maybe)(?![\w])"
    r"|n[’']t(?![\w])|\?",
    re.IGNORECASE,
)
# A journey anywhere in the sentence that happens only in a dream or in the mind: "Wren
# wandered to Fennick in her dreams".
_DREAM = re.compile(
    r"(?<![\w])(?:in\s+(?:her|his|their|my|our|your|a|the)\s+(?:own\s+)?"
    r"(?:dreams?|daydreams?|nightmares?|sleep|imagination|mind|thoughts|fancy)"
    r"|dreaming|daydreaming|imagining)(?![\w])",
    re.IGNORECASE,
)
# The one named is the object of a clause about something else: "The horse that Wren
# rode to Fennick was lame" says what the horse was, and is not read as a journey.
_RELATIVE = re.compile(r"(?<![\w’'])(?:that|which|whom)[\s,]*$", re.IGNORECASE)
# What may stand between the one who goes and the verb: "Wren then rode", "Wren, alone, rode".
_BETWEEN = re.compile(
    r"^(?:[\s,]*(?:then|soon|at\s+last|later|once\s+more|again|alone|herself|himself|"
    r"themselves|finally|quickly|slowly|quietly|[a-z]+ly))*[\s,]*$"
)
_AND = re.compile(r"^\s*(?:,\s*)?(?:and\s+)?$")
_WORD = re.compile(r"[\w’'-]+")
# How far before the verb the one who goes is looked for.
_REACH = 80
# The most words a place's name has.
_MOST_WORDS = 6


@dataclass(frozen=True)
class PlaceKnown:
    """A place the story bible knows, by its id and every name it goes by."""

    key: str
    name: str
    names: tuple[str, ...]


@dataclass(frozen=True)
class Elsewhere:
    """Where someone of the scene is in story time just before or after it, from the story
    bible and the plan, with how much story time there is at most between the two."""

    who: str
    place: str
    # "the scene “The Inn” (day 6)"
    where: str
    gap: float
    gap_words: str
    after: bool


@dataclass(frozen=True)
class Kept:
    """A status that keeps someone of the scene at its time: held (somewhere, or not said)
    or exiled from a place. ``said`` is the fact in words."""

    who: str
    kind: str  # held | exiled
    said: str
    place: str | None = None


@dataclass
class MovementBrief:
    """What the travel check reads about the scene, gathered from the story."""

    # Entry id -> every name of each person in the scene (a name two of them share is
    # not read: it cannot say which one goes).
    people: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Entry id -> the name the notes call them by (the id itself when not given).
    shown: dict[str, str] = field(default_factory=dict)
    places: list[PlaceKnown] = field(default_factory=list)
    # The plan's place for the scene (its key), if it has one.
    scene_place: str | None = None
    # Place key -> the place it counts as (places joined by a travel time of 0).
    same: dict[str, str] = field(default_factory=dict)
    # (one place, the other, how long, said in the story's unit).
    travel: list[tuple[str, str, float, str]] = field(default_factory=list)
    elsewhere: list[Elsewhere] = field(default_factory=list)
    kept: list[Kept] = field(default_factory=list)


@dataclass(frozen=True)
class Move:
    who: str
    place: str
    # Where in the text: from the name of the one who goes to the end of the place's name.
    start: int
    end: int


def _place_index(places: Sequence[PlaceKnown]) -> dict[str, list[tuple[tuple[str, ...], str]]]:
    """First word (folded) -> (every word of a name, folded; the place's key), longest first."""
    index: dict[str, list[tuple[tuple[str, ...], str]]] = {}
    for place in places:
        for name in place.names:
            words = tuple(word.casefold() for word in _WORD.findall(name))
            if not words or len(words) > _MOST_WORDS:
                continue
            index.setdefault(words[0], []).append((words, place.key))
    for candidates in index.values():
        candidates.sort(key=lambda item: -len(item[0]))
    return index


def _place_at(
    text: str, position: int, index: Mapping[str, list[tuple[tuple[str, ...], str]]]
) -> tuple[str, int] | None:
    """The place whose name starts at ``position``, and where its name ends."""
    words: list[tuple[str, int, int]] = []
    for match in _WORD.finditer(text, position, min(len(text), position + 120)):
        if words and text[words[-1][2] : match.start()].strip():
            break  # punctuation: the name has ended
        words.append((match.group(0).casefold(), match.start(), match.end()))
        if len(words) >= _MOST_WORDS:
            break
    if not words or words[0][1] != position:
        return None
    for candidate, key in index.get(words[0][0], []):
        if len(candidate) <= len(words) and all(
            candidate[i] == words[i][0] for i in range(len(candidate))
        ):
            return key, words[len(candidate) - 1][2]
    return None


def _name_pattern(
    people: Mapping[str, Iterable[str]],
) -> tuple[re.Pattern[str], dict[str, str | None]]:
    """Every name, and whose it is: ``None`` for a name more than one person goes by."""
    owner: dict[str, str | None] = {}
    for who, names in people.items():
        for name in names:
            if not name:
                continue
            folded = name.casefold()
            if folded in owner and owner[folded] != who:
                owner[folded] = None
            else:
                owner[folded] = who
    alternatives = sorted(owner, key=len, reverse=True)
    pattern = re.compile(
        r"(?<![\w’'])(" + "|".join(re.escape(name) for name in alternatives) + r")(?![\w’'])",
        re.IGNORECASE,
    )
    return pattern, owner


def find_moves(
    text: str, people: Mapping[str, Iterable[str]], places: Sequence[PlaceKnown]
) -> list[Move]:
    """Each person of the scene the narration takes to a known place, in order."""
    if not text or not people or not places:
        return []
    plain = blank_spoken(text)
    index = _place_index(places)
    if not index:
        return []
    names, owner = _name_pattern(people)
    sentences = [(match.start(), match.end()) for match in _SENTENCE.finditer(plain)]
    starts = [start for start, _ in sentences]
    # Where every hedge and dream is, found once: each move looks its sentence up in
    # these, so a long sentence of many moves is not read again for each.
    hedges = [match.start() for match in _HEDGE.finditer(plain)]
    dreams = [match.start() for match in _DREAM.finditer(plain)]
    found: list[Move] = []
    for verb in _MOVE.finditer(plain):
        at = _place_at(plain, verb.end(), index)
        if at is None:
            continue
        key, place_end = at
        sentence_index = bisect.bisect_right(starts, verb.start()) - 1
        sentence_start, sentence_end = (
            sentences[sentence_index] if sentence_index >= 0 else (0, len(plain))
        )
        # The clause up to the place must say it happened, and the sentence must not say
        # it was a dream.
        if _any_within(hedges, sentence_start, place_end) or _any_within(
            dreams, sentence_start, max(sentence_end, place_end)
        ):
            continue
        window_start = max(sentence_start, verb.start() - _REACH)
        named = list(names.finditer(plain, window_start, verb.start()))
        who: list[tuple[str, int]] = []
        end = verb.start()
        for match in reversed(named):
            between = plain[match.end() : end]
            person = owner.get(match.group(1).casefold())
            if person is None or not (_BETWEEN.match(between) if not who else _AND.match(between)):
                break
            who.append((person, match.start()))
            end = match.start()
        if who and _RELATIVE.search(plain, max(sentence_start, who[-1][1] - 16), who[-1][1]):
            continue
        for person, start in reversed(who):
            found.append(Move(person, key, start, place_end))
    return found


def _any_within(positions: Sequence[int], start: int, end: int) -> bool:
    """Whether any of the sorted ``positions`` is in [start, end)."""
    at = bisect.bisect_left(positions, start)
    return at < len(positions) and positions[at] < end


# ------------------------------------------------------------------ against the story


def _same(brief: MovementBrief, a: str, b: str) -> bool:
    return a == b or brief.same.get(a, a) == brief.same.get(b, b)


def _travel(brief: MovementBrief, a: str, b: str) -> tuple[float, str] | None:
    for one, other, amount, words in brief.travel:
        if {one, other} == {a, b}:
            return amount, words
    return None


@dataclass(frozen=True)
class MoveFinding:
    code: str
    message: str
    start: int
    end: int
    subject: str


def movement_findings(text: str, brief: MovementBrief) -> list[MoveFinding]:
    """Notes where the words take someone somewhere the plan or the story bible does not
    allow. Each quotes the words; none is a warning, since the words may mean a journey
    the plan has not caught up with."""
    moves = find_moves(text, brief.people, brief.places)
    if not moves:
        return []
    names = {place.key: place.name for place in brief.places}
    found: list[MoveFinding] = []
    seen: set[str] = set()

    def add(code: str, move: Move, message: str, about: str) -> None:
        subject = f"{move.who}\x1e{code}\x1e{move.place}\x1e{about}"
        if subject in seen:
            return
        seen.add(subject)
        found.append(MoveFinding(code, message, move.start, move.end, subject))

    for move in moves:
        who = brief.shown.get(move.who, move.who)
        quote = " ".join(text[move.start : move.end].split())
        if len(quote) > 160:
            quote = quote[:157].rstrip() + "…"
        there = names.get(move.place, "a place")
        planned = brief.scene_place
        if planned is not None and not _same(brief, planned, move.place):
            add(
                "moves_elsewhere",
                move,
                f"Here the words take {who} to {there} (“{quote}”), but the plan places "
                f"this scene at {names.get(planned, 'another place')}. If the scene moves on, "
                "that may be meant; if not, change the scene's place or the words.",
                planned,
            )
        for other in brief.elsewhere:
            if other.who != move.who or _same(brief, other.place, move.place):
                continue
            journey = _travel(brief, move.place, other.place)
            if journey is None or journey[0] <= 0 or other.gap >= journey[0]:
                continue
            if other.after:
                said = (
                    f"{who} is at {names.get(other.place, 'another place')} in "
                    f"{other.where}, at most {other.gap_words} later"
                )
            else:
                said = (
                    f"{who} was at {names.get(other.place, 'another place')} in "
                    f"{other.where}, at most {other.gap_words} before"
                )
            add(
                "travel_too_soon",
                move,
                f"Here the words take {who} to {there} (“{quote}”), but {said}, and "
                f"the journey between them takes {journey[1]}.",
                other.where,
            )
        for kept in brief.kept:
            if kept.who != move.who:
                continue
            if kept.kind == "held" and (
                kept.place is None or not _same(brief, kept.place, move.place)
            ):
                add(
                    "moves_while_held",
                    move,
                    f"Here the words take {who} to {there} (“{quote}”), but the story "
                    f"bible says “{kept.said}” at this time, and nothing has freed them yet.",
                    kept.said,
                )
            elif (
                kept.kind == "exiled"
                and kept.place is not None
                and _same(brief, kept.place, move.place)
            ):
                add(
                    "moves_into_exile",
                    move,
                    f"Here the words take {who} to {there} (“{quote}”), but the story "
                    f"bible says “{kept.said}” at this time.",
                    kept.said,
                )
    return found


__all__ = [
    "Elsewhere",
    "Kept",
    "Move",
    "MoveFinding",
    "MovementBrief",
    "PlaceKnown",
    "find_moves",
    "movement_findings",
]
