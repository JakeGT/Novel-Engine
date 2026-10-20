"""Continuity across story time and place, worked out on read (CON-003, CON-004).

Deterministic and light: every check reads the story bible (entries, facts, events),
the plan's scenes and the author's own "how long things take" table, and nothing
else. Nothing here calls a model, guesses a time, a place or a journey's length, or
blocks a change: a finding is a warning (something cannot all be true) or a note
(worth a look). Each one names the records it comes from so the author can open them.

Only scenes placed in story time are checked, since every check here is about *when*.
Times are the intervals ``worldtime.resolve`` gives: an event or a scene happens at some
moment inside its interval; a fact with a span is certainly true between its latest
possible start and its earliest possible end. A start or an end that is given but cannot
be placed (an event that is gone, or has no time) could be any time at all. A finding is
raised only when it holds however those uncertain times fall, so loose times never
produce a warning.

In a story counted in days or longer units, a whole number is the whole of that unit:
"year 5" is some time in year 5, not its first instant (``_whole``).

What each check covers is listed in ``COVERAGE`` and shown to the author word for word.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.domain.canon.assertions import Fact
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import CLAIM_KIND, Event
from backend.domain.canon.plans import PlanItem, PlanKind
from backend.domain.knowledge import scene_time
from backend.domain.plan_checks import fact_sentence
from backend.domain.worldtime import (
    LIMIT,
    At,
    Between,
    Interval,
    Resolved,
    Span,
    Truth,
    describe,
    number_words,
    resolve,
    resolve_all,
    span_of,
    true_at,
    unit_words,
)

Severity = Literal["warning", "note"]
RecordKind = Literal["entry", "fact", "event", "scene"]

MAX_TRAVEL_ROWS = 200
MAX_LASTING_ROWS = 200
# Warnings and notes are capped apart, so many notes never hide a warning.
MAX_WARNINGS = 200
MAX_NOTES = 100
_EPS = 1e-9
# How far short of the next whole unit a coarse time ends: "year 5" is [5, 6 - _WITHIN].
_WITHIN = 1e-6
# Units fine enough that a whole number is one moment; anything else counts whole units.
_FINE_UNITS = frozenset({"second", "sec", "minute", "min", "hour", "hr"})


def counts_whole_units(unit: str) -> bool:
    """Whether "5" in this unit means some time in the fifth unit, not one instant."""
    word = " ".join(unit.replace("_", " ").replace("-", " ").lower().split())
    return word not in _FINE_UNITS and word.removesuffix("s") not in _FINE_UNITS


# ------------------------------------------------------------------ the author's table


class TravelTime(BaseModel):
    """How long it takes to get from one place to the other, either way, in story units.

    Zero says one place is inside the other (a room in a house): they are never
    treated as two places at once.
    """

    model_config = ConfigDict(extra="forbid")

    from_id: str = Field(min_length=1, max_length=200)
    to_id: str = Field(min_length=1, max_length=200)
    amount: float = Field(ge=0, le=LIMIT)

    @model_validator(mode="after")
    def _two_places(self) -> TravelTime:
        if self.from_id == self.to_id:
            raise ValueError("a journey is between two different places")
        return self


class Lasting(BaseModel):
    """An event or a fact the author says lasts so long, give or take."""

    model_config = ConfigDict(extra="forbid")

    subject_kind: Literal["event", "fact"]
    subject_id: str = Field(min_length=1, max_length=200)
    amount: float = Field(gt=0, le=LIMIT)
    give_or_take: float = Field(default=0, ge=0, le=LIMIT)


class HowLong(BaseModel):
    """The story's own table of journeys and lengths. Never filled in by guessing."""

    model_config = ConfigDict(extra="forbid")

    travel: list[TravelTime] = Field(default_factory=list, max_length=MAX_TRAVEL_ROWS)
    lasting: list[Lasting] = Field(default_factory=list, max_length=MAX_LASTING_ROWS)

    @model_validator(mode="after")
    def _once_each(self) -> HowLong:
        pairs = [frozenset((row.from_id, row.to_id)) for row in self.travel]
        if len(set(pairs)) != len(pairs):
            raise ValueError("each pair of places has one travel time")
        subjects = [(row.subject_kind, row.subject_id) for row in self.lasting]
        if len(set(subjects)) != len(subjects):
            raise ValueError("each event or fact has one length")
        return self


def read_how_long(raw: object) -> HowLong:
    """A stored table, keeping every row that still reads.

    A row that no longer reads (or repeats a pair or a subject already read) is left out
    on its own, so one bad row never empties the table and the next save never loses
    the rest of it.
    """
    if not raw:
        return HowLong()
    try:
        return HowLong.model_validate(raw)
    except ValueError:
        pass
    rows = raw if isinstance(raw, Mapping) else {}
    travel: list[TravelTime] = []
    pairs: set[frozenset[str]] = set()
    for item in _rows(rows.get("travel"), MAX_TRAVEL_ROWS):
        try:
            journey = TravelTime.model_validate(item)
        except ValueError:
            continue
        pair = frozenset((journey.from_id, journey.to_id))
        if pair not in pairs:
            pairs.add(pair)
            travel.append(journey)
    lasting: list[Lasting] = []
    subjects: set[tuple[str, str]] = set()
    for item in _rows(rows.get("lasting"), MAX_LASTING_ROWS):
        try:
            length = Lasting.model_validate(item)
        except ValueError:
            continue
        subject = (length.subject_kind, length.subject_id)
        if subject not in subjects:
            subjects.add(subject)
            lasting.append(length)
    return HowLong(travel=travel, lasting=lasting)


def _rows(value: object, most: int) -> list[object]:
    return list(value)[:most] if isinstance(value, list) else []


def table_problem(problem: str) -> str:
    """A table that cannot be saved, in plain words, from what its check said."""
    said = problem.removeprefix("Value error, ").strip().rstrip(".")
    known = {
        "a journey is between two different places": "A journey is between two different places.",
        "each pair of places has one travel time": "Each pair of places has one travel time.",
        "each event or fact has one length": "Each event or fact has one length.",
    }
    return known.get(said, "Each pair of places, and each event or fact, can appear only once.")


# ------------------------------------------------------------------ findings


@dataclass(frozen=True)
class Record:
    """A record a finding comes from, so the author can open it."""

    kind: RecordKind
    id: str
    label: str
    # For a fact: the entry it is about, whose page shows it.
    entry_id: str | None = None


