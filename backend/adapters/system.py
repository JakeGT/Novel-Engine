from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def uuid7() -> uuid.UUID:
    """Generate a time-ordered UUIDv7 as specified by RFC 9562."""
    timestamp_ms = time.time_ns() // 1_000_000
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)

    value = (timestamp_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)


class TimeOrderedIdGenerator:
    """Sortable UUIDv7 identifiers, optionally prefixed by kind."""

    def new_id(self, kind: str | None = None) -> str:
        identifier = str(uuid7())
        return f"{kind}_{identifier}" if kind else identifier
