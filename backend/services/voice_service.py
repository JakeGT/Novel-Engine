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
    ESCALATION_VERSION,
    MIN_ATTRIBUTED_SHARE,
    MIN_BASELINE_SCENES,
    MIN_BASELINE_WORDS,
    MIN_SCENE_WORDS,
    VOICE_VERSION,
    Baseline,
    FlaggedPassage,
    Speech,
    Trend,
    VoiceNote,
    VoiceStats,
    baseline,
    drift,
    flagged_passages,
    measure,
    speech_drift,
    speech_drift_ready,
    speeches,
    strong_drift,
    trends,
)

logger = logging.getLogger(__name__)

# The most scenes one reading looks at; a longer book is measured in its first this many.
MAX_SCENES = 400
MAX_KEPT = 4_000
MAX_LEFT_OUT = 400
# The voice check of flagged passages (CON-018), a job of its own.
VOICE_CHECK_JOB = "voice_check"


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


@dataclass(frozen=True)
class SpeakerRow:
    """One character's dialogue in the scene, and whether it was compared (CON-018)."""

    speech: Speech
    # Other scenes with enough of their dialogue to compare with.
    elsewhere: int
    compared: bool


@dataclass
class SceneVoice:
    scene: PlanItem
    reading: SceneReading | None
    usual: Usual
    notes: list[VoiceNote] = field(default_factory=list)
    # Who says what here, when a tag or turns in an exchange says so (CON-018).
    speakers: list[SpeakerRow] = field(default_factory=list)
    # The share of the scene's dialogue tied to a speaker; notes need at least half.
    attributed: float = 0.0
    # Each note's fingerprint, and the author's dismissal of it if one holds.
    keys: list[str] = field(default_factory=list)
    dismissals: dict[str, Any] = field(default_factory=dict)
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


@dataclass
class VoiceEscalation:
    """Whether the assistant's voice check of a few flagged passages is offered, which
    passages it would read and why, and roughly what it would use (CON-018)."""

    offered: bool
    # Why it is not offered, in plain words, when it is not.
    why_not: str | None = None
    notes: list[str] = field(default_factory=list)
    passages: list[FlaggedPassage] = field(default_factory=list)
    words: int = 0
    total_tokens: int = 0
    source_id: str | None = None
    version_id: str | None = None