@dataclass(frozen=True)
class ContinuityFinding:
    check: str
    code: str
    severity: Severity
    message: str
    records: tuple[Record, ...]
    # The scenes it is about, so the plan and the studio can show it beside them.
    scene_ids: tuple[str, ...] = ()

    @property
    def subject(self) -> str:
        """What it is about, stable while those records are, for recognising it again."""
        return f"{self.code}:" + ",".join(sorted(f"{r.kind}:{r.id}" for r in self.records))


@dataclass(frozen=True)
class Coverage:
    check: str
    title: str
    covers: str


COVERAGE: tuple[Coverage, ...] = (
    Coverage(
        "simultaneity",
        "In two places at once",
        "A character or object at two different places at the same moment, from scenes placed "
        "in story time (their place, point of view and the people and things in them), events "
        "with one place, and “is located at” facts with a start or an end. Places with a travel "
        "time of 0 between them count as one place. When story time is counted in days or "
        "longer (anything but hours, minutes or seconds), a whole number is the whole of that "
        "unit: “year 5” means some time in year 5, so two scenes in year 5 can be at two places.",
    ),
    Coverage(
        "travel",
        "Not enough time to get there",
        "One appearance of a character or object after another, at two places closer together "
        "in story time than the travel time you set between those places. Places with no "
        "travel time in your table are never checked.",
    ),
    Coverage(
        "age",
        "Age",
        "A character's age from their birth (an event of the kind “birth”, placed in story "
        "time) against facts that give an age from a time (a kind of fact you named “age” or "
        "“has age”, with a start), and those facts against each other. Only when story time is counted in years, or the age "
        "is given in the story's own unit.",
    ),
    Coverage(
        "duration",
        "How long things last",
        "Facts you said last a certain time, against their start and end: a start and end too "
        "close together or too far apart. An event's length is kept but not checked, since the "
        "time you give an event says when it happens, not how long it takes.",
    ),
    Coverage(
        "place",
        "Places that do not exist yet, or any more",
        "Scenes, events and “is located at” facts (with a start) at a place before an event "
        "that founds or builds it, or after one that destroys or abandons it (events of the "
        "kind “founded”, “built”, “destroyed”, “abandoned” and the like, with the place in "
        "them).",
    ),
    Coverage(
        "possession",
        "Who has what",
        "One object that two own, hold, carry, possess or wield at the same time, and an object in a scene when the character who has it then is "
        "not in that scene. Two owners with no times at all are a note: they may share it.",
    ),
    Coverage(
        "life",
        "Before birth or after death",
        "Scenes and events with a character in them before their birth or after their death "
        "(events of the kind “birth” or “death”, or drowned, perished or slain, with one "
        "character in them, or one whose part in it is “born” or “died”; a killing, murder, "
        "execution or assassination only for the one whose part in it is “victim”, “killed” "
        "or “died”, since the one person in it may be the one who did it; any event for the "
        "one whose part is “died” or “deceased”; or a status such as dead, with a start), a "
        "death before a birth, and two deaths.",
    ),
    Coverage(
        "state",
        "Injury and condition",
        "A character in a scene while a status says they are dead, injured, wounded, ill, "
        "unconscious, captive or similar. A note, so the scene can show it; a status with no "
        "time is said to have none.",
    ),
    Coverage(
        "change",
        "Relationships and goals that change off the page",
        "A relationship (related to, knows, member of, or any fact between two characters) or a "
        "goal (wants, seeks, has goal, or a fact about a goal in the story bible) that starts or ends "
        "at a story time with no scene or event then that has either of them in it. A note.",
    ),
)

ALWAYS_SAID = (
    "Only scenes placed in story time are checked. Nothing here guesses a time, a place or a "
    "travel time, and nothing here stops you saving a change."
)

# ------------------------------------------------------------------ vocabulary

BIRTH_KINDS = frozenset({"birth", "born"})
DEATH_KINDS = frozenset(
    {
        "death",
        "died",
        "dies",
        "drowned",
        "drowning",
        "drowns",
        "perished",
        "perishes",
        "slain",
        "killed",
        "killing",
        "murder",
        "murdered",
        "execution",
        "executed",
        "assassination",
        "assassinated",
        "slaying",
    }
)
# Kinds that say whose life it is when only one character is in them: a death no one does
# to them (drowned, perished) or a word that only ever means the one it happened to (slain).
# In the others the one character may be the one who did it: "Mara killed the guard",
# "Mara murdered him", "Mara executed the traitor" are all written with the same word as
# "Mara was murdered", so a part must say who died.
PLAIN_LIFE_KINDS = frozenset(
    {
        "birth",
        "born",
        "death",
        "died",
        "dies",
        "drowned",
        "drowning",
        "drowns",
        "perished",
        "perishes",
        "slain",
    }
)
BIRTH_ROLES = frozenset({"born", "child", "subject", "baby"})
DEATH_ROLES = frozenset(
    {
        "died",
        "dies",
        "deceased",
        "victim",
        "killed",
        "subject",
        "drowned",
        "perished",
        "slain",
        "murdered",
        "executed",
        "assassinated",
    }
)
# A part that says the person died is their death whatever the event is called
# ("a duel", "the flood").
DIED_ROLES = frozenset({"died", "dies", "deceased"})
FOUNDING_KINDS = frozenset(
    {
        "founding",
        "founded",
        "built",
        "building",
        "construction",
        "established",
        "establishment",
        "creation",
        "created",
        "opening",
        "opened",
    }
)
ENDING_KINDS = frozenset(
    {
        "destruction",
        "destroyed",
        "demolished",
        "demolition",
        "abandoned",
        "abandonment",
        "ruined",
        "razed",
        "sunk",
        "fall",
    }
)
AGE_KINDS = frozenset({"AGE", "HAS_AGE", "AGED"})
POSSESSION_KINDS = frozenset({"OWNS", "HOLDS", "CARRIES", "POSSESSES", "WIELDS"})
RELATIONSHIP_KINDS = frozenset({"RELATED_TO", "KNOWS", "MEMBER_OF"})
GOAL_KINDS = frozenset({"WANTS", "SEEKS", "DESIRES", "HAS_GOAL", "PURSUES"})
DEAD_WORDS = frozenset(
    {
        "dead",
        "deceased",
        "killed",
        "died",
        "drowned",
        "perished",
        "slain",
        "murdered",
        "executed",
        "assassinated",
    }
)
STATE_WORDS = frozenset(
    {
        "injured",
        "wounded",
        "hurt",
        "unconscious",
        "ill",
        "sick",
        "blind",
        "blinded",
        "poisoned",
        "imprisoned",
        "captive",
        "missing",
    }
)
# Who can only be in one place at a time.
_BODIES = frozenset({"Character", "Object"})


