"""The author's voice across the book, worked out on read with no model (CON-018).

Each written scene of the line of the story being worked in is measured
(``backend.domain.voice``) from its newest version; a measure depends only on the words,
so it is kept in memory by version and a book is read again quickly. The author's
**usual** is the same measures across their own scenes: scenes whose newest version they
wrote themselves (a kept draft from the assistant is left out while there are enough of
their own), less any scene they chose to leave out. A scene is compared with the usual
made from the *other* scenes, so it never sets the bar it is measured against.

What the comparison finds are notes beside the scene in the studio, never errors, and
nothing here changes the story. The only thing kept is which scenes the author left out
of their usual (``voice_baselines``).
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from backend.core.logging import log_event
from backend.domain.canon.plans import PlanItem, PlanKind
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.knowledge import reading_order
from backend.domain.sources import VersionOrigin
from backend.domain.tenancy import Permission
from backend.domain.voice import (
    MIN_BASELINE_SCENES,
    MIN_BASELINE_WORDS,
    MIN_SCENE_WORDS,
    VOICE_VERSION,
    Baseline,
    Trend,
    VoiceNote,
    VoiceStats,
    baseline,
    drift,
    measure,
    trends,
)

logger = logging.getLogger(__name__)

# The most scenes one reading looks at; a longer book is measured in its first this many.
MAX_SCENES = 400
MAX_KEPT = 4_000
MAX_LEFT_OUT = 400


@dataclass(frozen=True)
class SceneReading:
    """One written scene, as measured from one version of its words."""

    plan_id: str
    title: str
    source_id: str
    version_id: str
    version_number: int
    # Whether the author wrote this version themselves, rather than keeping a draft.
    own: bool
    stats: VoiceStats


@dataclass(frozen=True)
class Usual:
    """The author's usual, and what it was made from."""

    baseline: Baseline
    # The scenes it was made from.
    scenes: tuple[str, ...]
    # True when only scenes the author wrote themselves were used.
    own_only: bool
    # Scenes the author chose to leave out.
    left_out: tuple[str, ...]
    # Scenes left out because their newest version is a kept draft.
    drafts_left_out: int = 0

    @property
    def enough(self) -> bool:
        return self.baseline.enough


@dataclass
class SceneVoice:
    scene: PlanItem
    reading: SceneReading | None
    usual: Usual
    notes: list[VoiceNote] = field(default_factory=list)
    # Whether this scene counts towards the usual, and whether the author left it out.
    counted: bool = False
    left_out: bool = False
    # Why nothing is compared, in plain words, when nothing is.
    not_compared: str | None = None


@dataclass
class BookVoice:
    rows: list[tuple[SceneReading, list[VoiceNote]]]
    usual: Usual
    # Scenes in the plan with nothing written, and those too long a book to read.
    unwritten: int = 0
    stopped: bool = False


@dataclass
class RangeVoice:
    rows: list[tuple[SceneReading, list[VoiceNote]]]
    usual: Usual
    trends: list[Trend] = field(default_factory=list)


def not_compared(reading: SceneReading | None, usual: Usual) -> str | None:
    if reading is None:
        return "Nothing is written in this scene yet."
    if reading.stats.words < MIN_SCENE_WORDS:
        return f"Too short to compare yet: a scene is compared from {MIN_SCENE_WORDS} words."
    if not usual.enough:
        return (
            f"Not enough of your writing yet to know your usual: it needs {MIN_BASELINE_SCENES} "
            f"scenes and {MIN_BASELINE_WORDS:,} words of your own."
        )
    return None


