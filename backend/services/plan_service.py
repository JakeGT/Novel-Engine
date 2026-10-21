"""The story plan by hand: read the outline, add to it, change it, reorder it, remove from it.

Every change goes through the same reviewed-operation path as the rest of canon,
as one version made by the person who asked. Nothing here calls a model, so a
whole plan can be built with no provider connected.

What is still open in a plan is *derived*, never stored: a scene with no place, a
beat with no scene, a thread nothing resolves. These are things to do, not errors;
a plan with gaps is a valid plan. So are the checks on its order (a reveal given away
early, a thread resolved before it opens, a required condition not true where its
scene comes), the promises it makes the reader (STY-005, STY-007), and how each
arc's character changes stage by stage (STY-006).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from backend.adapters.system import uuid7
from backend.domain.arcs import (
    MAX_STAGES,
    ArcReading,
    ArcStage,
    FactStanding,
    ScaleProgress,
    ScaleSet,
    arc_findings,
    arc_path,
    arcs_with_paths,
    lapse_findings,
    path_fits,
    read_arcs,
    read_scales,
    scale_findings,
    scale_progress,
    scale_removal_problem,
    with_path,
    without_scenes,
)
from backend.domain.branching import working_in
from backend.domain.canon.assertions import Belief, Fact
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import CLAIM_KIND, CLAIM_NARRATOR, CLAIM_SPEAKER, Event
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.phrasing import phrase
from backend.domain.canon.plans import (
    ALLOWED_PARENTS,
    MAX_THREAD_LINKS,
    PlanItem,
    PlanKind,
    PlanRef,
)
from backend.domain.canon.propositions import Proposition
from backend.domain.errors import ConflictError, NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.knowledge import (
    MAX_REVEALS,
    Reveal,
    reading_order,
    scene_reveals,
    scene_time,
    with_reveals,
    with_scene_time,
)
from backend.domain.operations import (
    CanonOperation,
    CreatePlanItemOp,
    RetirePlanItemOp,
    UpdatePlanItemOp,
)
from backend.domain.plan_checks import (
    Promise,
    fact_sentence,
    order_findings,
    promises,
    reveal_findings,
)
from backend.domain.rules import World, body_problems, world_from_canon
from backend.domain.scene_conditions import (
    MAX_CONDITIONS,
    ConditionState,
    SceneCondition,
    condition_body,
    condition_state,
    plain_details,
    planned_change,
    scene_conditions,
    with_conditions,
)
from backend.domain.scene_intent import SceneIntent, with_intent
from backend.domain.tenancy import Permission
from backend.domain.threads import (
    never_moved_along,
    outline_order,
    thread_advances,
    thread_progress,
    with_advances,
)
from backend.domain.worldtime import Relative, Truth, is_unknown, true_at
from backend.ports import CanonRepositoryPort, SchemaVersionStorePort
from backend.services.knowledge_timing import plan_signals
from backend.services.mutation_service import CommitResult, MutationService
from backend.services.story_time_service import (
    MAX_RECORDS,
    Story,
    event_times,
    fact_span,
    read_story,
    time_of,
)

MAX_PLAN_ITEMS = 2000
MAX_WORLD_ENTITIES = 2000
MAX_WORLD_FACTS = 10_000
# Up to this many entries are read one by one for the plan's checks; past it, all at once.
MAX_ENTRY_LOOKUPS = 200


@dataclass(frozen=True)
class OpenItem:
    """Something a plan item still needs, said in an author's words."""

    plan_id: str
    code: str
    message: str
    # gap: something not filled in yet. problem: out of order, or not ready.
    kind: str = "gap"
    # The records it comes from, so the author can open them (continuity findings).
    records: tuple[Any, ...] = ()


@dataclass(frozen=True)
class PlanReading:
    """The plan in outline order, with everything derived from it on read."""

    items: list[PlanItem]
    open: list[OpenItem]
    conditions: list[ConditionState]
    promises: list[Promise]
    # Each arc's stages: where each is reached and whether its fact holds (STY-006).
    arcs: list[ArcReading] = field(default_factory=list)
    # Where each arc's character stands on each shared scale, scene by scene (STY-006).
    scales: list[ScaleProgress] = field(default_factory=list)


def _label(item: PlanItem) -> str:
    return {
        PlanKind.STORY: "The story",
        PlanKind.ARC: "This arc",
        PlanKind.BEAT: "This beat",
        PlanKind.SCENE: "This scene",
        PlanKind.THREAD: "This thread",
    }[item.kind]


