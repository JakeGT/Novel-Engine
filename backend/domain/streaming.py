"""Drafts shown as they are written (SCN-002): the pieces of an answer, gathered and sent on.

A model that streams sends its answer in many small pieces. They are gathered here and
passed on in batches, at most every ``FLUSH_SECONDS`` or ``FLUSH_CHARS``, so a long draft
costs a bounded number of writes and every piece is copied a fixed number of times
(linear in the answer's length, whatever the size of the pieces).

Server-sent events (the WHATWG HTML standard, section 9.2) carry them to the studio:
``event:`` names the kind, ``data:`` one line of JSON. A line break in the words is
inside the JSON string, so one event is always one ``data:`` line.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# How often the words written so far are passed on while a model writes.
FLUSH_SECONDS = 0.4
FLUSH_CHARS = 4000
# The most of one draft kept while it is written; beyond it the draft is still written and
# stored whole, but the studio stops showing it grow (a runaway answer cannot fill a table).
MAX_STREAM_CHARS = 200_000
# How often the stream to the studio looks for new words, and how long one stream lasts
# before the studio connects again.
POLL_SECONDS = 0.3
STREAM_SECONDS = 600.0
# A comment line sent when nothing happened for a while, so a proxy keeps the stream open.
KEEPALIVE_SECONDS = 15.0


@dataclass
class StreamBuffer:
    """The pieces of one answer not yet passed on, and how many characters were kept."""

    clock: Callable[[], float] = time.monotonic
    flush_seconds: float = FLUSH_SECONDS
    flush_chars: int = FLUSH_CHARS
    limit: int = MAX_STREAM_CHARS
    _pending: list[str] = field(default_factory=list)
    _pending_chars: int = 0
    _kept: int = 0
    _last: float | None = None

    @property
    def kept(self) -> int:
        """Characters passed on or waiting to be (never more than ``limit``)."""
        return self._kept + self._pending_chars

    @property
    def full(self) -> bool:
        return self.kept >= self.limit

    def add(self, piece: str) -> bool:
        """Keep a piece; True when what is waiting should be passed on now."""
        if self._last is None:
            self._last = self.clock()
        room = self.limit - self.kept
        if piece and room > 0:
            piece = piece[:room]
            self._pending.append(piece)
            self._pending_chars += len(piece)
        return self.due()

    def due(self) -> bool:
        if not self._pending:
            return False
        if self._pending_chars >= self.flush_chars:
            return True
        return self._last is not None and self.clock() - self._last >= self.flush_seconds

    def take(self) -> str:
        """What is waiting, joined once, and cleared."""
        joined = "".join(self._pending)
        self._kept += self._pending_chars
        self._pending.clear()
        self._pending_chars = 0
        self._last = self.clock()
        return joined


def sse_event(event: str, data: dict[str, Any], *, event_id: str | None = None) -> bytes:
    """One server-sent event: a name, an optional id, and one line of JSON."""
    lines = [f"event: {event}"]
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append("data: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def sse_comment(text: str = "") -> bytes:
    return f": {text}\n\n".encode()


__all__ = [
    "FLUSH_CHARS",
    "FLUSH_SECONDS",
    "KEEPALIVE_SECONDS",
    "MAX_STREAM_CHARS",
    "POLL_SECONDS",
    "STREAM_SECONDS",
    "StreamBuffer",
    "sse_comment",
    "sse_event",
]