class VoiceService:
    def __init__(self, *, studio: Any, store: Any) -> None:
        self._studio = studio
        self._store = store
        # version id -> (whether the author wrote it, its measures). Words never change
        # under a version id, so nothing kept here can be out of date.
        self._kept: OrderedDict[str, tuple[VoiceStats, bool, int]] = OrderedDict()
        # (version id, the characters' names) -> who says what in it. Kept the same way.
        self._spoken: OrderedDict[tuple[str, str], tuple[dict[str, Speech], float]] = OrderedDict()

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
        if mine is not None:
            await self._speakers(workspace_id, project_id, found, mine, readings, left)
        await self._mark_dismissed(workspace_id, project_id, found)
        log_event(
            logger,
            "voice.scene_read",
            scenes=len(readings),
            notes=len(found.notes),
            version=VOICE_VERSION,
        )
        return found

    # ------------------------------------------------------------ who says what

    async def _people(self, workspace_id: str, project_id: str) -> dict[str, tuple[str, ...]]:
        """Every character's name and aliases, by the name the notes call them."""
        entities = await self._studio._reader.list_entities(
            workspace_id=workspace_id, project_id=project_id, entity_type="Character", limit=2000
        )
        return {
            entity.name: tuple(dict.fromkeys([entity.name, *getattr(entity, "aliases", [])]))
            for entity in entities
            if entity.name
        }

    async def _spoken_in(
        self,
        workspace_id: str,
        project_id: str,
        reading: SceneReading,
        people: dict[str, tuple[str, ...]],
        names_key: str,
    ) -> tuple[dict[str, Speech], float]:
        key = (reading.version_id, names_key)
        kept = self._spoken.get(key)
        if kept is not None:
            self._spoken.move_to_end(key)
            return kept
        _, version, _ = await self._studio._sources.version(
            workspace_id, project_id, reading.source_id, reading.version_id
        )
        found = await asyncio.to_thread(speeches, version.content, people)
        self._spoken[key] = found
        while len(self._spoken) > MAX_KEPT:
            self._spoken.popitem(last=False)
        return found

    async def _speakers(
        self,
        workspace_id: str,
        project_id: str,
        found: SceneVoice,
        mine: SceneReading,
        readings: list[SceneReading],
        left: list[str],
    ) -> None:
        """Who says what in this scene, each against their own dialogue in the author's
        other scenes (left-out scenes and kept drafts aside, as for the usual)."""
        people = await self._people(workspace_id, project_id)
        if not people:
            return
        names_key = "\x1f".join(f"{who}={'|'.join(names)}" for who, names in sorted(people.items()))
        here, share = await self._spoken_in(workspace_id, project_id, mine, people, names_key)
        found.attributed = share
        if not here:
            return
        others = [
            item
            for item in readings
            if item.plan_id != mine.plan_id
            and item.plan_id not in left
            and (item.own or not found.usual.own_only)
        ]
        elsewhere: dict[str, list[Speech]] = {}
        for item in others:
            spoken, other_share = await self._spoken_in(
                workspace_id, project_id, item, people, names_key
            )
            if other_share < MIN_ATTRIBUTED_SHARE:
                continue
            for who, speech in spoken.items():
                elsewhere.setdefault(who, []).append(speech)
        for who in sorted(here, key=lambda name: -here[name].words):
            notes = speech_drift(here[who], elsewhere.get(who, []), share)
            found.notes.extend(notes)
            found.speakers.append(
                SpeakerRow(
                    speech=here[who],
                    elsewhere=len(elsewhere.get(who, [])),
                    compared=share >= MIN_ATTRIBUTED_SHARE
                    and bool(speech_drift_ready(here[who], elsewhere.get(who, []))),
                )
            )

    # ------------------------------------------------------------- dismissing

    def _key(self, content: str, note: VoiceNote) -> str:
        from backend.domain.dismissals import anchor, voice_fingerprint

        return voice_fingerprint(
            note.code, note.subject, note.message, anchor(content, note.start, note.end)
        )

    async def _content(self, workspace_id: str, project_id: str, found: SceneVoice) -> str:
        reading = found.reading
        if reading is None:
            return ""
        _, version, _ = await self._studio._sources.version(
            workspace_id, project_id, reading.source_id, reading.version_id
        )
        return str(version.content)

    async def _mark_dismissed(self, workspace_id: str, project_id: str, found: SceneVoice) -> None:
        """Each note's fingerprint, and which the author dismissed. A dismissal holds while
        the note says the same thing (which way it points, whose speech), or for a note on
        a passage, while that sentence is unchanged."""
        if not found.notes:
            return
        from datetime import UTC, datetime

        content = await self._content(workspace_id, project_id, found)
        found.keys = [self._key(content, note) for note in found.notes]
        studio = self._studio
        standing = await studio._store.dismissals(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=await studio._branch_id(workspace_id, project_id),
            plan_id=found.scene.id,
        )
        now = datetime.now(UTC)
        held = {item.fingerprint: item for item in standing if item.applies(now)}
        found.dismissals = {key: held[key] for key in found.keys if key in held}

    async def dismiss(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        *,
        key: str,
        reason: Any,
        note: str = "",
        days: int | None = None,
    ) -> SceneVoice:
        """Dismiss a note on how the scene sounds, with a reason (CON-018). It stays
        dismissed while the note says the same thing; the measures still run."""
        from datetime import UTC, datetime

        from backend.adapters.system import uuid7
        from backend.domain.dismissals import (
            MAX_DISMISSALS_PER_SCENE,
            MAX_NOTE,
            DismissReason,
            FindingDismissal,
            FindingKind,
            expiry,
        )

        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        found = await self.scene(scope, workspace_id, project_id, plan_id)
        if key not in found.keys:
            raise NotFoundError("That note is no longer there: the scene may have changed.")
        voice_note = found.notes[found.keys.index(key)]
        note = " ".join(note.split())
        if reason is DismissReason.OTHER and not note:
            raise ValidationError("Say in a few words why this is not a problem.")
        if len(note) > MAX_NOTE:
            raise ValidationError(f"Keep the reason under {MAX_NOTE} characters.")
        now = datetime.now(UTC)
        try:
            expires_at = expiry(now, days)
        except ValueError:
            raise ValidationError("Choose how long it stays dismissed.") from None
        branch_id = await studio._branch_id(workspace_id, project_id)
        standing = await studio._store.dismissals(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id, plan_id=plan_id
        )
        if len(standing) >= MAX_DISMISSALS_PER_SCENE:
            raise ValidationError("This scene has as many dismissed findings as it can hold.")
        await studio._store.add_dismissal(
            FindingDismissal(
                workspace_id=workspace_id,
                project_id=project_id,
                branch_id=branch_id,
                id=str(uuid7()),
                plan_id=plan_id,
                fingerprint=key,
                kind=FindingKind.CHECK,
                code=voice_note.code,
                reason=reason,
                note=note,
                expires_at=expires_at,
                created_by=scope.actor_user_id,
                created_at=now,
            )
        )
        log_event(logger, "voice.note_dismissed", code=voice_note.code, reason=reason.value)
        return await self.scene(scope, workspace_id, project_id, plan_id)

    async def undo_dismissal(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        dismissal_id: str,
    ) -> SceneVoice:
        """Bring a dismissed note back. The dismissal stays on record, withdrawn."""
        from datetime import UTC, datetime

        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        await studio._scene(scope, workspace_id, project_id, plan_id)
        withdrawn = await studio._store.withdraw_dismissal(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=await studio._branch_id(workspace_id, project_id),
            plan_id=plan_id,
            dismissal_id=dismissal_id,
            at=datetime.now(UTC),
        )
        if not withdrawn:
            raise NotFoundError("That was already brought back.")
        return await self.scene(scope, workspace_id, project_id, plan_id)

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

    # ------------------------------------------------- the assistant's voice check

    def register_jobs(self, runner: Any) -> None:
        runner.register(VOICE_CHECK_JOB, self.run_check)

    async def escalation(
        self, scope: AccessScope, workspace_id: str, project_id: str, plan_id: str
    ) -> VoiceEscalation:
        """Whether the measures point strongly enough away from the author's usual to
        offer a small voice check with the assistant, on which passages, and its cost.
        Calls no model."""
        found = await self.scene(scope, workspace_id, project_id, plan_id)
        return await self._escalation(scope, workspace_id, project_id, found)

    async def _escalation(
        self, scope: AccessScope, workspace_id: str, project_id: str, found: SceneVoice
    ) -> VoiceEscalation:
        reading = found.reading
        if reading is None or found.not_compared is not None:
            return VoiceEscalation(
                offered=False, why_not=found.not_compared or "Nothing is written yet."
            )
        standing = [
            note
            for note, key in zip(found.notes, found.keys, strict=False)
            if key not in found.dismissals
        ]
        if not strong_drift(standing):
            return VoiceEscalation(
                offered=False,
                why_not="The measures do not point far enough from your usual to ask the "
                "assistant: that takes two different notes, or one saying the scene may "
                "sound like someone else.",
                notes=[note.message for note in standing],
            )
        content = await self._content(workspace_id, project_id, found)
        # Off the event loop: a long scene takes a moment to read.
        passages = await asyncio.to_thread(flagged_passages, content, standing, reading.stats)
        if not passages:
            return VoiceEscalation(
                offered=False,
                why_not="No passage shows what the measures found clearly enough to send.",
                notes=[note.message for note in standing],
            )
        messages = [note.message for note in standing]
        tokens = await self._estimate(
            scope, workspace_id, project_id, found, content, passages, messages
        )
        return VoiceEscalation(
            offered=True,
            notes=[note.message for note in standing],
            passages=passages,
            words=sum(len(content[p.start : p.end].split()) for p in passages),
            total_tokens=tokens,
            source_id=reading.source_id,
            version_id=reading.version_id,
        )

    async def _material(
        self, scope: AccessScope, workspace_id: str, project_id: str, plan_id: str, content: str
    ) -> Any:
        from backend.domain.check_passes import CheckPass

        plan = await self._studio.check_plan(
            scope, workspace_id, project_id, plan_id, content, (CheckPass.VOICE,)
        )
        return plan.materials[0]

    @staticmethod
    def _messages(
        material: Any, content: str, passages: list[FlaggedPassage], notes: list[str]
    ) -> list[dict[str, str]]:
        from backend.services.scene_checks import GAP, voice_passages_messages

        text = (
            GAP.strip("\n")
            .join(f"\n\n{content[p.start : p.end].strip()}\n\n" for p in passages)
            .strip()
        )
        # Every note the passages were chosen for, each once.
        flagged = "\n".join(
            f"- {why}" for why in dict.fromkeys([*notes, *(p.why for p in passages)])
        )
        return voice_passages_messages(material.story, material.voice, text, flagged)

    async def _estimate(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        found: SceneVoice,
        content: str,
        passages: list[FlaggedPassage],
        notes: list[str],
    ) -> int:
        import math

        from backend.domain.check_passes import PASS_ANSWER_TOKENS, TOKENS_PER_WORD

        material = await self._material(scope, workspace_id, project_id, found.scene.id, content)
        messages = self._messages(material, content, passages, notes)
        reads = sum(len(message["content"].split()) for message in messages)
        return math.ceil(reads * TOKENS_PER_WORD) + PASS_ANSWER_TOKENS

    async def start_check(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        *,
        confirmed_tokens: int | None = None,
    ) -> Any:
        """Ask the assistant about the flagged passages only. Its notes quote the words
        and are kept with the version read, as the voice pass's notes."""
        from backend.services.job_runner import JobSubmission

        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        found = await self.escalation(scope, workspace_id, project_id, plan_id)
        if not found.offered:
            raise ValidationError(found.why_not or "There is nothing for the assistant to check.")
        await studio.confirm(
            workspace_id,
            project_id,
            estimated_tokens=found.total_tokens,
            confirmed_tokens=confirmed_tokens,
            doing="Checking how these passages sound",
        )
        if studio._usage is not None:
            await studio._usage.ensure_within_limits(
                workspace_id=workspace_id,
                actor_user_id=scope.actor_user_id,
                estimated_tokens=found.total_tokens,
            )
        branch = await studio._journal.working_branch(
            workspace_id=workspace_id, project_id=project_id
        )
        return await studio._runner.submit(
            scope,
            workspace_id,
            project_id,
            JobSubmission(
                kind=VOICE_CHECK_JOB,
                payload={
                    "plan_id": plan_id,
                    "branch_id": None if branch.is_root else branch.id,
                    "source_id": found.source_id,
                    "version_id": found.version_id,
                    "passages": [[p.start, p.end, p.why] for p in found.passages],
                    "notes": found.notes,
                    "estimated_tokens": found.total_tokens,
                    "confirmed_tokens": confirmed_tokens,
                },
            ),
        )

    async def run_check(self, ctx: Any) -> dict[str, Any]:
        from backend.domain.check_passes import CheckPass
        from backend.services.scene_checks import CheckNotes, keep_notes

        studio = self._studio
        job, scope = ctx.job, ctx.scope
        workspace_id, project_id, payload = job.workspace_id, job.project_id, job.payload
        earlier = await studio._store.findings_for_job(
            workspace_id=workspace_id, project_id=project_id, job_id=job.id
        )
        if earlier:
            # An earlier run of this job finished the call and kept its notes.
            return {"findings": len(earlier), "dropped": 0, "earlier": True}
        await ctx.step("Reading the passages and how you write", 10)
        # Exactly the version the author asked about, even if they have written since.
        _, version, _ = await studio._sources.version(
            workspace_id, project_id, payload["source_id"], payload["version_id"]
        )
        content = str(version.content)
        passages = [
            FlaggedPassage(int(start), int(end), str(why))
            for start, end, why in payload.get("passages") or []
            if 0 <= int(start) < int(end) <= len(content)
        ]
        if not passages:
            raise ValidationError("The passages are no longer in this version of the scene.")
        material = await self._material(
            scope, workspace_id, project_id, payload["plan_id"], content
        )
        notes = [str(note) for note in payload.get("notes") or []]
        messages = self._messages(material, content, passages, notes)
        import math

        from backend.domain.check_passes import PASS_ANSWER_TOKENS, TOKENS_PER_WORD

        # Worked out again as it runs: the story may have grown since it was asked for.
        reads = sum(len(message["content"].split()) for message in messages)
        estimated = math.ceil(reads * TOKENS_PER_WORD) + PASS_ANSWER_TOKENS
        await studio.confirm_again(
            workspace_id,
            project_id,
            payload,
            estimated_tokens=estimated,
            doing="Checking how these passages sound",
        )
        key = f"{job.id}:{job.attempts}:voice-passages"
        api_key, base_url, sink = await studio._provider(
            scope, estimated_tokens=estimated, idempotency_key=key
        )
        await ctx.step("The assistant is reading the passages", 40)
        async with ctx.holding():
            response = await studio._ai.generate_structured(
                messages=messages,
                response_format=CheckNotes,
                model=studio._model,
                api_key=api_key,
                base_url=base_url,
            )
            # Recorded inside the lease: a call that finished is counted even if the lease
            # is lost just after it.
            await sink(response.usage, response.model, idempotency_key=key)
            await ctx.used(
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
                response.usage.cached_prompt_tokens,
                response.usage.cost_usd,
            )
        kept, dropped = keep_notes(
            response.parsed.notes or [],
            content,
            check=CheckPass.VOICE,
            target_kind="version",
            target_id=version.id,
            job_id=job.id,
            within=[(p.start, p.end) for p in passages],
        )
        await ctx.step("Saving what it found", 90)
        await studio._store.add_findings(
            workspace_id=workspace_id, project_id=project_id, findings=kept
        )
        log_event(
            logger,
            "voice.passages_checked",
            passages=len(passages),
            kept=len(kept),
            dropped=dropped,
            version=ESCALATION_VERSION,
        )
        return {"findings": len(kept), "dropped": dropped, "passages": len(passages)}

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
    "VOICE_CHECK_JOB",
    "VoiceEscalation",
    "SpeakerRow",
    "BookVoice",
    "RangeVoice",
    "SceneReading",
    "SceneVoice",
    "Usual",
    "VoiceService",
    "not_compared",
]
