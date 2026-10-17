"""Remembering what the author wrote: a derived, rebuildable passage index (ADR-021).

Each text's latest version is cut into passages; each passage is kept as a pointer
with the entries it names, found deterministically. When an embedding provider is
configured, passages are also embedded for search by meaning; otherwise that signal is
simply absent and the context says so. Nothing here is canon, and every row can be
dropped and rebuilt from the texts.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from backend.core.logging import log_event
from backend.domain.passages import IndexedPassage, entities_in, split_passages
from backend.domain.sources import find_mentions

logger = logging.getLogger(__name__)

MAX_SEMANTIC = 20


@dataclass
class IndexStatus:
    texts: int
    indexed: int
    # Titles of texts whose latest words are not in the index yet.
    behind: list[str] = field(default_factory=list)
    # The story's version when names were last found, for the oldest indexed text.
    names_as_of: int | None = None
    semantic: bool = False


def vector_key(source_id: str, number: int) -> str:
    return f"passage:{source_id}:{number}"


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
    ) -> None:
        self._store = store
        self._sources = sources
        self._reader = reader
        self._journal = journal
        self._embeddings = embeddings
        self._vectors = vectors

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

    async def index_source(self, workspace_id: str, project_id: str, source_id: str) -> int:
        """Index a text's latest version, replacing whatever was indexed for it."""
        _, version, _ = await self._sources.version(workspace_id, project_id, source_id)
        names, _, _ = await self._sources.index_for(workspace_id, project_id, source_id)
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
                await asyncio.to_thread(
                    self._vectors.add_documents,
                    [version.content[item.start : item.end] for item in passages],
                    [
                        {"kind": "passage", "source_id": source_id, "number": item.number}
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
        return status

    async def semantic_scores(
        self, workspace_id: str, project_id: str, query: str
    ) -> dict[str, float]:
        """Passage key → similarity, when search by meaning is available; else nothing."""
        if not self.semantic or not query.strip():
            return {}
        results = await asyncio.to_thread(
            self._vectors.query,
            query,
            workspace_id=workspace_id,
            project_id=project_id,
            n_results=MAX_SEMANTIC,
        )
        scores: dict[str, float] = {}
        for result in results:
            key = str(result.get("id", ""))
            if key.startswith("passage:"):
                scores[key] = float(result.get("score") or 0.0)
        return scores


__all__ = ["IndexStatus", "PassageIndex", "vector_key"]