class VoiceService:
    def __init__(self, *, studio: Any, store: Any) -> None:
        self._studio = studio
        self._store = store
        # version id -> (whether the author wrote it, its measures). Words never change
        # under a version id, so nothing kept here can be out of date.
        self._kept: OrderedDict[str, tuple[VoiceStats, bool, int]] = OrderedDict()

    # ------------------------------------------------------------------ reading

    def _keep(self, version_id: str, found: tuple[VoiceStats, bool, int]) -> None:
        self._kept[version_id] = found
        while len(self._kept) > MAX_KEPT:
            self._kept.popitem(last=False)

    async def _scenes(self, workspace_id: str, project_id: str) -> list[PlanItem]:
        studio = self._studio
        items = await studio._reader.list_plan_items(
            workspace_id=workspace_id, project_id=project_id
        )
        return [item for item in reading_order(items) if item.kind is PlanKind.SCENE]

    async def _readings(
        self, workspace_id: str, project_id: str, scenes: list[PlanItem]
    ) -> tuple[list[SceneReading], int, bool]:
        """Every written scene's measures, in reading order; how many have nothing written;
        and whether the book was longer than one reading looks at."""
        studio = self._studio
        links = dict(
            await studio._store.manuscripts_for(
                workspace_id=workspace_id,
                project_id=project_id,
                branch_id=await studio._branch_id(workspace_id, project_id),
            )
        )
        texts = {
            text.id: text
            for text in await studio._sources.all_texts(workspace_id, project_id)
            if text.removed_at is None
        }
        found: list[SceneReading] = []
        unwritten = 0
        for scene in scenes:
            if len(found) >= MAX_SCENES:
                return found, unwritten, True
            text = texts.get(links.get(scene.id) or "")
            if text is None or not text.latest_version_id or text.length == 0:
                unwritten += 1
                continue
            found.append(
                await self._reading(
                    workspace_id, project_id, scene, text.id, latest=text.latest_version_id
                )
            )
        return found, unwritten, False

    async def _reading(
        self,
        workspace_id: str,
        project_id: str,
        scene: PlanItem,
        source_id: str,
        version_id: str | None = None,
        *,
        latest: str | None = None,
    ) -> SceneReading:
        """One version's measures (the newest when none is named), read once per version:
        the words never change under a version id, so what is kept is never out of date."""
        known = version_id or latest
        kept = self._kept.get(known) if known else None
        if kept is not None and known is not None:
            self._kept.move_to_end(known)
            stats, own, number = kept
            return SceneReading(scene.id, scene.title, source_id, known, number, own, stats)
        _, version, _ = await self._studio._sources.version(
            workspace_id, project_id, source_id, version_id
        )
        own = version.origin is VersionOrigin.AUTHOR
        # Off the event loop: a long scene takes a moment to read, and requests go on.
        stats = await asyncio.to_thread(measure, version.content)
        self._keep(version.id, (stats, own, version.number))
        return SceneReading(
            scene.id, scene.title, source_id, version.id, version.number, own, stats
        )

    async def left_out(self, workspace_id: str, project_id: str) -> list[str]:
        return list(await self._store.left_out(workspace_id=workspace_id, project_id=project_id))

    def usual(
        self, readings: list[SceneReading], left_out: list[str], *, besides: str | None = None
    ) -> Usual:
        """The usual from these scenes: the author's own, less those left out and the scene
        ``besides`` (the one being compared). Every written scene when too few are their own."""
        chosen = [
            item for item in readings if item.plan_id not in left_out and item.plan_id != besides
        ]
        own = [item for item in chosen if item.own]
        mine = baseline([item.stats for item in own])
        if mine.enough:
            return Usual(
                baseline=mine,
                scenes=tuple(item.plan_id for item in own),
                own_only=True,
                left_out=tuple(left_out),
                drafts_left_out=len(chosen) - len(own),
            )
        return Usual(
            baseline=baseline([item.stats for item in chosen]),
            scenes=tuple(item.plan_id for item in chosen),
            own_only=len(own) == len(chosen),
            left_out=tuple(left_out),
        )

    # ------------------------------------------------------------- entry points

    async def scene(
        self, scope: AccessScope, workspace_id: str, project_id: str, plan_id: str
    ) -> SceneVoice:
        """One scene's voice against the author's usual, for the studio's margin."""
        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        item = await studio._scene(scope, workspace_id, project_id, plan_id)
        scenes = await self._scenes(workspace_id, project_id)
        readings, _, _ = await self._readings(workspace_id, project_id, scenes)
        mine = next((reading for reading in readings if reading.plan_id == plan_id), None)
        if mine is None:
            # Past the most one reading looks at, or not linked in this line yet.
            text = await studio._text(workspace_id, project_id, plan_id)
            if text is not None and text.length:
                mine = await self._reading(workspace_id, project_id, item, text.id)
        left = await self.left_out(workspace_id, project_id)
        usual = self.usual(readings, left, besides=plan_id)
        found = SceneVoice(
            scene=item,
            reading=mine,
            usual=usual,
            left_out=plan_id in left,
            counted=bool(mine and plan_id not in left and (mine.own or not usual.own_only)),
        )
        found.not_compared = not_compared(mine, usual)
        if mine is not None and found.not_compared is None:
            found.notes = drift(mine.stats, usual.baseline)
        log_event(
            logger,
            "voice.scene_read",
            scenes=len(readings),
            notes=len(found.notes),
            version=VOICE_VERSION,
        )
        return found

    async def book(self, scope: AccessScope, workspace_id: str, project_id: str) -> BookVoice:
        """Every written scene's voice, in reading order, against the usual."""
        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        scenes = await self._scenes(workspace_id, project_id)
        readings, unwritten, stopped = await self._readings(workspace_id, project_id, scenes)
        left = await self.left_out(workspace_id, project_id)
        usual = self.usual(readings, left)
        rows = [
            (reading, drift(reading.stats, usual.baseline) if usual.enough else [])
            for reading in readings
        ]
        return BookVoice(rows=rows, usual=usual, unwritten=unwritten, stopped=stopped)

    async def for_versions(
        self,
        workspace_id: str,
        project_id: str,
        entries: list[dict[str, Any]],
    ) -> RangeVoice:
        """The voice of exact versions of some scenes (an audit's), against the usual of the
        book as it is now, with the trends across them in the order given. The caller has
        already been authorized."""
        scenes = await self._scenes(workspace_id, project_id)
        by_id = {scene.id: scene for scene in scenes}
        readings, _, _ = await self._readings(workspace_id, project_id, scenes)
        left = await self.left_out(workspace_id, project_id)
        usual = self.usual(readings, left)
        rows: list[tuple[SceneReading, list[VoiceNote]]] = []
        for entry in entries:
            scene = by_id.get(str(entry.get("plan_id")))
            if scene is None or not entry.get("source_id"):
                continue
            try:
                reading = await self._reading(
                    workspace_id,
                    project_id,
                    scene,
                    str(entry["source_id"]),
                    str(entry.get("version_id") or "") or None,
                )
            except NotFoundError:
                continue
            # Each scene against the usual made from the others, as in the studio.
            own = self.usual(readings, left, besides=reading.plan_id)
            notes = drift(reading.stats, own.baseline) if own.enough else []
            rows.append((reading, notes))
        return RangeVoice(
            rows=rows,
            usual=usual,
            trends=trends([(reading.plan_id, reading.stats) for reading, _ in rows]),
        )

    async def leave_out(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        *,
        left_out: bool,
    ) -> list[str]:
        """Leave a scene out of the author's usual, or count it again."""
        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        await studio._scene(scope, workspace_id, project_id, plan_id)
        current = await self.left_out(workspace_id, project_id)
        if left_out and plan_id not in current:
            if len(current) >= MAX_LEFT_OUT:
                raise ValidationError(
                    f"At most {MAX_LEFT_OUT} scenes can be left out of your usual."
                )
            current.append(plan_id)
        elif not left_out:
            current = [item for item in current if item != plan_id]
        await self._store.set_left_out(
            workspace_id=workspace_id,
            project_id=project_id,
            plan_ids=current,
            actor_user_id=scope.actor_user_id,
        )
        return current

    # ---------------------------------------------------------------- archive

    async def export_left_out(self, workspace_id: str, project_id: str) -> list[str]:
        return await self.left_out(workspace_id, project_id)

    async def restore_left_out(
        self, workspace_id: str, project_id: str, plan_ids: Any, known: set[str] | None = None
    ) -> int:
        """Put back the scenes left out of the usual, from an archive. Only plan ids that
        are text (and, when ``known`` is given, are in the restored plan) are kept."""
        if not isinstance(plan_ids, list):
            return 0
        kept = [
            str(item)
            for item in plan_ids[:MAX_LEFT_OUT]
            if isinstance(item, str) and 0 < len(item) <= 200 and (known is None or item in known)
        ]
        if kept:
            await self._store.set_left_out(
                workspace_id=workspace_id, project_id=project_id, plan_ids=kept, actor_user_id=None
            )
        return len(kept)


__all__ = [
    "BookVoice",
    "RangeVoice",
    "SceneReading",
    "SceneVoice",
    "Usual",
    "VoiceService",
    "not_compared",
]