def _word(text: str) -> str:
    return " ".join(text.replace("_", " ").replace("-", " ").lower().split())


# ------------------------------------------------------------------ inputs


@dataclass
class StoryParts:
    """Everything the checks read, already filtered to what is current."""

    entities: dict[str, CanonEntity]
    facts: list[Fact]
    events: list[Event]
    # The plan's scenes, in reading order.
    scenes: list[PlanItem]
    unit: str = "year"
    how_long: HowLong = field(default_factory=HowLong)


@dataclass(frozen=True)
class _Presence:
    entity_id: str
    place_id: str
    lo: float
    hi: float
    # A stay is certainly true over [lo, hi); a moment happens somewhere in [lo, hi].
    stay: bool
    record: Record
    scene_id: str | None = None


class _Checker:
    def __init__(self, parts: StoryParts) -> None:
        self.parts = parts
        self.unit = parts.unit
        self.coarse = counts_whole_units(parts.unit)
        # Room for the end of a whole unit when comparing lengths ("year 5 to year 6").
        self.tolerance = _EPS + (2 * _WITHIN if self.coarse else 0)
        self.entities = {key: value for key, value in parts.entities.items() if value.is_active}
        self.events = [
            event
            for event in parts.events
            if event.retired_revision is None and event.kind != CLAIM_KIND
        ]
        self.facts = [fact for fact in parts.facts if fact.closed_revision is None]
        self.times: dict[str, Resolved] = resolve_all(
            {event.id: self._whole(event.when) for event in self.events}
        )
        self.event_names = {event.id: event.name for event in self.events}
        self.names = {key: value.name for key, value in self.entities.items()}
        self.found: list[ContinuityFinding] = []
        self._seen: set[str] = set()
        self._shown: dict[Severity, int] = {"warning": 0, "note": 0}
        # Found but past the cap for its kind, so never listed.
        self.not_shown = 0
        self._spans: dict[int, Span] = {}
        self.scenes = [
            scene for scene in parts.scenes if scene.is_active and scene.kind is PlanKind.SCENE
        ]
        self.people_in: dict[str, list[str]] = {
            scene.id: list(
                dict.fromkeys([*scene.refs_as("point_of_view"), *scene.refs_as("participant")])
            )
            for scene in self.scenes
        }
        self.scene_moment: dict[str, Interval] = {}
        for scene in self.scenes:
            moment = self.interval(scene_time(scene.details))
            if moment is not None:
                self.scene_moment[scene.id] = moment
        self._journeys: dict[frozenset[str], float] = {
            frozenset((row.from_id, row.to_id)): row.amount for row in parts.how_long.travel
        }
        self.groups = self._place_groups()
        # Each entry's appearances in story time, from timed scenes and events, built once.
        self.appearances: dict[str, list[Interval]] = {}
        for scene in self.scenes:
            at = self.scene_moment.get(scene.id)
            if at is not None:
                for entity_id in self.people_in[scene.id]:
                    self.appearances.setdefault(entity_id, []).append(at)
        for event in self.events:
            at = self.times[event.id].interval
            if at is not None:
                for entity_id in dict.fromkeys(item.entity_id for item in event.participants):
                    self.appearances.setdefault(entity_id, []).append(at)

    # -------------------------------------------------------------- helpers

    def _whole(self, when: Any) -> Any:
        """In a story counted in whole units, "year 5" is any time in year 5.

        A whole-number time, or the whole-number latest end of a span, reaches to just
        short of the next unit. Fractions are the author being exact, and are kept.
        """
        if not self.coarse:
            return when
        if isinstance(when, At) and float(when.at).is_integer():
            return Between.model_construct(
                earliest=when.at, latest=when.at + 1 - _WITHIN, label=when.label
            )
        if (
            isinstance(when, Between)
            and when.latest is not None
            and float(when.latest).is_integer()
        ):
            return Between.model_construct(
                earliest=when.earliest, latest=when.latest + 1 - _WITHIN, label=when.label
            )
        return when

    def when_of(self, event_id: str) -> Any:
        resolved = self.times.get(event_id)
        if resolved is None:
            return False
        if resolved.interval is None:
            return None
        interval = resolved.interval
        return Between.model_construct(
            earliest=None if math.isinf(interval.lo) else interval.lo,
            latest=None if math.isinf(interval.hi) else interval.hi,
        )

    def interval(self, when: Any) -> Interval | None:
        if when is None:
            return None
        return resolve(self._whole(when), self.when_of).interval

    def _bound(self, when: Any) -> Resolved | None:
        """A start or an end. One that is given but cannot be placed could be any time."""
        if when is None:
            return None
        resolved = resolve(self._whole(when), self.when_of)
        if resolved.interval is None:
            return Resolved(Interval(-math.inf, math.inf), resolved.problem)
        return resolved

    def span(self, fact: Fact) -> Span:
        """When a fact is true. ``stated`` is False only for a fact with no times at all."""
        found = self._spans.get(id(fact))
        if found is None:
            found = span_of(self._bound(fact.starts), self._bound(fact.ends))
            self._spans[id(fact)] = found
        return found

    @staticmethod
    def placed(span: Span) -> bool:
        """Whether a span has a start or an end that can be placed in story time."""
        return any(
            bound is not None and not (math.isinf(bound.lo) and math.isinf(bound.hi))
            for bound in (span.start, span.end)
        )

    @staticmethod
    def certain(span: Span) -> tuple[float, float] | None:
        """Where a stated span is certainly true, as [lo, hi); None when nowhere."""
        if not span.stated:
            return None
        lo = span.start.hi if span.start is not None else -math.inf
        hi = span.end.lo if span.end is not None else math.inf
        return (lo, hi) if lo < hi else None

    def _ends(self, interval: Interval) -> tuple[float, float, bool]:
        """The ends to say: a coarse end just short of a whole unit is said as the unit it
        ends in ("year 5", not "year 5.999999"). And whether that was done."""
        lo, hi = interval.lo, interval.hi
        if self.coarse and not math.isinf(hi):
            nearest = round(hi + _WITHIN)
            if abs(hi + _WITHIN - nearest) < _WITHIN / 2 and nearest - 1 >= lo:
                return lo, float(nearest - 1), True
        return lo, hi, False

    def words(self, interval: Interval) -> str:
        unit = self.unit
        lo, hi, whole = self._ends(interval)
        if lo == hi:
            return f"{unit} {number_words(lo)}"
        if math.isinf(lo) and math.isinf(hi):
            return "at some time"
        if math.isinf(lo):
            return f"{'in or ' if whole else ''}before {unit} {number_words(hi)}"
        if math.isinf(hi):
            return f"after {unit} {number_words(lo)}"
        return f"between {unit} {number_words(lo)} and {number_words(hi)}"

    def at_words(self, interval: Interval) -> str:
        """``words`` as a phrase after a verb: "in year 5", "between year 1 and 2"."""
        said = self.words(interval)
        lo, hi, _ = self._ends(interval)
        return f"in {said}" if lo == hi else said

    def amount(self, value: float) -> str:
        return f"{number_words(round(value, 2))} {unit_words(self.unit, value)}"

    def when_words_for_fact(self, fact: Fact) -> str:
        if fact.starts is None:
            return "at some time"
        return "at " + describe(fact.starts, unit=self.unit, event_name=self.event_names.get)

    def event_when(self, event: Event) -> str:
        return describe(event.when, unit=self.unit, event_name=self.event_names.get)

    def scene_when(self, scene: PlanItem) -> str:
        return describe(scene_time(scene.details), unit=self.unit, event_name=self.event_names.get)

    def name(self, entity_id: str) -> str:
        return self.names.get(entity_id, "someone")

    def entry(self, entity_id: str) -> Record:
        return Record("entry", entity_id, self.name(entity_id))

    def fact_record(self, fact: Fact) -> Record:
        return Record(
            "fact",
            fact.id,
            fact_sentence(fact, self.names),
            entry_id=fact.proposition.subject_id,
        )

    @staticmethod
    def event_record(event: Event) -> Record:
        return Record("event", event.id, event.name)

    @staticmethod
    def scene_record(scene: PlanItem) -> Record:
        return Record("scene", scene.id, scene.title)

    def add(
        self,
        check: str,
        code: str,
        severity: Severity,
        message: str,
        records: Iterable[Record],
        scene_ids: Iterable[str | None] = (),
    ) -> None:
        finding = ContinuityFinding(
            check,
            code,
            severity,
            message,
            tuple(records),
            tuple(dict.fromkeys(scene_id for scene_id in scene_ids if scene_id)),
        )
        if finding.subject in self._seen:
            return
        self._seen.add(finding.subject)
        if self._shown[severity] >= (MAX_WARNINGS if severity == "warning" else MAX_NOTES):
            self.not_shown += 1
            return
        self._shown[severity] += 1
        self.found.append(finding)

    def scene_people(self, scene: PlanItem) -> list[str]:
        """Who and what is in a scene: its point of view and its participants."""
        return self.people_in[scene.id]

    def scene_place(self, scene: PlanItem) -> str | None:
        place = next(iter(scene.refs_as("place")), None)
        if place is None:
            return None
        entity = self.entities.get(place)
        return place if entity is not None and entity.type == "Location" else None

    def event_place(self, event: Event) -> str | None:
        """An event's one place: its only location, or its only place among those in it."""
        places = [pid for pid in event.location_ids if pid in self.entities]
        if not places:
            places = [
                item.entity_id
                for item in event.participants
                if (entity := self.entities.get(item.entity_id)) is not None
                and entity.type == "Location"
            ]
        places = list(dict.fromkeys(places))
        return places[0] if len(places) == 1 else None

    def kind_of(self, entity_id: str) -> str | None:
        entity = self.entities.get(entity_id)
        return entity.type if entity is not None else None

    def travel(self, a: str, b: str) -> float | None:
        return self._journeys.get(frozenset((a, b)))

    def _place_groups(self) -> dict[str, str]:
        """Places joined by a travel time of 0 count as one place."""
        parent: dict[str, str] = {}

        def find(item: str) -> str:
            parent.setdefault(item, item)
            while parent[item] != item:
                parent[item] = parent[parent[item]]
                item = parent[item]
            return item

        for row in self.parts.how_long.travel:
            if row.amount == 0:
                parent[find(row.from_id)] = find(row.to_id)
        return {item: find(item) for item in list(parent)}

    def same_place(self, a: str, b: str) -> bool:
        return a == b or self.groups.get(a, a) == self.groups.get(b, b)

    # -------------------------------------------------------------- birth and death

    def life_events(
        self,
        kinds: frozenset[str],
        roles: frozenset[str],
        any_kind_roles: frozenset[str] = frozenset(),
    ) -> list[tuple[str, Event]]:
        """(character, event) for each birth or death event that says whose it is."""
        found: list[tuple[str, Event]] = []
        for event in self.events:
            people = [
                item for item in event.participants if self.kind_of(item.entity_id) == "Character"
            ]
            if _word(event.kind) not in kinds:
                found.extend(
                    (entity_id, event)
                    for entity_id in dict.fromkeys(
                        item.entity_id for item in people if _word(item.role) in any_kind_roles
                    )
                )
                continue
            named = [item.entity_id for item in people if _word(item.role) in roles]
            # Only a plain birth or death is guessed to be its one character's.
            if not named and len(people) == 1 and _word(event.kind) in PLAIN_LIFE_KINDS:
                named = [people[0].entity_id]
            found.extend((entity_id, event) for entity_id in dict.fromkeys(named))
        return found


