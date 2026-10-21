"""Remembering what the author wrote: a derived, rebuildable passage index (ADR-021).

Each text's latest version is cut into passages; each passage is kept as a pointer
with the entries it names, found deterministically. When an embedding provider is
configured, passages are also embedded for search by meaning; otherwise that signal is
simply absent and the context says so. Nothing here is canon, and every row can be
dropped and rebuilt from the texts.

A change to a text does not re-read it at once (CTX-005). It puts the text on a
waiting list (``request``); the background worker reads waiting texts in
(``deliver_waiting``), so saving a text never waits for names to be found or passages
to be embedded, and a failure is tried again later instead of being lost. How many
texts wait, and since when, is the index's lag, shown with the scene's context. Where
nothing runs in the background (a command-line tool, the tests), ``deliver_now`` reads
a story's waiting texts as soon as they are asked for, through the same list.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from backend.core.logging import log_event
from backend.core.metrics import metrics
from backend.domain.context import IndexWaiting
from backend.domain.errors import NotFoundError
from backend.domain.passages import IndexedPassage, entities_in, split_passages
from backend.domain.sources import find_mentions

logger = logging.getLogger(__name__)

MAX_SEMANTIC = 20
# How many waiting texts one delivery reads for a story, and how many stories a round.
DELIVER_BATCH = 20
STORIES_PER_ROUND = 50
# A claim lasts this long; a server that dies mid-read lets go when it ends. Each text
# is claimed just before it is read, so the lease covers one text, not a whole batch.
LEASE_SECONDS = 120.0
# After a failure a text waits this long, doubling with each failure, up to the cap.
RETRY_SECONDS = 5.0
MAX_RETRY_SECONDS = 600.0


@dataclass
class IndexStatus:
    texts: int
    indexed: int
    # Titles of texts whose latest words are not in the index yet.
    behind: list[str] = field(default_factory=list)
    # The story's version when names were last found, for the oldest indexed text.
    names_as_of: int | None = None
    semantic: bool = False
    # The waiting list (CTX-005): texts changed and not yet read in, since when, and
    # how many of them failed at least once.
    waiting: int = 0
    waiting_seconds: float | None = None
    failing: int = 0


@dataclass(frozen=True)
class Delivery:
    read: int = 0
    failed: int = 0


def vector_key(source_id: str, number: int) -> str:
    return f"passage:{source_id}:{number}"


def retry_after(attempts: int) -> float:
    """How long a text that has failed ``attempts`` times before waits until it is tried."""
    return min(MAX_RETRY_SECONDS, RETRY_SECONDS * (2 ** max(0, attempts)))


class PassageIndex:
    def __init__(
        self,
        *,
        store: Any,
        sources: Any,
        reader: Any,
        journal: Any,
        embeddings: Any = None,
        vectors: Any = None,
        queue: Any = None,
        deliver_now: bool = False,
    ) -> None:
        self._store = store
        self._sources = sources
        self._reader = reader
        self._journal = journal
        self._embeddings = embeddings
        self._vectors = vectors
        self._queue = queue
        # Read a story's waiting texts as soon as they are asked for: for places where
        # no background worker runs. The app leaves it off and the worker delivers.
        self.deliver_now = deliver_now

    @property
    def semantic(self) -> bool:
        try:
            return bool(
                self._embeddings is not None
                and self._vectors is not None
                and self._embeddings.available()
            )
        except Exception:  # noqa: BLE001 - an unavailable provider is simply no signal
            return False

    async def _head(self, workspace_id: str, project_id: str) -> int:
        main = await self._journal.ensure_root_branch(
            workspace_id=workspace_id, project_id=project_id
        )
        return int(
            await self._reader.get_head(
                workspace_id=workspace_id, project_id=project_id, branch_id=main.id
            )
        )

    # ------------------------------------------------------------ the waiting list

    async def request(
        self, workspace_id: str, project_id: str, source_id: str, *, gone: bool = False
    ) -> None:
        """Ask for a text to be read into the index (or, ``gone``, forgotten)."""
        if self._queue is None:
            # No waiting list here: read it in at once, as before.
            if gone:
                await self.forget_source(workspace_id, project_id, source_id)
            else:
                await self.index_source(workspace_id, project_id, source_id)
            return
        await self._queue.request(
            workspace_id=workspace_id, project_id=project_id, source_id=source_id, gone=gone
        )
        if self.deliver_now:
            await self.deliver(workspace_id, project_id)

    async def deliver(
        self, workspace_id: str, project_id: str, *, limit: int = DELIVER_BATCH
    ) -> Delivery:
        """Read up to ``limit`` of a story's waiting texts into the index."""
        if self._queue is None:
            return Delivery()
        read = failed = 0
        for _ in range(limit):
            # One at a time: a text's lease starts when it is read, so a slow text early
            # in a batch never lets the lease of those after it run out unread.
            claimed = await self._queue.claim(
                workspace_id=workspace_id,
                project_id=project_id,
                limit=1,
                lease_seconds=LEASE_SECONDS,
            )
            if not claimed:
                break
            # Every row a claim returns is read; none is left held until its lease ends.
            for waiting in claimed:
                try:
                    if waiting.gone:
                        await self.forget_source(workspace_id, project_id, waiting.source_id)
                    else:
                        try:
                            await self.index_source(workspace_id, project_id, waiting.source_id)
                        except NotFoundError:
                            # Removed since it was asked for: nothing of it stays in the index.
                            await self.forget_source(workspace_id, project_id, waiting.source_id)
                except Exception as error:  # noqa: BLE001 - tried again later; the text is safe
                    failed += 1
                    await self._queue.failed(
                        workspace_id=workspace_id,
                        project_id=project_id,
                        source_id=waiting.source_id,
                        generation=waiting.generation,
                        error=type(error).__name__,
                        retry_after_seconds=retry_after(waiting.attempts),
                    )
                    log_event(
                        logger,
                        "passages.index_failed",
                        level=logging.WARNING,
                        error_type=type(error).__name__,
                        attempts=waiting.attempts + 1,
                    )
                    continue
                await self._queue.done(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    source_id=waiting.source_id,
                    generation=waiting.generation,
                )
                read += 1
        if read:
            metrics.increment(
                "passage_index_deliveries_total",
                "Texts read into the passage index from its waiting list, by outcome.",
                amount=read,
                outcome="read",
            )
        if failed:
            metrics.increment(
                "passage_index_deliveries_total",
                "Texts read into the passage index from its waiting list, by outcome.",
                amount=failed,
                outcome="failed",
            )
        return Delivery(read=read, failed=failed)

    async def deliver_waiting(self, *, stories: int = STORIES_PER_ROUND) -> Delivery:
        """One round of the background worker: every story with a text ready to read."""
        if self._queue is None:
            return Delivery()
        started = time.monotonic()
        read = failed = 0
        for workspace_id, project_id in await self._queue.stories_waiting(limit=stories):
            try:
                done = await self.deliver(workspace_id, project_id)
            except Exception as error:  # noqa: BLE001 - one story never stops the others
                log_event(
                    logger,
                    "passages.worker_error",
                    level=logging.WARNING,
                    error_type=type(error).__name__,
                )
                continue
            read += done.read
            failed += done.failed
        if read or failed:
            log_event(
                logger,
                "passages.delivered",
                read=read,
                failed=failed,
                ms=round((time.monotonic() - started) * 1000),
            )
        return Delivery(read=read, failed=failed)

    async def waiting(self, workspace_id: str, project_id: str) -> IndexWaiting:
        if self._queue is None:
            return IndexWaiting()
        result: IndexWaiting = await self._queue.waiting(
            workspace_id=workspace_id, project_id=project_id
        )
        return result

    # ------------------------------------------------------------- reading texts in

    async def index_source(self, workspace_id: str, project_id: str, source_id: str) -> int:
        """Index a text's latest version, replacing whatever was indexed for it."""
        source, version, _ = await self._sources.version(workspace_id, project_id, source_id)
        # Every line of the story shares this index, so a text is read as its own line reads
        # it: the main story's, unless only a what-if holds it (CTX-016).
        names, _, _ = await self._sources.index_for(
            workspace_id, project_id, source_id, own_line=True
        )
        mentions = find_mentions(version.content, names)
        head = await self._head(workspace_id, project_id)
        passages = [
            IndexedPassage(
                source_id=source_id,
                version_id=version.id,
                number=passage.number,
                start=passage.start,
                end=passage.end,
                section=passage.section,
                entity_ids=tuple(entities_in(passage, mentions)),
                indexed_revision=head,
            )
            for passage in split_passages(version.content)
        ]
        before = await self._store.for_source(
            workspace_id=workspace_id, project_id=project_id, source_id=source_id
        )
        await self._store.replace_source(
            workspace_id=workspace_id,
            project_id=project_id,
            source_id=source_id,
            passages=passages,
        )
        if self.semantic:
            gone = [
                vector_key(source_id, item.number) for item in before if item.number > len(passages)
            ]
            if gone:
                await asyncio.to_thread(
                    self._vectors.delete, gone, workspace_id=workspace_id, project_id=project_id
                )
            if passages:
                kind = str(getattr(getattr(source, "kind", None), "value", "") or "")
                await asyncio.to_thread(
                    self._vectors.add_documents,
                    [version.content[item.start : item.end] for item in passages],
                    [
                        {
                            "kind": "passage",
                            "source_id": source_id,
                            "number": item.number,
                            # Filterable at search time (CTX-004).
                            "source_kind": kind,
                        }
                        for item in passages
                    ],
                    [vector_key(source_id, item.number) for item in passages],
                    workspace_id=workspace_id,
                    project_id=project_id,
                )
        log_event(logger, "passages.indexed", passages=len(passages))
        return len(passages)

    async def forget_source(self, workspace_id: str, project_id: str, source_id: str) -> None:
        before = await self._store.for_source(
            workspace_id=workspace_id, project_id=project_id, source_id=source_id
        )
        await self._store.forget_source(
            workspace_id=workspace_id, project_id=project_id, source_id=source_id
        )
        if self.semantic and before:
            await asyncio.to_thread(
                self._vectors.delete,
                [vector_key(source_id, item.number) for item in before],
                workspace_id=workspace_id,
                project_id=project_id,
            )

    async def rebuild(self, workspace_id: str, project_id: str) -> int:
        total = 0
        for source in await self._sources.all_texts(workspace_id, project_id):
            total += await self.index_source(workspace_id, project_id, source.id)
        return total

    async def status(self, workspace_id: str, project_id: str) -> IndexStatus:
        texts = await self._sources.all_texts(workspace_id, project_id)
        indexed = await self._store.indexed_versions(
            workspace_id=workspace_id, project_id=project_id
        )
        status = IndexStatus(texts=len(texts), indexed=0, semantic=self.semantic)
        revisions: list[int] = []
        for text in texts:
            entry = indexed.get(text.id)
            if entry is not None and entry[0] == text.latest_version_id:
                status.indexed += 1
                revisions.append(entry[1])
            else:
                status.behind.append(text.title)
        status.names_as_of = min(revisions) if revisions else None
        waiting = await self.waiting(workspace_id, project_id)
        if self._queue is not None and len(status.behind) > waiting.count:
            # A text behind with nothing waiting for it (a server stopped between saving
            # the text and asking for it, or a text from before the waiting list): ask.
            for text in texts:
                entry = indexed.get(text.id)
                if entry is None or entry[0] != text.latest_version_id:
                    await self._queue.request(
                        workspace_id=workspace_id, project_id=project_id, source_id=text.id
                    )
            waiting = await self.waiting(workspace_id, project_id)
        status.waiting = waiting.count
        status.waiting_seconds = waiting.oldest_seconds
        status.failing = waiting.failing
        return status

    async def semantic_scores(
        self,
        workspace_id: str,
        project_id: str,
        query: str,
        *,
        source_ids: Collection[str] | None = None,
        limit: int = MAX_SEMANTIC,
    ) -> dict[str, float]:
        """Passage key → similarity, when search by meaning is available; else nothing.

        ``source_ids`` limits the search to those texts, in the same scan as the ranking
        (CTX-004): the texts of one line of the story, or the scenes seen through one
        point of view. An empty collection finds nothing.
        """
        if not self.semantic or not query.strip():
            return {}
        if source_ids is not None and not source_ids:
            return {}
        where = {"source_id": sorted(source_ids)} if source_ids is not None else None
        results = await asyncio.to_thread(
            self._vectors.query,
            query,
            workspace_id=workspace_id,
            project_id=project_id,
            n_results=limit,
            where=where,
        )
        scores: dict[str, float] = {}
        for result in results:
            key = str(result.get("id", ""))
            if key.startswith("passage:"):
                scores[key] = float(result.get("score") or 0.0)
        return scores


__all__ = ["Delivery", "IndexStatus", "PassageIndex", "retry_after", "vector_key"]
