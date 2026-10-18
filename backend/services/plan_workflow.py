"""Planning the story by talking it through.

Three things the assistant can do with a plan, all of which end in a set of
suggested changes for the author to review, never in a change:

- ask a few questions about what the author means to happen;
- draft part of the plan (the premise and arcs, their beats, or scenes) from what
  the author said and answered;
- offer two or three directions the story could take next, each with what it would
  change, for the author to pick, edit or turn down.

It is grounded in the story bible: it names real entries by id, and a person or
place it needs that does not exist is drafted as a bare entry to fill in later. It
leaves honest gaps: what the author has not decided is reported as open, never
invented. Whatever the model returns is untrusted: every item is typed, bounded and
checked here before it becomes an operation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.marks import marks
from backend.domain.canon.naming import normalize_name, slugify
from backend.domain.canon.plans import (
    ALLOWED_PARENTS,
    MAX_SUMMARY_LENGTH,
    MAX_TITLE_LENGTH,
    PLAN_ROLES,
    SINGLE_ROLES,
    PlanItem,
    PlanKind,
    PlanRef,
)
from backend.domain.canon.revisions import OriginKind
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.journal import MutationSet
from backend.domain.operations import (
    CanonOperation,
    CreateEntityOp,
    CreatePlanItemOp,
    EvidenceRef,
)
from backend.domain.prompts import Messages, prompt_version
from backend.domain.tenancy import Permission
from backend.services.canon_character_workflow import (
    MAX_ANSWERS,
    CharacterAnswer,
    CharacterQuestionSheet,
    DiscardedItem,
    UsageSink,
)
from backend.services.canon_creation_workflow import _QUESTION_ID, MAX_NAME_LENGTH
from backend.services.canon_revise_workflow import CanonLinkWorkflow
from backend.services.mutation_service import ValidationReport

logger = logging.getLogger(__name__)

PLAN_QUESTIONS_PROMPT_VERSION = prompt_version("plan-questions")
PLAN_DRAFT_PROMPT_VERSION = prompt_version("plan-draft")
PLAN_NEXT_PROMPT_VERSION = prompt_version("plan-next")

MAX_DESCRIPTION_LENGTH = 6000
MAX_DRAFT_ITEMS = 60
MAX_NEW_ENTRIES = 20
MAX_DIRECTIONS = 3
MAX_LISTED_PLAN = 400

# How far one round of planning goes, and how that is put to the model.
DEPTHS: dict[str, str] = {
    "arcs": (
        "Draft the premise (one story item, only if the plan has none) and the main arcs. "
        "Do not draft beats or scenes."
    ),
    "beats": (
        "Draft the arcs that are missing and the beats of each arc: its turning points, in "
        "order. Do not draft scenes."
    ),
    "scenes": (
        "Draft down to scenes: the scenes that carry each beat, in order, with a point of "
        "view and a place only where the author has said them."
    ),
}


class DraftPlanRef(BaseModel):
    ref: str = Field(description="The id of an existing entry, or the ref of a new one")
    role: str = Field(description="One of: point_of_view, place, participant, about")


class DraftPlanItem(BaseModel):
    ref: str = Field(description="A short local handle for this item")
    kind: str = Field(description="One of: story, arc, beat, scene, thread")
    title: str
    summary: str = Field(default="", description="What is meant to happen, in a sentence or two")
    parent_ref: str | None = Field(
        default=None, description="The id of an existing plan item, or the ref of a drafted one"
    )
    entries: list[DraftPlanRef] = Field(default_factory=list)
    sets_up: list[str] = Field(default_factory=list, description="Threads this opens")
    pays_off: list[str] = Field(default_factory=list, description="Threads this resolves")


class DraftPlanEntry(BaseModel):
    ref: str
    type: str = Field(description="One of the allowed entry types")
    name: str


class PlanDraft(BaseModel):
    """Structured model output. Untrusted: typed and checked before it becomes operations."""

    understood: str = Field(default="", description="One sentence restating what is planned")
    new_entries: list[DraftPlanEntry] = Field(
        default_factory=list,
        description="People, places or things the plan needs that are not in the story bible",
    )
    items: list[DraftPlanItem] = Field(default_factory=list)
    gaps: list[str] = Field(
        default_factory=list,
        description="What the author has not decided, left open rather than made up",
    )


class Direction(BaseModel):
    title: str = Field(description="A few words naming this direction")
    what_happens: str = Field(description="Two or three sentences")
    what_it_changes: list[str] = Field(
        default_factory=list, description="What would be different in the story if it went this way"
    )
    new_entries: list[DraftPlanEntry] = Field(default_factory=list)
    items: list[DraftPlanItem] = Field(default_factory=list)


class DirectionSheet(BaseModel):
    directions: list[Direction] = Field(default_factory=list)


@dataclass(frozen=True)
class OfferedDirection:
    title: str
    what_happens: str
    what_it_changes: list[str]
    # How many parts of the plan and new entries picking it would suggest.
    adds: int
    # Sent back unchanged by the author who picks it; checked again when it is.
    draft: PlanDraft


@dataclass(frozen=True)
class PlanProposalResult:
    session_id: str
    understood: str
    mutation: MutationSet
    operation_count: int
    validation: ValidationReport
    notes: list[str] = field(default_factory=list)
    discarded: list[DiscardedItem] = field(default_factory=list)


def plan_operations(
    draft: PlanDraft,
    *,
    plan: list[PlanItem],
    entities: list[CanonEntity],
    entity_types: set[str],
    session_id: str,
) -> tuple[list[CanonOperation], list[DiscardedItem]]:
    """Typed operations for a drafted piece of plan, and what was left out and why."""
    evidence = [EvidenceRef(source_artefact_id=session_id)]
    discarded: list[DiscardedItem] = []
    operations: list[CanonOperation] = []

    # ref → (entry id, entry type). Existing ids resolve to themselves.
    active = [entity for entity in entities if entity.is_active]
    known: dict[str, tuple[str, str]] = {entity.id: (entity.id, entity.type) for entity in active}
    by_name = {
        (entity.type, normalize_name(label)): entity
        for entity in active
        for label in (entity.name, *entity.aliases)
    }
    for index, entry in enumerate(draft.new_entries[:MAX_NEW_ENTRIES]):
        name = entry.name.strip()
        if not name or len(name) > MAX_NAME_LENGTH or entry.type not in entity_types:
            discarded.append(
                DiscardedItem("entity", f"{name or 'an entry'}: not a known type of entry")
            )
            continue
        if entry.ref in known:
            continue
        match = by_name.get((entry.type, normalize_name(name)))
        if match is not None:
            # Already in the story bible: use it rather than making a twin.
            known[entry.ref] = (match.id, match.type)
            continue
        temporary = f"tmp:plan-entry-{slugify(name) or 'entry'}-{index}"
        known[entry.ref] = (temporary, entry.type)
        by_name[(entry.type, normalize_name(name))] = CanonEntity.model_construct(
            id=temporary, type=entry.type, name=name, aliases=[]
        )
        operations.append(
            CreateEntityOp(
                id=temporary,
                type=entry.type,
                name=name,
                # Only a name so far: it goes on the list of entries to fill in.
                attributes=marks(needs_detail=True),
                evidence=evidence,
            )
        )

    current = [item for item in plan if item.is_active]
    # ref → (plan id, kind). Existing ids resolve to themselves.
    placed: dict[str, tuple[str, PlanKind]] = {item.id: (item.id, item.kind) for item in current}
    existing = {(item.kind, normalize_name(item.title), item.parent_id): item for item in current}
    next_position: dict[tuple[str | None, PlanKind | None], int] = {}

    def position_for(parent_id: str | None, kind: PlanKind) -> int:
        # Under a parent everything shares one order; at the top each kind has its own.
        key = (parent_id, None if parent_id else kind)
        if key not in next_position:
            beside = [
                item.position
                for item in current
                if item.parent_id == parent_id and (parent_id is not None or item.kind is kind)
            ]
            next_position[key] = max(beside, default=-1) + 1
        value = next_position[key]
        next_position[key] = value + 1
        return value

    items = [item for item in draft.items[:MAX_DRAFT_ITEMS] if item.ref not in placed]
    # Threads first, so anything that opens or resolves one finds it; then parents before
    # what sits under them, however the model ordered them.
    pending = sorted(items, key=lambda item: 0 if item.kind.strip().lower() == "thread" else 1)
    created: list[CreatePlanItemOp] = []
    progress = True
    while pending and progress:
        progress = False
        waiting: list[DraftPlanItem] = []
        for item in pending:
            title = item.title.strip()
            try:
                kind = PlanKind(item.kind.strip().lower())
            except ValueError:
                discarded.append(
                    DiscardedItem("plan", f"{title or 'an item'}: unknown kind of plan item")
                )
                progress = True
                continue
            if not title or len(title) > MAX_TITLE_LENGTH:
                discarded.append(
                    DiscardedItem("plan", "a plan item with a missing or over-long title")
                )
                progress = True
                continue
            parent_ref = (item.parent_ref or "").strip() or None
            if parent_ref is not None and parent_ref not in placed:
                if any(other.ref == parent_ref for other in pending if other is not item):
                    waiting.append(item)
                    continue
                discarded.append(
                    DiscardedItem("plan", f"{title}: what it sits under is not in the plan")
                )
                progress = True
                continue
            parent = placed[parent_ref] if parent_ref is not None else None
            if parent is not None and parent[1] not in ALLOWED_PARENTS[kind]:
                discarded.append(
                    DiscardedItem(
                        "plan", f"{title}: a {kind.value} cannot sit under a {parent[1].value}"
                    )
                )
                progress = True
                continue
            parent_id = parent[0] if parent is not None else None
            twin = existing.get((kind, normalize_name(title), parent_id))
            if twin is not None:
                # Already planned: what was drafted under it attaches to the one that exists.
                placed[item.ref] = (twin.id, twin.kind)
                progress = True
                continue

            refs: list[PlanRef] = []
            taken: set[str] = set()
            for named in item.entries:
                role = named.role.strip().lower()
                target = known.get(named.ref)
                if role not in PLAN_ROLES or target is None:
                    discarded.append(
                        DiscardedItem("plan", f"{title}: names an entry that is not known")
                    )
                    continue
                wanted = SINGLE_ROLES.get(role)
                if role in SINGLE_ROLES and (role in taken or (wanted and target[1] != wanted)):
                    discarded.append(
                        DiscardedItem("plan", f"{title}: its {role.replace('_', ' ')} was left out")
                    )
                    continue
                if any(ref.entity_id == target[0] and ref.role == role for ref in refs):
                    continue
                taken.add(role)
                refs.append(PlanRef(entity_id=target[0], role=role))

            def threads(names: list[str]) -> list[str]:
                found: list[str] = []
                for name in names:
                    thread = placed.get(name)
                    if (
                        thread is not None
                        and thread[1] is PlanKind.THREAD
                        and thread[0] not in found
                    ):
                        found.append(thread[0])
                return found

            temporary = f"tmp:plan-{len(created)}"
            placed[item.ref] = (temporary, kind)
            created.append(
                CreatePlanItemOp(
                    id=temporary,
                    kind=kind,
                    title=title,
                    summary=item.summary.strip()[:MAX_SUMMARY_LENGTH],
                    parent_id=parent_id,
                    position=position_for(parent_id, kind),
                    refs=refs,
                    sets_up=[] if kind is PlanKind.THREAD else threads(item.sets_up),
                    pays_off=[] if kind is PlanKind.THREAD else threads(item.pays_off),
                    evidence=evidence,
                )
            )
            progress = True
        pending = waiting
    discarded.extend(
        DiscardedItem(
            "plan", f"{item.title.strip() or 'an item'}: its place in the outline is circular"
        )
        for item in pending
    )
    return [*operations, *created], discarded


class PlanWorkflow(CanonLinkWorkflow):
    async def _plan(self, workspace_id: str, project_id: str) -> list[PlanItem]:
        return list(
            await self._reader.list_plan_items(
                workspace_id=workspace_id, project_id=project_id, limit=MAX_LISTED_PLAN
            )
        )

    async def _plan_context(
        self, workspace_id: str, project_id: str, about: PlanItem | None
    ) -> tuple[str, list[PlanItem], list[CanonEntity]]:
        """The story bible and the plan as it stands, as the model is shown them."""
        story, entities, _facts, _beliefs = await self._story(workspace_id, project_id, None)
        plan = await self._plan(workspace_id, project_id)
        lines = [story, ""]
        if plan:
            lines.append("The plan so far (intent, not fact):")
            lines.extend(
                f"- {item.id}: {item.kind.value} “{item.title}”"
                + (f" under {item.parent_id}" if item.parent_id else "")
                + (f" — {item.summary}" if item.summary else "")
                for item in plan
            )
        else:
            lines.append("There is no plan yet.")
        if about is not None:
            lines.append(
                f"\nThe author is working on: {about.kind.value} “{about.title}” ({about.id}). "
                "Draft only what belongs under or beside it."
            )
        return "\n".join(lines), plan, entities

    async def _about(
        self, workspace_id: str, project_id: str, about_id: str | None
    ) -> PlanItem | None:
        if not about_id:
            return None
        item = await self._reader.get_plan_item(
            workspace_id=workspace_id, project_id=project_id, plan_id=about_id
        )
        if item is None or not item.is_active:
            raise NotFoundError("That part of the plan was not found.")
        return item

    @staticmethod
    def _checked(description: str, depth: str) -> str:
        description = description.strip()
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise ValidationError("That is too long to plan from in one go (6000 characters).")
        if depth not in DEPTHS:
            raise ValidationError("Choose how far to plan: arcs, beats or scenes.")
        return description

    async def plan_questions(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        description: str,
        depth: str,
        about_id: str | None = None,
        credential: str | None = None,
        provider_base_url: str | None = None,
        usage_sink: UsageSink | None = None,
    ) -> CharacterQuestionSheet:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        description = self._checked(description, depth)
        about = await self._about(workspace_id, project_id, about_id)
        context, _plan, _entities = await self._plan_context(workspace_id, project_id, about)
        sheet: CharacterQuestionSheet = await self._generate(
            messages=plan_questions_messages(context, description, depth),
            response_format=CharacterQuestionSheet,
            prompt_version=PLAN_QUESTIONS_PROMPT_VERSION,
            credential=credential,
            provider_base_url=provider_base_url,
            usage_sink=usage_sink,
        )
        return CharacterQuestionSheet(
            questions=[
                question.model_copy(
                    update={
                        "id": question.id if _QUESTION_ID.match(question.id) else f"q{index + 1}"
                    }
                )
                for index, question in enumerate(sheet.questions[:MAX_ANSWERS])
            ]
        )

    async def _propose(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        draft: PlanDraft,
        *,
        plan: list[PlanItem],
        entities: list[CanonEntity],
        session_id: str,
        expected_revision: int,
        reason: str,
    ) -> PlanProposalResult:
        _predicates, entity_types = await self._vocabulary(workspace_id, project_id)
        operations, discarded = plan_operations(
            draft, plan=plan, entities=entities, entity_types=entity_types, session_id=session_id
        )
        if not any(isinstance(operation, CreatePlanItemOp) for operation in operations):
            raise ValidationError(
                "Nothing could be added to the plan from that. It may already be planned, or "
                "it may need more to go on."
            )
        mutation = await self._mutation.propose(
            scope,
            workspace_id=workspace_id,
            project_id=project_id,
            operations=operations,
            expected_head=expected_revision,
            origin=OriginKind.MODEL.value,
        )
        review_scope = AccessScope(
            actor_user_id=scope.actor_user_id,
            auth_mode=scope.auth_mode,
            correlation_id=scope.correlation_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        mutation = await self._mutation.submit(review_scope, mutation.id, reason)
        log_event(
            logger,
            "plan.workflow.proposed",
            mutation_id=mutation.id,
            operations=len(operations),
            discarded=len(discarded),
        )
        return PlanProposalResult(
            session_id=session_id,
            understood=draft.understood.strip()[:500],
            mutation=mutation,
            operation_count=len(operations),
            validation=await self._mutation.validate(review_scope, mutation.id),
            # What the author has not decided is said, never filled in.
            notes=[f"Left open: {gap.strip()[:300]}" for gap in draft.gaps if gap.strip()][:12],
            discarded=discarded,
        )

    async def plan_proposal(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        description: str,
        depth: str,
        answers: list[CharacterAnswer],
        expected_revision: int,
        about_id: str | None = None,
        session_id: str | None = None,
        credential: str | None = None,
        provider_base_url: str | None = None,
        usage_sink: UsageSink | None = None,
    ) -> PlanProposalResult:
        """One round of planning: what was said and answered becomes plan items to review."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        description = self._checked(description, depth)
        answered = [answer for answer in answers[:MAX_ANSWERS] if answer.answer.strip()]
        about = await self._about(workspace_id, project_id, about_id)
        if not description and not answered and about is None:
            raise ValidationError("Say something about the story first, or answer a question.")
        context, plan, entities = await self._plan_context(workspace_id, project_id, about)
        _predicates, entity_types = await self._vocabulary(workspace_id, project_id)
        draft: PlanDraft = await self._generate(
            messages=plan_draft_messages(context, description, depth, entity_types, answered),
            response_format=PlanDraft,
            prompt_version=PLAN_DRAFT_PROMPT_VERSION,
            credential=credential,
            provider_base_url=provider_base_url,
            usage_sink=usage_sink,
        )
        return await self._propose(
            scope,
            workspace_id,
            project_id,
            draft,
            plan=plan,
            entities=entities,
            session_id=session_id or f"pnv_{uuid7()}",
            expected_revision=expected_revision,
            reason="planned by conversation, awaiting review",
        )

    async def plan_directions(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        about_id: str | None = None,
        credential: str | None = None,
        provider_base_url: str | None = None,
        usage_sink: UsageSink | None = None,
    ) -> list[OfferedDirection]:
        """Two or three ways the story could go from here. Nothing is proposed or changed."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        about = await self._about(workspace_id, project_id, about_id)
        context, plan, entities = await self._plan_context(workspace_id, project_id, about)
        if not plan and not entities:
            raise ValidationError("There is nothing to go on yet. Describe the story first.")
        _predicates, entity_types = await self._vocabulary(workspace_id, project_id)
        sheet: DirectionSheet = await self._generate(
            messages=plan_next_messages(context, entity_types),
            response_format=DirectionSheet,
            prompt_version=PLAN_NEXT_PROMPT_VERSION,
            credential=credential,
            provider_base_url=provider_base_url,
            usage_sink=usage_sink,
        )
        offered: list[OfferedDirection] = []
        for direction in sheet.directions[:MAX_DIRECTIONS]:
            draft = PlanDraft(
                understood=direction.title.strip()[:200],
                new_entries=direction.new_entries[:MAX_NEW_ENTRIES],
                items=direction.items[:MAX_DRAFT_ITEMS],
            )
            operations, _discarded = plan_operations(
                draft,
                plan=plan,
                entities=entities,
                entity_types=entity_types,
                session_id="pdr_check",
            )
            adds = sum(isinstance(operation, CreatePlanItemOp) for operation in operations)
            # A direction that would add nothing usable is not offered.
            if not direction.title.strip() or adds == 0:
                continue
            offered.append(
                OfferedDirection(
                    title=direction.title.strip()[:200],
                    what_happens=direction.what_happens.strip()[:1200],
                    what_it_changes=[
                        change.strip()[:300]
                        for change in direction.what_it_changes
                        if change.strip()
                    ][:6],
                    adds=len(operations),
                    draft=draft,
                )
            )
        if not offered:
            raise ValidationError(
                "The assistant could not offer a direction that fits what is recorded. Try "
                "again, or add to the plan by hand."
            )
        return offered

    async def plan_from_direction(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        draft: PlanDraft,
        *,
        expected_revision: int,
    ) -> PlanProposalResult:
        """The direction an author picked, checked again from scratch, as changes to review.

        It arrives from the author's browser, so it is trusted no more than a model's
        output: the same typing and bounds apply, and no model is called.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        _text, plan, entities = await self._plan_context(workspace_id, project_id, None)
        return await self._propose(
            scope,
            workspace_id,
            project_id,
            draft,
            plan=plan,
            entities=entities,
            session_id=f"pdr_{uuid7()}",
            expected_revision=expected_revision,
            reason="a direction the author picked, awaiting review",
        )


