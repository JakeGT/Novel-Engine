"""The God Hand: change anything in the story by saying so.

The author gives one instruction in plain words ("Mara is Tobin's sister, not his
rival", "move the guild to Saltmere", "merge these two characters"). The model
reads the story bible and returns every change the instruction implies, of any
kind: new entries, renames, new and removed details and names, facts added and
ended, beliefs added and ended, entries archived.

It has access to everything and authority over nothing. The result is an
ordinary model-origin proposal in the review queue; canon changes only when a
person approves it (ADR-012, ADR-014). Model output is untrusted: every item is
resolved against real records and typed, and what does not fit is discarded with
a reason. When the instruction is ambiguous the workflow returns the model's
questions instead of guessing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.domain.canon.assertions import Belief, Fact
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.naming import normalize_name
from backend.domain.canon.registry import Cardinality, PredicateSpec
from backend.domain.canon.revisions import OriginKind
from backend.domain.errors import ValidationError
from backend.domain.identity import AccessScope
from backend.domain.journal import MutationSet
from backend.domain.operations import (
    AssertFactOp,
    CanonOperation,
    CloseBeliefOp,
    CloseFactOp,
    CreateEntityOp,
    EvidenceRef,
    RetireEntityOp,
    UpdateEntityOp,
)
from backend.domain.tenancy import Permission
from backend.services.canon_character_workflow import DiscardedItem, UsageSink
from backend.services.canon_creation_workflow import (
    MAX_ALIASES,
    MAX_ATTRIBUTES,
    MAX_CONTEXT_ENTITIES,
    MAX_NAME_LENGTH,
    CanonCreationWorkflow,
    CreationBrief,
    CreationDraft,
    DraftAttribute,
    DraftBelief,
    DraftEntity,
    DraftFact,
)
from backend.services.mutation_service import ValidationReport

logger = logging.getLogger(__name__)

CHANGE_PROMPT_VERSION = "change-by-instruction/1"

MAX_INSTRUCTION_LENGTH = 4000
MAX_CONTEXT_FACTS = 300
MAX_CONTEXT_BELIEFS = 200
MAX_CHANGES = 80
MAX_QUESTIONS = 5


class EntityChange(BaseModel):
    ref: str = Field(description="The id of the existing entry to change")
    new_name: str | None = Field(default=None, description="Only when the entry is renamed")
    add_aliases: list[str] = Field(default_factory=list)
    remove_aliases: list[str] = Field(default_factory=list)
    set_attributes: list[DraftAttribute] = Field(default_factory=list)
    remove_attributes: list[str] = Field(default_factory=list)


class RecordRef(BaseModel):
    id: str = Field(description="The id of an existing record, exactly as listed")
    reason: str = ""


class ChangePlan(BaseModel):
    """Structured model output. Untrusted: resolved and typed before it becomes operations."""

    understood: str = Field(description="One sentence restating what will change")
    questions: list[str] = Field(
        default_factory=list,
        description="Asked instead of guessing when the instruction is ambiguous",
    )
    new_entities: list[DraftEntity] = Field(default_factory=list)
    entity_changes: list[EntityChange] = Field(default_factory=list)
    add_facts: list[DraftFact] = Field(default_factory=list)
    end_facts: list[RecordRef] = Field(default_factory=list)
    add_beliefs: list[DraftBelief] = Field(default_factory=list)
    end_beliefs: list[RecordRef] = Field(default_factory=list)
    archive_entities: list[RecordRef] = Field(default_factory=list)
    notes: list[str] = Field(
        default_factory=list,
        description="Anything else in the story this affects that the author should know",
    )


@dataclass(frozen=True)
class ChangeResult:
    session_id: str
    understood: str
    # Set when there is something to review; None when the model only asked questions.
    mutation: MutationSet | None = None
    operation_count: int = 0
    validation: ValidationReport | None = None
    questions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    discarded: list[DiscardedItem] = field(default_factory=list)


def _value(fact_object: object, names: dict[str, str]) -> str:
    target = getattr(fact_object, "entity_id", None)
    if target:
        return f"{names.get(target, target)} ({target})"
    return str(getattr(fact_object, "value", ""))


class CanonChangeWorkflow(CanonCreationWorkflow):
    """Any instruction about the story → one reviewable proposal of everything it implies."""

    async def _story(
        self, workspace_id: str, project_id: str, focus: CanonEntity | None
    ) -> tuple[str, list[CanonEntity], list[Fact], list[Belief]]:
        entities = await self._existing(workspace_id, project_id)
        if focus is not None and all(entity.id != focus.id for entity in entities):
            entities.append(focus)
        facts = list(
            await self._reader.list_facts(
                workspace_id=workspace_id, project_id=project_id, limit=MAX_CONTEXT_FACTS
            )
        )
        beliefs = list(
            await self._reader.list_beliefs(
                workspace_id=workspace_id, project_id=project_id, limit=MAX_CONTEXT_BELIEFS
            )
        )
        names = {entity.id: entity.name for entity in entities}
        lines = []
        if focus is not None:
            lines.append(f"The author is looking at: {focus.name} ({focus.id}).")
        lines.append("Entries:" if entities else "There are no entries yet.")
        for entity in entities:
            parts = [f"- {entity.id}: {entity.name} ({entity.type})"]
            if entity.aliases:
                parts.append(f"also {', '.join(entity.aliases)}")
            if entity.attributes:
                parts.append(f"details {entity.attributes}")
            lines.append("; ".join(parts))
        if facts:
            lines.append("Facts (true in the story):")
            lines.extend(
                f"- {fact.id}: {names.get(fact.proposition.subject_id, fact.proposition.subject_id)}"
                f" {fact.proposition.predicate} {_value(fact.proposition.object, names)}"
                for fact in facts
            )
        if beliefs:
            lines.append("Beliefs (what a character thinks, which may be false):")
            lines.extend(
                f"- {belief.id}: {names.get(belief.holder_entity_id, belief.holder_entity_id)} believes "
                f"{names.get(belief.proposition.subject_id, belief.proposition.subject_id)}"
                f" {belief.proposition.predicate} {_value(belief.proposition.object, names)}"
                for belief in beliefs
            )
        if len(entities) >= MAX_CONTEXT_ENTITIES or len(facts) >= MAX_CONTEXT_FACTS:
            lines.append("(The story is larger than this list; ask if something seems missing.)")
        return "\n".join(lines), entities, facts, beliefs

    async def change_by_instruction(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        instruction: str,
        expected_revision: int,
        focus_entity_id: str | None = None,
        credential: str | None = None,
        provider_base_url: str | None = None,
        usage_sink: UsageSink | None = None,
    ) -> ChangeResult:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        instruction = instruction.strip()
        if not instruction or len(instruction) > MAX_INSTRUCTION_LENGTH:
            raise ValidationError("Say what should change (up to 4000 characters).")
        predicates, entity_types = await self._vocabulary(workspace_id, project_id)
        focus = None
        if focus_entity_id:
            _, focus = await self._check_brief(
                CreationBrief(entity_id=focus_entity_id), entity_types, workspace_id, project_id
            )
        story, entities, facts, beliefs = await self._story(workspace_id, project_id, focus)

        predicate_lines = "\n".join(
            f"- {spec.predicate}: subject is one of {sorted(spec.subject_types)}; object is "
            f"{spec.object_shape.value}"
            + (f" of type {sorted(spec.object_types)}" if spec.object_types else "")
            + ("; only one at a time" if spec.cardinality is Cardinality.ONE else "")
            for spec in predicates.values()
        )
        plan: ChangePlan = await self._generate(
            system=(
                "You are the editor of an author's story bible. The author gives one instruction "
                "and you work out every change it implies, and only those. You never decide the "
                "story: do what was asked, and nothing that was not. Rules:\n"
                "- refer to existing records by the exact ids listed. Never invent an id.\n"
                "- if the instruction could mean more than one thing, or names something you "
                "cannot find, put short questions in `questions` and propose nothing.\n"
                "- to correct a fact, end the old one (end_facts) and add the right one "
                "(add_facts). Facts are never edited in place.\n"
                "- to rename, use entity_changes.new_name; facts follow the entry automatically.\n"
                "- to merge two entries, move what is known about one onto the other (details, "
                "names, facts), then archive the one that is no longer needed.\n"
                "- archive an entry only when the author asks for it to be removed or merged.\n"
                "- create an entry for anything new the instruction names; give it a short ref.\n"
                f"- entry types must be one of: {sorted(entity_types)}\n"
                "- use only these kinds of fact, respecting subject and object types:\n"
                f"{predicate_lines}\n"
                "- a belief is what a character thinks and may be false; never mark it true or "
                "false.\n"
                "- in `notes`, tell the author about anything else this affects that they did "
                "not mention: facts that now look wrong, characters whose beliefs no longer "
                "match. Do not change those things yourself.\n"
                "- `understood` restates the change in one plain sentence."
            ),
            user=f"{story}\n\nInstruction:\n{instruction}",
            response_format=ChangePlan,
            prompt_version=CHANGE_PROMPT_VERSION,
            credential=credential,
            provider_base_url=provider_base_url,
            usage_sink=usage_sink,
        )

        session_id = f"chg_{uuid7()}"
        understood = plan.understood.strip()[:500]
        questions = [q.strip()[:300] for q in plan.questions if q.strip()][:MAX_QUESTIONS]
        operations, discarded, notes = self._change_operations(
            plan,
            entities=entities,
            facts=facts,
            beliefs=beliefs,
            predicates=predicates,
            entity_types=entity_types,
            session_id=session_id,
            project_id=project_id,
        )
        notes = [*notes, *(note.strip()[:400] for note in plan.notes if note.strip())][:12]
        if not operations:
            if questions:
                # Ambiguous: ask rather than guess. Nothing is proposed.
                return ChangeResult(
                    session_id=session_id,
                    understood=understood,
                    questions=questions,
                    notes=notes,
                    discarded=discarded,
                )
            raise ValidationError(
                "That instruction did not lead to any change. It may already be true, or it "
                "may refer to something the story bible does not have."
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
        mutation = await self._mutation.submit(
            review_scope, mutation.id, "change by instruction awaiting review"
        )
        validation = await self._mutation.validate(review_scope, mutation.id)
        log_event(
            logger,
            "change.workflow.proposed",
            mutation_id=mutation.id,
            operations=len(operations),
            discarded=len(discarded),
            questions=len(questions),
            prompt_version=CHANGE_PROMPT_VERSION,
        )
        return ChangeResult(
            session_id=session_id,
            understood=understood,
            mutation=mutation,
            operation_count=len(operations),
            validation=validation,
            questions=questions,
            notes=notes,
            discarded=discarded,
        )

    def _change_operations(
        self,
        plan: ChangePlan,
        *,
        entities: list[CanonEntity],
        facts: list[Fact],
        beliefs: list[Belief],
        predicates: dict[str, PredicateSpec],
        entity_types: set[str],
        session_id: str,
        project_id: str,
    ) -> tuple[list[CanonOperation], list[DiscardedItem], list[str]]:
        discarded: list[DiscardedItem] = []
        notes: list[str] = []
        evidence = [EvidenceRef(source_artefact_id=session_id)]
        active = {entity.id: entity for entity in entities if entity.is_active}
        open_facts = {fact.id: fact for fact in facts}
        open_beliefs = {belief.id: belief for belief in beliefs}

        # New entries and new facts/beliefs go through the same typed path as assisted
        # creation, which also links by name instead of duplicating.
        drafted, draft_discards = self._draft_operations(
            CreationDraft(
                entities=plan.new_entities[:MAX_CHANGES],
                facts=plan.add_facts[:MAX_CHANGES],
                beliefs=plan.add_beliefs[:MAX_CHANGES],
            ),
            brief=CreationBrief(),
            subject=None,
            existing=entities,
            predicates=predicates,
            entity_types=entity_types,
            session_id=session_id,
            answered=set(),
            project_id=project_id,
        )
        discarded.extend(draft_discards)
        creates = [op for op in drafted if isinstance(op, CreateEntityOp)]
        asserts = [op for op in drafted if not isinstance(op, CreateEntityOp)]

        updates: list[CanonOperation] = []
        changed: set[str] = set()
        for change in plan.entity_changes[:MAX_CHANGES]:
            entity = active.get(change.ref)
            if entity is None:
                discarded.append(
                    DiscardedItem("change", f"{change.ref!r} is not an existing entry")
                )
                continue
            if entity.id in changed:
                discarded.append(DiscardedItem("change", f"{entity.name}: changed twice"))
                continue
            name = (change.new_name or "").strip()
            if len(name) > MAX_NAME_LENGTH:
                discarded.append(
                    DiscardedItem("change", f"{entity.name}: the new name is too long")
                )
                continue
            renamed = bool(name) and name != entity.name
            removed = {normalize_name(alias) for alias in change.remove_aliases}
            aliases = [alias for alias in entity.aliases if normalize_name(alias) not in removed]
            seen = {normalize_name(name if renamed else entity.name), *map(normalize_name, aliases)}
            for alias in change.add_aliases[:MAX_ALIASES]:
                if alias.strip() and normalize_name(alias) not in seen:
                    seen.add(normalize_name(alias))
                    aliases.append(alias.strip())
            attributes = {
                key: value
                for key, value in entity.attributes.items()
                if key not in {item.strip() for item in change.remove_attributes}
            }
            for pair in change.set_attributes[:MAX_ATTRIBUTES]:
                if pair.key.strip() and pair.value.strip():
                    attributes[pair.key.strip()] = pair.value.strip()
            new_aliases = aliases if aliases != list(entity.aliases) else None
            new_attributes = attributes if attributes != dict(entity.attributes) else None
            if not renamed and new_aliases is None and new_attributes is None:
                continue
            changed.add(entity.id)
            updates.append(
                UpdateEntityOp(
                    id=entity.id,
                    name=name if renamed else None,
                    aliases=new_aliases,
                    attributes=new_attributes,
                    evidence=evidence,
                )
            )

        closes: list[CanonOperation] = []
        ending: set[str] = set()
        for ref in plan.end_facts[:MAX_CHANGES]:
            if ref.id not in open_facts:
                discarded.append(DiscardedItem("fact", f"{ref.id!r} is not a current fact"))
            elif ref.id not in ending:
                ending.add(ref.id)
                closes.append(CloseFactOp(id=ref.id, evidence=evidence))
        for ref in plan.end_beliefs[:MAX_CHANGES]:
            if ref.id not in open_beliefs:
                discarded.append(DiscardedItem("belief", f"{ref.id!r} is not a current belief"))
            elif ref.id not in ending:
                ending.add(ref.id)
                closes.append(CloseBeliefOp(id=ref.id, evidence=evidence))

        # A kind of fact that holds one value at a time: adding a new value replaces
        # the old one, whether or not the model remembered to end it.
        for op in asserts:
            if not isinstance(op, AssertFactOp):
                continue
            spec = predicates.get(op.proposition.predicate)
            if spec is None or spec.cardinality is not Cardinality.ONE:
                continue
            for fact in facts:
                same = (
                    fact.proposition.subject_id == op.proposition.subject_id
                    and fact.proposition.predicate == op.proposition.predicate
                )
                if same and fact.id not in ending:
                    ending.add(fact.id)
                    closes.append(CloseFactOp(id=fact.id, evidence=evidence))
                    subject = active.get(fact.proposition.subject_id)
                    phrase = spec.predicate.replace("_", " ").lower()
                    notes.append(
                        f"{subject.name if subject else 'An entry'} can be “{phrase}” only one "
                        "thing at a time, so the current one is ended."
                    )

        retires: list[CanonOperation] = []
        archived: set[str] = set()
        for ref in plan.archive_entities[:MAX_CHANGES]:
            entity = active.get(ref.id)
            if entity is None:
                discarded.append(DiscardedItem("archive", f"{ref.id!r} is not an existing entry"))
                continue
            if entity.id in archived:
                continue
            archived.add(entity.id)
            retires.append(RetireEntityOp(id=entity.id, evidence=evidence))
            # Deterministic consequence report: what will still point at it afterwards.
            mentions = 0
            for fact in facts:
                if fact.id in ending:
                    continue
                target = getattr(fact.proposition.object, "entity_id", None)
                if entity.id in (fact.proposition.subject_id, target):
                    mentions += 1
            if mentions:
                notes.append(
                    f"{mentions} current fact{'s' if mentions != 1 else ''} will still mention "
                    f"{entity.name} after it is archived."
                )

        # Order matters to the validator: things must exist before they are linked, and
        # an old single-valued fact must end before its replacement is added.
        operations = [*creates, *updates, *closes, *asserts, *retires]
        return operations[: MAX_CHANGES * 2], discarded, notes


__all__ = [
    "CHANGE_PROMPT_VERSION",
    "MAX_INSTRUCTION_LENGTH",
    "CanonChangeWorkflow",
    "ChangePlan",
    "ChangeResult",
    "EntityChange",
    "RecordRef",
]
