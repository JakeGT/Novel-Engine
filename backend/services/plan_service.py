"""The story plan by hand: read the outline, add to it, change it, reorder it, remove from it.

Every change goes through the same reviewed-operation path as the rest of canon,
as one version made by the person who asked. Nothing here calls a model, so a
whole plan can be built with no provider connected.

What is still open in a plan is *derived*, never stored: a scene with no place, a
beat with no scene, a thread nothing resolves. These are things to do, not errors;
a plan with gaps is a valid plan.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from backend.adapters.system import uuid7
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.plans import (
    ALLOWED_PARENTS,
    MAX_THREAD_LINKS,
    PlanItem,
    PlanKind,
    PlanRef,
)
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.knowledge import MAX_REVEALS, Reveal, with_reveals, with_scene_time
from backend.domain.operations import (
    CanonOperation,
    CreatePlanItemOp,
    RetirePlanItemOp,
    UpdatePlanItemOp,
)
from backend.domain.rules import body_problems, world_from_canon
from backend.domain.scene_conditions import (
    MAX_CONDITIONS,
    ConditionState,
    SceneCondition,
    condition_body,
    condition_state,
    plain_details,
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
from backend.domain.worldtime import Relative
from backend.ports import CanonRepositoryPort, SchemaVersionStorePort
from backend.services.mutation_service import CommitResult, MutationService

MAX_PLAN_ITEMS = 2000
MAX_WORLD_ENTITIES = 2000
MAX_WORLD_FACTS = 10_000


@dataclass(frozen=True)
class OpenItem:
    """Something a plan item still needs, said in an author's words."""

    plan_id: str
    code: str
    message: str


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
            if not kinds & {PlanKind.BEAT, PlanKind.SCENE}:
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
    ) -> None:
        self._mutation = mutation
        self._reader = reader
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
    async def outline(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> tuple[list[PlanItem], list[OpenItem]]:
        """The whole plan in outline order, and what it still needs."""
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
        return items, open_items(items, entities)

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

    async def conditions(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> list[ConditionState]:
        """Every scene's conditions, and whether each holds in the story as it stands."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        scenes = [
            (item, scene_conditions(item.details))
            for item in await self._items(workspace_id, project_id)
            if item.is_active and item.kind is PlanKind.SCENE
        ]
        if not any(found for _, found in scenes):
            return []
        world = world_from_canon(
            await self._reader.list_entities(
                workspace_id=workspace_id, project_id=project_id, limit=MAX_WORLD_ENTITIES
            ),
            await self._reader.list_facts(
                workspace_id=workspace_id, project_id=project_id, limit=MAX_WORLD_FACTS
            ),
        )
        return [
            condition_state(item.id, condition, world)
            for item, found in scenes
            for condition in found
        ]

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
        expected_revision: int,
    ) -> tuple[CommitResult, PlanItem]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if not title.strip():
            raise ValidationError("Give it a title.")
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


__all__ = ["MAX_PLAN_ITEMS", "OpenItem", "PlanService", "open_items"]