# ------------------------------------------------------------------ the checks


def _presences(c: _Checker) -> dict[str, list[_Presence]]:
    found: dict[str, list[_Presence]] = {}

    def add(presence: _Presence) -> None:
        if c.kind_of(presence.entity_id) in _BODIES:
            found.setdefault(presence.entity_id, []).append(presence)

    for scene in c.scenes:
        moment = c.scene_moment.get(scene.id)
        place = c.scene_place(scene)
        if moment is None or place is None:
            continue
        for entity_id in c.scene_people(scene):
            add(
                _Presence(
                    entity_id, place, moment.lo, moment.hi, False, c.scene_record(scene), scene.id
                )
            )
    for event in c.events:
        moment = c.times[event.id].interval
        place = c.event_place(event)
        if moment is None or place is None:
            continue
        for item in event.participants:
            add(
                _Presence(item.entity_id, place, moment.lo, moment.hi, False, c.event_record(event))
            )
    for fact in c.facts:
        if fact.proposition.predicate != "LOCATED_AT":
            continue
        place = getattr(fact.proposition.object, "entity_id", None)
        if place not in c.entities:
            continue
        stretch = c.certain(c.span(fact))
        if stretch is None:
            continue
        add(
            _Presence(
                fact.proposition.subject_id,
                place,
                stretch[0],
                stretch[1],
                True,
                c.fact_record(fact),
            )
        )
    return found


