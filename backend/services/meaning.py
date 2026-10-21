"""Finding entries by meaning with the model for search, when one is configured.

Used where the story bible is larger than what the assistant can be listed
(AST-EXP-017: Change anything finds the entries an instruction is about even when they
did not fit in the listing) and to choose which pairs of entries to look at for a
missing connection (AST-EXP-007). Without a model for search, or when it fails, the
callers fall back to the listing and say so.

The model for search runs on this server (``backend/adapters/embedding.py``): it spends
nothing from the author's provider allowance. Each entry is embedded from its name,
type, other names and details, never from the author's prose; vectors are kept in
memory only, by content, so an unchanged entry is not embedded twice. Nothing is
logged but counts.
"""

from __future__ import annotations

import asyncio
import logging
import operator
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from backend.core.logging import log_event
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.marks import details
from backend.domain.embedding import EmbeddingTask, TextInput

logger = logging.getLogger(__name__)

# Entries embedded for one request at most, and vectors kept in memory.
MAX_ENTRIES = 1500
MAX_CACHED = 20_000
# Entries compared pair by pair for missing connections (each pair is one comparison).
MAX_PAIR_ENTRIES = 400
BATCH = 32
ENTRY_TEXT_LIMIT = 600


def entry_text(entity: CanonEntity) -> str:
    """What an entry is embedded from: its names and details, not the author's prose."""
    parts = [entity.name, entity.type]
    if entity.aliases:
        parts.append("also " + ", ".join(entity.aliases))
    shown = details(entity.attributes)
    if shown:
        parts.append("; ".join(f"{key} {value}" for key, value in shown.items()))
    return ". ".join(parts)[:ENTRY_TEXT_LIMIT]


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return float(sum(map(operator.mul, left, right)))


@dataclass(frozen=True)
class Pair:
    left: str
    right: str
    score: float


class EntryMeaning:
    """Entries compared by meaning; shared by every request in this process."""

    def __init__(self, embedder: Any) -> None:
        self._embedder = embedder
        self._cache: OrderedDict[tuple[str, str], tuple[float, ...]] = OrderedDict()

    def usable(self) -> bool:
        try:
            return self._embedder is not None and bool(self._embedder.available())
        except Exception:  # noqa: BLE001 - an unusable model means the listing is used
            return False

    async def _embed(self, texts: list[str], task: EmbeddingTask) -> list[tuple[float, ...]]:
        found: list[tuple[float, ...]] = []
        for start in range(0, len(texts), BATCH):
            chunk = [TextInput(text=text) for text in texts[start : start + BATCH]]
            vectors = await asyncio.to_thread(self._embedder.embed, chunk, task=task)
            found.extend(tuple(vector.values) for vector in vectors)
        return found

    async def vectors(self, entities: Sequence[CanonEntity]) -> dict[str, tuple[float, ...]]:
        space = self._embedder.space.key
        keyed = {entity.id: TextInput(text=entry_text(entity)) for entity in entities}
        missing = [
            (entity_id, item)
            for entity_id, item in keyed.items()
            if (space, item.content_hash) not in self._cache
        ]
        if missing:
            values = await self._embed([item.text for _, item in missing], EmbeddingTask.DOCUMENT)
            for (_, item), vector in zip(missing, values, strict=True):
                self._cache[(space, item.content_hash)] = vector
            while len(self._cache) > MAX_CACHED:
                self._cache.popitem(last=False)
        found: dict[str, tuple[float, ...]] = {}
        for entity_id, item in keyed.items():
            cached = self._cache.get((space, item.content_hash))
            if cached is not None:
                self._cache.move_to_end((space, item.content_hash))
                found[entity_id] = cached
        return found

    async def closest(
        self,
        query: str,
        entities: Sequence[CanonEntity],
        *,
        limit: int,
        threshold: float,
    ) -> list[tuple[str, float]]:
        """The entries nearest in meaning to ``query``, best first, above ``threshold``."""
        if not query.strip() or not entities or not self.usable():
            return []
        try:
            vectors = await self.vectors(list(entities)[:MAX_ENTRIES])
            (asked,) = await self._embed([query[:2000]], EmbeddingTask.QUERY)
        except Exception as error:  # noqa: BLE001 - the listing still works
            log_event(
                logger,
                "meaning.closest.failed",
                level=logging.WARNING,
                error_type=type(error).__name__,
            )
            return []
        scored = sorted(
            ((entity_id, _dot(asked, vector)) for entity_id, vector in vectors.items()),
            key=lambda item: (-item[1], item[0]),
        )
        return [(entity_id, score) for entity_id, score in scored if score >= threshold][:limit]

    async def pairs(
        self,
        entities: Sequence[CanonEntity],
        *,
        allowed: Callable[[CanonEntity, CanonEntity], bool],
        limit: int,
        threshold: float,
    ) -> list[Pair]:
        """Pairs of entries close in meaning that ``allowed`` lets be connected, best first."""
        considered = list(entities)[:MAX_PAIR_ENTRIES]
        if len(considered) < 2 or not self.usable():
            return []
        try:
            vectors = await self.vectors(considered)
        except Exception as error:  # noqa: BLE001 - the listing still works
            log_event(
                logger,
                "meaning.pairs.failed",
                level=logging.WARNING,
                error_type=type(error).__name__,
            )
            return []

        def score() -> list[Pair]:
            found: list[Pair] = []
            ready = [entity for entity in considered if entity.id in vectors]
            for index, left in enumerate(ready):
                for right in ready[index + 1 :]:
                    if not allowed(left, right):
                        continue
                    value = _dot(vectors[left.id], vectors[right.id])
                    if value >= threshold:
                        found.append(Pair(left.id, right.id, value))
            found.sort(key=lambda pair: (-pair.score, pair.left, pair.right))
            return found[:limit]

        return await asyncio.to_thread(score)


__all__ = ["EntryMeaning", "Pair", "entry_text"]
