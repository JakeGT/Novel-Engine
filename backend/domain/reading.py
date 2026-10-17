"""What to read when a scene is read into the story again (Phase 6).

The first time, the whole text is read. After that, only the paragraphs that are not
in the version last read, grouped into passages, so changes already proposed from
words that have not changed are not proposed again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PARAGRAPH = re.compile(r"\n\s*\n")
# One passage is read at a time; neighbouring changed paragraphs are read together.
MAX_PASSAGE_CHARS = 24_000


@dataclass(frozen=True)
class Passage:
    start: int
    end: int


def paragraphs(text: str) -> list[Passage]:
    found: list[Passage] = []
    cursor = 0
    for match in _PARAGRAPH.finditer(text):
        if text[cursor : match.start()].strip():
            found.append(Passage(cursor, match.start()))
        cursor = match.end()
    if text[cursor:].strip():
        found.append(Passage(cursor, len(text)))
    return found


def _key(text: str) -> str:
    return " ".join(text.split())


def changed_passages(
    current: str, previous: str | None, *, limit: int = MAX_PASSAGE_CHARS
) -> list[Passage]:
    """The passages of ``current`` to read: all of it, or what is new since ``previous``."""
    every = paragraphs(current)
    seen = (
        {_key(previous[p.start : p.end]) for p in paragraphs(previous)}
        if previous is not None
        else set()
    )
    merged: list[Passage] = []
    last_number = -2
    for number, part in enumerate(every):
        if _key(current[part.start : part.end]) in seen:
            continue
        # Next to the previous changed paragraph, and still within one passage's length.
        if merged and number == last_number + 1 and part.end - merged[-1].start <= limit:
            merged[-1] = Passage(merged[-1].start, part.end)
        else:
            merged.append(part)
        last_number = number
    return merged


__all__ = ["MAX_PASSAGE_CHARS", "Passage", "changed_passages", "paragraphs"]