def _where(c: _Checker, presence: _Presence) -> str:
    if presence.record.kind == "fact":
        return f"“{presence.record.label}”"
    kind = "the scene" if presence.record.kind == "scene" else "the event"
    return f"{kind} “{presence.record.label}”"


def check_places(c: _Checker) -> None:
    """In two places at once, and not enough time to get from one to the other."""
    for entity_id, presences in _presences(c).items():
        who = c.name(entity_id)
        clashed: set[tuple[str, str]] = set()
        ordered = sorted(presences, key=lambda item: (item.lo, item.hi))
        for index, a in enumerate(ordered):
            for b in ordered[index + 1 :]:
                if b.lo > a.hi:
                    break  # Everything after this starts once ``a`` is over.
                if a.record.id == b.record.id or c.same_place(a.place_id, b.place_id):
                    continue
                if _at_once(a, b):
                    clashed.add((a.record.id, b.record.id))
                    clashed.add((b.record.id, a.record.id))
                    c.add(
                        "simultaneity",
                        "two_places_at_once",
                        "warning",
                        f"{who} is at {c.name(a.place_id)} in {_where(c, a)} and at "
                        f"{c.name(b.place_id)} in {_where(c, b)} at the same time.",
                        [c.entry(entity_id), a.record, b.record],
                        [a.scene_id, b.scene_id],
                    )
        for a, b in zip(ordered, ordered[1:], strict=False):
            if (a.record.id, b.record.id) in clashed or c.same_place(a.place_id, b.place_id):
                continue
            needed = c.travel(a.place_id, b.place_id)
            if needed is None or needed <= 0:
                continue
            gap = _longest_gap(a, b)
            if gap is None or gap >= needed - c.tolerance:
                continue
            c.add(
                "travel",
                "too_little_travel_time",
                "warning",
                f"{who} is at {c.name(a.place_id)} in {_where(c, a)} and then at "
                f"{c.name(b.place_id)} in {_where(c, b)}, at most {c.amount(max(gap, 0))} "
                f"later, but getting from one to the other takes {c.amount(needed)}.",
                [c.entry(entity_id), a.record, b.record],
                [a.scene_id, b.scene_id],
            )


def _at_once(a: _Presence, b: _Presence) -> bool:
    if a.stay and b.stay:
        return False  # One place at a time is already enforced for a fact's own kind.
    if not a.stay and not b.stay:
        return a.lo == a.hi == b.lo == b.hi
    stay, moment = (a, b) if a.stay else (b, a)
    return moment.lo >= stay.lo and moment.hi < stay.hi


def _longest_gap(a: _Presence, b: _Presence) -> float | None:
    """The most story time there can be between two appearances, ``a`` first."""
    if not a.stay and not b.stay:
        return max(b.hi - a.lo, a.hi - b.lo)
    if a.stay and not b.stay:
        return b.hi - a.hi if b.lo >= a.hi else None
    if b.stay and not a.stay:
        return b.lo - a.lo if a.hi <= b.lo else None
    return b.lo - a.hi if a.hi <= b.lo else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return None


def _same_unit(said: str, unit: str) -> bool:
    said, unit = _word(said), _word(unit)
    return said in {unit, f"{unit}s"} or said.rstrip("s") == unit.rstrip("s")


@dataclass(frozen=True)
class _Bound:
    lo: float
    hi: float
    lo_open: bool
    record: Record
    words: str


def _disjoint(a: _Bound, b: _Bound) -> bool:
    def below(x: _Bound, y: _Bound) -> bool:
        # Everything in x is before everything in y.
        return x.hi < y.lo - _EPS or (y.lo_open and x.hi <= y.lo + _EPS)

    return below(a, b) or below(b, a)


def check_ages(c: _Checker) -> None:
    """A birth against stated ages, and stated ages against each other."""
    births: dict[str, list[_Bound]] = {}
    for entity_id, event in c.life_events(BIRTH_KINDS, BIRTH_ROLES):
        moment = c.times[event.id].interval
        if moment is not None:
            births.setdefault(entity_id, []).append(
                _Bound(
                    moment.lo,
                    moment.hi,
                    False,
                    c.event_record(event),
                    f"“{event.name}” ({c.event_when(event)})",
                )
            )
    ages: dict[str, list[_Bound]] = {}
    for fact in c.facts:
        if fact.proposition.predicate not in AGE_KINDS or fact.starts is None:
            continue
        obj = fact.proposition.object
        value = _number(getattr(obj, "value", None))
        if value is None or value < 0:
            continue
        said_unit = getattr(obj, "unit", None)
        if said_unit is not None:
            if not _same_unit(str(said_unit), c.unit):
                continue
        elif _word(c.unit) != "year":
            continue
        span = c.span(fact)
        if span.start is None:
            continue
        lo, lo_open = span.start.lo - value - 1, True
        if span.end is not None and span.end.lo - value - 1 >= lo:
            lo, lo_open = span.end.lo - value - 1, False
        hi = span.start.hi - value
        if lo > hi:
            continue
        ages.setdefault(fact.proposition.subject_id, []).append(
            _Bound(
                lo,
                hi,
                lo_open,
                c.fact_record(fact),
                f"{number_words(value)} {c.when_words_for_fact(fact)} (“{fact_sentence(fact, c.names)}”)",
            )
        )
    for entity_id in sorted(set(births) | set(ages)):
        who = c.name(entity_id)
        stated = ages.get(entity_id, [])
        for birth in births.get(entity_id, []):
            for age in stated:
                if _disjoint(birth, age):
                    c.add(
                        "age",
                        "age_against_birth",
                        "warning",
                        f"{who}'s birth, {birth.words}, means {who} cannot be {age.words}.",
                        [c.entry(entity_id), birth.record, age.record],
                    )
        for index, first in enumerate(stated):
            for second in stated[index + 1 :]:
                if _disjoint(first, second):
                    c.add(
                        "age",
                        "ages_disagree",
                        "warning",
                        f"{who} cannot be both {first.words} and {second.words}.",
                        [c.entry(entity_id), first.record, second.record],
                    )


