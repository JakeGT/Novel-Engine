"""The picture and words search evaluation (CTX-014).

For each embedder configuration (by default the deterministic stand-in at 768 and 512
numbers, and a text-only stand-in that knows pictures only by their accepted captions),
the synthetic corpus is built into a fresh generation through the real build service,
then searched through the real media search service, with the real gates:

- words to picture: Recall@5/@10, mean reciprocal rank, nDCG@10, and how often a
  matching picture ranks above every hard negative (same shape, other colour; or the
  reverse);
- picture to words: the passage written about the picture's subject;
- picture to picture: a second drawing of the same subject;
- links first: a linked picture is always placed first, even when it matches less;
- leakage: secret, rejected, retired, secretly linked and other-story pictures returned
  where their gate applies (must be zero, by search and by explicit link);
- rebuild: building the same corpus twice gives the same rows; a build stopped part way
  leaves nothing behind;
- resources: bytes per vector and in all, and build and query time (reported, never
  pinned; machine-dependent).

With the stand-in, pictures are described to it with their words, so these numbers say
the harness, gates and ranking work. They say nothing about any real model. Point the
harness at a real one with ``run.py --provider embeddinggemma``.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from backend.adapters.vector import PgVectorStore
from backend.domain.embedding import (
    CaptionSource,
    EmbeddingInput,
    EmbeddingProvenance,
    EmbeddingSpace,
    EmbeddingTask,
    EmbeddingVector,
    ImageInput,
    Modality,
    SearchGate,
    space_key,
    truncate,
)
from backend.services.embedding_index import BuildCancelled, EmbeddingIndexService
from backend.services.media_search import MediaSearchService
from tests.embedding_doubles import FakeObjects, InMemoryEmbeddingIndexStore, MediaWorld, Picture
from tests.eval.multimodal.corpus import (
    PASSAGES,
    PICTURES,
    PROJECT,
    SET_VERSION,
    TEXT_QUERIES,
    WORKSPACE,
    draw,
    hard_negatives,
    relevant_pictures,
)
from tests.eval.report import latency, ratio, recall_at, reciprocal_rank

POV = SearchGate(point_of_view=True, branch_id="br_main")


class Shortened:
    """A configured embedder seen at a shorter Matryoshka size: same model, vectors cut
    and normalized again. Lets one loaded model be measured at several sizes."""

    def __init__(self, base: Any, dimension: int) -> None:
        self._base = base
        space = base.space
        self._space = EmbeddingSpace(
            model=space.model,
            revision=space.revision,
            dimension=dimension,
            native_dimension=space.native_dimension,
            modalities=space.modalities,
            tasks=dict(space.tasks),
            task_set=space.task_set,
        )

    @property
    def space(self) -> EmbeddingSpace:
        return self._space

    def available(self) -> bool:
        return bool(self._base.available())

    def loaded(self) -> bool:
        return bool(self._base.loaded())

    def health(self) -> Any:
        return self._base.health()

    def describe(self, picture: bytes, words: str) -> None:
        describe = getattr(self._base, "describe", None)
        if describe is not None:
            describe(picture, words)

    def embed(
        self, inputs: Sequence[EmbeddingInput], *, task: EmbeddingTask
    ) -> list[EmbeddingVector]:
        out = []
        for vector in self._base.embed(inputs, task=task):
            values = (
                vector.values
                if len(vector.values) == self._space.dimension
                else truncate(vector.values, self._space.dimension)
            )
            out.append(
                EmbeddingVector(
                    values=values,
                    provenance=EmbeddingProvenance(
                        model=self._space.model,
                        revision=self._space.revision,
                        dimension=self._space.dimension,
                        task=task,
                        modality=vector.provenance.modality,
                        normalized=True,
                        space_key=space_key(
                            self._space.model,
                            self._space.revision,
                            self._space.dimension,
                            self._space.task_set,
                        ),
                        content_hash=vector.provenance.content_hash,
                    ),
                )
            )
        return out


@dataclass
class Corpus:
    world: MediaWorld
    index: InMemoryEmbeddingIndexStore
    objects: FakeObjects
    data: dict[str, bytes]


def _world(embedder: Any, *, captions: bool) -> Corpus:
    world = MediaWorld(root_branch="br_main")
    index = InMemoryEmbeddingIndexStore(world)
    data: dict[str, bytes] = {}
    for item in PICTURES:
        bytes_ = draw(item)
        data[item.id] = bytes_
        describe = getattr(embedder, "describe", None)
        if describe is not None:
            describe(bytes_, item.words)
        world.add_picture(
            WORKSPACE,
            item.project,
            Picture(
                asset_id=item.id,
                version_id=f"ver_{item.id}",
                data=bytes_,
                content_type="image/png",
                current=not item.retired,
                rejected=item.rejected,
                secret=item.secret,
                # The point of view is not among those who know what it shows.
                secret_links=[("br_main", ())] if item.secret_link else [],
            ),
        )
        if captions:
            world.add_caption(
                WORKSPACE,
                item.project,
                CaptionSource(
                    observation_id=f"obs_{item.id}",
                    asset_id=item.id,
                    asset_version_id=f"ver_{item.id}",
                    text=item.words,
                ),
            )
    return Corpus(world=world, index=index, objects=FakeObjects(world), data=data)


def _ndcg(ranked: Sequence[str], relevant: set[str], k: int = 10) -> float:
    gains = sum(1 / math.log2(i + 2) for i, key in enumerate(ranked[:k]) if key in relevant)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, len(relevant))))
    return round(gains / ideal, 3) if ideal else 1.0


def _mean(values: Sequence[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def _p95(samples: Sequence[float]) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    return round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 3)


async def _build(corpus: Corpus, embedder: Any) -> tuple[str, EmbeddingIndexService, float]:
    service = EmbeddingIndexService(index=corpus.index, embedder=embedder, objects=corpus.objects)
    started = time.perf_counter()
    generation = await service.start_build()
    # Passages go in through the document path, as the author's writing does.
    vectors = PgVectorStore("unused", embedder, index=corpus.index)
    vectors.add_documents(
        [p.words for p in PASSAGES],
        [{"kind": "passage"} for _ in PASSAGES],
        [p.id for p in PASSAGES],
        workspace_id=WORKSPACE,
        project_id=PROJECT,
    )
    await service.build_all(generation.id)
    await service.activate(generation.id)
    return generation.id, service, (time.perf_counter() - started) * 1000


async def evaluate_configuration(
    name: str, embedder: Any, *, captions: bool = False
) -> dict[str, Any]:
    corpus = _world(embedder, captions=captions)
    generation_id, service, build_ms = await _build(corpus, embedder)
    search = MediaSearchService(index=corpus.index, embedder=embedder)
    takes_images = Modality.IMAGE in embedder.space.modalities
    gated = {p.id for p in PICTURES if p.gated}
    leaks: list[str] = []
    query_ms: list[float] = []

    async def pictures(query: Any, *, linked: Sequence[str] = (), limit: int = 10) -> list[str]:
        started = time.perf_counter()
        result = await search.pictures_for(
            WORKSPACE, PROJECT, query=query, linked_asset_ids=linked, gate=POV, limit=limit
        )
        query_ms.append((time.perf_counter() - started) * 1000)
        ranked = [p.asset_id for p in result.pictures]
        leaks.extend(sorted(set(ranked) & gated))
        return ranked

    # Words to picture.
    r5: list[float] = []
    r10: list[float] = []
    mrr: list[float] = []
    ndcg: list[float] = []
    hard_wins = 0
    for query in TEXT_QUERIES:
        ranked = await pictures(query.words)
        relevant = relevant_pictures(query.shape, query.colour)
        negatives = hard_negatives(query.shape, query.colour)
        r5.append(recall_at(ranked, relevant, 5))
        r10.append(recall_at(ranked, relevant, 10))
        mrr.append(reciprocal_rank(ranked, relevant))
        ndcg.append(_ndcg(ranked, relevant))
        first_relevant = min((ranked.index(k) for k in relevant if k in ranked), default=10**6)
        first_negative = min((ranked.index(k) for k in negatives if k in ranked), default=10**6)
        hard_wins += first_relevant < first_negative

    # Links first: link a picture of another colour; it must still come first.
    link_first = 0
    for number, query in enumerate(TEXT_QUERIES):
        colours = sorted({p.colour for p in PICTURES} - {query.colour})
        linked = f"pic_{colours[number % len(colours)]}_{query.shape}"
        ranked = await pictures(query.words, linked=[linked])
        link_first += bool(ranked) and ranked[0] == linked
    # Linked pictures behind a gate are never given, even though they are linked.
    for item in PICTURES:
        if item.gated:
            await pictures("anything", linked=[item.id])

    # Picture to words and picture to picture (only for a model that reads pictures).
    to_text: dict[str, float] = {}
    to_picture: dict[str, float] = {}
    if takes_images:
        t_r1: list[float] = []
        t_mrr: list[float] = []
        for item in PICTURES:
            if item.gated or item.variant:
                continue
            hits = await search.search(
                WORKSPACE,
                PROJECT,
                ImageInput(data=corpus.data[item.id], media_type="image/png"),
                gate=SearchGate(kinds=frozenset({"passage"}), assets=False),
                limit=10,
            )
            ranked = [hit.id for hit in hits]
            relevant = {p.id for p in PASSAGES if p.about == (item.shape, item.colour)}
            t_r1.append(recall_at(ranked, relevant, 1))
            t_mrr.append(reciprocal_rank(ranked, relevant))
        to_text = {"recall_at_1": _mean(t_r1), "mrr": _mean(t_mrr), "queries": len(t_r1)}
        p_r1: list[float] = []
        p_mrr: list[float] = []
        for item in PICTURES:
            if not item.variant:
                continue
            ranked = [
                key
                for key in await pictures(
                    ImageInput(data=corpus.data[item.id], media_type="image/png")
                )
                if key != item.id
            ]
            original = item.id.removesuffix("_again")
            p_r1.append(recall_at(ranked, {original}, 1))
            p_mrr.append(reciprocal_rank(ranked, {original}))
        to_picture = {"recall_at_1": _mean(p_r1), "mrr": _mean(p_mrr), "queries": len(p_r1)}

    # Rebuild equivalence, and a stopped build leaving nothing behind.
    again = await service.start_build()
    await service.build_all(again.id)
    first_rows = {(r.id, r.content_hash, r.values) for r in corpus.index.rows(generation_id)}
    second_rows = {(r.id, r.content_hash, r.values) for r in corpus.index.rows(again.id)}
    equivalent = first_rows == second_rows
    await service.discard(again.id)
    stopped = await service.start_build()
    steps = 0

    async def stop_on_second(stage: str, pct: int) -> None:
        nonlocal steps
        steps += 1
        if steps == 2:
            await service.cancel(stopped.id)

    cancelled_cleanly = False
    try:
        await service.build_all(stopped.id, step=stop_on_second)
    except BuildCancelled:
        cancelled_cleanly = corpus.index.get(stopped.id) is None

    rows = corpus.index.count(generation_id)
    dimension = embedder.space.dimension
    return {
        "configuration": name,
        "model": embedder.space.model,
        "dimension": dimension,
        "reads_pictures": takes_images,
        "words_to_picture": {
            "queries": len(TEXT_QUERIES),
            "recall_at_5": _mean(r5),
            "recall_at_10": _mean(r10),
            "mrr": _mean(mrr),
            "ndcg_at_10": _mean(ndcg),
            "beats_hard_negatives": ratio(hard_wins, len(TEXT_QUERIES)),
        },
        "picture_to_words": to_text,
        "picture_to_picture": to_picture,
        "links_first": ratio(link_first, len(TEXT_QUERIES)),
        "leaks": len(leaks),
        "leaked_ids": sorted(set(leaks)),
        "rebuild_equivalent": equivalent,
        "stopped_build_left_nothing": cancelled_cleanly,
        "storage": {
            "rows": rows,
            "bytes_per_vector": 4 * dimension,
            "vector_bytes": 4 * dimension * rows,
        },
        "timing": {
            "build_ms": round(build_ms, 3),
            "query": latency(query_ms) | {"p95_ms": _p95(query_ms)},
        },
    }


def default_configurations() -> list[tuple[str, Callable[[], Any], bool]]:
    from backend.adapters.embedding_fake import DeterministicEmbedding

    return [
        ("stand-in-768", lambda: DeterministicEmbedding(dimension=768), False),
        ("stand-in-512", lambda: Shortened(DeterministicEmbedding(dimension=768), 512), False),
        (
            "stand-in-text-only-captions",
            lambda: DeterministicEmbedding(
                dimension=384, native_dimension=384, modalities=(Modality.TEXT,)
            ),
            True,
        ),
    ]


def evaluate(
    configurations: Sequence[tuple[str, Callable[[], Any], bool]] | None = None,
) -> dict[str, Any]:
    results = []
    for name, make, captions in configurations or default_configurations():
        results.append(asyncio.run(evaluate_configuration(name, make(), captions=captions)))
    return {
        "set": SET_VERSION,
        "pictures": len(PICTURES),
        "passages": len(PASSAGES),
        "queries": len(TEXT_QUERIES),
        "configurations": results,
        "note": (
            "Stand-in configurations measure the harness, gates and ranking: pictures are "
            "described to the stand-in in words. They say nothing about a real model."
        ),
    }


def pinned(measured: dict[str, Any]) -> dict[str, Any]:
    """The measured numbers without the machine-dependent timings."""
    return {
        **measured,
        "configurations": [
            {key: value for key, value in item.items() if key != "timing"}
            for item in measured["configurations"]
        ],
    }


__all__ = ["Shortened", "default_configurations", "evaluate", "evaluate_configuration", "pinned"]
