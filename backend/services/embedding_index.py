"""Building, switching and keeping up the search index by meaning (CTX-011/CTX-012).

The index is derived from the author's texts, the captions the author accepted and the
pictures media processing has prepared. It is kept in *generations*: one per model
configuration. A new generation is built beside the one in use, can be stopped part
way, and is switched to in one step once it has caught up; the previous one is kept so
the switch can be undone. Nothing here is story truth, and every row can be dropped and
built again.

What is embedded, per project:

- documents (passages of the author's writing and other indexed text): copied into a
  new generation from the one in use and re-embedded there; with ``from_texts`` the
  passages are cut from the texts again instead;
- accepted or corrected captions, as text, pointing at their picture;
- pictures, by their web-safe copy, as images: only when the generation's model takes
  images. A text-only generation holds the captions and never pretends to have seen a
  picture;
- parts of pictures the author marked and named or linked to an entry (CTX-012): each
  as its own row pointing at its segment. Every generation holds a part's words (its
  name, the entry it shows and what the author accepted about it); one whose model takes
  images also holds the part itself, cut from the web-safe copy in the same limited
  worker that prepares pictures. A part taken off is refused by the search gate at once
  and its rows are removed when the story's parts are next brought up to date.

Rows are compared by content hash, so a build that is stopped and started again, or
run twice, does only the work still missing. Counts only are logged.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from backend.core.logging import log_event
from backend.domain.embedding import (
    EmbeddingError,
    EmbeddingTask,
    Generation,
    GenerationState,
    ImageInput,
    Modality,
    TextInput,
    VectorRow,
)
from backend.domain.media_observations import region_caption

logger = logging.getLogger(__name__)

EMBEDDING_REINDEX_KIND = "embedding_reindex"
# Bringing a story's marked parts up to date after one is marked, changed or taken off:
# queued from the author's request, never run inside it. One waits per picture at a
# time; another change to that picture before it starts is caught by the same one.
REGION_REFRESH_KIND = "embedding_regions"
# A waiting refresh older than this is taken to be lost (stopped, or the server was
# restarted), so the next change queues a new one.
REGION_REFRESH_WAIT_SECONDS = 300.0
PICTURE_KIND = "picture"
CAPTION_KIND = "caption"
# A marked part of a picture: the part itself (image spaces) and its words (every space).
REGION_KIND = "region"
REGION_CAPTION_KIND = "region_caption"
MEDIA_KINDS = (PICTURE_KIND, CAPTION_KIND, REGION_KIND, REGION_CAPTION_KIND)
BATCH = 16
# The scheduled catch-up (CTX-012): stories looked at, and brought up to date, per round,
# and at most this many things embedded in one round however many a story is missing
# (jobs wait while it runs; the rest is caught up the next round).
CATCH_UP_SCAN = 50
CATCH_UP_STORIES = 3
CATCH_UP_ITEMS = 200
# The lock one server takes for a round, so two never catch up at the same time.
CATCH_UP_LOCK = "embedding-catch-up"

Step = Callable[[str, int], Awaitable[None]]


class BuildCancelled(EmbeddingError):
    """The build was asked to stop; what it finished is kept until it is discarded."""


class NotCaughtUp(EmbeddingError):
    """The new index is still missing rows; switching to it would lose search results."""


def picture_row_id(version_id: str) -> str:
    return f"picture:{version_id}"


def caption_row_id(observation_id: str) -> str:
    return f"caption:{observation_id}"


def region_row_id(segment_id: str) -> str:
    return f"region:{segment_id}"


def region_caption_row_id(segment_id: str) -> str:
    return f"region_caption:{segment_id}"


def region_image_hash(source: Any) -> str:
    """A part's image row is current while its picture's copy and its place are."""
    return hashlib.sha256(f"{source.checksum}:{source.region_hash}".encode()).hexdigest()


def region_words(source: Any) -> str:
    return region_caption(source.label, source.entity_name, list(source.notes))


def wanted_region_rows(sources: Sequence[Any], *, images: bool) -> dict[str, str]:
    """Each region row a generation should hold, by id, with its content hash."""
    wanted: dict[str, str] = {}
    for source in sources:
        words = region_words(source)
        if words:
            wanted[region_caption_row_id(source.segment_id)] = TextInput(text=words).content_hash
        if images:
            wanted[region_row_id(source.segment_id)] = region_image_hash(source)
    return wanted


@dataclass
class ProjectBuild:
    """Counts from building one project into one generation; never names or content."""

    documents: int = 0
    captions: int = 0
    pictures: int = 0
    unchanged: int = 0
    removed: int = 0
    pictures_unreadable: int = 0
    regions: int = 0
    regions_unreadable: int = 0

    def add(self, other: ProjectBuild) -> None:
        for name in vars(self):
            setattr(self, name, getattr(self, name) + getattr(other, name))

    @property
    def embedded(self) -> int:
        """Things sent to the model to embed (a picture that could not be read counts)."""
        return (
            self.documents
            + self.captions
            + self.pictures
            + self.pictures_unreadable
            + self.regions
            + self.regions_unreadable
        )


@dataclass
class Lag:
    """What a generation is still missing, by kind, for one project or overall."""

    documents: int = 0
    captions: int = 0
    pictures: int = 0
    projects: int = 0
    regions: int = 0

    @property
    def total(self) -> int:
        return self.documents + self.captions + self.pictures + self.regions

    def as_dict(self) -> dict[str, int]:
        return {
            "documents": self.documents,
            "captions": self.captions,
            "pictures": self.pictures,
            "regions": self.regions,
            "projects": self.projects,
            "total": self.total,
        }

    def add(self, other: Lag) -> None:
        self.documents += other.documents
        self.captions += other.captions
        self.pictures += other.pictures
        self.regions += other.regions
        if other.total:
            self.projects += 1


@dataclass
class IndexStatus:
    configured: dict[str, Any]
    active: dict[str, Any] | None
    building: dict[str, Any] | None
    # Whether the configured model can search the index in use right now.
    searchable: bool
    reason: str | None
    lag: dict[str, int] = field(default_factory=dict)
    building_lag: dict[str, int] | None = None
    retired: list[dict[str, Any]] = field(default_factory=list)


def _describe(generation: Generation, rows: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": generation.id,
        "model": generation.model,
        "revision": generation.revision,
        "dimension": generation.dimension,
        "modalities": sorted(m.value for m in generation.modalities),
        "task_set": generation.task_set,
        "normalization": generation.normalization,
        "state": generation.state.value,
        "previous_generation_id": generation.previous_generation_id,
        "cancel_requested": generation.cancel_requested,
        "activated_at": generation.activated_at,
        "created_at": generation.created_at,
    }
    if rows is not None:
        out["rows"] = rows
    return out


class EmbeddingIndexService:
    def __init__(
        self,
        *,
        index: Any,
        embedder: Any,
        objects: Any = None,
        passages: Any = None,
        runner: Any = None,
        batch_size: int = BATCH,
        cropper: Any = None,
    ) -> None:
        self._index = index
        # Cuts a marked part from a picture's web copy, in the limited picture worker.
        self._cropper = cropper
        self._embedder = embedder
        self._objects = objects
        self._passages = passages
        self._runner = runner
        self._batch = max(1, batch_size)
        # Where the next scheduled catch-up round starts, so every story gets its turn.
        self._catch_up_from = 0
        # Refreshes of marked parts waiting to start, by (workspace, story, picture): when
        # each was queued (the event loop's clock).
        self._regions_waiting: dict[tuple[str, str, str], float] = {}

    # --- configuration --------------------------------------------------------------

    @property
    def embedder(self) -> Any:
        return self._embedder

    def configured_ready(self) -> bool:
        try:
            return self._embedder is not None and bool(self._embedder.available())
        except Exception:  # noqa: BLE001 - an unavailable model is simply not ready
            return False

    def _matches(self, generation: Generation | None) -> bool:
        return (
            generation is not None
            and self._embedder is not None
            and generation.space_key == self._embedder.space.key
        )

    async def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(getattr(self._index, name), *args, **kwargs)

    # --- status ---------------------------------------------------------------------

    async def status(self, *, with_lag: bool = True) -> IndexStatus:
        active = await self._call("active")
        building = await self._call("building")
        generations = await self._call("list_generations")
        if self._embedder is None:
            configured: dict[str, Any] = {"provider": "none", "available": False}
        else:
            health = self._embedder.health()
            space = self._embedder.space
            configured = {
                "model": space.model,
                "revision": space.revision,
                "dimension": space.dimension,
                "modalities": sorted(m.value for m in space.modalities),
                "task_set": space.task_set,
                "available": health.available,
                "loaded": health.loaded,
                "reason": health.reason,
            }
        reason: str | None = None
        searchable = False
        if self._embedder is None:
            reason = "Search by meaning is switched off."
        elif active is None:
            reason = "No search index has been built yet."
        elif not self._matches(active) and await self._call("count", active.id) == 0:
            reason = (
                "Nothing has been indexed with the model set for this server yet. It starts "
                "with the next writing indexed, or build it now (make reindex)."
            )
        elif not self._matches(active):
            reason = (
                "The search index in use was built with a different model from the one set "
                "for this server. Build a new one (make reindex) and switch to it."
            )
        elif not configured.get("available"):
            reason = str(configured.get("reason") or "The model for search is not available.")
        else:
            searchable = True
        status = IndexStatus(
            configured=configured,
            active=_describe(active, await self._call("count", active.id)) if active else None,
            building=(
                _describe(building, await self._call("count", building.id)) if building else None
            ),
            searchable=searchable,
            reason=reason,
            retired=[_describe(g) for g in generations if g.state is GenerationState.RETIRED],
        )
        if with_lag:
            if active is not None:
                status.lag = (await self.lag(active)).as_dict()
            if building is not None:
                status.building_lag = (await self.lag(building)).as_dict()
        return status

    async def lag(self, generation: Generation) -> Lag:
        total = Lag()
        for workspace_id, project_id in await self._call("projects"):
            total.add(await self.project_lag(generation, workspace_id, project_id))
        return total

    async def project_lag(self, generation: Generation, workspace_id: str, project_id: str) -> Lag:
        scope = {"workspace_id": workspace_id, "project_id": project_id}
        have = await self._call("hashes", generation.id, **scope)
        lag = Lag()
        active = await self._call("active")
        if active is not None and active.id != generation.id:
            for row in await self._call("text_rows", active.id, **scope):
                if row.asset_id is None and row.id not in have:
                    lag.documents += 1
        for caption in await self._call("accepted_captions", **scope):
            row_id = caption_row_id(caption.observation_id)
            if have.get(row_id) != TextInput(text=caption.text).content_hash:
                lag.captions += 1
        if generation.supports(Modality.IMAGE):
            for picture in await self._call("pictures_ready", **scope):
                if have.get(picture_row_id(picture.asset_version_id)) != picture.checksum:
                    lag.pictures += 1
        wanted = wanted_region_rows(
            await self._call("regions_ready", **scope),
            images=generation.supports(Modality.IMAGE),
        )
        lag.regions += sum(1 for row_id, digest in wanted.items() if have.get(row_id) != digest)
        return lag

    # --- building -------------------------------------------------------------------

    async def start_build(self, *, note: str | None = None) -> Generation:
        """Make an empty generation for the configured model beside the one in use."""
        if self._embedder is None:
            raise EmbeddingError("search by meaning is switched off; there is nothing to build")
        if not self.configured_ready():
            raise EmbeddingError(
                str(self._embedder.health().reason or "the model for search is not available")
            )
        generation: Generation = await self._call(
            "create_building", self._embedder.space, note=note
        )
        log_event(logger, "embedding.build_started", generation=generation.id)
        return generation

    async def _check_cancel(
        self, generation_id: str, step: Step | None, stage: str, pct: int
    ) -> None:
        if step is not None:
            await step(stage, pct)
        current = await self._call("get", generation_id)
        if current is None or current.cancel_requested:
            raise BuildCancelled("the build was asked to stop")

    async def _target(self, generation_id: str) -> Generation:
        generation: Generation | None = await self._call("get", generation_id)
        if generation is None:
            raise EmbeddingError("that search index does not exist")
        if generation.state is GenerationState.RETIRED:
            raise EmbeddingError("a retired search index cannot be built into")
        if not self._matches(generation):
            raise EmbeddingError(
                "that search index was made for a different model from the one set for "
                "this server, so it cannot be built here"
            )
        return generation

    async def build_project(
        self,
        generation_id: str,
        workspace_id: str,
        project_id: str,
        *,
        from_texts: bool = False,
        step: Step | None = None,
        limit: int | None = None,
    ) -> ProjectBuild:
        """Bring one project in one generation up to date. Idempotent and resumable.

        With ``limit``, stop after embedding about that many things; what is left is
        brought up to date by the next build."""
        generation = await self._target(generation_id)
        scope = {"workspace_id": workspace_id, "project_id": project_id}
        report = ProjectBuild()
        await self._check_cancel(generation.id, step, "documents", 5)
        if from_texts and self._passages is not None:
            # Passages are cut from the texts again and written to every generation in the
            # configured model's space (this one included).
            report.documents += int(await self._passages.rebuild(workspace_id, project_id))
        have: dict[str, str] = await self._call("hashes", generation.id, **scope)
        active = await self._call("active")
        if active is not None and active.id != generation.id:
            documents = [
                row
                for row in await self._call("text_rows", active.id, **scope)
                if row.asset_id is None and row.id not in have
            ]
            if limit is not None:
                documents = documents[:limit]
            for start in range(0, len(documents), self._batch):
                await self._check_cancel(generation.id, step, "documents", 10)
                chunk = documents[start : start + self._batch]
                report.documents += await self._embed_text_rows(generation, chunk, scope)

        def room() -> int | None:
            return None if limit is None else max(0, limit - report.embedded)

        await self._check_cancel(generation.id, step, "captions", 40)
        report.add(await self._build_captions(generation, have, scope, room=room()))
        await self._check_cancel(generation.id, step, "regions", 50)
        report.add(await self._build_regions(generation, have, scope, room=room()))
        if generation.supports(Modality.IMAGE):
            await self._check_cancel(generation.id, step, "pictures", 60)
            report.add(await self._build_pictures(generation, have, scope, step=step, room=room()))
        else:
            # A text-only space never holds picture rows.
            report.removed += await self._call(
                "delete_kinds_except", generation.id, [PICTURE_KIND], [], **scope
            )
        log_event(
            logger,
            "embedding.project_built",
            generation=generation.id,
            documents=report.documents,
            captions=report.captions,
            pictures=report.pictures,
            unchanged=report.unchanged,
            removed=report.removed,
        )
        return report

    async def _embed_text_rows(
        self, generation: Generation, rows: Sequence[VectorRow], scope: dict[str, str]
    ) -> int:
        if not rows:
            return 0
        items = [TextInput(text=row.document or "") for row in rows]
        vectors = await asyncio.to_thread(self._embedder.embed, items, task=EmbeddingTask.DOCUMENT)
        fresh = [
            VectorRow(
                id=row.id,
                kind=row.kind,
                modality=Modality.TEXT,
                task=EmbeddingTask.DOCUMENT,
                content_hash=item.content_hash,
                values=vector.values,
                document=row.document,
                metadata=row.metadata,
            )
            for row, item, vector in zip(rows, items, vectors, strict=True)
        ]
        return int(await self._call("upsert", generation.id, fresh, **scope))

    async def _build_captions(
        self,
        generation: Generation,
        have: dict[str, str],
        scope: dict[str, str],
        *,
        room: int | None = None,
    ) -> ProjectBuild:
        report = ProjectBuild()
        captions = await self._call("accepted_captions", **scope)
        wanted: list[str] = []
        pending = []
        for caption in captions:
            row_id = caption_row_id(caption.observation_id)
            wanted.append(row_id)
            item = TextInput(text=caption.text)
            if have.get(row_id) == item.content_hash:
                report.unchanged += 1
                continue
            pending.append((row_id, caption, item))
        if room is not None:
            pending = pending[:room]
        for start in range(0, len(pending), self._batch):
            chunk = pending[start : start + self._batch]
            vectors = await asyncio.to_thread(
                self._embedder.embed, [item for _, _, item in chunk], task=EmbeddingTask.DOCUMENT
            )
            rows = [
                VectorRow(
                    id=row_id,
                    kind=CAPTION_KIND,
                    modality=Modality.TEXT,
                    task=EmbeddingTask.DOCUMENT,
                    content_hash=item.content_hash,
                    values=vector.values,
                    # The caption's words are the author's accepted words; kept so a
                    # rebuild needs no second read, never logged.
                    document=caption.text,
                    metadata={"kind": CAPTION_KIND},
                    asset_id=caption.asset_id,
                    asset_version_id=caption.asset_version_id,
                )
                for (row_id, caption, item), vector in zip(chunk, vectors, strict=True)
            ]
            report.captions += int(await self._call("upsert", generation.id, rows, **scope))
        report.removed += int(
            await self._call("delete_kinds_except", generation.id, [CAPTION_KIND], wanted, **scope)
        )
        return report

    async def _build_regions(
        self,
        generation: Generation,
        have: dict[str, str],
        scope: dict[str, str],
        *,
        room: int | None = None,
    ) -> ProjectBuild:
        """Bring the story's named marked parts up to date: their words in every
        generation, the parts themselves in one that takes images (CTX-012)."""
        report = ProjectBuild()
        images = generation.supports(Modality.IMAGE)
        sources = await self._call("regions_ready", **scope)
        wanted = wanted_region_rows(sources, images=images)

        def left() -> int | None:
            return (
                None if room is None else max(0, room - report.regions - report.regions_unreadable)
            )

        pending = []
        for source in sources:
            row_id = region_caption_row_id(source.segment_id)
            if row_id not in wanted:
                continue
            if have.get(row_id) == wanted[row_id]:
                report.unchanged += 1
                continue
            pending.append((row_id, source, TextInput(text=region_words(source))))
        if room is not None:
            pending = pending[:room]
        for start in range(0, len(pending), self._batch):
            chunk = pending[start : start + self._batch]
            vectors = await asyncio.to_thread(
                self._embedder.embed, [item for _, _, item in chunk], task=EmbeddingTask.DOCUMENT
            )
            rows = [
                VectorRow(
                    id=row_id,
                    kind=REGION_CAPTION_KIND,
                    modality=Modality.TEXT,
                    task=EmbeddingTask.DOCUMENT,
                    content_hash=item.content_hash,
                    values=vector.values,
                    # The author's own words and accepted notes; never logged.
                    document=item.text,
                    metadata={"kind": REGION_CAPTION_KIND},
                    asset_id=source.asset_id,
                    asset_version_id=source.asset_version_id,
                    segment_id=source.segment_id,
                )
                for (row_id, source, item), vector in zip(chunk, vectors, strict=True)
            ]
            report.regions += int(await self._call("upsert", generation.id, rows, **scope))
        if images:
            opened: tuple[str, bytes] | None = None
            # Parts of one picture together, so its copy is read once.
            for source in sorted(sources, key=lambda item: (item.object_key, item.segment_id)):
                row_id = region_row_id(source.segment_id)
                if have.get(row_id) == wanted[row_id]:
                    report.unchanged += 1
                    continue
                if (remaining := left()) is not None and remaining <= 0:
                    # The rest waits for the next round; a row still wanted is never removed.
                    continue
                cropper = self._cropper
                if self._objects is None or cropper is None:
                    report.regions_unreadable += 1
                    continue
                try:
                    if opened is None or opened[0] != source.object_key:
                        data = await asyncio.to_thread(
                            self._objects.open, source.object_key, **scope
                        )
                        opened = (source.object_key, data)
                    part = await cropper.crop(opened[1], source.box)
                    vector = (
                        await asyncio.to_thread(
                            self._embedder.embed,
                            [ImageInput(data=part.data, media_type=part.content_type)],
                            task=EmbeddingTask.DOCUMENT,
                        )
                    )[0]
                except Exception:  # noqa: BLE001 - an unreadable part is counted, not fatal
                    report.regions_unreadable += 1
                    continue
                row = VectorRow(
                    id=row_id,
                    kind=REGION_KIND,
                    modality=Modality.IMAGE,
                    task=EmbeddingTask.DOCUMENT,
                    content_hash=wanted[row_id],
                    values=vector.values,
                    document=None,
                    metadata={"kind": REGION_KIND},
                    asset_id=source.asset_id,
                    asset_version_id=source.asset_version_id,
                    segment_id=source.segment_id,
                )
                report.regions += int(await self._call("upsert", generation.id, [row], **scope))
        keep_captions = [
            row_id for row_id in wanted if row_id.startswith(region_caption_row_id(""))
        ]
        keep_images = [row_id for row_id in wanted if row_id.startswith(region_row_id(""))]
        report.removed += int(
            await self._call(
                "delete_kinds_except", generation.id, [REGION_CAPTION_KIND], keep_captions, **scope
            )
        )
        report.removed += int(
            await self._call(
                "delete_kinds_except", generation.id, [REGION_KIND], keep_images, **scope
            )
        )
        return report

    async def regions_changed(self, workspace_id: str, project_id: str) -> int:
        """Bring a story's marked parts up to date in every generation in use (after a
        part is marked, changed or taken off, or a note about one is reviewed). Every
        other generation cannot be embedded into, but a part's rows that no longer match
        are removed from it, so it never answers with a part taken off or renamed.
        Best effort: the next build catches up."""
        scope = {"workspace_id": workspace_id, "project_id": project_id}
        made = 0
        try:
            live = await self._live() if self.configured_ready() else []
            for generation in live:
                have = await self._call("hashes", generation.id, **scope)
                made += (await self._build_regions(generation, have, scope)).regions
            done = {generation.id for generation in live}
            sources = None
            for generation in await self._call("list_generations"):
                if generation.id in done:
                    continue
                if sources is None:
                    sources = await self._call("regions_ready", **scope)
                wanted = wanted_region_rows(sources, images=generation.supports(Modality.IMAGE))
                have = await self._call("hashes", generation.id, **scope)
                stale = [
                    row_id
                    for row_id, content_hash in have.items()
                    if row_id.startswith((region_row_id(""), region_caption_row_id("")))
                    and wanted.get(row_id) != content_hash
                ]
                if stale:
                    await self._call("delete_ids_in", generation.id, stale, **scope)
        except Exception as error:  # noqa: BLE001 - search can catch up later
            log_event(
                logger,
                "embedding.regions_failed",
                level=logging.WARNING,
                error_type=type(error).__name__,
            )
        return made

    async def parts_changed(
        self,
        workspace_id: str,
        project_id: str,
        scope: Any = None,
        asset_id: str = "",
    ) -> bool:
        """Media's hook when a picture's marked parts change: queue bringing them up to
        date as a job, unless one for this picture is waiting to start already. Without
        a job runner (or anyone to queue it for) it is done here, as before. True when
        something was queued or done."""
        if self._runner is None or scope is None:
            await self.regions_changed(workspace_id, project_id)
            return True
        key = (workspace_id, project_id, asset_id)
        now = asyncio.get_running_loop().time()
        queued = self._regions_waiting.get(key)
        if queued is not None and now - queued < REGION_REFRESH_WAIT_SECONDS:
            return False
        from backend.services.job_runner import JobSubmission

        self._regions_waiting[key] = now
        try:
            await self._runner.submit(
                scope,
                workspace_id,
                project_id,
                JobSubmission(kind=REGION_REFRESH_KIND, payload={"asset_id": asset_id}),
            )
        except BaseException:
            self._regions_waiting.pop(key, None)
            raise
        return True

    async def _run_regions_job(self, ctx: Any) -> dict[str, Any]:
        job = ctx.job
        # Started: a change from now on needs a refresh of its own.
        self._regions_waiting.pop(
            (job.workspace_id, job.project_id, str(job.payload.get("asset_id") or "")), None
        )
        await ctx.step("parts", 10)
        made = await self.regions_changed(job.workspace_id, job.project_id)
        await ctx.step("done", 100)
        return {"regions": made}

    async def _build_pictures(
        self,
        generation: Generation,
        have: dict[str, str],
        scope: dict[str, str],
        *,
        step: Step | None = None,
        only_version: str | None = None,
        room: int | None = None,
    ) -> ProjectBuild:
        report = ProjectBuild()
        pictures = await self._call("pictures_ready", **scope)
        wanted = [picture_row_id(p.asset_version_id) for p in pictures]
        for picture in pictures:
            if only_version is not None and picture.asset_version_id != only_version:
                continue
            row_id = picture_row_id(picture.asset_version_id)
            if have.get(row_id) == picture.checksum:
                report.unchanged += 1
                continue
            if room is not None and report.pictures + report.pictures_unreadable >= room:
                # The rest waits for the next round; a row still wanted is never removed.
                continue
            if step is not None:
                await self._check_cancel(generation.id, step, "pictures", 70)
            if self._objects is None:
                report.pictures_unreadable += 1
                continue
            try:
                data = await asyncio.to_thread(self._objects.open, picture.object_key, **scope)
            except Exception:  # noqa: BLE001 - a missing copy is counted, not fatal
                report.pictures_unreadable += 1
                continue
            item = ImageInput(data=data, media_type=picture.content_type, checksum=picture.checksum)
            try:
                vector = (
                    await asyncio.to_thread(
                        self._embedder.embed, [item], task=EmbeddingTask.DOCUMENT
                    )
                )[0]
            except EmbeddingError:
                report.pictures_unreadable += 1
                continue
            row = VectorRow(
                id=row_id,
                kind=PICTURE_KIND,
                modality=Modality.IMAGE,
                task=EmbeddingTask.DOCUMENT,
                content_hash=picture.checksum,
                values=vector.values,
                document=None,
                metadata={"kind": PICTURE_KIND},
                asset_id=picture.asset_id,
                asset_version_id=picture.asset_version_id,
                segment_id=picture.segment_id,
            )
            report.pictures += int(await self._call("upsert", generation.id, [row], **scope))
        if only_version is None:
            report.removed += int(
                await self._call(
                    "delete_kinds_except", generation.id, [PICTURE_KIND], wanted, **scope
                )
            )
        return report

    async def build_all(
        self,
        generation_id: str,
        *,
        from_texts: bool = False,
        step: Step | None = None,
        projects: Sequence[tuple[str, str]] | None = None,
    ) -> ProjectBuild:
        total = ProjectBuild()
        targets = list(projects) if projects is not None else await self._call("projects")
        try:
            for workspace_id, project_id in targets:
                total.add(
                    await self.build_project(
                        generation_id, workspace_id, project_id, from_texts=from_texts, step=step
                    )
                )
        except BuildCancelled:
            generation = await self._call("get", generation_id)
            if generation is not None and generation.state is GenerationState.BUILDING:
                await self._call("discard", generation_id)
                log_event(logger, "embedding.build_cancelled", generation=generation_id)
            raise
        return total

    # --- switching ------------------------------------------------------------------

    async def activate(self, generation_id: str, *, force: bool = False) -> Generation:
        """Switch searching to ``generation_id`` in one transaction."""
        generation = await self._call("get", generation_id)
        if generation is None:
            raise EmbeddingError("that search index does not exist")
        if generation.cancel_requested:
            raise EmbeddingError("that search index was asked to stop; build a new one")
        if not force:
            if not self._matches(generation):
                raise EmbeddingError(
                    "that search index was made with a different model from the one set for "
                    "this server; change the setting first, or use --force"
                )
            lag = await self.lag(generation)
            if lag.total:
                raise NotCaughtUp(
                    f"the new search index is still missing {lag.total} rows "
                    f"in {lag.projects} stories; finish the build first, or use --force"
                )
        activated: Generation = await self._call("activate", generation_id)
        log_event(logger, "embedding.activated", generation=activated.id)
        return activated

    async def rollback(self, *, force: bool = False) -> Generation:
        """Go back to the generation in use before the last switch.

        Like a switch forward, it is refused while the earlier index is missing rows
        (writing or captions changed since it was put aside), unless forced."""
        active = await self._call("active")
        if active is None or not active.previous_generation_id:
            raise EmbeddingError("there is no earlier search index to go back to")
        previous = await self._call("get", active.previous_generation_id)
        if previous is None:
            raise EmbeddingError("the earlier search index has been removed")
        if not force:
            lag = await self.lag(previous)
            if lag.total:
                raise NotCaughtUp(
                    f"the earlier search index is missing {lag.total} rows "
                    f"in {lag.projects} stories; rebuild it, or use --force"
                )
        restored: Generation = await self._call("rollback")
        log_event(logger, "embedding.rolled_back", generation=restored.id)
        return restored

    async def cancel(self, generation_id: str | None = None) -> bool:
        building = await self._call("building")
        target = generation_id or (building.id if building else None)
        if target is None:
            return False
        return bool(await self._call("request_cancel", target))

    async def discard(self, generation_id: str) -> int:
        return int(await self._call("discard", generation_id))

    # --- keeping up between builds ----------------------------------------------------

    async def _live(self) -> list[Generation]:
        out: list[Generation] = []
        for generation in (await self._call("active"), await self._call("building")):
            if self._matches(generation):
                out.append(generation)
        return out

    async def picture_ready(self, workspace_id: str, project_id: str, version_id: str) -> int:
        """Embed one newly prepared picture into every generation in use that takes
        images. Best effort: a failure is logged, and the next build catches up."""
        if not self.configured_ready():
            return 0
        scope = {"workspace_id": workspace_id, "project_id": project_id}
        made = 0
        try:
            for generation in await self._live():
                if not generation.supports(Modality.IMAGE):
                    continue
                have = await self._call("hashes", generation.id, **scope)
                report = await self._build_pictures(
                    generation, have, scope, only_version=version_id
                )
                made += report.pictures
        except Exception as error:  # noqa: BLE001 - search can catch up later
            log_event(
                logger,
                "embedding.picture_failed",
                level=logging.WARNING,
                error_type=type(error).__name__,
            )
        return made

    async def captions_changed(self, workspace_id: str, project_id: str) -> int:
        """Bring a project's accepted captions up to date in every generation in use
        (called after a caption is accepted, corrected, rejected or superseded, and
        after an archive's notes are restored).

        Every other generation (another model's, or one kept for a rollback) cannot be
        embedded into, but a caption that was rejected or whose words changed is
        removed from it, so it never answers with words the author turned down."""
        scope = {"workspace_id": workspace_id, "project_id": project_id}
        made = 0
        try:
            live = await self._live() if self.configured_ready() else []
            for generation in live:
                have = await self._call("hashes", generation.id, **scope)
                made += (await self._build_captions(generation, have, scope)).captions
            wanted = {
                caption_row_id(caption.observation_id): TextInput(text=caption.text).content_hash
                for caption in await self._call("accepted_captions", **scope)
            }
            done = {generation.id for generation in live}
            for generation in await self._call("list_generations"):
                if generation.id in done:
                    continue
                have = await self._call("hashes", generation.id, **scope)
                stale = [
                    row_id
                    for row_id, content_hash in have.items()
                    if row_id.startswith(caption_row_id("")) and wanted.get(row_id) != content_hash
                ]
                if stale:
                    await self._call("delete_ids_in", generation.id, stale, **scope)
        except Exception as error:  # noqa: BLE001 - search can catch up later
            log_event(
                logger,
                "embedding.captions_failed",
                level=logging.WARNING,
                error_type=type(error).__name__,
            )
        # What the author accepts about a marked part is part of that part's words.
        made += await self.regions_changed(workspace_id, project_id)
        return made

    async def versions_removed(
        self, workspace_id: str, project_id: str, version_ids: list[str]
    ) -> int:
        """Media's cleanup hook: forget retired or discarded picture versions in every
        generation, including ones being built and ones kept for a rollback."""
        removed = int(
            await self._call(
                "delete_assets",
                workspace_id=workspace_id,
                project_id=project_id,
                asset_version_ids=list(version_ids),
            )
        )
        log_event(logger, "embedding.versions_forgotten", versions=len(version_ids), rows=removed)
        return removed

    # --- the scheduled catch-up (CTX-012) ---------------------------------------------

    def schedule_catch_up(self, runner: Any, minutes: int) -> bool:
        """Run ``catch_up`` about every ``minutes`` between jobs; off when ``minutes`` is 0
        (the default setting, ``SEARCH_INDEX_CATCH_UP_MINUTES``)."""
        if runner is None or minutes <= 0:
            return False
        runner.every(float(minutes) * 60.0, self.catch_up)
        return True

    async def catch_up(
        self,
        *,
        stories: int = CATCH_UP_STORIES,
        scan: int = CATCH_UP_SCAN,
        items: int = CATCH_UP_ITEMS,
    ) -> ProjectBuild:
        """One round of the scheduled catch-up, what ``make reindex-refresh`` does, a little
        at a time: in the index in use, when it is the configured model's and the model is
        ready, look at up to ``scan`` stories (taking turns from where the last round
        stopped) and bring up to ``stories`` of those still missing something up to date,
        embedding at most ``items`` things in all. Bounded, since jobs wait while it runs;
        idempotent, like every build; one server at a time (another's round is skipped)."""
        total = ProjectBuild()
        if not self.configured_ready():
            return total
        held = await self._call("hold_lock", CATCH_UP_LOCK)
        if held is None:
            return total
        try:
            return await self._catch_up_round(total, stories, scan, max(1, items))
        finally:
            await self._call("release_lock", held)

    async def _catch_up_round(
        self, total: ProjectBuild, stories: int, scan: int, items: int
    ) -> ProjectBuild:
        active = await self._call("active")
        if active is None or not self._matches(active):
            return total
        projects = list(await self._call("projects"))
        if not projects:
            return total
        start = self._catch_up_from % len(projects)
        turn = (projects[start:] + projects[:start])[: max(1, scan)]
        brought = looked = 0
        for workspace_id, project_id in turn:
            if brought >= stories or total.embedded >= items:
                break
            looked += 1
            lag = await self.project_lag(active, workspace_id, project_id)
            if not lag.total:
                continue
            room = items - total.embedded
            report = await self.build_project(active.id, workspace_id, project_id, limit=room)
            total.add(report)
            brought += 1
            if report.embedded >= room:
                # Perhaps cut short: the next round starts with this story again.
                looked -= 1
                break
        self._catch_up_from = start + looked
        if brought:
            log_event(
                logger,
                "embedding.caught_up",
                generation=active.id,
                stories=brought,
                captions=total.captions,
                pictures=total.pictures,
                removed=total.removed,
            )
        return total

    # --- the job ----------------------------------------------------------------------

    def register_jobs(self) -> None:
        if self._runner is not None:
            self._runner.register(EMBEDDING_REINDEX_KIND, self._run_job, keeps_partial=True)
            self._runner.register(REGION_REFRESH_KIND, self._run_regions_job)

    async def _run_job(self, ctx: Any) -> dict[str, Any]:
        job = ctx.job
        generation_id = str(job.payload.get("generation_id") or "")
        if not generation_id:
            building = await self._call("building")
            active = await self._call("active")
            target = building if self._matches(building) else active
            if target is None:
                raise EmbeddingError("there is no search index to build into")
            generation_id = target.id
        report = await self.build_project(
            generation_id,
            job.workspace_id,
            job.project_id,
            from_texts=bool(job.payload.get("from_texts")),
            step=ctx.step,
        )
        ctx.partial = vars(report)
        await ctx.step("done", 100)
        return {"generation_id": generation_id, **vars(report)}


def wire_observations(observations: Any, service: EmbeddingIndexService) -> None:
    """Connect reviewed captions to the index: after a caption is reviewed (accepted,
    corrected or rejected) and after an archive's notes are restored, the project's
    captions are brought up to date in the index straight away instead of waiting for
    the next build.

    ``on_captions_changed`` is used when the observation service offers it; otherwise
    ``review`` and ``restore_notes`` on this instance are wrapped. Best effort: the
    index catching up never fails the review or the restore."""
    if observations is None:
        return
    hook = getattr(observations, "on_captions_changed", None)
    if hook is not None:
        hook(service.captions_changed)
        return
    review = getattr(observations, "review", None)
    if review is not None:

        async def review_then_index(
            scope: Any, workspace_id: str, project_id: str, *args: Any, **kwargs: Any
        ) -> Any:
            decided = await review(scope, workspace_id, project_id, *args, **kwargs)
            # A caption is indexed itself; any note about a marked part is in its words.
            if (
                str(getattr(decided, "kind", CAPTION_KIND)) == CAPTION_KIND
                or getattr(decided, "segment_id", None) is not None
            ):
                await service.captions_changed(workspace_id, project_id)
            return decided

        observations.review = review_then_index
    restore = getattr(observations, "restore_notes", None)
    if restore is not None:

        async def restore_then_index(
            workspace_id: str, project_id: str, *args: Any, **kwargs: Any
        ) -> Any:
            restored = await restore(workspace_id, project_id, *args, **kwargs)
            if restored:
                await service.captions_changed(workspace_id, project_id)
            return restored

        observations.restore_notes = restore_then_index


def wire_media(media_service: Any, service: EmbeddingIndexService) -> None:
    """Connect media to the index: forget removed picture versions, and embed a picture
    once its web-safe copy has been made.

    The removal hook is media's own (``on_versions_removed``). For "ready", media's
    ``on_version_ready`` hook is used when it offers one; otherwise ``process_version``
    on this instance is wrapped so a picture that came back ready is embedded after.
    Either way embedding is best effort and never fails the picture's preparation."""
    if media_service is None:
        return
    on_removed = getattr(media_service, "on_versions_removed", None)
    if on_removed is not None:
        on_removed(service.versions_removed)
    on_regions = getattr(media_service, "on_regions_changed", None)
    if on_regions is not None:
        on_regions(service.parts_changed)
    on_ready = getattr(media_service, "on_version_ready", None)
    if on_ready is not None:
        on_ready(service.picture_ready)
        return
    process = getattr(media_service, "process_version", None)
    if process is None:
        return

    async def process_then_embed(
        workspace_id: str, project_id: str, version_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        result: dict[str, Any] = await process(workspace_id, project_id, version_id, **kwargs)
        if result.get("outcome") in ("ready", "already_done"):
            await service.picture_ready(workspace_id, project_id, version_id)
        return result

    media_service.process_version = process_then_embed


__all__ = [
    "wire_media",
    "wire_observations",
    "CAPTION_KIND",
    "EMBEDDING_REINDEX_KIND",
    "MEDIA_KINDS",
    "REGION_REFRESH_KIND",
    "PICTURE_KIND",
    "REGION_CAPTION_KIND",
    "REGION_KIND",
    "BuildCancelled",
    "EmbeddingIndexService",
    "IndexStatus",
    "Lag",
    "NotCaughtUp",
    "ProjectBuild",
    "caption_row_id",
    "picture_row_id",
    "region_caption_row_id",
    "region_row_id",
]