def check_durations(c: _Checker) -> None:
    """A fact's start and end against the length the author gave it.

    An event's length is not checked: its time says when it happens (at some moment
    inside it), not how long it takes, so it cannot be too short to hold the event.
    """
    facts = {fact.id: fact for fact in c.facts}
    for row in c.parts.how_long.lasting:
        if row.subject_kind != "fact":
            continue
        least, most = row.amount - row.give_or_take, row.amount + row.give_or_take
        fact = facts.get(row.subject_id)
        if fact is None:
            continue
        span = c.span(fact)
        if span.start is None or span.end is None:
            continue
        shortest = span.end.lo - span.start.hi
        longest = span.end.hi - span.start.lo
        if math.isinf(shortest) or math.isinf(longest):
            continue
        if longest < least - c.tolerance:
            how = f"at most {c.amount(max(longest, 0))} long"
        elif shortest > most + c.tolerance:
            how = f"at least {c.amount(shortest)} long"
        else:
            continue
        c.add(
            "duration",
            "span_wrong_length",
            "warning",
            f"“{fact_sentence(fact, c.names)}” lasts {c.amount(row.amount)}, but its start and "
            f"end make it {how}.",
            [c.fact_record(fact)],
        )


def check_place_lifetimes(c: _Checker) -> None:
    """Scenes, events and stays at a place before it is founded or after it is gone."""
    founded: dict[str, list[tuple[Interval, Event]]] = {}
    ended: dict[str, list[tuple[Interval, Event]]] = {}
    marking: set[str] = set()
    for event in c.events:
        word = _word(event.kind)
        target = founded if word in FOUNDING_KINDS else ended if word in ENDING_KINDS else None
        moment = c.times[event.id].interval
        if target is None or moment is None:
            continue
        places = {pid for pid in event.location_ids if c.kind_of(pid) == "Location"}
        places |= {
            item.entity_id for item in event.participants if c.kind_of(item.entity_id) == "Location"
        }
        for marked in places:
            target.setdefault(marked, []).append((moment, event))
        marking.add(event.id)

    def judge(
        place: str, moment: Interval, record: Record, scene_id: str | None, what: str
    ) -> None:
        for start, event in founded.get(place, []):
            if moment.hi < start.lo - _EPS:
                c.add(
                    "place",
                    "place_not_yet",
                    "warning",
                    f"{what} at {c.name(place)} {c.at_words(moment)}, before it exists: "
                    f"“{event.name}” ({c.event_when(event)}).",
                    [record, c.entry(place), c.event_record(event)],
                    [scene_id],
                )
        for end, event in ended.get(place, []):
            if moment.lo > end.hi + _EPS:
                c.add(
                    "place",
                    "place_gone",
                    "warning",
                    f"{what} at {c.name(place)} {c.at_words(moment)}, after it is gone: "
                    f"“{event.name}” ({c.event_when(event)}).",
                    [record, c.entry(place), c.event_record(event)],
                    [scene_id],
                )

    if not founded and not ended:
        return
    for scene in c.scenes:
        moment = c.scene_moment.get(scene.id)
        place = c.scene_place(scene)
        if moment is not None and place is not None:
            judge(place, moment, c.scene_record(scene), scene.id, f"“{scene.title}” happens")
    for event in c.events:
        moment = c.times[event.id].interval
        place = c.event_place(event)
        if event.id in marking or moment is None or place is None:
            continue
        judge(place, moment, c.event_record(event), None, f"“{event.name}” happens")
    for fact in c.facts:
        if fact.proposition.predicate != "LOCATED_AT" or fact.starts is None:
            continue
        place = getattr(fact.proposition.object, "entity_id", None)
        span = c.span(fact)
        if place is None or span.start is None:
            continue
        start = span.start
        for begun, event in founded.get(place, []):
            if start.hi < begun.lo - _EPS:
                c.add(
                    "place",
                    "place_not_yet",
                    "warning",
                    f"“{fact_sentence(fact, c.names)}” from {c.words(start)}, before "
                    f"{c.name(place)} exists: “{event.name}” ({c.event_when(event)}).",
                    [c.fact_record(fact), c.entry(place), c.event_record(event)],
                )
        for end, event in ended.get(place, []):
            if start.lo > end.hi + _EPS:
                c.add(
                    "place",
                    "place_gone",
                    "warning",
                    f"“{fact_sentence(fact, c.names)}” from {c.words(start)}, after "
                    f"{c.name(place)} is gone: “{event.name}” ({c.event_when(event)}).",
                    [c.fact_record(fact), c.entry(place), c.event_record(event)],
                )


def _holders(c: _Checker) -> dict[str, list[Fact]]:
    found: dict[str, list[Fact]] = {}
    for fact in c.facts:
        if fact.proposition.predicate not in POSSESSION_KINDS:
            continue
        thing = getattr(fact.proposition.object, "entity_id", None)
        if thing in c.entities and fact.proposition.subject_id in c.entities:
            found.setdefault(thing, []).append(fact)
    return found