def plan_questions_messages(context: str, description: str, depth: str) -> Messages:
    """The ``plan-questions`` prompt: ``context`` is the story bible and plan as listed."""
    system = (
        "You help an author plan a novel by asking, never by deciding. Ask four to "
        "seven short questions whose answers would let this part of the plan be "
        "written down: what is at stake, whose story each arc is, what turns, what "
        "is promised to the reader and where it is kept. Name the story's own "
        "people and places where that helps. Do not ask what the author has already "
        "said, and do not suggest answers.\n" + DEPTHS[depth]
    )
    user = context + (f"\n\nWhat the author says so far:\n{description}" if description else "")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def plan_draft_messages(
    context: str,
    description: str,
    depth: str,
    entity_types: set[str],
    answered: list[CharacterAnswer],
) -> Messages:
    """The ``plan-draft`` prompt."""
    interview = "\n".join(f"Q: {a.question}\nA: {a.answer.strip()}" for a in answered)
    system = (
        "Turn what an author has said about their story into plan items for them "
        "to review. A plan is what the author intends, not what is true in the "
        "story. Rules:\n"
        "- write down only what the author said or answered. Never invent a turn, "
        "an ending, a motive or a scene. What they have not decided goes in `gaps`, "
        "in plain words, and is left out of the plan.\n"
        "- kinds: story (the premise; at most one), arc (a line of change, usually "
        "one character's), beat (a turning point in an arc), scene (where a beat "
        "happens), thread (a question raised or a promise made to the reader).\n"
        "- an arc sits under the story; a beat under an arc; a scene under a beat. "
        "Use parent_ref with the id of an existing plan item or the ref of one you "
        "draft. Do not repeat an item that is already in the plan.\n"
        "- name the story's own entries by their exact ids, with a role: "
        "point_of_view and place (a scene, one each, only if the author said so), "
        "participant (who or what is in a beat or scene), about (whose arc or "
        "thread it is).\n"
        "- if the plan needs a person, place or thing that is not in the story "
        f"bible, add it to new_entries with a type from {sorted(entity_types)} and "
        "refer to it by its ref. Do not describe it.\n"
        "- when something sets up a question or a promise, draft a thread for it "
        "and list it in sets_up; list it in pays_off where it is answered.\n"
        "- `understood` restates what is being planned in one plain sentence.\n" + DEPTHS[depth]
    )
    user = (
        context
        + (f"\n\nWhat the author says:\n{description}" if description else "")
        + (f"\n\nAnswers:\n{interview}" if interview else "")
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def plan_next_messages(context: str, entity_types: set[str]) -> Messages:
    """The ``plan-next`` prompt."""
    system = (
        "An author asks what could happen next in their story. Offer two or three "
        "genuinely different directions for them to choose between, edit or turn "
        "down. You are offering options, not deciding. Rules:\n"
        "- each direction must follow from what the story bible and the plan "
        "already hold: name real people and places by their exact ids, and build "
        "on threads that are open.\n"
        "- say in `what_it_changes` what would be different if the story went this "
        "way: who it costs, what it closes off, which thread it answers.\n"
        "- give each direction the few plan items it would add (beats and scenes, "
        "with parent_ref set to where they belong), the same way a plan is "
        "drafted: kinds story, arc, beat, scene, thread; roles point_of_view, "
        "place, participant, about.\n"
        "- if a direction needs someone or somewhere new, add it to that "
        f"direction's new_entries with a type from {sorted(entity_types)}.\n"
        "- do not contradict anything recorded as fact."
    )
    user = context
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


__all__ = [
    "DEPTHS",
    "MAX_DESCRIPTION_LENGTH",
    "Direction",
    "DirectionSheet",
    "DraftPlanEntry",
    "DraftPlanItem",
    "DraftPlanRef",
    "OfferedDirection",
    "PlanDraft",
    "PlanProposalResult",
    "PlanWorkflow",
    "plan_draft_messages",
    "plan_next_messages",
    "plan_operations",
    "plan_questions_messages",
]
