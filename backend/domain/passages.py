"""The author's writing in passages, for remembering what was written (ADR-021).

A text version is cut into passages of a few paragraphs, never across a heading and
never mid-paragraph unless one paragraph alone is very long. A passage is a pointer
(version and character range) plus the entries it mentions; the words stay in the text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.domain.sources import HEADING, Mention, split_sections

TARGET = 1200
LONGEST = 2400
_PARAGRAPH = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*\s+")


@dataclass(frozen=True)
class Passage:
    number: int
    start: int
    end: int
    section: str

    @property
    def location(self) -> str:
        return f"chars:{self.start}-{self.end}"


def _paragraphs(content: str, start: int, end: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = start
    for match in _PARAGRAPH.finditer(content, start, end):
        if content[cursor : match.start()].strip():
            spans.append((cursor, match.start()))
        cursor = match.end()
    if content[cursor:end].strip():
        spans.append((cursor, end))
    out: list[tuple[int, int]] = []
    for begin, finish in spans:
        # Trim whitespace so a passage starts and ends on words.
        while begin < finish and content[begin].isspace():
            begin += 1
        while finish > begin and content[finish - 1].isspace():
            finish -= 1
        out.extend(_split_long(content, begin, finish))
    return out


def _split_long(content: str, start: int, end: int) -> list[tuple[int, int]]:
    """A very long paragraph is cut at the last sentence end before the limit."""
    pieces: list[tuple[int, int]] = []
    while end - start > LONGEST:
        cut = start + TARGET
        found = [m.end() for m in _SENTENCE_END.finditer(content, start, start + LONGEST)]
        if found:
            cut = max(found)
        pieces.append((start, cut))
        start = cut
        while start < end and content[start].isspace():
            start += 1
    if end > start:
        pieces.append((start, end))
    return pieces


def split_passages(content: str) -> list[Passage]:
    passages: list[Passage] = []
    for section in split_sections(content):
        body = section.start
        # A section's own heading line is not part of its prose.
        heading = HEADING.match(content, section.start)
        if section.title and heading is not None:
            body = heading.end()
        group_start: int | None = None
        group_end = 0
        for begin, finish in _paragraphs(content, body, section.end):
            if group_start is not None and finish - group_start > TARGET:
                passages.append(Passage(len(passages) + 1, group_start, group_end, section.title))
                group_start = None
            if group_start is None:
                group_start = begin
            group_end = finish
        if group_start is not None:
            passages.append(Passage(len(passages) + 1, group_start, group_end, section.title))
    return passages


@dataclass(frozen=True)
class IndexedPassage:
    """One passage in the index: a pointer and the entries it mentions."""

    source_id: str
    version_id: str
    number: int
    start: int
    end: int
    section: str
    entity_ids: tuple[str, ...]
    indexed_revision: int

    @property
    def key(self) -> str:
        return f"passage:{self.source_id}:{self.number}"


def entities_in(passage: Passage, mentions: list[Mention]) -> list[str]:
    """The entries a passage certainly mentions, in order of first mention."""
    found: list[str] = []
    for mention in mentions:
        if mention.start >= passage.start and mention.end <= passage.end:
            if len(mention.entity_ids) == 1 and mention.entity_ids[0] not in found:
                found.append(mention.entity_ids[0])
    return found


__all__ = ["LONGEST", "TARGET", "IndexedPassage", "Passage", "entities_in", "split_passages"]
