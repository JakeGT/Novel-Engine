"""What the assistant would be given to write a scene, and why (ADR-021).

Candidates come from the story itself, in fixed ways, each with a reason in plain
words: the scene and where it sits in the plan, who and what is in it, what is true
of them at the scene's time, what the viewpoint character knows and gets wrong, what
has happened, what the reader has been shown, the story rules, pictures, and passages
of the author's writing that name the scene's people and places. Search by meaning,
when available, only adds to the score of a passage; it never adds a fact.

The author's pins, exclusions, budget and choice about secrets are kept with the
scene and changed through the ordinary reviewed path.
"""

from __future__ import annotations

import math
import re
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from backend.adapters.system import uuid7
from backend.domain.acquisition import held_at
from backend.domain.arcs import moves_in_scene
from backend.domain.branching import working_branch_id
from backend.domain.canon.assertions import FactVisibility
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import CLAIM_KIND, INFERRED_KIND
from backend.domain.canon.marks import IMPORTANCE_KEY, clean_importance
from backend.domain.canon.marks import details as plain_details_of
from backend.domain.canon.naming import normalize_name
from backend.domain.canon.phrasing import phrase
from backend.domain.canon.plans import PlanItem, PlanKind
from backend.domain.context import (
    CONTINUITY_BOOST,
    NEAR_SCENES,
    RULE_BOOST,
    SCENE_DRAFT_POLICY,
    STORY_TIME_SHARE,
    ContextComparison,
    ContextItem,
    ContextManifest,
    ContextPolicy,
    ContextSection,
    ContextSettings,
    LaterInTime,
    Signal,
    assemble,
    compare_contexts,
    context_settings,
    count_words,
    distance_signal,
    diversify,
    importance_signal,
    later_in_time_signal,
    stored_manifest,
    story_time_signal,
    with_context_settings,
    with_signals,
)
from backend.domain.context_parts import (
    REPRESENTATION_WORDS,
    ContextChoice,
    Delivery,
    MediaLimits,
    planned_representation,
)
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.knowledge import knowledge_of, reader_journey, reading_order, scene_reveals
from backend.domain.knowledge import scene_time as scene_when
from backend.domain.media import link_applies_at, link_window_words
from backend.domain.rules import RuleStatus, read_rule, violations, world_from_canon
from backend.domain.scene_conditions import condition_reading, plain_details, scene_conditions
from backend.domain.scene_intent import intent_sentences, scene_intent
from backend.domain.sources import SourceKind, SourceText, find_mentions
from backend.domain.tenancy import Permission
from backend.domain.threads import thread_advances
from backend.domain.worldtime import (
    Interval,
    Order,
    Truth,
    compare,
    describe,
    true_at,
)
from backend.services.knowledge_timing import StoryOrder
from backend.services.passage_index import IndexStatus
from backend.services.scene_summaries import plan_card_summary
from backend.services.story_time_service import (
    anchor_lookup,
    event_times,
    fact_span,
    read_story,
    time_of,
)

MAX_PASSAGES = 40
# Whole-story continuity findings and the rules' world, kept for this many lines of the
# story at one version each (CTX-007): every context built at that version reuses them.
STORY_WIDE_KEPT = 8
MAX_EVENTS = 40
PASSAGE_WORDS = 220
# Earlier scenes given as a summary and an event block (CTX-006), nearest first.
EARLIER_SCENES = 5
# How much of the scene before is given as written (CTX-006A), from its end.
BEFORE_WORDS = 250
# Passages from each other scene on one of this scene's threads (CTX-006A).
THREAD_PASSAGES = 2
# Events listed in one scene's event block.
BLOCK_EVENTS = 8
# Other recent scenes whose ends are given as written (CTX-006A): at most this many, from
# this far back in reading order, on the same viewpoint or one of this scene's threads.
NEARBY_SCENES = 2
NEARBY_WINDOW = 8
NEARBY_WORDS = 120
# Passages from scenes later in the story's time listed as left out, at most (CTX-004).
LATER_LISTED = 5
# A continuity warning's words given in a reason, at most.
FINDING_WORDS = 24

ROLE_WORDS = {
    "point_of_view": "is the scene's point of view",
    "place": "is where the scene happens",
    "participant": "is in the scene",
    "about": "is what the scene is about",
}
ROLE_SCORE = {"point_of_view": 10.0, "place": 9.0, "participant": 8.0, "about": 6.0}
# A picture shown on a scene links to the plan item that is the scene.
SCENE_TARGET = "plan_item"
# Pictures found by meaning, at most, after those the author linked.
MAX_SIMILAR_PICTURES = 3


def _listing(names: list[str]) -> str:
    """ "Mara", "Mara and Tobin", "Mara, Tobin and Saltmere"."""
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


@dataclass(frozen=True)
class KeptContext:
    """The context a kept version of the scene was drafted from."""

    manifest_id: str
    version_id: str
    version_number: int
    kept_at: Any
    words: int


@dataclass(frozen=True)
class SceneContext:
    scene: PlanItem
    point_of_view: str | None
    settings: ContextSettings
    manifest: ContextManifest
    index: IndexStatus | None
    semantic: bool


def _phrase(predicate: str) -> str:
    return phrase(predicate)


def _value(obj: Any, names: dict[str, str]) -> str:
    target = getattr(obj, "entity_id", None)
    if target:
        return names.get(target, "something no longer in the story")
    value = getattr(obj, "value", None)
    if isinstance(value, dict):
        return " ".join(str(part) for part in value.values())
    return "" if value is None else str(value)


def _sentence(proposition: Any, names: dict[str, str]) -> str:
    subject = names.get(proposition.subject_id, "Something")
    return f"{subject} {_phrase(proposition.predicate)} {_value(proposition.object, names)}".strip()


def _trim(text: str, words: int) -> str:
    parts = text.split()
    return text.strip() if len(parts) <= words else " ".join(parts[:words]) + " …"


def _hidden(fact: Any, pov: str | None, known_ids: set[str]) -> bool:
    """A secret that must stay off the page: the viewpoint does not know it, or none is chosen."""
    return fact.visibility is FactVisibility.SECRET and (pov is None or fact.id not in known_ids)


def _secret_words(sentence: str, pov: str | None, names: dict[str, str]) -> tuple[str, str]:
    """The text and reason of a secret kept for the author alone."""
    if pov is None:
        return (
            f"{sentence}. It is a secret.",
            "It is a secret, and no point of view is chosen for the scene.",
        )
    who = names.get(pov)
    return (
        f"{sentence}. {who or 'The viewpoint character'} does not know this.",
        f"It is a secret {who or 'the viewpoint character'} does not know.",
    )


def ranked(item: ContextItem, signals: list[Signal | None]) -> ContextItem:
    """The item with these signals applied to its score and said in its reason (CTX-007)."""
    applied = [signal for signal in signals if signal is not None and signal.effect]
    if not applied:
        return item
    notes = " ".join(signal.note for signal in applied if signal.note)
    if not notes:
        return replace(
            item,
            score=round(item.score + sum(signal.effect for signal in applied), 3),
            signals=(*item.signals, *applied),
        )
    return replace(
        item,
        score=round(item.score + sum(signal.effect for signal in applied), 3),
        signals=(*item.signals, *applied),
        reason=f"{item.reason} {notes}",
    )


def _latest_in_time(scene_ids: Sequence[str], ranking: _Ranking) -> str | None:
    """Of a part's scenes, the one that places it in the story's time: one certainly later
    than the scene being written, else one that may be, else its last in reading order."""
    if not scene_ids:
        return None
    for where in ("later", "maybe"):
        found = next((s for s in scene_ids if ranking.in_time(s)[0] == where), None)
        if found is not None:
            return found
    return scene_ids[-1]


def _tail(text: str, words: int) -> tuple[str, int]:
    """The last ``words`` words of a text, and where they start in it."""
    parts = list(re.finditer(r"\S+", text))
    if len(parts) <= words:
        start = parts[0].start() if parts else 0
        return text[start:].strip(), start
    start = parts[-words].start()
    return "… " + text[start:].strip(), start


@dataclass
class _Ranking:
    """What the ranking signals read, worked out once per context (CTX-007)."""

    # Entry id -> the author's importance mark.
    importance: dict[str, str]
    # Record id -> a continuity warning or broken rule about it at or near the scene.
    impact: dict[str, tuple[str, float]]
    # Scene id -> its place in reading order.
    place: dict[str, int]
    position: int | None
    # The second clock (CTX-007): each scene's place in the story's time, this scene's,
    # how fast prose loses weight going back in it, and in what unit it is counted.
    times: dict[str, Interval | None] | None = None
    moment: Interval | None = None
    half_life: float | None = None
    unit: str = "year"
    # What the policy does with material from a scene later in the story's time (CTX-004).
    later_policy: LaterInTime = LaterInTime.LEAVE_OUT

    def in_time(self, scene_id: str | None) -> tuple[str, float]:
        """Where a scene sits in the story's time against this one: "later" (certainly),
        "maybe" (it may be later), "before" with how long before, or "" (not known)."""
        if scene_id is None or self.times is None or self.moment is None:
            return "", 0.0
        other = self.times.get(scene_id)
        if other is None:
            return "", 0.0
        if other.lo > self.moment.hi:
            return "later", 0.0
        if other.hi > self.moment.hi:
            return "maybe", 0.0
        if other.hi < self.moment.lo and math.isfinite(other.hi) and math.isfinite(self.moment.lo):
            return "before", self.moment.lo - other.hi
        return "", 0.0

    def is_later(self, scene_id: str | None) -> bool:
        return self.in_time(scene_id)[0] == "later"

    def timed(self, item: ContextItem, scene_id: str | None) -> ContextItem:
        """The item weighed by the story's time as well as reading order (CTX-007), and,
        from a scene certainly later in the story's time, marked so the policy can leave
        it out or lower it (CTX-004)."""
        where, gap = self.in_time(scene_id)
        if where == "later":
            if self.later_policy is LaterInTime.LOWER:
                return replace(
                    ranked(item, [later_in_time_signal(item.score, certain=True)]), later=True
                )
            return replace(
                item,
                later=True,
                reason=f"{item.reason} It happens later in the story's time than this scene.",
            )
        if where == "maybe":
            return ranked(item, [later_in_time_signal(item.score, certain=False)])
        if where == "before":
            return ranked(item, [story_time_signal(item.score, gap, self.half_life, self.unit)])
        return item

    def distance(self, scene_id: str) -> int | None:
        """How many scenes back ``scene_id`` is from this one (negative: later)."""
        if self.position is None or scene_id not in self.place:
            return None
        return self.position - self.place[scene_id]

    def importance_of(self, item: ContextItem, about: list[str], names: dict[str, str]):
        """The strongest importance mark among the entries an item is about."""
        from backend.domain.context import IMPORTANCE_WEIGHT

        marked = [entity_id for entity_id in about if entity_id in self.importance]
        if not marked:
            return None
        best = max(
            marked,
            key=lambda entity_id: (
                abs(IMPORTANCE_WEIGHT[self.importance[entity_id]] - 1.0),
                entity_id,
            ),
        )
        return importance_signal(item.score, self.importance[best], names.get(best, "it"))

    def impact_of(self, *record_ids: str) -> Signal | None:
        for record_id in record_ids:
            found = self.impact.get(record_id)
            if found is not None:
                return Signal("continuity", found[1], found[0])
        return None


