"""Finding pictures for a scene or a question: links first, then meaning (ADR-010).

Order of precedence, as the multimodal plan sets it:

1. pictures the caller names because the author linked them (a portrait on an entry
   in the scene, a picture on the scene itself), in the caller's order;
2. pictures found by meaning in the active search index: the picture itself when the
   index's model takes images, else its accepted caption.

Every hard gate is applied before anything is ranked or returned, for both kinds:
project scope, the picture and its version being current and not rejected, the
whole-picture segment not retired, and, for a point of view, secret pictures and
pictures secretly linked to what that point of view does not know. Similarity never
lets an ineligible picture back in: the gates sit in the same query as the ordering.

A picture and its caption are one piece of evidence, not two votes: hits are grouped
by picture and the better score kept. The result says whether search by meaning was
available and, when not, why, so the context can say so instead of guessing.

Nothing here reads or logs the picture's bytes, the caption's words or any vector.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from backend.domain.branching import working_branch_id
from backend.domain.embedding import (
    EmbeddingError,
    EmbeddingInput,
    EmbeddingTask,
    Generation,
    Modality,
    SearchGate,
    StoredHit,
    TextInput,
)
from backend.services.embedding_index import MEDIA_KINDS, REGION_CAPTION_KIND, REGION_KIND

REGION_KINDS = frozenset({REGION_KIND, REGION_CAPTION_KIND})

# How many raw hits are read per picture wanted, so grouping a picture with its caption
# and dropping linked ones still fills the list.
OVERFETCH = 3


def _in_working_line(gate: SearchGate | None) -> SearchGate:
    """The gate, judged in the line of the story this request works in when the
    caller named none: a secret link in a what-if is judged there, not on the main
    story."""
    gate = gate or SearchGate()
    if gate.branch_id is None and working_branch_id() is not None:
        return replace(gate, branch_id=working_branch_id())
    return gate


@dataclass(frozen=True)
class PictureCandidate:
    asset_id: str
    asset_version_id: str | None
    segment_id: str | None
    # "linked": the author linked it; "similar": found by meaning.
    reason: Literal["linked", "similar"]
    score: float | None = None
    # What matched, for a similar picture: "picture" (the image), "caption", or "part"
    # (a part the author marked, as itself or by its words; ``segment_id`` names it).
    matched: Literal["picture", "caption", "part"] | None = None
    generation_id: str | None = None


@dataclass(frozen=True)
class SearchHit:
    """A ranked row of any kind (documents too), for cross-modal search."""

    id: str
    kind: str
    modality: Modality
    score: float
    asset_id: str | None = None
    asset_version_id: str | None = None
    segment_id: str | None = None


@dataclass
class MediaSearchResult:
    pictures: list[PictureCandidate] = field(default_factory=list)
    semantic: bool = False
    # Why search by meaning was not used, in plain words; None when it was.
    reason: str | None = None
    generation_id: str | None = None


class MediaSearchService:
    def __init__(self, *, index: Any, embedder: Any) -> None:
        self._index = index
        self._embedder = embedder

    async def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(getattr(self._index, name), *args, **kwargs)

    async def searchable(self) -> tuple[Generation | None, str | None]:
        """The active generation when the configured model can query it, else why not."""
        if self._embedder is None:
            return None, "Search by meaning is switched off."
        try:
            if not self._embedder.available():
                return None, str(
                    self._embedder.health().reason or "The model for search is not available."
                )
            active: Generation | None = await self._call("active")
        except Exception:  # noqa: BLE001 - an outage leaves links working
            return None, "The search index cannot be reached; linked pictures are still used."
        if active is None:
            return None, "No search index has been built yet."
        if active.space_key != self._embedder.space.key:
            return None, (
                "The search index was built with a different model; until it is rebuilt "
                "only linked pictures are used."
            )
        return active, None

    async def _embed_query(self, query: EmbeddingInput) -> tuple[float, ...]:
        vectors = await asyncio.to_thread(self._embedder.embed, [query], task=EmbeddingTask.QUERY)
        return tuple(vectors[0].values)

    async def pictures_for(
        self,
        workspace_id: str,
        project_id: str,
        *,
        query: str | EmbeddingInput = "",
        linked_asset_ids: Sequence[str] = (),
        gate: SearchGate | None = None,
        limit: int = 8,
    ) -> MediaSearchResult:
        """Pictures for ``query`` (words, or a picture), linked ones first.

        ``linked_asset_ids`` are the pictures the author linked to what the request is
        about, in the order the caller wants them; they are gated like any other."""
        gate = _in_working_line(gate)
        scope = {"workspace_id": workspace_id, "project_id": project_id}
        result = MediaSearchResult()
        linked = list(dict.fromkeys(linked_asset_ids))
        eligible: set[str] = (
            await self._call("eligible_assets", linked, gate=gate, **scope) if linked else set()
        )
        for asset_id in linked:
            if asset_id in eligible and len(result.pictures) < limit:
                result.pictures.append(
                    PictureCandidate(
                        asset_id=asset_id, asset_version_id=None, segment_id=None, reason="linked"
                    )
                )
        active, reason = await self.searchable()
        result.reason = reason
        item = TextInput(text=query) if isinstance(query, str) else query
        if active is None:
            return result
        if isinstance(item, TextInput) and not item.text.strip():
            result.reason = None
            return result
        if not self._embedder.space.supports(item):
            result.reason = "The search model cannot read that kind of input."
            return result
        try:
            values = await self._embed_query(item)
        except EmbeddingError:
            result.reason = "The search model could not read the request."
            return result
        result.semantic = True
        result.generation_id = active.id
        room = limit - len(result.pictures)
        if room <= 0:
            return result
        hits: list[StoredHit] = await self._call(
            "search",
            active.id,
            values,
            gate=replace(gate, kinds=frozenset(MEDIA_KINDS), assets=True),
            limit=room * OVERFETCH + len(linked),
            **scope,
        )
        seen = set(linked)
        for hit in hits:
            if hit.asset_id is None or hit.asset_id in seen:
                continue
            seen.add(hit.asset_id)
            result.pictures.append(
                PictureCandidate(
                    asset_id=hit.asset_id,
                    asset_version_id=hit.asset_version_id,
                    segment_id=hit.segment_id,
                    reason="similar",
                    score=hit.score,
                    matched=(
                        "part"
                        if hit.kind in REGION_KINDS
                        else "picture"
                        if hit.modality is Modality.IMAGE
                        else "caption"
                    ),
                    generation_id=active.id,
                )
            )
            if len(result.pictures) >= limit:
                break
        return result

    async def search(
        self,
        workspace_id: str,
        project_id: str,
        query: EmbeddingInput,
        *,
        gate: SearchGate | None = None,
        limit: int = 10,
    ) -> list[SearchHit]:
        """Ranked rows of any kind for ``query`` in the active generation (text to
        picture, picture to text, picture to picture), gated before ranking."""
        active, _ = await self.searchable()
        if active is None or not self._embedder.space.supports(query):
            return []
        values = await self._embed_query(query)
        hits: list[StoredHit] = await self._call(
            "search",
            active.id,
            values,
            gate=_in_working_line(gate),
            limit=limit,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        return [
            SearchHit(
                id=hit.id,
                kind=hit.kind,
                modality=hit.modality,
                score=hit.score,
                asset_id=hit.asset_id,
                asset_version_id=hit.asset_version_id,
                segment_id=hit.segment_id,
            )
            for hit in hits
        ]


__all__ = ["MediaSearchResult", "MediaSearchService", "PictureCandidate", "SearchHit"]
