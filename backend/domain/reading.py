"""What to read when a scene is read into the story again (Phase 6).

The first time, the whole text is read. After that, only the lines that have not been
read already, neighbours together, so changes already proposed from words that have not
changed are not proposed again. A line is the unit, not a blank-line paragraph: a scene
typed with one line break between paragraphs would otherwise be one paragraph, read in
full after any edit. Spacing inside a line does not count as a change.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# One passage is read at a time; neighbouring changed lines are read together.
MAX_PASSAGE_CHARS = 24_000


@dataclass(frozen=True)
class Passage:
    start: int
    end: int


def lines(text: str) -> list[Passage]:
    """Every line with words on it, as spans of ``text``."""
    found: list[Passage] = []
    cursor = 0
    for line in text.split("\n"):
        if line.strip():
            found.append(Passage(cursor, cursor + len(line)))
        cursor += len(line) + 1
    return found


def _key(text: str) -> str:
    return " ".join(text.split())


def read_keys(text: str, spans: Iterable[tuple[int, int]] | None = None) -> set[str]:
    """The lines of ``text`` that count as read: all of them, or those inside ``spans``."""
    chosen = list(spans) if spans is not None else None
    keys = set()
    for line in lines(text):
        if chosen is None or any(a <= line.start and line.end <= b for a, b in chosen):
            keys.add(_key(text[line.start : line.end]))
    return keys


def changed_passages(
    current: str, seen: set[str] | None, *, limit: int = MAX_PASSAGE_CHARS
) -> list[Passage]:
    """The passages of ``current`` to read: all of it, or the lines not in ``seen``."""
    merged: list[Passage] = []
    last_number = -2
    for number, line in enumerate(lines(current)):
        if seen is not None and _key(current[line.start : line.end]) in seen:
            continue
        if merged and number == last_number + 1 and line.end - merged[-1].start <= limit:
            merged[-1] = Passage(merged[-1].start, line.end)
        else:
            merged.append(line)
        last_number = number
    return merged


__all__ = ["MAX_PASSAGE_CHARS", "Passage", "changed_passages", "lines", "read_keys"]
