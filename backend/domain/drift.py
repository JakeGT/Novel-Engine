"""How relationships move across a range of scenes, read without a model (CON-011).

For each pair of characters, across the scenes of a part of the story in reading order:
the scenes that have them both (named in the scene's plan or found by name in its
words), and what the story bible says holds between them at each scene's point in story
time. Two kinds of drift are noted, never as errors:

* **Changes while apart.** What holds between them is different in the next scene that
  has them both, with scenes between where they are apart: the change happens off the
  page.
* **Drifting apart on the page.** They share scenes, then none for a long stretch to the
  end of the range (about an act: a quarter of its scenes, and at least four), while the
  story bible still says something holds between them.

What the words say about how they feel needs a reading of the prose (a check with the
assistant); this only compares the story bible with where the pair appear.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

Standing = Literal["holds", "not_then", "gone", "unclear"]

# A stretch apart is noted when it is longer than this share of the range and at least
# this many scenes (as the arcs' long stretch).
APART_SHARE = 0.25
MIN_APART = 4
MAX_PAIRS = 20


@dataclass(frozen=True)
class RangeScene:
    plan_id: str
    title: str
    # The characters in it: named in its plan or found by name in its words.
    present: frozenset[str]


@dataclass(frozen=True)
class PairFact:
    """A fact between two characters, and how it reads."""

    id: str
    ends: tuple[str, str]
    reading: str


@dataclass
class PairRow:
    a: str
    b: str
    names: tuple[str, str]
    together: list[str] = field(default_factory=list)
    # What the story bible holds between them, as recorded now.
    recorded: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def _quoted(readings: Sequence[str]) -> str:
    if not readings:
        return "nothing recorded"
    return " and ".join(f"“{reading}”" for reading in readings[:2]) + (
        " and more" if len(readings) > 2 else ""
    )


def relationship_drift(
    scenes: Sequence[RangeScene],
    facts: Sequence[PairFact],
    names: Mapping[str, str],
    standing: Callable[[str, int], Standing],
) -> list[PairRow]:
    """Pairs of characters across ``scenes`` (in reading order). ``standing(fact, i)`` says
    how a fact stands at the i-th scene. A pair is listed when it shares a scene or has a
    fact between them; notes say how it drifts."""
    by_pair: dict[tuple[str, str], list[PairFact]] = {}
    for fact in facts:
        a, b = fact.ends
        if a != b and a in names and b in names:
            by_pair.setdefault(_key(a, b), []).append(fact)
    together: dict[tuple[str, str], list[int]] = {}
    for index, scene in enumerate(scenes):
        people = sorted(person for person in scene.present if person in names)
        for i, a in enumerate(people):
            for b in people[i + 1 :]:
                together.setdefault(_key(a, b), []).append(index)
    rows: list[PairRow] = []
    total = len(scenes)
    for pair in sorted(set(by_pair) | set(together)):
        shared = together.get(pair, [])
        pair_facts = by_pair.get(pair, [])
        if not pair_facts and len(shared) < 2:
            continue
        a, b = pair
        row = PairRow(
            a=a,
            b=b,
            names=(names[a], names[b]),
            together=[scenes[index].plan_id for index in shared],
            recorded=[fact.reading for fact in pair_facts],
        )

        def holding(index: int, pair_facts: list[PairFact] = pair_facts) -> list[str] | None:
            found: list[str] = []
            for fact in pair_facts:
                state = standing(fact.id, index)
                if state == "unclear":
                    return None
                if state == "holds":
                    found.append(fact.reading)
            return found

        for earlier, later in zip(shared, shared[1:], strict=False):
            if later - earlier < 2:
                continue  # nothing between them: the change may be on the page
            before, after = holding(earlier), holding(later)
            if before is None or after is None or sorted(before) == sorted(after):
                continue
            row.notes.append(
                f"Between “{scenes[earlier].title}” and “{scenes[later].title}”, what holds "
                f"between {names[a]} and {names[b]} goes from {_quoted(before)} to "
                f"{_quoted(after)}, in scenes where they are apart: the change happens off "
                "the page."
            )
        if len(shared) >= 2:
            after_last = total - 1 - shared[-1]
            if after_last >= max(MIN_APART, int(total * APART_SHARE + 0.999)):
                still = holding(total - 1)
                if still is None:
                    still = [fact.reading for fact in pair_facts]
                if still:
                    row.notes.append(
                        f"{names[a]} and {names[b]} share {len(shared)} scenes up to "
                        f"“{scenes[shared[-1]].title}”, then none of the last {after_last}, "
                        f"while the story bible still says {_quoted(still)}."
                    )
        rows.append(row)
    rows.sort(key=lambda row: (-len(row.notes), -len(row.together), row.names))
    return rows[:MAX_PAIRS]


__all__ = [
    "APART_SHARE",
    "MIN_APART",
    "PairFact",
    "PairRow",
    "RangeScene",
    "Standing",
    "relationship_drift",
]
