"""The document vector store on generation-versioned rows (CTX-012, migration 0047).

``PgVectorStore`` keeps the ``VectorStorePort`` contract callers already use (add,
query, delete by id, delete a project or workspace) on top of the embedding
generations: every vector it writes is made by the configured typed embedder and
lands only in generations whose space is that embedder's (the active one and a
rebuild in progress); a query is embedded with the query task and searches only the
active generation, and only when that generation was built by the configured model.
When it was not (the model was changed and the rebuild has not been switched to yet),
search by meaning is off and health says why. Nothing is ever compared across spaces.

A changed document's old vector is removed from every other generation, in use or kept
for a rollback, so an older space can never answer with words that are no longer there.

The documents are derived and rebuildable. Never logs the words or vectors.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from backend.adapters.embedding import MiniLMTextEmbedding
from backend.adapters.embedding_generations import PgEmbeddingIndexStore
from backend.core.logging import instrument, log_event
from backend.domain.embedding import (
    EmbeddingError,
    EmbeddingTask,
    Generation,
    Modality,
    SearchGate,
    TextInput,
    VectorRow,
    metadata_filter,
)

logger = logging.getLogger(__name__)


class VectorStoreError(RuntimeError):
    """Raised when the vector store cannot satisfy the configured embedding contract."""


def as_multimodal(embeddings: Any) -> Any:
    """A typed embedder for ``embeddings``: itself when it already is one, else the
    text-only ``EmbeddingPort`` wrapped as a plain-prompt generation."""
    if hasattr(embeddings, "space") and hasattr(embeddings, "health"):
        return embeddings
    return MiniLMTextEmbedding(embeddings)


class PgVectorStore:
    """Production vector store backed by PostgreSQL/pgvector generations."""

    def __init__(
        self,
        dsn: str,
        embeddings: Any,
        dimension: int | None = None,
        *,
        index: Any = None,
    ) -> None:
        self._dsn = dsn
        self._embedder = as_multimodal(embeddings) if embeddings is not None else None
        self._index = index if index is not None else PgEmbeddingIndexStore(dsn)
        space_dimension = self._embedder.space.dimension if self._embedder else None
        self._dimension = dimension if dimension is not None else space_dimension

    @property
    def embedder(self) -> Any:
        return self._embedder

    @property
    def index(self) -> Any:
        return self._index

    def dimension_supported(self) -> bool:
        """The configured size (``EMBEDDING_DIMENSION`` for a text-only model) must be the
        embedder's own; a generation stores any size from 1 to 2000."""
        if self._embedder is None:
            return True
        return self._dimension == self._embedder.space.dimension

    def _require_supported_dimension(self) -> None:
        if not self.dimension_supported():
            assert self._embedder is not None
            raise VectorStoreError(
                f"configured EMBEDDING_DIMENSION={self._dimension} does not match the "
                f"model's {self._embedder.space.dimension}"
            )

    # --- which generations a write goes to ----------------------------------------

    def _writable(self) -> list[Generation]:
        """The generations in use that are in the embedder's space."""
        assert self._embedder is not None
        key = self._embedder.space.key
        matching = [
            generation
            for generation in (self._index.active(), self._index.building())
            if generation is not None and generation.space_key == key
        ]
        if not matching and self._may_start_fresh():
            matching.append(self._start_fresh())
        return matching

    def _may_start_fresh(self) -> bool:
        """An install whose index in use is empty (or absent) and that is not building
        one takes the configured model's space at once: nothing is lost or mixed."""
        if self._index.building() is not None:
            return False
        active = self._index.active()
        return active is None or self._index.count(active.id) == 0

    def _start_fresh(self) -> Generation:
        assert self._embedder is not None
        try:
            created = self._index.create_building(
                self._embedder.space, note="Started empty with the configured model."
            )
        except EmbeddingError:
            # Another process started it first; use theirs.
            building = self._index.building()
            if building is None:
                raise
            created = building
        activated: Generation = self._index.activate(created.id)
        log_event(logger, "vectors.generation_started_empty", generation=activated.id)
        return activated

    # --- the port ------------------------------------------------------------------

    @instrument("vector.add_documents", logger)
    def add_documents(
        self,
        documents: list[str],
        metadatas: list[dict[str, Any]],
        ids: list[str],
        *,
        workspace_id: str,
        project_id: str,
    ) -> None:
        self._require_supported_dimension()
        if not ids:
            return
        if self._embedder is None:
            raise VectorStoreError("search by meaning is switched off (EMBEDDING_PROVIDER=none)")
        matching = self._writable()
        written = {generation.id for generation in matching}
        for generation in self._index.list_generations():
            if generation.id in written:
                continue
            # Never leave an older space answering with words that have changed: not
            # one still in use, nor one kept for a rollback (whose lag check then says
            # it is missing them).
            self._index.delete_ids_in(
                generation.id, ids, workspace_id=workspace_id, project_id=project_id
            )
        if not matching:
            log_event(
                logger,
                "vectors.not_indexed",
                level=logging.WARNING,
                reason="space_mismatch",
                documents=len(ids),
            )
            return
        items = [TextInput(text=document) for document in documents]
        try:
            vectors = self._embedder.embed(items, task=EmbeddingTask.DOCUMENT)
        except EmbeddingError as error:
            raise VectorStoreError(f"documents could not be embedded: {error}") from error
        rows = [
            VectorRow(
                id=document_id,
                kind=str(metadata.get("kind") or "document"),
                modality=Modality.TEXT,
                task=EmbeddingTask.DOCUMENT,
                content_hash=item.content_hash,
                values=vector.values,
                document=item.text,
                metadata=dict(metadata),
            )
            for document_id, metadata, item, vector in zip(
                ids, metadatas, items, vectors, strict=True
            )
        ]
        for generation in matching:
            self._index.upsert(
                generation.id, rows, workspace_id=workspace_id, project_id=project_id
            )

    def searchable(self) -> Generation | None:
        """The active generation, when the configured embedder can query it."""
        if self._embedder is None or not self.dimension_supported():
            return None
        active = self._index.active()
        if active is None or active.space_key != self._embedder.space.key:
            return None
        return active  # type: ignore[no-any-return]

    @instrument("vector.query", logger)
    def query(
        self,
        query_text: str,
        *,
        workspace_id: str,
        project_id: str,
        n_results: int = 5,
        where: dict[str, Sequence[str]] | None = None,
    ) -> list[dict[str, Any]]:
        """The nearest documents; ``where`` filters them by metadata in the same scan
        (CTX-004: which text, and its kind)."""
        self._require_supported_dimension()
        metadata = metadata_filter(where)
        active = self.searchable()
        if active is None or self._embedder is None or not query_text.strip():
            return []
        try:
            vector = self._embedder.embed([TextInput(text=query_text)], task=EmbeddingTask.QUERY)[0]
        except EmbeddingError:
            return []
        hits = self._index.search(
            active.id,
            vector.values,
            workspace_id=workspace_id,
            project_id=project_id,
            # Documents only: pictures and their captions are searched by media search.
            gate=SearchGate(assets=False, metadata=metadata),
            limit=n_results,
        )
        return [
            {
                "id": hit.id,
                "document": hit.document,
                "metadata": hit.metadata,
                "score": hit.score,
                "embedding_model": active.model,
                "embedding_version": active.revision,
                "embedding_dimension": active.dimension,
                "generation_id": active.id,
            }
            for hit in hits
        ]

    @instrument("vector.delete", logger)
    def delete(self, ids: Sequence[str], *, workspace_id: str, project_id: str) -> None:
        if not ids:
            return
        self._index.delete_ids(ids, workspace_id=workspace_id, project_id=project_id)

    @instrument("vector.delete_workspace", logger)
    def delete_workspace(self, *, workspace_id: str) -> int:
        return int(self._index.delete_workspace(workspace_id=workspace_id))

    @instrument("vector.delete_project", logger)
    def delete_project(self, *, workspace_id: str, project_id: str) -> int:
        return int(self._index.delete_project(workspace_id=workspace_id, project_id=project_id))

    def health(self) -> bool:
        """Reachable, and the configured model can search what is in use (or there is
        nothing in use yet, so the first write starts in its space)."""
        if not self.dimension_supported():
            return False
        ping = getattr(self._index, "ping", None)
        if ping is not None and not ping():
            return False
        if self._embedder is None:
            return False
        try:
            return self.searchable() is not None or self._may_start_fresh()
        except Exception:  # noqa: BLE001 - unreachable is simply down
            return False


__all__ = ["PgVectorStore", "VectorStoreError", "as_multimodal"]
