"""What someone says aloud in prose, found in one pass.

Speech is what lies between straight double quotes, curly double quotes, or curly single
quotes. A closing ’ is also an apostrophe ("Wren’s"), so a single-quoted span ends at the
first ’ after it that no letter follows. This is exactly what the pattern
``"[^"]*"|“[^”]*”|‘(?:[^’]|’(?=\\w))*’(?!\\w)`` finds, without its cost: that pattern
reads to the end of the text from every quote that is never closed, so a long text with
many open quotes took time growing with the square of its length. Here each kind of
quote looks for its closer at most once past the last one found, and stops looking once
none are left, so the whole text is read a fixed number of times.
"""

from __future__ import annotations

import re

_OPENERS = re.compile('["“‘]')
# A ’ that ends a single-quoted span: no letter, digit or underscore follows it.
_SINGLE_CLOSE = re.compile(r"’(?!\w)")


def spoken_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of each stretch said aloud, in order, quotes included."""
    spans: list[tuple[int, int]] = []
    closers: list[int] | None = None
    next_closer = 0
    exhausted: set[str] = set()
    position = 0
    while True:
        opener = _OPENERS.search(text, position)
        if opener is None:
            break
        start = opener.start()
        mark = text[start]
        end = -1
        if mark not in exhausted:
            if mark == '"':
                end = text.find('"', start + 1)
            elif mark == "“":
                end = text.find("”", start + 1)
            else:
                if closers is None:
                    closers = [match.start() for match in _SINGLE_CLOSE.finditer(text)]
                while next_closer < len(closers) and closers[next_closer] <= start:
                    next_closer += 1
                if next_closer < len(closers):
                    end = closers[next_closer]
        if end < 0:
            # No closer after this one, so none after any later quote of its kind either.
            exhausted.add(mark)
            position = start + 1
            continue
        spans.append((start, end + 1))
        position = end + 1
    return spans


def blank_spoken(text: str) -> str:
    """The text with what is said aloud replaced by spaces, so offsets still match."""
    spans = spoken_spans(text)
    if not spans:
        return text
    pieces: list[str] = []
    cursor = 0
    for start, end in spans:
        pieces.append(text[cursor:start])
        pieces.append(" " * (end - start))
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


__all__ = ["blank_spoken", "spoken_spans"]