def check_possession(c: _Checker) -> None:
    holders = _holders(c)
    for thing, facts in holders.items():
        for index, a in enumerate(facts):
            for b in facts[index + 1 :]:
                if a.proposition.subject_id == b.proposition.subject_id:
                    continue
                span_a, span_b = c.span(a), c.span(b)
                first = c.name(a.proposition.subject_id)
                second = c.name(b.proposition.subject_id)
                records = [c.entry(thing), c.fact_record(a), c.fact_record(b)]
                if not span_a.stated and not span_b.stated:
                    c.add(
                        "possession",
                        "two_holders",
                        "note",
                        f"{c.name(thing)} belongs to both {first} and {second}, with no time "
                        "given for either. If they share it, leave this; if one has it after the "
                        "other, say when.",
                        records,
                    )
                    continue
                stretch_a = c.certain(span_a) if span_a.stated else (-math.inf, math.inf)
                stretch_b = c.certain(span_b) if span_b.stated else (-math.inf, math.inf)
                if stretch_a is None or stretch_b is None:
                    continue
                if max(stretch_a[0], stretch_b[0]) < min(stretch_a[1], stretch_b[1]):
                    c.add(
                        "possession",
                        "two_holders_at_once",
                        "warning",
                        f"{c.name(thing)} belongs to {first} and to {second} at the same time.",
                        records,
                    )
    for scene in c.scenes:
        moment = c.scene_moment.get(scene.id)
        if moment is None:
            continue
        people = set(c.scene_people(scene))
        for thing in people:
            if c.kind_of(thing) != "Object":
                continue
            owners = [
                fact
                for fact in holders.get(thing, [])
                if c.kind_of(fact.proposition.subject_id) == "Character"
                and true_at(c.span(fact), moment) is Truth.TRUE
            ]
            if not owners or any(fact.proposition.subject_id in people for fact in owners):
                continue
            who = " or ".join(c.name(fact.proposition.subject_id) for fact in owners)
            c.add(
                "possession",
                "object_away_from_holder",
                "note",
                f"{c.name(thing)} is in this scene, but {who} has it then and is not in it.",
                [c.scene_record(scene), c.entry(thing), *[c.fact_record(f) for f in owners]],
                [scene.id],
            )


def _status_word(fact: Fact) -> str | None:
    value = getattr(fact.proposition.object, "value", None)
    if fact.proposition.object.kind not in {"enum", "string"} or not isinstance(value, str):
        return None
    word = _word(value)
    return word if word in DEAD_WORDS or word in STATE_WORDS else None


def check_life(c: _Checker) -> None:
    """Before birth, after death, a death before a birth, two deaths, and conditions."""
    births: dict[str, list[tuple[Interval, Record, str]]] = {}
    deaths: dict[str, list[tuple[Interval, Record, str]]] = {}
    marking: set[str] = set()
    for entity_id, event in c.life_events(BIRTH_KINDS, BIRTH_ROLES):
        moment = c.times[event.id].interval
        marking.add(event.id)
        if moment is not None:
            births.setdefault(entity_id, []).append(
                (moment, c.event_record(event), c.event_when(event))
            )
    for entity_id, event in c.life_events(DEATH_KINDS, DEATH_ROLES, DIED_ROLES):
        moment = c.times[event.id].interval
        marking.add(event.id)
        if moment is not None:
            deaths.setdefault(entity_id, []).append(
                (moment, c.event_record(event), c.event_when(event))
            )
    timeless_states: dict[str, list[Fact]] = {}
    timed_states: dict[str, list[Fact]] = {}
    for fact in c.facts:
        word = _status_word(fact)
        subject = fact.proposition.subject_id
        if word is None or c.kind_of(subject) != "Character":
            continue
        span = c.span(fact)
        if not c.placed(span):
            timeless_states.setdefault(subject, []).append(fact)
            continue
        timed_states.setdefault(subject, []).append(fact)
        if word in DEAD_WORDS and span.start is not None:
            deaths.setdefault(subject, []).append(
                (span.start, c.fact_record(fact), f"from {c.words(span.start)}")
            )

    def judge(entity_id: str, moment: Interval, record: Record, scene_id: str | None) -> None:
        who = c.name(entity_id)
        what = "the scene" if record.kind == "scene" else "the event"
        for death, source, words in deaths.get(entity_id, []):
            if moment.lo > death.hi + _EPS:
                c.add(
                    "life",
                    "after_death",
                    "warning",
                    f"{who} is in {what} “{record.label}” ({c.words(moment)}), after their "
                    f"death: “{source.label}” ({words}).",
                    [c.entry(entity_id), record, source],
                    [scene_id],
                )
        for birth, source, words in births.get(entity_id, []):
            if moment.hi < birth.lo - _EPS:
                c.add(
                    "life",
                    "before_birth",
                    "warning",
                    f"{who} is in {what} “{record.label}” ({c.words(moment)}), before they are "
                    f"born: “{source.label}” ({words}).",
                    [c.entry(entity_id), record, source],
                    [scene_id],
                )

    for scene in c.scenes:
        moment = c.scene_moment.get(scene.id)
        if moment is None:
            continue
        for entity_id in c.scene_people(scene):
            if c.kind_of(entity_id) != "Character":
                continue
            judge(entity_id, moment, c.scene_record(scene), scene.id)
            for fact in timed_states.get(entity_id, []):
                word = _status_word(fact) or ""
                if word in STATE_WORDS and true_at(c.span(fact), moment) is Truth.TRUE:
                    c.add(
                        "state",
                        "condition_then",
                        "note",
                        f"{c.name(entity_id)} is {word} at this time (“"
                        f"{fact_sentence(fact, c.names)}”); the scene should show it.",
                        [c.scene_record(scene), c.fact_record(fact)],
                        [scene.id],
                    )
            for fact in timeless_states.get(entity_id, []):
                word = _status_word(fact) or ""
                given = (
                    "with no time given"
                    if fact.starts is None and fact.ends is None
                    else "at a time that cannot be placed yet"
                )
                said = (
                    f"{c.name(entity_id)} is recorded as {word}, {given}, and is in "
                    "this scene. If that comes later, give it a time."
                    if word in DEAD_WORDS
                    else f"{c.name(entity_id)} is recorded as {word}, {given}; "
                    "if that holds here, the scene should show it."
                )
                c.add(
                    "state",
                    "condition_no_time",
                    "note",
                    said,
                    [c.scene_record(scene), c.fact_record(fact)],
                    [scene.id],
                )
    for event in c.events:
        moment = c.times[event.id].interval
        if event.id in marking or moment is None:
            continue
        for item in event.participants:
            if c.kind_of(item.entity_id) == "Character":
                judge(item.entity_id, moment, c.event_record(event), None)
    for entity_id in sorted(set(births) | set(deaths)):
        who = c.name(entity_id)
        for death, death_record, death_words in deaths.get(entity_id, []):
            for birth, birth_record, birth_words in births.get(entity_id, []):
                if death.hi < birth.lo - _EPS:
                    c.add(
                        "life",
                        "dies_before_birth",
                        "warning",
                        f"{who} dies before being born: “{death_record.label}” ({death_words}) "
                        f"comes before “{birth_record.label}” ({birth_words}).",
                        [c.entry(entity_id), death_record, birth_record],
                    )
        recorded = deaths.get(entity_id, [])
        for index, (first, first_record, first_words) in enumerate(recorded):
            for second, second_record, second_words in recorded[index + 1 :]:
                if first.hi < second.lo - _EPS or second.hi < first.lo - _EPS:
                    c.add(
                        "life",
                        "dies_twice",
                        "warning",
                        f"{who} dies twice: “{first_record.label}” ({first_words}) and "
                        f"“{second_record.label}” ({second_words}).",
                        [c.entry(entity_id), first_record, second_record],
                    )


