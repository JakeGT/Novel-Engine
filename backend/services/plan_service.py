"""The story plan by hand: read the outline, add to it, change it, reorder it, remove from it.

Every change goes through the same reviewed-operation path as the rest of canon,
as one version made by the person who asked. Nothing here calls a model, so a
whole plan can be built with no provider connected.

What is still open in a plan is *derived*, never stored: a scene with no place, a
beat with no scene, a thread nothing resolves. These are things to do, not errors;
a plan with gaps is a valid plan.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.adapters.system import uuid7
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.plans import ALLOWED_PARENTS, PlanItem, PlanKind, PlanRef
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.operations import (
    CanonOperation,
    CreatePlanItemOp,
    RetirePlanItemOp,
    UpdatePlanItemOp,
)
from backend.domain.tenancy import Permission
from backend.ports import CanonRepositoryPort, SchemaVersionStorePort
from backend.services.mutation_service import CommitResult, MutationService

MAX_PLAN_ITEMS = 2000


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
        expected_revision: int,
    ) -> tuple[CommitResult, PlanItem]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if not title.strip():
            raise ValidationError("Give it a title.")
        siblings = [
            item
            for item in await self._items(workspace_id, project_id)
            if item.parent_id == parent_id and (parent_id is not None or item.kind is kind)
        ]
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
            details=details or {},
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
        expected_revision: int,
    ) -> tuple[CommitResult, PlanItem]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        current = await self.get(scope, workspace_id, project_id, plan_id)
        if title is not None and not title.strip():
            raise ValidationError("Give it a title.")
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
            if sets_up != item.sets_up or pays_off != item.pays_off:
                operations.append(
                    UpdatePlanItemOp(
                        id=item.id,
                        sets_up=sets_up if sets_up != item.sets_up else None,
                        pays_off=pays_off if pays_off != item.pays_off else None,
                    )
                )
        operations.extend(RetirePlanItemOp(id=item_id) for item_id in doomed)
        result = await self._commit(scope, workspace_id, project_id, operations, expected_revision)
        return result, len(doomed)


__all__ = ["MAX_PLAN_ITEMS", "OpenItem", "PlanService", "open_items"]