@dataclass
class _StoryWide:
    """What ranking works out over the whole story, once per line and version."""

    findings: list[Any] | None = None
    world: Any = None


def _usable(source: SourceText, scene_texts: set[str]) -> bool:
    """Whether a text may shape a draft in this line of the story."""
    return source.kind != SourceKind.SCENE or source.id in scene_texts


class ContextService:
    def __init__(
        self,
        *,
        reader: Any,
        plan: Any,
        rules: Any = None,
        sources: Any = None,
        passages: Any = None,
        passage_store: Any = None,
        manifests: Any = None,
        clocks: Any = None,
        tenancy: Any = None,
        journal: Any = None,
        studio: Any = None,
        local_mode: bool = False,
    ) -> None:
        self._reader = reader
        self._plan = plan
        self._rules = rules
        self._sources = sources
        self._passages = passages
        self._passage_store = passage_store
        self._manifests = manifests
        self._clocks = clocks
        self._tenancy = tenancy
        self._journal = journal
        # The scene studio's store: which scene texts belong to which line of the story.
        # Set after construction, because the studio is built after this service.
        self.studio = studio
        # Pictures: their accepted descriptions, choices and bytes (CTX-013). Set after
        # construction, because the media services are built after this one.
        self.pictures: Any = None
        # Search by meaning for pictures (area B's ``MediaSearchService``); None leaves
        # the scene's context to explicit links only.
        self.search: Any = None
        # Kept summaries of scenes' prose (CTX-006); set after construction, because the
        # summaries are built with the studio.
        self.summaries: Any = None
        self._local_mode = local_mode
        # How a scene's context is put together; tests may give another version of it.
        self.policy: ContextPolicy = SCENE_DRAFT_POLICY
        # (workspace, project, line, version, clock) -> whole-story ranking inputs.
        self._story_wide: OrderedDict[tuple[Any, ...], _StoryWide] = OrderedDict()

    async def _authorize(
        self, scope: AccessScope, workspace_id: str, project_id: str, permission: Permission
    ) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(scope, workspace_id, project_id, permission)

    async def _scene(
        self, scope: AccessScope, workspace_id: str, project_id: str, plan_id: str
    ) -> PlanItem:
        scene = await self._plan.get(scope, workspace_id, project_id, plan_id)
        if scene.kind is not PlanKind.SCENE:
            raise ValidationError("Only a scene has something to write.")
        return scene  # type: ignore[no-any-return]

    # ------------------------------------------------------------------ preview

    async def for_scene(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        *,
        budget: int | None = None,
        include_secrets: bool | None = None,
    ) -> SceneContext:
        """What the assistant would be given for this scene now, and what it would not."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        scene = await self._scene(scope, workspace_id, project_id, plan_id)
        settings = context_settings(scene.details)
        candidates, pov = await self.candidates(workspace_id, project_id, scene)
        # A picture's own choice (AST-007): one to send is kept like a pin, one to leave
        # out is left out like an exclusion; the scene's own pins and exclusions still win.
        chosen_pins = [
            item.key
            for item in candidates
            if item.media is not None
            and item.media.choice is ContextChoice.SEND
            and item.key not in settings.excludes
        ]
        chosen_out = [
            item.key
            for item in candidates
            if item.media is not None and item.media.choice is ContextChoice.LEAVE_OUT
        ]
        manifest = assemble(
            candidates,
            policy=self.policy,
            budget=budget if budget is not None else settings.budget,
            pins=[*settings.pins, *chosen_pins],
            excludes=[*settings.excludes, *chosen_out],
            include_secrets=settings.include_secrets
            if include_secrets is None
            else include_secrets,
        )
        index = None
        semantic = False
        if self._passages is not None:
            index = await self._passages.status(workspace_id, project_id)
            semantic = index.semantic
        return SceneContext(
            scene=scene,
            point_of_view=pov,
            settings=settings,
            manifest=manifest,
            index=index,
            semantic=semantic,
        )

    async def set_settings(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        *,
        settings: ContextSettings,
        expected_revision: int,
    ) -> PlanItem:
        """Keep the author's pins, exclusions, budget and choice about secrets with the scene."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        scene = await self._scene(scope, workspace_id, project_id, plan_id)
        overlap = set(settings.pins) & set(settings.excludes)
        if overlap:
            raise ValidationError("Something cannot be both always used and never used.")
        _result, item = await self._plan._set_scene_details(  # noqa: SLF001 - same plan path
            scope,
            workspace_id,
            project_id,
            scene.id,
            lambda details: with_context_settings(details, settings),
            expected_revision,
        )
        return item  # type: ignore[no-any-return]

    async def keep(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        context: SceneContext,
        *,
        purpose: str,
        delivery: Delivery | None = None,
    ) -> str:
        """Record exactly what was given, so what came of it can be traced back; with
        ``delivery``, how each picture reached the model (CTX-013)."""
        if self._manifests is None:
            raise ValidationError("Context cannot be kept here.")
        manifest_id = f"ctx_{uuid7()}"
        manifest = context.manifest
        await self._manifests.save(
            workspace_id=workspace_id,
            project_id=project_id,
            manifest_id=manifest_id,
            branch_id=working_branch_id(),
            purpose=purpose,
            plan_id=context.scene.id,
            policy_version=manifest.policy_version,
            checksum=manifest.checksum,
            words=manifest.words,
            budget=manifest.budget,
            manifest=stored_manifest(manifest, context.point_of_view, delivery),
            created_by=scope.actor_user_id,
        )
        return manifest_id

    # ---------------------------------------------------------------- comparing

    async def kept_for_scene(
        self, scope: AccessScope, workspace_id: str, project_id: str, plan_id: str
    ) -> list[KeptContext]:
        """The versions of this scene's prose that were kept from a draft, newest first.

        Only the line of the story being worked in: a what-if's scene text is its own.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        scene = await self._scene(scope, workspace_id, project_id, plan_id)
        if self._manifests is None or self._sources is None:
            return []
        _usable, own = await self._scene_texts(workspace_id, project_id, scene)
        if own is None:
            return []
        try:
            _source, _current, versions = await self._sources.version(workspace_id, project_id, own)
        except NotFoundError:
            return []
        kept: list[KeptContext] = []
        for version in reversed(versions):
            if not version.manifest_id:
                continue
            saved = await self._manifests.get(
                workspace_id=workspace_id, project_id=project_id, manifest_id=version.manifest_id
            )
            if saved is None:
                continue
            kept.append(
                KeptContext(
                    manifest_id=saved.id,
                    version_id=version.id,
                    version_number=version.number,
                    kept_at=version.created_at,
                    words=saved.words,
                )
            )
        return kept

    async def compare_kept(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        manifest_id: str,
    ) -> ContextComparison:
        """The context now against the one kept for a draft of this scene.

        A kept context is read only for its own scene and its own line of the story: one
        made in this line, or one a version of this scene's prose here still points to
        (a what-if started after the draft was kept carries that version with it).
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        scene = await self._scene(scope, workspace_id, project_id, plan_id)
        missing = NotFoundError("That kept context was not found for this scene.")
        if self._manifests is None:
            raise missing
        saved = await self._manifests.get(
            workspace_id=workspace_id, project_id=project_id, manifest_id=manifest_id
        )
        if saved is None or saved.plan_id != scene.id:
            raise missing
        if saved.branch_id != working_branch_id():
            carried = {
                kept.manifest_id
                for kept in await self.kept_for_scene(scope, workspace_id, project_id, plan_id)
            }
            if saved.id not in carried:
                raise missing
        now = await self.for_scene(scope, workspace_id, project_id, plan_id)
        return compare_contexts(now.manifest, saved)

    # --------------------------------------------------------------- candidates

    async def candidates(
        self, workspace_id: str, project_id: str, scene: PlanItem
    ) -> tuple[list[ContextItem], str | None]:
        # Which version of the story this is, read before the story: work kept for it is
        # never older than the version it is kept under.
        wide = await self._story_wide_for(workspace_id, project_id)
        story = await read_story(self._reader, workspace_id, project_id)
        entities: dict[str, CanonEntity] = story.entities
        names = {entity.id: entity.name for entity in entities.values()}
        beliefs = await self._reader.list_beliefs(
            workspace_id=workspace_id, project_id=project_id, limit=10_000
        )
        items_all = await self._reader.list_plan_items(  # type: ignore[call-arg]
            workspace_id=workspace_id, project_id=project_id
        )
        plan_by_id = {item.id: item for item in items_all}
        unit = "year"
        if self._clocks is not None:
            unit = (
                await self._clocks.get_unit(workspace_id=workspace_id, project_id=project_id)
                or "year"
            )
        times = event_times(story.events)
        event_names = {event.id: event.name for event in story.events}
        moment: Interval | None = time_of(scene_when(scene.details), times)

        pov = next(iter(scene.refs_as("point_of_view")), None)
        cast: dict[str, str] = {}
        for ref in scene.refs:
            if ref.entity_id in entities and ref.entity_id not in cast:
                cast[ref.entity_id] = ref.role
        out: list[ContextItem] = []
        order = reading_order(items_all)
        position = next((i for i, item in enumerate(order) if item.id == scene.id), None)
        rules = (
            await self._rules.list_rules(workspace_id=workspace_id, project_id=project_id)
            if self._rules is not None
            else []
        )
        ranking = _Ranking(
            importance={
                entity.id: level
                for entity in entities.values()
                if (level := clean_importance(entity.attributes.get(IMPORTANCE_KEY)))
            },
            impact=await self._impact(
                workspace_id,
                project_id,
                story,
                items_all,
                order,
                position,
                cast,
                beliefs,
                rules,
                wide,
            ),
            place={item.id: index for index, item in enumerate(order)},
            position=position,
            later_policy=self.policy.later_in_story_time,
            unit=unit,
        )
        if moment is not None:
            # The second clock (CTX-007): every scene placed in the story's time; prose
            # loses half its weight every quarter of the time the placed scenes span.
            ranking.moment = moment
            ranking.times = {item.id: time_of(scene_when(item.details), times) for item in order}
            finite = [
                interval
                for interval in ranking.times.values()
                if interval is not None
                and math.isfinite(interval.lo)
                and math.isfinite(interval.hi)
            ]
            if len(finite) >= 2:
                span = max(i.hi for i in finite) - min(i.lo for i in finite)
                ranking.half_life = span * STORY_TIME_SHARE if span > 0 else None

        # The scene itself: always given, last in the rendering.
        when = describe(scene_when(scene.details), unit=unit, event_name=event_names.get)
        text = f"{scene.title}."
        if scene.summary:
            text += f" {scene.summary}"
        if scene_when(scene.details) is not None:
            text += f" It happens {when}."
        notes = plain_details(scene.details)
        if notes:
            text += " " + " ".join(f"{key}: {value}." for key, value in notes.items())
        out.append(
            ContextItem(
                key=f"scene:{scene.id}",
                section=ContextSection.SCENE,
                text=text,
                reason="This is the scene to write.",
                authority="plan",
                record_type="plan_item",
                record_id=scene.id,
                score=100,
                hard=True,
            )
        )
        intent = scene_intent(scene.details)
        if intent is not None:
            # What the viewpoint character wants, what is in the way, and how it ends:
            # the question the scene asks and the answer the author chose.
            out.append(
                ContextItem(
                    key=f"intent:{scene.id}",
                    section=ContextSection.SCENE,
                    text=intent_sentences(intent, names.get(pov) if pov else None),
                    reason="You said what the viewpoint character wants here and how it ends.",
                    authority="plan",
                    record_type="plan_item",
                    record_id=scene.id,
                    score=95,
                    hard=True,
                )
            )
        for index, condition in enumerate(scene_conditions(scene.details)):
            # What must be true first and what the scene should leave true (STY-005):
            # required ones are never cut for length; preferred ones may be.
            required = condition.strength == "hard"
            lead = {
                ("before", True): "Must be true before this scene",
                ("before", False): "Better true before this scene, though not needed",
                ("after", True): "This scene must leave this true",
                ("after", False): "This scene would ideally leave this true",
            }[(condition.when, required)]
            out.append(
                ContextItem(
                    key=f"condition:{scene.id}:{index}",
                    section=ContextSection.SCENE,
                    text=f"{lead}: {condition_reading(condition, names).rstrip('.')}.",
                    reason=(
                        "You said this must hold around the scene."
                        if required
                        else "You said you would like this to hold around the scene."
                    ),
                    authority="plan",
                    record_type="plan_item",
                    record_id=scene.id,
                    score=90 if required else 75,
                    hard=required,
                )
            )
        facts_by_id = {fact.id: fact for fact in story.facts}
        # What the viewpoint character knows by this scene: a secret they lack, or only
        # learn later (KNW-002, CON-006), stays the author's alone.
        if pov:
            placed = StoryOrder(items_all, story.events)
            beliefs = held_at(pov, scene.id, beliefs, placed.by_id, placed.where)
        known = knowledge_of(pov, story.facts, beliefs) if pov else None
        known_ids = known.known_ids() if known else set()
        # The viewpoint character's arc, where this scene moves it (STY-006): the stage
        # going in and the stage reached, so the change happens on the page.
        for move in moves_in_scene(items_all, scene.id):
            if pov is None or pov not in move.about:
                continue
            who = names.get(pov, "The viewpoint character")
            text = f"{who}'s arc “{move.arc_title}”: "
            if move.current is not None:
                text += f"going in, {who} is at “{move.current}”; in this scene, "
            else:
                text += "in this scene, "
            text += f"{who} reaches “{move.reached}”."
            tied = facts_by_id.get(move.fact_id) if move.fact_id else None
            if tied is not None and tied.closed_revision is not None:
                tied = None
            if tied is not None and _hidden(tied, pov, known_ids):
                # A secret the viewpoint does not know is not an instruction for the page:
                # it is kept apart, for the author, like any other secret.
                secret_text, secret_reason = _secret_words(
                    f"From here on: {_sentence(tied.proposition, names)}", pov, names
                )
                out.append(
                    ContextItem(
                        key=f"arc:{scene.id}:{move.arc_id}:fact",
                        section=ContextSection.SECRETS,
                        text=secret_text,
                        reason=f"The stage {who} reaches in this scene is tied to it. "
                        + secret_reason,
                        authority="plan",
                        record_type="fact",
                        record_id=tied.id,
                        score=5,
                    )
                )
            elif tied is not None:
                text += f" From here on: {_sentence(tied.proposition, names)}."
            out.append(
                ContextItem(
                    key=f"arc:{scene.id}:{move.arc_id}",
                    section=ContextSection.SCENE,
                    text=text,
                    reason=f"You said this scene moves {who}'s arc.",
                    authority="plan",
                    record_type="plan_item",
                    record_id=move.arc_id,
                    score=88,
                    hard=True,
                )
            )
        for reveal in scene_reveals(scene.details):
            fact = facts_by_id.get(reveal.fact_id)
            if fact is None:
                continue
            how = "shows the reader" if reveal.how == "shown" else "only hints at"
            out.append(
                ContextItem(
                    key=f"reveal:{scene.id}:{fact.id}",
                    section=ContextSection.SCENE,
                    text=f"This scene {how}: {_sentence(fact.proposition, names)}.",
                    reason="You said the reader learns this here.",
                    authority="plan",
                    record_type="fact",
                    record_id=fact.id,
                    score=85,
                    hard=True,
                )
            )

        # Who and what is in it.
        for entity_id, role in cast.items():
            entity = entities[entity_id]
            details = plain_details_of(entity.attributes)
            text = f"{entity.name} ({entity.type.lower()})"
            if entity.aliases:
                text += f", also called {', '.join(entity.aliases)}"
            if details:
                text += ": " + "; ".join(f"{key} {value}" for key, value in details.items())
            out.append(
                ContextItem(
                    key=f"entity:{entity_id}",
                    section=ContextSection.CAST,
                    text=text + ".",
                    reason=f"{entity.name} {ROLE_WORDS.get(role, 'is in the scene')}.",
                    authority="entry",
                    record_type="entity",
                    record_id=entity_id,
                    score=ROLE_SCORE.get(role, 5.0),
                    hard=role in ("point_of_view", "place"),
                )
            )

        # What is true of them at the scene's time; secrets the viewpoint does not know apart.
        linked: dict[str, int] = {}
        for fact in story.facts:
            if fact.closed_revision is not None:
                continue
            ends = [fact.proposition.subject_id]
            target = getattr(fact.proposition.object, "entity_id", None)
            if target:
                ends.append(target)
            inside = [entity_id for entity_id in ends if entity_id in cast]
            if not inside:
                continue
            truth = Truth.TRUE
            if moment is not None:
                truth = true_at(fact_span(fact, times), moment)
                if truth is Truth.FALSE:
                    continue
            sentence = _sentence(fact.proposition, names)
            if truth is Truth.MAYBE:
                sentence += " (around this time; when it starts or ends is not certain)"
            about = names.get(inside[0], "someone in the scene")
            # With no viewpoint chosen, nobody's knowledge says a secret may be on the
            # page, so every secret is the author's alone.
            if _hidden(fact, pov, known_ids):
                text, reason = _secret_words(sentence, pov, names)
                out.append(
                    ContextItem(
                        key=f"fact:{fact.id}",
                        section=ContextSection.SECRETS,
                        text=text,
                        reason=reason,
                        authority="fact",
                        record_type="fact",
                        record_id=fact.id,
                        score=4 + len(inside),
                    )
                )
                continue
            # Only an ordinary or common fact makes a neighbour worth naming: a secret's
            # other end, named on its own, would hint at the secret.
            for entity_id in ends:
                if entity_id not in cast:
                    linked[entity_id] = linked.get(entity_id, 0) + 1
            out.append(
                ContextItem(
                    key=f"fact:{fact.id}",
                    section=ContextSection.TRUE,
                    text=sentence + ".",
                    reason=f"It is about {about}, who is in the scene.",
                    authority="fact",
                    record_type="fact",
                    record_id=fact.id,
                    score=5 + len(inside) + (1 if fact.visibility is FactVisibility.COMMON else 0),
                )
            )
        # Entries one step away: named so the facts above make sense.
        for entity_id, count in sorted(linked.items(), key=lambda pair: (-pair[1], pair[0]))[:20]:
            neighbour = entities.get(entity_id)
            if neighbour is None or not neighbour.is_active:
                continue
            out.append(
                ContextItem(
                    key=f"entity:{entity_id}",
                    section=ContextSection.CAST,
                    text=f"{neighbour.name} ({neighbour.type.lower()}).",
                    reason=f"Someone in the scene is connected to {neighbour.name}.",
                    authority="entry",
                    record_type="entity",
                    record_id=entity_id,
                    score=2 + min(count, 3) * 0.5,
                )
            )

        # What the viewpoint character knows and gets wrong.
        if known is not None and pov is not None:
            who = names.get(pov, "The viewpoint character")
            for belief, fact in known.wrong:
                out.append(
                    ContextItem(
                        key=f"belief:{belief.id}",
                        section=ContextSection.WRONG,
                        text=(
                            f"{who} believes {_sentence(belief.proposition, names)}; "
                            f"in truth {_sentence(fact.proposition, names)}."
                        ),
                        reason=f"{who} sees the scene, and is wrong about this.",
                        authority="belief",
                        record_type="belief",
                        record_id=belief.id,
                        score=7,
                    )
                )
            for belief in known.unconfirmed:
                out.append(
                    ContextItem(
                        key=f"belief:{belief.id}",
                        section=ContextSection.KNOWS,
                        text=f"{who} believes {_sentence(belief.proposition, names)}.",
                        reason=f"{who} sees the scene; the story does not say whether it is so.",
                        authority="belief",
                        record_type="belief",
                        record_id=belief.id,
                        score=5,
                    )
                )
            for fact, _how in known.knows:
                if fact.visibility is FactVisibility.SECRET:
                    out.append(
                        ContextItem(
                            key=f"knows:{fact.id}",
                            section=ContextSection.KNOWS,
                            text=f"{who} knows a secret: {_sentence(fact.proposition, names)}.",
                            reason=f"{who} sees the scene and holds this secret.",
                            authority="belief",
                            record_type="fact",
                            record_id=fact.id,
                            score=6,
                        )
                    )

        # What has happened, nearest the scene's time first.
        involved = [
            event
            for event in story.events
            if any(part.entity_id in cast for part in event.participants)
            or any(location in cast for location in event.location_ids)
        ]
        for event in involved[:MAX_EVENTS]:
            interval = times[event.id].interval
            if moment is not None and interval is not None:
                if compare(interval, moment) is Order.AFTER:
                    continue
            words = describe(event.when, unit=unit, event_name=event_names.get)
            closeness = 0.0
            if moment is not None and interval is not None:
                closeness = 1 / (1 + abs(moment.lo - interval.hi))
            out.append(
                ContextItem(
                    key=f"event:{event.id}",
                    section=ContextSection.HAPPENED,
                    text=f"{event.name} ({words}).",
                    reason="Someone or somewhere in the scene was part of it.",
                    authority="event",
                    record_type="event",
                    record_id=event.id,
                    score=3 + 3 * closeness,
                )
            )

        # Where it sits in the plan.
        parent_id = scene.parent_id
        depth = 0
        while parent_id and parent_id in plan_by_id and depth < 5:
            parent = plan_by_id[parent_id]
            if parent.is_active:
                summary = f" {parent.summary}" if parent.summary else ""
                out.append(
                    ContextItem(
                        key=f"plan:{parent.id}",
                        section=ContextSection.PLAN,
                        text=f"Part of the {parent.kind.value} “{parent.title}”.{summary}",
                        reason=f"The scene sits under this {parent.kind.value}.",
                        authority="plan",
                        record_type="plan_item",
                        record_id=parent.id,
                        score=6 - depth,
                    )
                )
            parent_id = parent.parent_id
            depth += 1
        for thread_id, verb in (
            [(t, "opens") for t in scene.sets_up]
            + [(t, "moves") for t in thread_advances(scene.details)]
            + [(t, "resolves") for t in scene.pays_off]
        ):
            thread = plan_by_id.get(thread_id)
            if thread is not None and thread.is_active:
                along = " along" if verb == "moves" else ""
                out.append(
                    ContextItem(
                        key=f"thread:{scene.id}:{thread.id}",
                        section=ContextSection.PLAN,
                        text=f"This scene {verb} the thread “{thread.title}”{along}.",
                        reason=f"You said the scene {verb} it{along}.",
                        authority="plan",
                        record_type="plan_item",
                        record_id=thread.id,
                        score=8,
                    )
                )
        if position is not None and position > 0:
            before = order[position - 1]
            summary = f" {before.summary}" if before.summary else ""
            seen_by = next(iter(before.refs_as("point_of_view")), None)
            other_eyes = ""
            if pov and seen_by and seen_by != pov:
                # In the words given, so the assistant knows it too.
                other_eyes = (
                    f" It is seen through {names.get(seen_by, 'someone else')}; "
                    f"{names.get(pov, 'the viewpoint character')} may not know all of it."
                )
            out.append(
                ContextItem(
                    key=f"plan:{before.id}",
                    section=ContextSection.PLAN,
                    text=f"The scene before: “{before.title}”.{summary}{other_eyes}",
                    reason="It comes just before this one." + other_eyes,
                    authority="plan",
                    record_type="plan_item",
                    record_id=before.id,
                    score=6,
                )
            )

        # What the reader has already been shown, before this scene.
        if position is not None and position > 0:
            steps = reader_journey(order[: position + 1], story.facts, beliefs)
            shown_before = steps[position - 1].reader_knows if position >= 1 else set()
            for fact_id in sorted(shown_before):
                fact = facts_by_id.get(fact_id)
                if fact is None:
                    continue
                if _hidden(fact, pov, known_ids):
                    # The reader knowing a secret does not mean the viewpoint character
                    # does: it stays with the author, like any other secret.
                    text, reason = _secret_words(
                        f"An earlier scene showed the reader that {_sentence(fact.proposition, names)}",
                        pov,
                        names,
                    )
                    out.append(
                        ContextItem(
                            key=f"reader:{fact_id}",
                            section=ContextSection.SECRETS,
                            text=text,
                            reason="An earlier scene showed the reader this. " + reason,
                            authority="reader",
                            record_type="fact",
                            record_id=fact_id,
                            score=4,
                        )
                    )
                    continue
                out.append(
                    ContextItem(
                        key=f"reader:{fact_id}",
                        section=ContextSection.READER,
                        text=f"{_sentence(fact.proposition, names)}.",
                        reason="An earlier scene already showed the reader this.",
                        authority="reader",
                        record_type="fact",
                        record_id=fact_id,
                        score=4,
                    )
                )

        # The story's rules.
        if rules:
            types = {entities[e].type for e in cast}
            for rule in rules:
                if rule.status is not RuleStatus.ON:
                    continue
                reading = rule.sentence or read_rule(rule.body, names)
                applies = (
                    rule.body.applies_to.type in types or rule.body.applies_to.entity_id in cast
                )
                out.append(
                    ContextItem(
                        key=f"rule:{rule.id}",
                        section=ContextSection.RULES,
                        text=reading.rstrip(".") + ".",
                        reason="It is one of your story rules"
                        + (" and is about someone or something in the scene." if applies else "."),
                        authority="rule",
                        record_type="story_rule",
                        record_id=rule.id,
                        score=6 if applies else 3,
                    )
                )

        # Pictures on the scene and of who and what is in it (AST-004, CTX-013).
        out.extend(
            await self._pictures(
                workspace_id,
                project_id,
                scene,
                cast,
                names,
                pov=pov,
                moment=moment,
                times=times,
                unit=unit,
                event_names=event_names,
                known_ids=known_ids,
            )
        )

        # Earlier scenes, as summaries and event blocks, and this scene's own events
        # (CTX-006); the end of the scene before, as written (CTX-006A).
        out.extend(
            await self._earlier(
                workspace_id,
                project_id,
                scene,
                order,
                position,
                story.events,
                names,
                pov,
                ranking,
                items_all,
            )
        )

        # The author's own writing that names the scene's people and places, and
        # passages from other scenes on the same threads.
        out.extend(
            await self._writing(
                workspace_id, project_id, scene, cast, names, ranking, plan_by_id, order, pov
            )
        )
        out = [self._signals(item, ranking, facts_by_id, cast, names) for item in out]
        # Many near-copies of one passage say one thing many times: each passage of the
        # author's writing is ranked against those above it (CTX-007).
        # A passage inside the end of the scene before (or a nearby scene) is already given
        # as such. When that end is left out for happening later in the story's time, it is
        # listed once under Left out with its own "Use it"; its passages are not listed again.
        tails = {
            item.record_id: int(item.location.split(":")[1].split("-")[0])
            for item in out
            if item.section in (ContextSection.BEFORE, ContextSection.NEARBY) and item.location
        }
        out = [
            item
            for item in out
            if not (
                item.section is ContextSection.WRITING
                and item.record_id in tails
                and item.location
                and int(item.location.split(":")[1].split("-")[0]) >= tails[item.record_id]
            )
        ]
        prose = [item for item in out if item.authority == "writing"]
        rest = [item for item in out if item.authority != "writing"]
        return rest + diversify(prose), pov

    @staticmethod
    def _signals(
        item: ContextItem,
        ranking: _Ranking,
        facts: dict[str, Any],
        cast: dict[str, str],
        names: dict[str, str],
    ) -> ContextItem:
        """The author's importance and open continuity findings, on items about the story's
        records (CTX-007). The scene's own items and hard items are given whatever their
        score, so they are left as they are; passages are ranked where they are found."""
        if item.section is ContextSection.SCENE or item.hard or item.authority == "writing":
            return item
        about: list[str] = []
        related: list[str] = [item.record_id]
        if item.record_type == "entity":
            about = [item.record_id]
        elif item.record_type == "fact" and item.record_id in facts:
            proposition = facts[item.record_id].proposition
            ends = [proposition.subject_id, getattr(proposition.object, "entity_id", None)]
            about = [entity_id for entity_id in ends if entity_id and entity_id in cast]
        signals: list[Signal | None] = []
        if item.section is not ContextSection.SECRETS:
            signals.append(ranking.importance_of(item, about, names))
        signals.append(ranking.impact_of(*related))
        return ranked(item, signals)

    async def _impact(
        self,
        workspace_id: str,
        project_id: str,
        story: Any,
        items: list[PlanItem],
        order: list[PlanItem],
        position: int | None,
        cast: dict[str, str],
        beliefs: list[Any],
        rules: list[Any],
        wide: _StoryWide | None = None,
    ) -> dict[str, tuple[str, float]]:
        """Records with an open continuity warning at or near this scene, and entries a
        story rule does not hold for (CTX-007): record id -> (why, by how much).

        The whole-story work (every continuity finding, the rules' world) is done once per
        line of the story and version of it, and reused by every context built there."""
        found: dict[str, tuple[str, float]] = {}
        wide = wide or _StoryWide()
        if position is not None and any(scene_when(item.details) is not None for item in order):
            from backend.services.continuity_service import story_continuity

            near = {
                item.id
                for item in order[max(0, position - NEAR_SCENES) : position + NEAR_SCENES + 1]
            }
            if wide.findings is None:
                try:
                    wide.findings = list(
                        await story_continuity(
                            self._reader,
                            self._clocks,
                            workspace_id,
                            project_id,
                            story=story,
                            items=items,
                        )
                    )
                except Exception:  # noqa: BLE001 - ranking is extra; the context stands without it
                    wide.findings = []
            for finding in wide.findings:
                if finding.severity != "warning" or not near & set(finding.scene_ids):
                    continue
                words = _trim(finding.message, FINDING_WORDS).rstrip(".")
                note = f"There is a continuity warning about it near this scene: {words}."
                for record in finding.records:
                    found.setdefault(record.id, (note, CONTINUITY_BOOST))
                    if record.entry_id:
                        found.setdefault(record.entry_id, (note, CONTINUITY_BOOST))
        active = [rule for rule in rules if rule.status is RuleStatus.ON]
        if active and cast:
            if wide.world is None:
                wide.world = world_from_canon(
                    story.entities.values(), story.facts, beliefs, story.events
                )
            world = wide.world
            for rule in active:
                broken = violations(rule.id, rule.body, world, only=list(cast), limit=5)
                if not broken:
                    continue
                who = _listing([item.entity_name for item in broken[:3]])
                found.setdefault(
                    rule.id,
                    (f"It does not hold for {who}, who is in the scene.", RULE_BOOST),
                )
                for item in broken:
                    found.setdefault(
                        item.entity_id,
                        ("One of your story rules does not hold for it.", RULE_BOOST),
                    )
        return found

    async def _story_wide_for(self, workspace_id: str, project_id: str) -> _StoryWide:
        """The whole-story ranking inputs for the line worked in at its version now.

        Keyed by the line, its version and the story clock's settings, so any change to
        the story, its plan or its clock makes a new entry; where the version cannot be
        read, nothing is kept."""
        if self._journal is None or not hasattr(self._reader, "get_head"):
            return _StoryWide()
        try:
            branch = await self._journal.working_branch(
                workspace_id=workspace_id, project_id=project_id
            )
            head = await self._reader.get_head(
                workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
            )
            clock: tuple[Any, ...] = ()
            if self._clocks is not None:
                from backend.services.continuity_service import how_long_of, unit_of

                _, how_long_version = await how_long_of(self._clocks, workspace_id, project_id)
                clock = (await unit_of(self._clocks, workspace_id, project_id), how_long_version)
        except Exception:  # noqa: BLE001 - nothing kept; worked out afresh
            return _StoryWide()
        key = (workspace_id, project_id, str(branch.id), int(head), *clock)
        wide = self._story_wide.get(key)
        if wide is None:
            wide = self._story_wide[key] = _StoryWide()
            while len(self._story_wide) > STORY_WIDE_KEPT:
                self._story_wide.popitem(last=False)
        else:
            self._story_wide.move_to_end(key)
        return wide

    async def _pictures(
        self,
        workspace_id: str,
        project_id: str,
        scene: PlanItem,
        cast: dict[str, str],
        names: dict[str, str],
        *,
        pov: str | None,
        moment: Interval | None,
        times: dict[str, Any],
        unit: str,
        event_names: dict[str, str],
        known_ids: set[str] | None = None,
    ) -> list[ContextItem]:
        """One item per picture: those on the scene, and those of who and what is in it.

        A link that applies only from a later story time is left out; one whose start
        the scene's time cannot settle is given with a note. A secret picture (AST-004)
        reaches the page only when the viewpoint character is among those who know what
        it shows; otherwise it sits with the secrets, for the author, and is never sent
        as a picture. A picture is one item however many of the scene's people it shows,
        so a portrait and its description are one piece of evidence, not two."""
        links = await self._reader.list_links(
            workspace_id=workspace_id, project_id=project_id, limit=500
        )
        found: dict[str, dict[str, Any]] = {}
        # Pictures of the scene left out by its story time: never brought back by meaning.
        skipped: set[str] = set()
        for link in links:
            if not link.is_active:
                continue
            on_scene = link.target_record_type == SCENE_TARGET and link.target_record_id == scene.id
            of_cast = link.target_record_type != SCENE_TARGET and link.target_record_id in cast
            if not (on_scene or of_cast):
                continue
            note = ""
            # AST-001: a link applies for a window of story time, from and/or until.
            truth = link_applies_at(link, moment, anchor_lookup(times))
            if truth is not None:
                window = link_window_words(link, unit=unit, event_name=event_names.get)
                if truth is Truth.FALSE:
                    skipped.add(link.asset_id)
                    continue
                if truth is Truth.MAYBE and window:
                    note = (
                        f" It applies {window}; the scene has no time, so it may not apply"
                        + (" yet." if link.applies_until is None else " then.")
                        if moment is None
                        else f" It applies {window}, around the scene's time."
                    )
            entry = found.setdefault(
                link.asset_id,
                {"targets": [], "scene": False, "secret": False, "notes": [], "versions": set()},
            )
            # AST-001: a link may show one version of the picture (None is its newest).
            entry["versions"].add(link.asset_version_id if link.shows_version else None)
            if on_scene:
                entry["scene"] = True
            else:
                entry["targets"].append(names.get(link.target_record_id, "someone in the scene"))
            if link.is_secret and (pov is None or pov not in link.known_by):
                entry["secret"] = True
            if note and note not in entry["notes"]:
                entry["notes"].append(note)
        similar = await self._similar_pictures(
            workspace_id, project_id, scene, pov, set(found) | skipped, known_ids or set(), links
        )
        if not found and not similar:
            return []
        pictures: dict[str, Any] = {}
        if self.pictures is not None:
            pinned = {
                asset_id: next(iter(entry["versions"]))
                for asset_id, entry in found.items()
                if len(entry["versions"]) == 1 and None not in entry["versions"]
            }
            pictures = await self.pictures.for_context(
                workspace_id, project_id, [*found, *similar], pinned=pinned
            )
        out: list[ContextItem] = []
        left_out: dict[str, str] = getattr(pictures, "left_out", {})
        for asset_id, entry in found.items():
            picture = pictures.get(asset_id)
            if self.pictures is not None and picture is None:
                # Retired, or no current version: nothing to show or send. One whose
                # chosen version is no longer shown is named, never swapped for another.
                if asset_id in left_out and not entry["secret"]:
                    targets = list(dict.fromkeys(entry["targets"]))
                    out.append(
                        ContextItem(
                            key=f"picture:{asset_id}",
                            section=ContextSection.PICTURES,
                            text=(
                                f"A picture of {_listing(targets) if targets else 'this scene'} "
                                "is left out."
                            ),
                            reason=left_out[asset_id],
                            authority="picture",
                            record_type="asset",
                            record_id=asset_id,
                            score=0.5,
                        )
                    )
                continue
            targets = list(dict.fromkeys(entry["targets"]))
            shows = _listing(targets) if targets else "this scene"
            title = f"“{picture.asset.name}”" if picture is not None else "a picture"
            label = f"{title}, version {picture.version.version}" if picture is not None else title
            description = picture.description if picture is not None else ""
            notes = "".join(entry["notes"])
            if description:
                text = f"A picture of {shows} ({title}): {description}{notes}"
            else:
                text = (
                    f"There is a picture of {shows} ({title}); it has no accepted "
                    f"description yet.{notes}"
                )
            why = "You put it on this scene." if entry["scene"] else f"It shows {shows}."
            key = f"picture:{asset_id}"
            # A version marked secret is kept from every point of view, as search keeps it.
            secret_version = picture is not None and picture.version.visibility == "secret"
            if entry["secret"] or secret_version:
                who = names.get(pov) if pov else None
                if entry["secret"]:
                    secret_reason = (
                        f"It is a secret picture {who or 'the viewpoint character'} does not know."
                        if pov
                        else "It is a secret picture, and no point of view is chosen for the scene."
                    )
                else:
                    secret_reason = "This version of the picture is marked secret."
                out.append(
                    ContextItem(
                        key=key,
                        section=ContextSection.SECRETS,
                        text=text,
                        reason=f"{why} {secret_reason} It is never sent as a picture.",
                        authority="picture",
                        record_type="asset",
                        record_id=asset_id,
                        score=3,
                    )
                )
                continue
            media = (
                self.pictures.context_part(picture, label=label, key=key)
                if picture is not None and self.pictures is not None
                else None
            )
            choice_words = ""
            if media is not None:
                choice_words = (
                    " "
                    + REPRESENTATION_WORDS[planned_representation(media.choice, bool(description))]
                )
            out.append(
                ContextItem(
                    key=key,
                    section=ContextSection.PICTURES,
                    text=text,
                    reason=why + choice_words,
                    authority="picture",
                    record_type="asset",
                    record_id=asset_id,
                    score=(3.0 if entry["scene"] else 1.5) + (0.5 if description else 0),
                    media=media,
                )
            )
        for asset_id, score in similar.items():
            picture = pictures.get(asset_id)
            # Found by meaning: only its accepted words are given, never the picture, and
            # never above a picture the author linked.
            if picture is None or not picture.description or self.pictures is None:
                continue
            if picture.version.visibility == "secret":
                continue
            key = f"picture:{asset_id}"
            media = self.pictures.context_part(
                picture,
                label=f"“{picture.asset.name}”, version {picture.version.version}",
                key=key,
            )
            # One the author chose to leave out stays out; one to send is only described.
            if media.choice is ContextChoice.LEAVE_OUT:
                continue
            if media.choice is ContextChoice.SEND:
                media = replace(media, choice=ContextChoice.DESCRIBE)
            out.append(
                ContextItem(
                    key=key,
                    section=ContextSection.PICTURES,
                    text=f"A picture like this scene (“{picture.asset.name}”): {picture.description}",
                    reason=(
                        "It was found by meaning, not linked by you, so only its accepted "
                        "description is given."
                    ),
                    authority="similar picture",
                    record_type="asset",
                    record_id=asset_id,
                    score=0.5 + min(max(score, 0.0), 1.0) * 0.4,
                    media=media,
                )
            )
        return out

    async def _similar_pictures(
        self,
        workspace_id: str,
        project_id: str,
        scene: PlanItem,
        pov: str | None,
        linked: set[str],
        known_ids: set[str],
        links: list[Any],
    ) -> dict[str, float]:
        """Pictures found by meaning for the scene (area B's search), behind every gate
        it applies, and this one too: a picture with a secret link the viewpoint is not
        among those who know is never suggested. Empty when search is not set up."""
        if self.search is None or self.pictures is None:
            return {}
        query = " ".join(part for part in (scene.title, scene.summary or "") if part).strip()
        if not query:
            return {}
        from backend.domain.embedding import SearchGate

        try:
            result = await self.search.pictures_for(
                workspace_id,
                project_id,
                query=query,
                gate=SearchGate(point_of_view=True, point_of_view_id=pov),
                limit=MAX_SIMILAR_PICTURES + len(linked),
            )
        except Exception:  # noqa: BLE001 - search by meaning is extra; links stand alone
            return {}
        secret = {
            link.asset_id
            for link in links
            if link.is_active and link.is_secret and (pov is None or pov not in link.known_by)
        }
        found: dict[str, float] = {}
        for candidate in result.pictures:
            if candidate.reason != "similar" or candidate.asset_id in linked:
                continue
            if candidate.asset_id in secret:
                continue
            found[candidate.asset_id] = float(candidate.score or 0.0)
            if len(found) >= MAX_SIMILAR_PICTURES:
                break
        return found

    async def deliver(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        context: SceneContext,
        limits: MediaLimits,
        *,
        sections: frozenset[ContextSection] | None = None,
    ) -> Any:
        """What a request made from this context carries for its pictures (CTX-013):
        each picture given, as the picture itself or its description, decided for the
        route the request uses. None when pictures are not set up here."""
        if self.pictures is None:
            return None
        parts = [
            item.media
            for item in context.manifest.items
            if item.media is not None and (sections is None or item.section in sections)
        ]
        return await self.pictures.deliver(scope, workspace_id, project_id, parts, limits)

    async def _line_links(self, workspace_id: str, project_id: str) -> dict[str, str]:
        """Scene id -> the text holding its prose, in the line of the story worked in."""
        if self.studio is None or self._journal is None:
            return {}
        branch = await self._journal.working_branch(
            workspace_id=workspace_id, project_id=project_id
        )
        links = await self.studio.manuscripts_for(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
        )
        return {plan_id: source_id for plan_id, source_id in links}

    async def _latest(self, workspace_id: str, project_id: str, source_id: str) -> Any:
        try:
            _, version, _ = await self._sources.version(workspace_id, project_id, source_id)
        except NotFoundError:
            return None
        return version

    async def _earlier(
        self,
        workspace_id: str,
        project_id: str,
        scene: PlanItem,
        order: list[PlanItem],
        position: int | None,
        events: list[Any],
        names: dict[str, str],
        pov: str | None,
        ranking: _Ranking,
        items: list[PlanItem] | None = None,
    ) -> list[ContextItem]:
        """Earlier scenes as summaries and event blocks, earlier arcs and beats as
        summaries, this scene's own events, the end of the scene before as written, and
        the ends of a few other recent scenes on the same viewpoint or thread (CTX-006,
        CTX-006A). Each is weighed by both clocks (CTX-007)."""
        out: list[ContextItem] = []
        by_scene: dict[str, list[Any]] = {}
        for event in events:
            if event.retired_revision is not None or event.kind in (CLAIM_KIND, INFERRED_KIND):
                continue
            if event.scene_id:
                by_scene.setdefault(event.scene_id, []).append(event)
        own = by_scene.get(scene.id, [])
        if own:
            listed = "; ".join(event.name.rstrip(".") for event in own[:BLOCK_EVENTS])
            more = len(own) - BLOCK_EVENTS
            out.append(
                ContextItem(
                    key=f"events:{scene.id}",
                    section=ContextSection.SCENE,
                    text=f"What you recorded happens in this scene: {listed}"
                    + (f"; and {count_words(more, 'more', 'more')}." if more > 0 else "."),
                    reason="You placed these events in this scene.",
                    authority="event",
                    record_type="plan_item",
                    record_id=scene.id,
                    score=80,
                )
            )
        if position is None or position == 0:
            return out
        earlier = [
            order[i] for i in range(position - 1, max(-1, position - 1 - EARLIER_SCENES), -1)
        ]
        links = await self._line_links(workspace_id, project_id)
        kept: dict[str, Any] = {}
        if self.summaries is not None:
            sources = [links[item.id] for item in earlier if item.id in links]
            kept = await self.summaries.kept_for(workspace_id, project_id, sources)
        who = names.get(pov) if pov else None
        for distance, before in enumerate(earlier, start=1):
            seen_by = next(iter(before.refs_as("point_of_view")), None)
            caveat: list[Signal | None] = []
            other_eyes = ""
            if pov and seen_by and seen_by != pov:
                other_eyes = (
                    f" It is seen through {names.get(seen_by, 'someone else')}; "
                    f"{who or 'the viewpoint character'} may not know all of it."
                )
            # The warning goes in the words given, not only the reason: the reason is
            # for the author, and the assistant reads only the text.
            eyes = f" ({other_eyes.strip()})" if other_eyes else ""
            source_id = links.get(before.id)
            summary = kept.get(source_id) if source_id else None
            if summary is not None:
                latest = await self._latest(workspace_id, project_id, summary.source_id)
                stale = latest is None or latest.id != summary.version_id
                by = {
                    "author": "your own summary",
                    "model": "a summary the assistant suggested and you kept",
                    "model_corrected": "a summary the assistant suggested and you corrected",
                }[summary.origin.value]
                item = ContextItem(
                    key=f"summary:{before.id}",
                    section=ContextSection.EARLIER,
                    text=f"“{before.title}”, as written: {summary.body}{eyes}",
                    reason=f"It is {by} of “{before.title}”.{other_eyes}",
                    authority="summary",
                    record_type="scene_summary",
                    record_id=summary.id,
                    score=5.0,
                )
                if stale:
                    caveat.append(
                        Signal(
                            "out of date",
                            -1.5,
                            "The scene has been rewritten since; this summary is of an "
                            "earlier version.",
                        )
                    )
                out.append(
                    ranking.timed(
                        ranked(item, [distance_signal(item.score, distance, ""), *caveat]),
                        before.id,
                    )
                )
            elif distance >= 2:
                card = plan_card_summary(before, names, seen_by)
                if card:
                    item = ContextItem(
                        key=f"summary:{before.id}",
                        section=ContextSection.EARLIER,
                        text=f"“{before.title}”, as planned: {card}{eyes}",
                        reason=f"It is what the plan card of “{before.title}” says happens."
                        + other_eyes,
                        authority="plan",
                        record_type="plan_item",
                        record_id=before.id,
                        score=4.0,
                    )
                    out.append(
                        ranking.timed(
                            ranked(item, [distance_signal(item.score, distance, "")]), before.id
                        )
                    )
            placed = by_scene.get(before.id, [])
            if placed:
                listed = "; ".join(event.name.rstrip(".") for event in placed[:BLOCK_EVENTS])
                more = len(placed) - BLOCK_EVENTS
                item = ContextItem(
                    key=f"events:{before.id}",
                    section=ContextSection.EARLIER,
                    text=f"In “{before.title}”: {listed}"
                    + (f"; and {count_words(more, 'more', 'more')}." if more > 0 else ".")
                    + eyes,
                    reason=f"These are the events you placed in “{before.title}”.{other_eyes}",
                    authority="event",
                    record_type="plan_item",
                    record_id=before.id,
                    score=4.5,
                )
                out.append(
                    ranking.timed(
                        ranked(item, [distance_signal(item.score, distance, "")]), before.id
                    )
                )
        # The end of the scene before, as written: local prose continuity.
        before = earlier[0]
        seen_by = next(iter(before.refs_as("point_of_view")), None)
        other_eyes = ""
        if pov and seen_by and seen_by != pov:
            other_eyes = (
                f" It is seen through {names.get(seen_by, 'someone else')}; "
                f"{who or 'the viewpoint character'} may not know all of it."
            )
        eyes = f" ({other_eyes.strip()})" if other_eyes else ""
        source_id = links.get(before.id)
        if source_id is not None and source_id != links.get(scene.id):
            version = await self._latest(workspace_id, project_id, source_id)
            if version is not None and version.content.strip():
                words, start = _tail(version.content, BEFORE_WORDS)
                # The scene before in reading order may happen later in the story's time
                # (a flash-forward): the policy applies to its end as to anything else.
                out.append(
                    ranking.timed(
                        ContextItem(
                            key=f"before:{before.id}",
                            section=ContextSection.BEFORE,
                            text=f"From the end of “{before.title}”: {words}{eyes}",
                            reason=f"It is how the scene before, “{before.title}”, ends, as "
                            f"you wrote it.{other_eyes}",
                            authority="writing",
                            record_type="source_version",
                            record_id=version.id,
                            location=f"chars:{start}-{len(version.content)}",
                            score=7.0,
                        ),
                        before.id,
                    )
                )
        out.extend(
            await self._nearby(
                workspace_id,
                project_id,
                scene,
                order,
                position,
                links,
                names,
                pov,
                ranking,
                {item.id: item for item in items or []},
            )
        )
        if items is not None:
            out.extend(
                await self._parts(
                    workspace_id, project_id, scene, order, position, items, names, pov, ranking
                )
            )
        return out

    async def _nearby(
        self,
        workspace_id: str,
        project_id: str,
        scene: PlanItem,
        order: list[PlanItem],
        position: int,
        links: dict[str, str],
        names: dict[str, str],
        pov: str | None,
        ranking: _Ranking,
        plan_by_id: dict[str, PlanItem] | None = None,
    ) -> list[ContextItem]:
        """The ends of up to ``NEARBY_SCENES`` other recent scenes, as written (CTX-006A):
        from two to ``NEARBY_WINDOW`` scenes back, seen through the same eyes or on one of
        this scene's threads, nearest first. Under the budget like everything else; one
        later in the story's time is left out or lowered by the policy (CTX-004)."""
        threads = self._threads_of(scene)
        own = links.get(scene.id)
        before = links.get(order[position - 1].id) if position >= 1 else None
        out: list[ContextItem] = []
        who = names.get(pov) if pov else None
        for distance in range(2, NEARBY_WINDOW + 1):
            if len(out) >= NEARBY_SCENES or position - distance < 0:
                break
            other = order[position - distance]
            source_id = links.get(other.id)
            if source_id is None or source_id in (own, before):
                continue
            seen_by = next(iter(other.refs_as("point_of_view")), None)
            same_eyes = bool(pov and seen_by == pov)
            shared = [
                (thread_id, verb)
                for thread_id, verb in self._threads_of(other).items()
                if thread_id in threads
            ]
            if not same_eyes and not shared:
                continue
            version = await self._latest(workspace_id, project_id, source_id)
            if version is None or not version.content.strip():
                continue
            words, start = _tail(version.content, NEARBY_WORDS)
            reasons = []
            if same_eyes:
                reasons.append(f"It is seen through {who or 'the same eyes'}, like this one.")
            for thread_id, _verb in shared[:1]:
                thread = (plan_by_id or {}).get(thread_id)
                reasons.append(
                    f"It is on the thread “{thread.title}” too."
                    if thread is not None
                    else "It is on one of this scene's threads too."
                )
            why = " ".join(reasons)
            other_eyes = ""
            if pov and seen_by and seen_by != pov:
                other_eyes = (
                    f" It is seen through {names.get(seen_by, 'someone else')}; "
                    f"{who or 'the viewpoint character'} may not know all of it."
                )
            eyes = f" ({other_eyes.strip()})" if other_eyes else ""
            item = ContextItem(
                key=f"nearby:{other.id}",
                section=ContextSection.NEARBY,
                text=f"From the end of “{other.title}”: {words}{eyes}",
                # How far back it is, the distance signal says.
                reason=f"It is how “{other.title}” ends, as you wrote it. {why}{other_eyes}",
                authority="writing",
                record_type="source_version",
                record_id=version.id,
                location=f"chars:{start}-{len(version.content)}",
                score=3.5,
            )
            out.append(
                ranking.timed(ranked(item, [distance_signal(item.score, distance, "")]), other.id)
            )
        return out

    async def _parts(
        self,
        workspace_id: str,
        project_id: str,
        scene: PlanItem,
        order: list[PlanItem],
        position: int,
        items: list[PlanItem],
        names: dict[str, str],
        pov: str | None,
        ranking: _Ranking,
    ) -> list[ContextItem]:
        """Earlier arcs and beats as summaries (CTX-006): the one the author kept, else
        one worked out from its scenes' kept summaries. Only parts further back than the
        scenes given one by one, and never one this scene is in."""
        parts = getattr(self.summaries, "parts", None)
        if parts is None:
            return []
        found = await parts.for_context(
            workspace_id, project_id, scene, order, items, position, EARLIER_SCENES
        )
        out: list[ContextItem] = []
        who = names.get(pov) if pov else None
        for entry in found:
            part = entry.part
            last = entry.scene_ids[-1]
            distance = ranking.distance(last) or 0
            others = {
                seen
                for item in order
                if item.id in entry.scene_ids
                for seen in item.refs_as("point_of_view")
                if pov and seen != pov
            }
            other_eyes = (
                f" Some of it is seen through other eyes; {who or 'the viewpoint character'} "
                "may not know all of it."
                if others
                else ""
            )
            eyes = f" ({other_eyes.strip()})" if other_eyes else ""
            kind = part.kind.value
            if entry.kept is not None:
                by = {
                    "author": "your own summary",
                    "model": "a summary the assistant suggested and you kept",
                    "model_corrected": "a summary the assistant suggested and you corrected",
                }[entry.kept.origin.value]
                item = ContextItem(
                    key=f"part:{part.id}",
                    section=ContextSection.EARLIER,
                    text=f"The {kind} “{part.title}”, in short: {entry.text}{eyes}",
                    reason=f"It is {by} of the {kind} “{part.title}”.{other_eyes}",
                    authority="summary",
                    record_type="part_summary",
                    record_id=entry.kept.id,
                    score=3.5,
                )
            else:
                item = ContextItem(
                    key=f"part:{part.id}",
                    section=ContextSection.EARLIER,
                    text=f"The {kind} “{part.title}”, from its scenes' summaries: "
                    f"{entry.text}{eyes}",
                    reason=f"It is worked out from the summaries you kept of the scenes in "
                    f"the {kind} “{part.title}”.{other_eyes}",
                    authority="summary",
                    record_type="plan_item",
                    record_id=part.id,
                    score=3.0,
                )
            signals: list[Signal | None] = [distance_signal(item.score, distance, "")]
            if entry.stale:
                signals.append(
                    Signal(
                        "out of date",
                        -1.0,
                        f"A summary of a scene in the {kind} has changed since this was written.",
                    )
                )
            # Placed in the story's time by the latest of its scenes there, not the last
            # one read: a part holding a flash-forward is later material all the same.
            out.append(
                ranking.timed(ranked(item, signals), _latest_in_time(entry.scene_ids, ranking))
            )
        return out

    async def _writing(
        self,
        workspace_id: str,
        project_id: str,
        scene: PlanItem,
        cast: dict[str, str],
        names: dict[str, str],
        ranking: _Ranking | None = None,
        plan_by_id: dict[str, PlanItem] | None = None,
        order: list[PlanItem] | None = None,
        pov: str | None = None,
    ) -> list[ContextItem]:
        if self._passage_store is None or self._sources is None:
            return []
        ranking = ranking or _Ranking(importance={}, impact={}, place={}, position=None)
        usable, own = await self._scene_texts(workspace_id, project_id, scene)
        links = await self._line_links(workspace_id, project_id)
        scene_of = {source_id: plan_id for plan_id, source_id in links.items()}
        passages: list[Any] = []
        if cast:
            passages = await self._passage_store.mentioning(
                workspace_id=workspace_id,
                project_id=project_id,
                entity_ids=list(cast),
                limit=MAX_PASSAGES * 4,
            )
        # The shared index reads each text as its own line does. When some answer holds only
        # in a what-if, a stretch of scenes or one viewpoint, each passage is read again as
        # this line reads it (CTX-016), and texts using such a phrase are looked at too.
        line = None
        if hasattr(self._sources, "line_names"):
            line = await self._sources.line_names(workspace_id, project_id)
        if line is not None and cast:
            passages = await self._with_limited_phrases(
                workspace_id, project_id, passages, line.phrases_for(cast), usable
            )
        # Other scenes on one of this scene's threads (CTX-006A): what was set up, moved
        # or resolved there is what this scene picks up.
        threads = self._threads_of(scene)
        on_thread: dict[str, str] = {}
        if threads and plan_by_id is not None and order is not None:
            # Only scenes before this one: a later scene's prose (a payoff, say) is not
            # what this scene picks up, and would give away what is still to come.
            ids = [item.id for item in order]
            here = ids.index(scene.id) if scene.id in ids else 0
            for other in order[:here]:
                if other.id not in links:
                    continue
                touching = [
                    (thread_id, verb)
                    for thread_id, verb in self._threads_of(other).items()
                    if thread_id in threads
                ]
                if not touching:
                    continue
                thread_id, verb = touching[0]
                thread = plan_by_id.get(thread_id)
                if thread is None or not thread.is_active:
                    continue
                along = " along" if verb == "moves" else ""
                on_thread[links[other.id]] = (
                    f"It is from “{other.title}”, which {verb} the thread “{thread.title}”{along}."
                )
        texts: dict[str, Any] = {}
        all_texts = await self._sources.all_texts(workspace_id, project_id)
        # Search by meaning only among this line's texts (CTX-004): another line's scene
        # prose is never scored, so it never takes a place from this line's.
        allowed = {text.id for text in all_texts if _usable(text, usable)}
        # Scene texts of scenes certainly later in the story's time (CTX-004): when the
        # policy leaves them out, search by meaning never scores them either, so they
        # never take a place from what may be given.
        later_texts = {
            source_id for plan_id, source_id in links.items() if ranking.is_later(plan_id)
        }
        if self.policy.later_in_story_time is LaterInTime.LEAVE_OUT:
            allowed -= later_texts
        semantic: dict[str, float] = {}
        if self._passages is not None:
            semantic = await self._passages.semantic_scores(
                workspace_id,
                project_id,
                f"{scene.title}. {scene.summary}",
                source_ids=allowed,
            )
        seen = {passage.key for passage in passages}
        thread_keys: set[str] = set()
        for source_id in on_thread:
            if source_id == own:
                continue
            found = await self._passage_store.for_source(
                workspace_id=workspace_id, project_id=project_id, source_id=source_id
            )
            # The passages naming most of the scene's people, then those closest in
            # meaning, then the first: at most a few from each scene.
            found.sort(
                key=lambda passage: (
                    -len(set(passage.entity_ids) & set(cast)),
                    -semantic.get(passage.key, 0.0),
                    passage.number,
                )
            )
            for passage in found[:THREAD_PASSAGES]:
                thread_keys.add(passage.key)
                if passage.key not in seen:
                    seen.add(passage.key)
                    passages.append(passage)
        out: list[ContextItem] = []
        for passage in passages:
            if passage.source_id not in texts:
                try:
                    source, version, _ = await self._sources.version(
                        workspace_id, project_id, passage.source_id
                    )
                except NotFoundError:
                    continue
                texts[passage.source_id] = (source, version)
            source, version = texts[passage.source_id]
            if not _usable(source, usable):
                continue  # a scene's words from another line of the story
            if version.id != passage.version_id:
                continue  # an older version's passage; the index is catching up
            words = version.content[passage.start : passage.end]
            named_here = passage.entity_ids
            if line is not None:
                named_here = tuple(
                    mention.entity_ids[0]
                    for mention in find_mentions(words, line.index(passage.source_id))
                    if len(mention.entity_ids) == 1
                )
            shared = list(dict.fromkeys(entity_id for entity_id in named_here if entity_id in cast))
            threaded = passage.key in thread_keys
            if line is not None and not shared and not threaded:
                continue  # in this line of the story the passage names nobody in the scene
            named = ", ".join(names.get(entity_id, "") for entity_id in shared[:3])
            reasons = [f"It mentions {named}."] if named else []
            if threaded:
                reasons.append(on_thread[passage.source_id])
            item = ContextItem(
                key=passage.key,
                section=ContextSection.WRITING,
                text=f"From “{source.title}”: {_trim(words, PASSAGE_WORDS)}",
                reason=" ".join(reasons) or "It is from your writing.",
                authority="writing",
                record_type="source_version",
                record_id=version.id,
                location=f"chars:{passage.start}-{passage.end}",
                score=1 + len(shared),
            )
            signals: list[Signal | None] = []
            if threaded:
                signals.append(Signal("thread", 2.0, ""))
            similarity = semantic.get(passage.key, 0.0)
            if similarity > 0:
                signals.append(
                    Signal(
                        "meaning", round(3 * similarity, 3), "It is close in meaning to this scene."
                    )
                )
            signals.append(ranking.importance_of(item, shared, names))
            signals.append(ranking.impact_of(*shared))
            # Prose from a scene far back, or later in the story, counts for less.
            scene_id = scene_of.get(passage.source_id)
            distance = ranking.distance(scene_id) if scene_id else None
            if distance is not None and distance != 0:
                base = with_signals(item.score, [x for x in signals if x is not None])
                signals.append(
                    # The item's text already says which scene it is from.
                    distance_signal(base, distance, "")
                    if plan_by_id and scene_id in plan_by_id
                    else None
                )
            # And by the story's time, the second clock (CTX-007; CTX-004 for later).
            out.append(ranking.timed(ranked(item, signals), scene_id))
        out.sort(key=lambda item: (-item.score, item.key))
        if self.policy.later_in_story_time is LaterInTime.LEAVE_OUT:
            # Left out by the policy: a few are listed, so the author can still choose
            # one; they never take a place from what may be given.
            given = [item for item in out if not item.later]
            listed = [item for item in out if item.later]
            out = given[:MAX_PASSAGES] + listed[:LATER_LISTED]
        else:
            out = out[:MAX_PASSAGES]
        # A sample of the author's voice, even when it names nobody in the scene: writers
        # report that re-feeding representative passages keeps voice from drifting.
        sample = await self._voice_sample(
            workspace_id,
            project_id,
            {item.key for item in out},
            usable,
            own,
            pov,
            links,
            order,
            scene.id,
            later_texts,
        )
        if sample is not None:
            out.append(sample)
        return out

    @staticmethod
    def _threads_of(item: PlanItem) -> dict[str, str]:
        """Thread id -> how this scene touches it: opens, moves or resolves."""
        found: dict[str, str] = {}
        for thread_id in item.sets_up:
            found.setdefault(thread_id, "opens")
        for thread_id in thread_advances(item.details):
            found.setdefault(thread_id, "moves")
        for thread_id in item.pays_off:
            found.setdefault(thread_id, "resolves")
        return found

    async def _with_limited_phrases(
        self,
        workspace_id: str,
        project_id: str,
        passages: list[Any],
        phrases: list[str],
        usable: set[str],
    ) -> list[Any]:
        """Add the passages of texts that use a phrase meaning someone only somewhere."""
        if not phrases:
            return passages
        wanted = [normalize_name(phrase) for phrase in phrases]
        seen = {passage.key for passage in passages}
        out = list(passages)
        for source in await self._sources.all_texts(workspace_id, project_id):
            if len(out) >= MAX_PASSAGES * 6:
                break
            if source.removed_at is not None or not _usable(source, usable):
                continue
            # Only the newest version's words are read: every older one would be loaded
            # whole for nothing, once per text, every time a context is built.
            version = await self._sources.latest_version(workspace_id, project_id, source)
            if version is None:
                continue
            content = normalize_name(version.content)
            if not any(phrase in content for phrase in wanted):
                continue
            for passage in await self._passage_store.for_source(
                workspace_id=workspace_id, project_id=project_id, source_id=source.id
            ):
                if passage.key in seen:
                    continue
                words = normalize_name(version.content[passage.start : passage.end])
                if any(phrase in words for phrase in wanted):
                    seen.add(passage.key)
                    out.append(passage)
        return out

    async def _scene_texts(
        self, workspace_id: str, project_id: str, scene: PlanItem
    ) -> tuple[set[str], str | None]:
        """The scene texts of the line of the story being worked in, and this scene's own.

        A scene's words written in a what-if must never shape the main story's drafts, or
        another what-if's. Without the studio's links nothing says which line a scene text
        belongs to, so none is used.
        """
        if self.studio is None or self._journal is None:
            return set(), None
        branch = await self._journal.working_branch(
            workspace_id=workspace_id, project_id=project_id
        )
        links = await self.studio.manuscripts_for(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
        )
        own = next((source_id for plan_id, source_id in links if plan_id == scene.id), None)
        return {source_id for _, source_id in links}, own

    async def _voice_sample(
        self,
        workspace_id: str,
        project_id: str,
        taken: set[str],
        usable: set[str],
        own: str | None,
        pov: str | None = None,
        links: dict[str, str] | None = None,
        order: list[PlanItem] | None = None,
        scene_id: str | None = None,
        later: set[str] | None = None,
    ) -> ContextItem | None:
        # A scene later in the story's time is never the voice sample (CTX-004).
        texts = [
            text
            for text in await self._sources.all_texts(workspace_id, project_id)
            if _usable(text, usable) and text.id not in (later or set())
        ]
        # Prose from later in the story never reaches an earlier scene's draft: a payoff's
        # words would give away what this scene only sets up. A scene text is used only
        # when its scene comes before this one in reading order; writing outside the plan
        # (and, last of all, the scene's own words) stays usable.
        if scene_id is not None:
            place = {
                links[item.id]: index
                for index, item in enumerate(order or [])
                if links and item.id in links
            }
            here = next(
                (index for index, item in enumerate(order or []) if item.id == scene_id), None
            )
            texts = [
                text
                for text in texts
                if text.kind != SourceKind.SCENE
                or text.id == own
                or (here is not None and place.get(text.id, here) < here)
            ]
        # The scene's own words are what is being rewritten: a sample of the author's
        # voice comes from elsewhere when there is anything else.
        others = [text for text in texts if text.id != own]
        texts = others or texts
        if not texts:
            return None
        # Prose from scenes seen through the same eyes is the closest voice (CTX-004:
        # search by meaning filtered to those scenes' texts, when it is available).
        same_eyes: set[str] = set()
        if pov and links and order:
            same_eyes = {
                links[item.id]
                for item in order
                if item.id in links and pov in item.refs_as("point_of_view")
            } & {text.id for text in texts}
        reason = "A sample of your voice, from your most recent writing."
        if same_eyes and self._passages is not None:
            found = await self._passages.semantic_scores(
                workspace_id,
                project_id,
                " ".join(text.title for text in texts if text.id in same_eyes),
                source_ids=same_eyes,
                limit=3,
            )
            for key in sorted(found, key=lambda key: (-found[key], key)):
                if key in taken:
                    continue
                _, source_id, number = key.split(":", 2)
                passages = await self._passage_store.for_source(
                    workspace_id=workspace_id, project_id=project_id, source_id=source_id
                )
                chosen = next((p for p in passages if str(p.number) == number), None)
                text = next((t for t in texts if t.id == source_id), None)
                if chosen is not None and text is not None:
                    return await self._sample(
                        workspace_id,
                        project_id,
                        text,
                        chosen,
                        "A sample of your voice, from a scene seen through the same eyes.",
                    )
        if same_eyes:
            texts = [text for text in texts if text.id in same_eyes]
            reason = (
                "A sample of your voice, from your most recent scene seen through the same eyes."
            )
        newest = max(texts, key=lambda text: (text.created_at, text.id))
        indexed = await self._passage_store.for_source(
            workspace_id=workspace_id, project_id=project_id, source_id=newest.id
        )
        if not indexed:
            return None
        last = indexed[-1]
        if last.key in taken:
            return None
        return await self._sample(workspace_id, project_id, newest, last, reason)

    async def _sample(
        self, workspace_id: str, project_id: str, text: Any, passage: Any, reason: str
    ) -> ContextItem | None:
        version = await self._latest(workspace_id, project_id, text.id)
        if version is None or version.id != passage.version_id:
            return None
        words = version.content[passage.start : passage.end]
        return ContextItem(
            key=passage.key,
            section=ContextSection.WRITING,
            text=f"From “{text.title}”: {_trim(words, PASSAGE_WORDS)}",
            reason=reason,
            authority="writing",
            record_type="source_version",
            record_id=version.id,
            location=f"chars:{passage.start}-{passage.end}",
            score=2.5,
        )


__all__ = ["ContextService", "KeptContext", "SceneContext"]