def open_items(items: list[PlanItem], entities: dict[str, CanonEntity]) -> list[OpenItem]:
    """What the plan still needs, in outline order. Derived each time, so it cannot drift."""
    active = [item for item in items if item.is_active]
    children: dict[str, list[PlanItem]] = {}
    for item in active:
        if item.parent_id:
            children.setdefault(item.parent_id, []).append(item)
    opened = {thread_id for item in active for thread_id in item.sets_up}
    resolved = {thread_id for item in active for thread_id in item.pays_off}
    order = outline_order(active)
    progress = thread_progress(active)

    found: list[OpenItem] = []

    def add(item: PlanItem, code: str, message: str) -> None:
        found.append(OpenItem(item.id, code, message))

    for item in active:
        kinds = {child.kind for child in children.get(item.id, [])}
        if item.kind is PlanKind.SCENE:
            if not item.refs_as("place"):
                add(item, "needs_place", "This scene needs a place.")
            if not item.refs_as("point_of_view"):
                add(item, "needs_point_of_view", "This scene needs a point of view.")
            if not item.summary.strip():
                add(item, "needs_intent", "This scene does not say what happens yet.")
        elif item.kind is PlanKind.BEAT:
            if PlanKind.SCENE not in kinds:
                add(item, "needs_scene", "This beat has no scene yet.")
        elif item.kind is PlanKind.ARC:
            # An arc told as a path of stages is carried by the scenes it names.
            if not kinds & {PlanKind.BEAT, PlanKind.SCENE} and not arc_path(item.details):
                add(item, "needs_beats", "This arc has no beats yet.")
            if not item.refs_as("about"):
                add(item, "needs_owner", "This arc does not say whose it is.")
        elif item.kind is PlanKind.STORY:
            if not item.summary.strip():
                add(item, "needs_premise", "The story has no premise written down yet.")
        elif item.kind is PlanKind.THREAD:
            if item.id not in opened:
                add(item, "needs_setup", "Nothing sets this thread up yet.")
            elif item.id not in resolved:
                add(item, "needs_payoff", "This thread is opened and never resolved.")
            elif never_moved_along(progress.get(item.id, []), order):
                add(
                    item,
                    "needs_progress",
                    "This thread opens and resolves, but no scene in between moves it along.",
                )
        for ref in item.refs:
            entity = entities.get(ref.entity_id)
            if entity is None or not entity.is_active:
                name = entity.name if entity is not None else "an entry"
                add(
                    item,
                    "names_archived_entry",
                    f"{_label(item)} names {name}, which is no longer in the story bible.",
                )
    return found