def _change_kind(c: _Checker, fact: Fact) -> str | None:
    predicate = fact.proposition.predicate
    other = getattr(fact.proposition.object, "entity_id", None)
    if predicate in GOAL_KINDS or (other is not None and c.kind_of(other) == "Goal"):
        return "goal"
    if predicate in POSSESSION_KINDS or predicate == "LOCATED_AT" or other is None:
        return None
    if predicate in RELATIONSHIP_KINDS:
        return "relationship"
    subject = c.kind_of(fact.proposition.subject_id)
    if subject == "Character" and c.kind_of(other) == "Character":
        return "relationship"
    return None


def check_changes(c: _Checker) -> None:
    """Relationships and goals that start or end with nothing on the page then."""
    for fact in c.facts:
        kind = _change_kind(c, fact)
        if kind is None:
            continue
        involved = {fact.proposition.subject_id}
        other = getattr(fact.proposition.object, "entity_id", None)
        if other is not None and c.kind_of(other) == "Character":
            involved.add(other)
        span = c.span(fact)
        for when, end, which in (
            (fact.starts, span.start, "starts"),
            (fact.ends, span.end, "ends"),
        ):
            if when is None or end is None:
                continue
            if math.isinf(end.lo) and math.isinf(end.hi):
                continue
            # Placed at an event: that event is where it changes.
            if getattr(when, "anchor_event_id", None) and getattr(when, "offset", 1) == 0:
                continue
            if _shown_then(c, end, involved):
                continue
            verb = "starts" if which == "starts" else "stops"
            sentence = fact_sentence(fact, c.names)
            c.add(
                "change",
                f"{kind}_changes_off_page",
                "note",
                f"“{sentence}” {verb} being true {c.at_words(end)}, and no scene or event then has "
                f"{' or '.join(sorted(c.name(i) for i in involved))} in it. A scene where it "
                "changes would show the reader why.",
                [c.fact_record(fact)],
            )


def _overlaps(a: Interval, b: Interval) -> bool:
    return not (a.hi < b.lo - _EPS or b.hi < a.lo - _EPS)


def _shown_then(c: _Checker, moment: Interval, involved: set[str]) -> bool:
    """Whether a timed scene or event then has any of them in it."""
    return any(
        _overlaps(at, moment) for entity_id in involved for at in c.appearances.get(entity_id, ())
    )


CHECKS: tuple[Callable[[_Checker], None], ...] = (
    check_places,
    check_ages,
    check_durations,
    check_place_lifetimes,
    check_possession,
    check_life,
    check_changes,
)


class ContinuityFindings(list[ContinuityFinding]):
    """The findings, warnings first, and how many more were found than are listed."""

    not_shown: int = 0


def continuity_findings(parts: StoryParts) -> ContinuityFindings:
    """Every continuity finding in the story, warnings first, each once.

    At most ``MAX_WARNINGS`` warnings and ``MAX_NOTES`` notes are listed; ``not_shown``
    counts the rest.
    """
    checker = _Checker(parts)
    for check in CHECKS:
        check(checker)
    order = {"warning": 0, "note": 1}
    found = ContinuityFindings(sorted(checker.found, key=lambda item: order[item.severity]))
    found.not_shown = checker.not_shown
    return found


def for_scene(findings: Sequence[ContinuityFinding], scene_id: str) -> list[ContinuityFinding]:
    return [item for item in findings if scene_id in item.scene_ids]


def known_records(
    how_long: HowLong,
    places: Mapping[str, CanonEntity],
    events: Iterable[str],
    facts: Iterable[str],
    *,
    kept: HowLong | None = None,
) -> list[str]:
    """Problems with a table about to be saved, in plain words; empty when it is fine.

    Only what a row names that is new since ``kept`` (the table as stored) is checked:
    the table is shared by every line of the story, so a row naming a place, an event or
    a fact another line has (a what-if's) is kept, and its amount can still be changed.
    """
    problems: list[str] = []
    event_ids, fact_ids = set(events), set(facts)
    old_travel = {frozenset((row.from_id, row.to_id)) for row in kept.travel} if kept else set()
    old_lasting = {(row.subject_kind, row.subject_id) for row in kept.lasting} if kept else set()

    def label(place_id: str) -> str | None:
        entity = places.get(place_id)
        return entity.name if entity is not None else None

    for row in how_long.travel:
        if frozenset((row.from_id, row.to_id)) in old_travel:
            continue
        ends = (row.from_id, row.to_id)
        for place_id in ends:
            entity = places.get(place_id)
            if entity is not None and entity.is_active and entity.type == "Location":
                continue
            other = label(ends[1] if place_id == ends[0] else ends[0])
            if entity is None:
                problems.append(
                    f"A journey from {other} names a place that is not in the story bible."
                    if other
                    else "A journey names a place that is not in the story bible."
                )
            else:
                between = f"The journey between {label(ends[0])} and {label(ends[1])}"
                problems.append(
                    f"{between} names {entity.name}, which is no longer in the story bible."
                    if not entity.is_active
                    else f"{between} names {entity.name}, which is not a place."
                )
            break
    for length in how_long.lasting:
        if (length.subject_kind, length.subject_id) in old_lasting:
            continue
        if length.subject_kind == "event" and length.subject_id not in event_ids:
            problems.append("A length names an event that is not in the story.")
        if length.subject_kind == "fact" and length.subject_id not in fact_ids:
            problems.append("A length names a fact that is not in the story bible.")
    return list(dict.fromkeys(problems))


__all__ = [
    "ALWAYS_SAID",
    "COVERAGE",
    "ContinuityFinding",
    "ContinuityFindings",
    "Coverage",
    "HowLong",
    "Lasting",
    "Record",
    "StoryParts",
    "TravelTime",
    "continuity_findings",
    "counts_whole_units",
    "for_scene",
    "known_records",
    "read_how_long",
    "table_problem",
]