class PlanService:
    def __init__(
        self,
        *,
        mutation: MutationService,
        reader: CanonRepositoryPort,
        schema_store: SchemaVersionStorePort | None = None,
        tenancy: object | None = None,
        local_mode: bool = False,
        clocks: Any = None,
    ) -> None:
        self._mutation = mutation
        self._reader = reader
        # Where the story's unit and "how long things take" are kept; set by the container.
        self.clocks: Any = clocks
        self._schema_store = schema_store
        self._tenancy = tenancy
        self._local_mode = local_mode

    async def _authorize(
        self, scope: AccessScope, workspace_id: str, project_id: str, permission: Permission
    ) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(scope, workspace_id, project_id, permission)  # type: ignore[attr-defined]

    async def _schema_id(self, workspace_id: str, project_id: str) -> str | None:
        if self._schema_store is None:
            return None
        published = await self._schema_store.get_published(
            workspace_id=workspace_id, project_id=project_id
        )
        if published is None:
            raise ValidationError("This project is not set up yet.")
        return published.id

    async def _items(self, workspace_id: str, project_id: str) -> list[PlanItem]:
        return list(
            await self._reader.list_plan_items(
                workspace_id=workspace_id, project_id=project_id, limit=MAX_PLAN_ITEMS
            )
        )

    async def _commit(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        operations: list[CanonOperation],
        expected_revision: int,
    ) -> CommitResult:
        mutation = await self._mutation.create_draft(
            scope,
            workspace_id=workspace_id,
            project_id=project_id,
            operations=operations,
            expected_head=expected_revision,
            idempotency_key="",
            schema_version_id=await self._schema_id(workspace_id, project_id),
        )
        # The author asked for exactly this; a long outline edit is not held for review.
        return await self._mutation.commit_author_change(
            scope, mutation.id, acknowledge_high_impact=True
        )

    # ------------------------------------------------------------------ read
    async def outline(self, scope: AccessScope, workspace_id: str, project_id: str) -> PlanReading:
        """The whole plan, what it still needs, its checks and its promises to the reader."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        items = await self._items(workspace_id, project_id)
        named = {ref.entity_id for item in items for ref in item.refs}
        entities: dict[str, CanonEntity] = {}
        for entity_id in named:
            entity = await self._reader.get_entity(
                workspace_id=workspace_id, project_id=project_id, entity_id=entity_id
            )
            if entity is not None:
                entities[entity_id] = entity
        found = open_items(items, entities)
        found += [
            OpenItem(item.plan_id, item.code, item.message, item.kind)
            for item in order_findings(items)
        ]
        scenes = reading_order(items)
        # Shown through, or said by, someone before they learn it (CON-006): warnings.
        found += await self._knowledge_findings(workspace_id, project_id, items)
        # Continuity across story time and place (CON-003, CON-004): warnings and notes.
        # It needs the whole story bible, read once here and shared with the checks below.
        whole_story = (
            await read_story(self._reader, workspace_id, project_id)
            if any(
                item.kind is PlanKind.SCENE
                and item.is_active
                and scene_time(item.details) is not None
                for item in items
            )
            else None
        )
        if whole_story is not None:
            found += await self._continuity_findings(workspace_id, project_id, items, whole_story)
        states: list[ConditionState] = []
        promised = promises(items, scenes, [])
        arcs = arcs_with_paths(items)
        arc_facts = {
            stage.fact_id
            for arc in arcs
            for stage in arc_path(arc.details)
            if stage.fact_id is not None
        }
        titles = {item.id: item.title for item in items}
        arc_readings = read_arcs(items, scenes)
        fact_words: Callable[[str], str] | None = None
        checked = any(
            scene_reveals(scene.details) or scene_conditions(scene.details) for scene in scenes
        )
        if checked or arc_facts:
            # Only read the part of the story bible a scene or an arc checks against,
            # unless the whole of it is already read.
            story = (
                whole_story
                if whole_story is not None
                else await self._story_for_checks(
                    workspace_id, project_id, scenes, also_facts=arc_facts
                )
            )
            names = {entity.id: entity.name for entity in story.entities.values()}
            if arc_facts:
                standing = fact_standing_in(story)
                arc_readings = read_arcs(items, scenes, standing)
                facts_by_id = {fact.id: fact for fact in story.facts}

                def fact_words(fact_id: str) -> str:
                    fact = facts_by_id.get(fact_id)
                    return fact_sentence(fact, names) if fact is not None else "its fact"

                # Whether each stage's fact still holds until the next stage (STY-006).
                found += [
                    OpenItem(item.plan_id, item.code, item.message, item.kind)
                    for item in lapse_findings(arc_readings, scenes, standing, titles, fact_words)
                ]

            if checked:
                found += [
                    OpenItem(item.plan_id, item.code, item.message, item.kind)
                    for item in reveal_findings(scenes, story.facts, names)
                ]
                states = condition_states(scenes, story)
                found += [
                    OpenItem(
                        state.plan_id,
                        "condition_not_met",
                        f"This scene needs this first, and it is not true at that point: "
                        f"{state.reading.rstrip('.')}.",
                        "problem",
                    )
                    for state in states
                    if state.when == "before"
                    and state.strength == "hard"
                    and state.status == "not_met"
                ]
                promised = promises(items, scenes, story.facts)
        found += [
            OpenItem(item.plan_id, item.code, item.message, item.kind)
            for item in arc_findings(arc_readings, scenes, titles, fact_words)
        ]
        scales = ScaleSet()
        if any(stage.scale_id for arc in arc_readings for stage in arc.stages):
            scales, _ = await self._read_scales(workspace_id, project_id)
            found += [
                OpenItem(item.plan_id, item.code, item.message, item.kind)
                for item in scale_findings(arc_readings, scales, titles)
            ]
        # In outline order, then threads, so the list reads like the page.
        place = {item.id: index for index, item in enumerate(outline_order(items))}
        found.sort(key=lambda item: place.get(item.plan_id, len(place)))
        return PlanReading(
            items=items,
            open=found,
            conditions=states,
            promises=promised,
            arcs=arc_readings,
            scales=scale_progress(arc_readings, scales),
        )

    # ------------------------------------------------------------------ scales (STY-006)
    async def scales(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> tuple[ScaleSet, int]:
        """The story's shared scales and their version (0 when none were ever saved)."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._read_scales(workspace_id, project_id)

    async def _read_scales(self, workspace_id: str, project_id: str) -> tuple[ScaleSet, int]:
        if self.clocks is None or not hasattr(self.clocks, "get_scales"):
            return ScaleSet(), 0
        raw, version = await self.clocks.get_scales(
            workspace_id=workspace_id, project_id=project_id
        )
        return read_scales(raw), version

    async def _arcs_on_every_line(
        self, workspace_id: str, project_id: str
    ) -> list[tuple[str, list[ArcStage]]]:
        """Each arc's title and stages, on the main story and every what-if: the scales
        are shared by all of them."""
        journal = getattr(self._mutation, "_journal", None)
        branches = (
            await journal.list_branches(workspace_id=workspace_id, project_id=project_id)
            if journal is not None and hasattr(journal, "list_branches")
            else []
        )
        lines = [None, *(branch.id for branch in branches if not branch.is_root)]
        found: list[tuple[str, list[ArcStage]]] = []
        for line in lines:
            with working_in(line):
                items = await self._items(workspace_id, project_id)
            found.extend(
                (item.title, arc_path(item.details))
                for item in items
                if item.is_active and item.kind is PlanKind.ARC
            )
        return found

    async def set_scales(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        scales: ScaleSet,
        expected_version: int,
    ) -> tuple[ScaleSet, int]:
        """Replace the story's scales. A scale or level an arc's stage stands on, on any line
        of the story, cannot be removed."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if self.clocks is None or not hasattr(self.clocks, "set_scales"):
            raise ValidationError("Scales cannot be kept here.")
        kept, _version = await self._read_scales(workspace_id, project_id)
        new = scales.by_id()
        drops = any(
            scale.id not in new or any(new[scale.id].rank(level) is None for level in scale.levels)
            for scale in kept.scales
        )
        if drops:
            problem = scale_removal_problem(
                kept, scales, await self._arcs_on_every_line(workspace_id, project_id)
            )
            if problem is not None:
                raise ValidationError(problem)
        version = await self.clocks.set_scales(
            workspace_id=workspace_id,
            project_id=project_id,
            scales=scales.model_dump(mode="json"),
            expected_version=expected_version,
        )
        if version is None:
            raise ConflictError(
                "Someone else changed the scales since you opened them. Look again, then save."
            )
        return scales, version

    async def _continuity_findings(
        self, workspace_id: str, project_id: str, items: list[PlanItem], story: Story
    ) -> list[OpenItem]:
        """Continuity findings beside the scenes they are about: a warning is something out
        of order or not ready, a note is a gap. Only asked for when a scene is placed in
        story time, since every scene check is about when, with the story already read."""
        from backend.services.continuity_service import story_continuity

        findings = await story_continuity(
            self._reader, self.clocks, workspace_id, project_id, story=story, items=items
        )
        more: list[OpenItem] = []
        if findings.not_shown:
            first = next(
                (
                    item
                    for item in reading_order(items)
                    if item.kind is PlanKind.SCENE and scene_time(item.details) is not None
                ),
                None,
            )
            if first is not None:
                count = findings.not_shown
                more.append(
                    OpenItem(
                        first.id,
                        "continuity_not_shown",
                        f"Continuity found more than can be listed: {count} more "
                        f"{'is' if count == 1 else 'are'} not shown here or on the timeline. "
                        "Fixing the ones shown brings the rest into view.",
                        "gap",
                    )
                )
        return more + [
            OpenItem(
                scene_id,
                finding.code,
                finding.message,
                "problem" if finding.severity == "warning" else "gap",
                finding.records,
            )
            for finding in findings
            for scene_id in finding.scene_ids
        ]

    async def _knowledge_findings(
        self, workspace_id: str, project_id: str, items: list[PlanItem]
    ) -> list[OpenItem]:
        """A reveal shown through a viewpoint character, or a claim said in a scene, before
        that person learns it (CON-006). Read only when something someone learned is
        placed in a scene or in story time; otherwise nothing could be found."""
        scenes = reading_order(items)
        # A scene that names nobody has no viewpoint or speaker to check: then nothing in
        # the story bible is read at all.
        if not any(scene.refs for scene in scenes):
            return []
        holders = {pov for scene in scenes for pov in scene.refs_as("point_of_view")}
        events = [
            event
            for event in await self._reader.list_events(
                workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
            )
            if event.retired_revision is None
        ]
        for event in events:
            if event.kind == CLAIM_KIND and event.claim is not None and event.claim.scene_id:
                holders.update(
                    p.entity_id
                    for p in event.participants
                    if p.role in (CLAIM_SPEAKER, CLAIM_NARRATOR)
                )
        placed = {
            event.id
            for event in events
            if event.scene_id
            or (event.claim is not None and event.claim.scene_id)
            or not is_unknown(event.when)
        }
        if not holders or not placed:
            return []
        beliefs: list[Belief] = []
        for holder in sorted(holders):
            beliefs += await self._reader.list_beliefs(
                workspace_id=workspace_id,
                project_id=project_id,
                holder_entity_id=holder,
                limit=MAX_RECORDS,
            )
        if not any(belief.source_ref in placed for belief in beliefs):
            return []
        facts = list(
            await self._reader.list_facts(
                workspace_id=workspace_id, project_id=project_id, limit=MAX_WORLD_FACTS
            )
        )
        names = {
            entity.id: entity.name
            for entity in await self._reader.list_entities(
                workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
            )
        }

        def words(proposition: Proposition) -> str:
            target = names.get(getattr(proposition.object, "entity_id", "") or "")
            if target is None:
                value = getattr(proposition.object, "value", None)
                target = "" if value is None else str(value)
            subject = names.get(proposition.subject_id, "Someone")
            return f"{subject} {phrase(proposition.predicate)} {target}".strip()

        return [
            OpenItem(item.plan_id, item.code, item.message, item.kind)
            for item in plan_signals(items, facts, events, beliefs, names, words)
        ]

    async def _story_for_checks(
        self,
        workspace_id: str,
        project_id: str,
        scenes: list[PlanItem],
        *,
        also_facts: set[str] | None = None,
    ) -> Story:
        """The part of the story bible the plan's checks look at, and no more.

        A condition about an entry depends only on that entry, the facts about it or
        pointing at it, and the entries at their other ends; a reveal or an arc's stage
        only on its own fact. Events are read only when a scene with conditions (or
        with ``also_facts`` to judge) is placed in story time, since only then are
        times worked out.
        """
        about: set[str] = set()
        named: set[str] = set()
        fact_ids: set[str] = set(also_facts or ())
        timed = bool(also_facts) and any(scene_time(scene.details) is not None for scene in scenes)
        for scene in scenes:
            conditions = scene_conditions(scene.details)
            for condition in conditions:
                if condition.entity_id is not None:
                    about.add(condition.entity_id)
                if condition.check is not None and condition.check.pattern.other_id:
                    named.add(condition.check.pattern.other_id)
            if conditions and scene_time(scene.details) is not None:
                timed = True
            fact_ids.update(reveal.fact_id for reveal in scene_reveals(scene.details))
        facts: dict[str, Fact] = {}
        for entity_id in sorted(about):
            about_it = await self._reader.list_facts(
                workspace_id=workspace_id,
                project_id=project_id,
                subject_id=entity_id,
                limit=MAX_WORLD_FACTS,
            )
            pointing_at_it = await self._reader.list_facts(
                workspace_id=workspace_id,
                project_id=project_id,
                object_entity_id=entity_id,
                limit=MAX_WORLD_FACTS,
            )
            for fact in [*about_it, *pointing_at_it]:
                facts[fact.id] = fact
        for fact_id in sorted(fact_ids - facts.keys()):
            found = await self._reader.get_fact(
                workspace_id=workspace_id, project_id=project_id, fact_id=fact_id
            )
            if found is not None:
                facts[fact_id] = found
        wanted = about | named
        for fact in facts.values():
            wanted.add(fact.proposition.subject_id)
            other = getattr(fact.proposition.object, "entity_id", None)
            if other:
                wanted.add(other)
        entities: dict[str, CanonEntity] = {}
        if len(wanted) > MAX_ENTRY_LOOKUPS:
            # Many entries: one read of the bible's entries is cheaper than one each.
            for entity in await self._reader.list_entities(
                workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
            ):
                if entity.id in wanted:
                    entities[entity.id] = entity
        else:
            for entity_id in sorted(wanted):
                one = await self._reader.get_entity(
                    workspace_id=workspace_id, project_id=project_id, entity_id=entity_id
                )
                if one is not None and one.is_active:
                    entities[entity_id] = one
        events: list[Event] = []
        if timed:
            events = [
                event
                for event in await self._reader.list_events(
                    workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
                )
                if event.retired_revision is None
            ]
        return Story(entities=entities, facts=list(facts.values()), events=events)

    async def get(
        self, scope: AccessScope, workspace_id: str, project_id: str, plan_id: str
    ) -> PlanItem:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        item = await self._reader.get_plan_item(
            workspace_id=workspace_id, project_id=project_id, plan_id=plan_id
        )
        if item is None:
            raise NotFoundError("That part of the plan was not found.")
        return item

    async def set_conditions(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        *,
        conditions: list[SceneCondition],
        expected_revision: int,
    ) -> tuple[CommitResult, PlanItem]:
        """Replace what must be true before and after a scene. Saved as a change to the plan."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        current = await self.get(scope, workspace_id, project_id, plan_id)
        if current.kind is not PlanKind.SCENE:
            raise ValidationError("Only a scene can say what must be true before and after it.")
        if len(conditions) > MAX_CONDITIONS:
            raise ValidationError(f"A scene can have at most {MAX_CONDITIONS} of these.")
        predicates, entity_types = await self._vocabulary(workspace_id, project_id)
        entities = await self._reader.list_entities(
            workspace_id=workspace_id, project_id=project_id, limit=MAX_WORLD_ENTITIES
        )
        entity_ids = {entity.id for entity in entities if entity.is_active}
        for condition in conditions:
            if condition.words is not None:
                continue
            problems = body_problems(
                condition_body(condition),
                predicates=predicates,
                entity_types=entity_types,
                entity_ids=entity_ids,
            )
            if problems:
                raise ValidationError(" ".join(problems))
        details = with_conditions(current.details, conditions)
        if details == current.details:
            raise ValidationError("Nothing has changed, so there is nothing to save.")
        operation = UpdatePlanItemOp(id=plan_id, details=details)
        result = await self._commit(scope, workspace_id, project_id, [operation], expected_revision)
        return result, await self.get(scope, workspace_id, project_id, plan_id)

    async def _set_scene_details(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        details_for: Callable[[dict[str, str]], dict[str, str]],
        expected_revision: int,
    ) -> tuple[CommitResult, PlanItem]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        current = await self.get(scope, workspace_id, project_id, plan_id)
        if current.kind is not PlanKind.SCENE:
            raise ValidationError("Only a scene can say this.")
        details = details_for(current.details)
        if details == current.details:
            raise ValidationError("Nothing has changed, so there is nothing to save.")
        operation = UpdatePlanItemOp(id=plan_id, details=details)
        result = await self._commit(scope, workspace_id, project_id, [operation], expected_revision)
        return result, await self.get(scope, workspace_id, project_id, plan_id)

    async def set_reveals(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        *,
        reveals: list[Reveal],
        expected_revision: int,
    ) -> tuple[CommitResult, PlanItem]:
        """Replace what a scene shows the reader. Saved as a change to the plan."""
        if len(reveals) > MAX_REVEALS:
            raise ValidationError(f"A scene can show at most {MAX_REVEALS} things here.")
        if len({reveal.fact_id for reveal in reveals}) != len(reveals):
            raise ValidationError("Each fact can be listed once per scene.")
        for reveal in reveals:
            fact = await self._reader.get_fact(
                workspace_id=workspace_id, project_id=project_id, fact_id=reveal.fact_id
            )
            if fact is None or fact.closed_revision is not None:
                raise ValidationError("A scene can only show facts that are in the story.")
        return await self._set_scene_details(
            scope,
            workspace_id,
            project_id,
            plan_id,
            lambda details: with_reveals(details, reveals),
            expected_revision,
        )

    async def set_scene_time(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        *,
        when: object | None,
        expected_revision: int,
    ) -> tuple[CommitResult, PlanItem]:
        """Say when in story time a scene happens, or clear it."""
        if isinstance(when, Relative):
            event = await self._reader.get_event(
                workspace_id=workspace_id, project_id=project_id, event_id=when.anchor_event_id
            )
            if event is None or event.retired_revision is not None:
                raise ValidationError("That event is not in the story.")
        return await self._set_scene_details(
            scope,
            workspace_id,
            project_id,
            plan_id,
            lambda details: with_scene_time(details, when),
            expected_revision,
        )

    async def set_path(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        *,
        stages: list[ArcStage],
        expected_revision: int,
    ) -> tuple[CommitResult, PlanItem]:
        """Replace an arc's stages, in order. Saved as a change to the plan (STY-006)."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        current = await self.get(scope, workspace_id, project_id, plan_id)
        await self._check_path(current.kind, stages, workspace_id, project_id)
        details = with_path(current.details, stages)
        if details == current.details:
            raise ValidationError("Nothing has changed, so there is nothing to save.")
        operation = UpdatePlanItemOp(id=plan_id, details=details)
        result = await self._commit(scope, workspace_id, project_id, [operation], expected_revision)
        return result, await self.get(scope, workspace_id, project_id, plan_id)

    async def _check_path(
        self, kind: PlanKind, stages: list[ArcStage], workspace_id: str, project_id: str
    ) -> None:
        """Stages belong to an arc, have their own names, and point at what is there."""
        if kind is not PlanKind.ARC:
            raise ValidationError("Only an arc can have stages.")
        if len(stages) > MAX_STAGES:
            raise ValidationError(f"An arc can have at most {MAX_STAGES} stages.")
        if len({stage.name.casefold() for stage in stages}) != len(stages):
            raise ValidationError("Give each stage its own name.")
        scene_ids = {stage.scene_id for stage in stages if stage.scene_id is not None}
        if scene_ids:
            scenes = {
                item.id
                for item in await self._items(workspace_id, project_id)
                if item.is_active and item.kind is PlanKind.SCENE
            }
            if scene_ids - scenes:
                raise ValidationError("A stage can only be reached in a scene of the plan.")
        for fact_id in sorted({stage.fact_id for stage in stages if stage.fact_id is not None}):
            fact = await self._reader.get_fact(
                workspace_id=workspace_id, project_id=project_id, fact_id=fact_id
            )
            if fact is None or fact.closed_revision is not None:
                raise ValidationError("A stage can only be tied to a fact that is in the story.")
        on_scale = [stage for stage in stages if stage.scale_id is not None]
        if on_scale:
            known, _ = await self._read_scales(workspace_id, project_id)
            by_id = known.by_id()
            for stage in on_scale:
                scale = by_id.get(stage.scale_id or "")
                if scale is None:
                    raise ValidationError("A stage can only stand on one of the story's scales.")
                if scale.rank(stage.level) is None:
                    raise ValidationError(f"{scale.name} has no level called “{stage.level}”.")
        if not path_fits(stages):
            raise ValidationError(
                "That is more than one arc can keep. Shorten the stage names or use fewer stages."
            )

    async def _vocabulary(self, workspace_id: str, project_id: str) -> tuple[set[str], set[str]]:
        from backend.domain.canon.registry import CORE_ENTITY_TYPES, CORE_PREDICATES
        from backend.services.schema_compiler import compile_definition

        predicates = set(CORE_PREDICATES)
        entity_types = set(CORE_ENTITY_TYPES)
        if self._schema_store is not None:
            published = await self._schema_store.get_published(
                workspace_id=workspace_id, project_id=project_id
            )
            if published is not None:
                compiled = compile_definition(
                    published.definition, workspace_id=workspace_id, project_id=project_id
                )
                predicates.update(compiled.predicate_specs)
                entity_types.update(compiled.entity_types)
        return predicates, entity_types

    @staticmethod
    def _check_advances(
        kind: PlanKind,
        advances: list[str],
        sets_up: list[str],
        pays_off: list[str],
        items: list[PlanItem],
    ) -> None:
        """A thread moved along must be a thread in the plan, named once, by a non-thread."""
        if kind is PlanKind.THREAD:
            raise ValidationError("A thread cannot move another thread along.")
        if len(advances) > MAX_THREAD_LINKS:
            raise ValidationError(f"Something can move at most {MAX_THREAD_LINKS} threads along.")
        if len(set(advances)) != len(advances):
            raise ValidationError("Each thread can be listed once.")
        PlanService._check_overlap(advances, sets_up, pays_off)
        threads = {item.id for item in items if item.is_active and item.kind is PlanKind.THREAD}
        if any(thread_id not in threads for thread_id in advances):
            raise ValidationError("Only a thread in the plan can be moved along.")

    @staticmethod
    def _check_overlap(advances: list[str], sets_up: list[str], pays_off: list[str]) -> None:
        """A thread is opened, resolved or moved along by an item, never two of these."""
        if set(advances) & (set(sets_up) | set(pays_off)):
            raise ValidationError(
                "A thread this opens or resolves is already part of it; list it in one place."
            )

    @staticmethod
    def _check_intent(kind: PlanKind) -> None:
        if kind is not PlanKind.SCENE:
            raise ValidationError(
                "Only a scene can say what its viewpoint character wants and how it ends."
            )

    # ----------------------------------------------------------------- write
    async def add(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        kind: PlanKind,
        title: str,
        summary: str = "",
        parent_id: str | None = None,
        refs: list[PlanRef] | None = None,
        sets_up: list[str] | None = None,
        pays_off: list[str] | None = None,
        details: dict[str, str] | None = None,
        advances: list[str] | None = None,
        intent: SceneIntent | None = None,
        path: list[ArcStage] | None = None,
        expected_revision: int,
    ) -> tuple[CommitResult, PlanItem]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if not title.strip():
            raise ValidationError("Give it a title.")
        if path:
            await self._check_path(kind, path, workspace_id, project_id)
        items = await self._items(workspace_id, project_id)
        siblings = [
            item
            for item in items
            if item.parent_id == parent_id and (parent_id is not None or item.kind is kind)
        ]
        kept = plain_details(details or {})
        if advances:
            self._check_advances(kind, advances, sets_up or [], pays_off or [], items)
            kept = with_advances(kept, advances)
        if intent is not None:
            self._check_intent(kind)
            kept = with_intent(kept, intent)
        if path:
            kept = with_path(kept, path)
        plan_id = format_canon_id("plan_item", str(uuid7()))
        operation = CreatePlanItemOp(
            id=plan_id,
            kind=kind,
            title=title.strip(),
            summary=summary.strip(),
            parent_id=parent_id,
            # New items go last among what they sit beside.
            position=max((item.position for item in siblings), default=-1) + 1,
            refs=refs or [],
            sets_up=sets_up or [],
            pays_off=pays_off or [],
            details=kept,
        )
        result = await self._commit(scope, workspace_id, project_id, [operation], expected_revision)
        return result, await self.get(scope, workspace_id, project_id, plan_id)

    async def change(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        *,
        title: str | None = None,
        summary: str | None = None,
        refs: list[PlanRef] | None = None,
        sets_up: list[str] | None = None,
        pays_off: list[str] | None = None,
        details: dict[str, str] | None = None,
        advances: list[str] | None = None,
        intent: SceneIntent | None = None,
        expected_revision: int,
    ) -> tuple[CommitResult, PlanItem]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        current = await self.get(scope, workspace_id, project_id, plan_id)
        if title is not None and not title.strip():
            raise ValidationError("Give it a title.")
        if details is not None:
            # A scene's conditions, reveals and time are changed in their own right,
            # never by editing its notes.
            details = plain_details(details)
            details.update(
                {key: value for key, value in current.details.items() if key.startswith("_")}
            )
        will_set_up = sets_up if sets_up is not None else current.sets_up
        will_pay_off = pays_off if pays_off is not None else current.pays_off
        if advances is not None and advances != thread_advances(current.details):
            self._check_advances(
                current.kind,
                advances,
                will_set_up,
                will_pay_off,
                await self._items(workspace_id, project_id),
            )
        elif will_set_up != current.sets_up or will_pay_off != current.pays_off:
            # Opening or resolving a thread this already moves along says it twice.
            self._check_overlap(
                advances if advances is not None else thread_advances(current.details),
                will_set_up,
                will_pay_off,
            )
        if advances is not None:
            details = with_advances(details if details is not None else current.details, advances)
        if intent is not None:
            self._check_intent(current.kind)
            details = with_intent(details if details is not None else current.details, intent)
        # Only what differs is sent, so an unrelated edit never rewrites other fields.
        operation = UpdatePlanItemOp(
            id=plan_id,
            title=title.strip() if title is not None and title.strip() != current.title else None,
            summary=summary.strip()
            if summary is not None and summary.strip() != current.summary
            else None,
            refs=refs if refs is not None and refs != current.refs else None,
            sets_up=sets_up if sets_up is not None and sets_up != current.sets_up else None,
            pays_off=pays_off if pays_off is not None and pays_off != current.pays_off else None,
            details=details if details is not None and details != current.details else None,
        )
        if operation == UpdatePlanItemOp(id=plan_id):
            raise ValidationError("Nothing has changed, so there is nothing to save.")
        result = await self._commit(scope, workspace_id, project_id, [operation], expected_revision)
        return result, await self.get(scope, workspace_id, project_id, plan_id)

    async def move(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        *,
        parent_id: str | None,
        before_id: str | None = None,
        expected_revision: int,
    ) -> tuple[CommitResult, PlanItem]:
        """Put an item under ``parent_id`` (or at the top), just before ``before_id`` or last."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        items = await self._items(workspace_id, project_id)
        by_id = {item.id: item for item in items}
        moving = by_id.get(plan_id)
        if moving is None:
            raise NotFoundError("That part of the plan was not found.")
        if parent_id is not None:
            parent = by_id.get(parent_id)
            if parent is None:
                raise NotFoundError("The place to move it to was not found.")
            if parent.kind not in ALLOWED_PARENTS[moving.kind]:
                raise ValidationError(
                    f"A {moving.kind.value} cannot go under a {parent.kind.value}."
                )
        # The items it will sit beside, in their current order, without itself.
        beside = sorted(
            (
                item
                for item in items
                if item.id != plan_id
                and item.parent_id == parent_id
                and (parent_id is not None or item.kind is moving.kind)
            ),
            key=lambda item: (item.position, item.created_revision, item.id),
        )
        index = next((i for i, item in enumerate(beside) if item.id == before_id), len(beside))
        ordered = [*beside[:index], moving, *beside[index:]]
        operations: list[CanonOperation] = []
        for position, item in enumerate(ordered):
            update = UpdatePlanItemOp(
                id=item.id, position=position if item.position != position else None
            )
            if item.id == plan_id and moving.parent_id != parent_id:
                update = update.model_copy(
                    update={"parent_id": parent_id, "detach": parent_id is None}
                )
            if update != UpdatePlanItemOp(id=item.id):
                operations.append(update)
        if not operations:
            raise ValidationError("It is already there.")
        result = await self._commit(scope, workspace_id, project_id, operations, expected_revision)
        return result, await self.get(scope, workspace_id, project_id, plan_id)

    async def remove(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        *,
        expected_revision: int,
    ) -> tuple[CommitResult, int]:
        """Remove an item and everything under it, as one version. Returns how many went."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        items = await self._items(workspace_id, project_id)
        by_id = {item.id: item for item in items}
        if plan_id not in by_id:
            raise NotFoundError("That part of the plan was not found.")
        children: dict[str, list[str]] = {}
        for item in items:
            if item.parent_id:
                children.setdefault(item.parent_id, []).append(item.id)
        # Deepest first, so nothing is removed while something still sits under it.
        doomed: list[str] = []

        def visit(current: str) -> None:
            for child in children.get(current, []):
                visit(child)
            doomed.append(current)

        visit(plan_id)
        gone = set(doomed)
        operations: list[CanonOperation] = []
        # A stage reached in a removed scene goes back to not placed, so nothing dangles.
        for item in items:
            if item.id in gone or item.kind is not PlanKind.ARC:
                continue
            kept = without_scenes(item.details, gone)
            if kept != item.details:
                operations.append(UpdatePlanItemOp(id=item.id, details=kept))
        # A removed thread is taken off whatever opened or resolved it, so nothing dangles.
        for item in items:
            if item.id in gone:
                continue
            sets_up = [thread for thread in item.sets_up if thread not in gone]
            pays_off = [thread for thread in item.pays_off if thread not in gone]
            moved = thread_advances(item.details)
            advances = [thread for thread in moved if thread not in gone]
            if sets_up != item.sets_up or pays_off != item.pays_off or advances != moved:
                operations.append(
                    UpdatePlanItemOp(
                        id=item.id,
                        sets_up=sets_up if sets_up != item.sets_up else None,
                        pays_off=pays_off if pays_off != item.pays_off else None,
                        details=with_advances(item.details, advances)
                        if advances != moved
                        else None,
                    )
                )
        operations.extend(RetirePlanItemOp(id=item_id) for item_id in doomed)
        result = await self._commit(scope, workspace_id, project_id, operations, expected_revision)
        return result, len(doomed)


def _world_at(story: Story, moment: object, times: dict) -> tuple[World, World]:
    """The story at one moment: facts certainly true then, and those that may be."""
    sure, maybe = [], []
    for fact in story.facts:
        if fact.closed_revision is not None:
            continue
        truth = true_at(fact_span(fact, times), moment)  # type: ignore[arg-type]
        if truth is Truth.TRUE:
            sure.append(fact)
        if truth is not Truth.FALSE:
            maybe.append(fact)
    entities = list(story.entities.values())
    return world_from_canon(entities, sure), world_from_canon(entities, maybe)


def fact_standing_in(story: Story) -> Callable[[str, PlanItem | None], FactStanding]:
    """How a fact stands at a scene: at its story time if it has one, else as it stands."""
    facts = {fact.id: fact for fact in story.facts}
    times = event_times(story.events)

    def standing(fact_id: str, scene: PlanItem | None) -> FactStanding:
        fact = facts.get(fact_id)
        if fact is None or fact.closed_revision is not None:
            return "gone"
        when = scene_time(scene.details) if scene is not None else None
        moment = time_of(when, times) if when is not None else None
        if moment is None:
            # With no time to judge at, only a fact that always holds can be said to.
            return "holds" if fact.starts is None and fact.ends is None else "unclear"
        truth = true_at(fact_span(fact, times), moment)
        if truth is Truth.TRUE:
            return "holds"
        return "not_then" if truth is Truth.FALSE else "unclear"

    return standing


def condition_states(scenes: list[PlanItem], story: Story) -> list[ConditionState]:
    """Every scene's conditions, judged where the scene comes (reading order).

    A scene placed in story time is judged against the facts true at that moment; one
    that is not is judged against the story as it stands. Earlier scenes' "after"
    conditions say what the plan means to change before it.
    """
    world = world_from_canon(story.entities.values(), story.facts)
    # Without a time of its own, a scene may come before or after a fact stops being
    # true; the story without such facts lets the check say it cannot tell.
    lasting = [fact for fact in story.facts if fact.ends is None]
    undated = (
        world_from_canon(story.entities.values(), lasting)
        if len(lasting) != len(story.facts)
        else None
    )
    times = event_times(story.events)
    earlier: list[tuple[str, str, list[SceneCondition]]] = []
    states: list[ConditionState] = []
    for scene in scenes:
        conditions = scene_conditions(scene.details)
        if conditions:
            when = scene_time(scene.details)
            moment = time_of(when, times) if when is not None else None
            at_time = _world_at(story, moment, times) if moment is not None else None
            for index, condition in enumerate(conditions):
                planned = planned_change(condition, earlier) if condition.when == "before" else None
                states.append(
                    condition_state(
                        scene.id,
                        condition,
                        world,
                        index=index,
                        at_time=at_time,
                        planned=planned,
                        undated=undated,
                    )
                )
        earlier.append((scene.id, scene.title, conditions))
    return states


__all__ = [
    "MAX_PLAN_ITEMS",
    "OpenItem",
    "PlanReading",
    "PlanService",
    "condition_states",
    "fact_standing_in",
    "open_items",
]
