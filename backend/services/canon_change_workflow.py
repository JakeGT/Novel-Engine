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
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, field_validator

from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.domain.assist import SchemaSuggestion
from backend.domain.canon.assertions import Belief, Fact, FactVisibility
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import Event, EventParticipant
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.naming import normalize_name
from backend.domain.canon.registry import Cardinality, PredicateSpec
from backend.domain.canon.revisions import OriginKind
from backend.domain.errors import ValidationError
from backend.domain.identity import AccessScope
from backend.domain.journal import MutationSet
from backend.domain.operations import (
    AmendEventOp,
    AmendFactOp,
    AssertFactOp,
    CanonOperation,
    CloseBeliefOp,
    CloseFactOp,
    CreateEntityOp,
    CreateEventOp,
    EvidenceRef,
    RetireEntityOp,
    UpdateEntityOp,
)
from backend.domain.tenancy import Permission
from backend.domain.worldtime import describe, resolve, resolve_all, span_of, spans_overlap
from backend.services.assist_time import StoryTime, to_world_time
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
from backend.services.schema_suggestions import (
    PROMPT_RULE,
    SuggestedFactKind,
    SuggestedType,
    clean_suggestions,
)
from backend.services.story_time_service import anchor_lookup

logger = logging.getLogger(__name__)

CHANGE_PROMPT_VERSION = "change-by-instruction/1"

MAX_INSTRUCTION_LENGTH = 4000
MAX_CONTEXT_FACTS = 300
MAX_CONTEXT_BELIEFS = 200
MAX_CONTEXT_EVENTS = 200
MAX_CHANGES = 80
MAX_QUESTIONS = 5
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")


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


class Elsewhere(BaseModel):
    place: str = Field(description="One of: history, people, setup, assistant, media")
    what: str = Field(description="One plain sentence saying what the author wants done there")


class Invite(BaseModel):
    role: str = Field(description="editor (may change the story) or reader (may only read)")
    email: str | None = Field(default=None, description="Only if the author gave one")


# Things an author may ask for that are not changes to the story bible, and where
# each is done. The assistant points the way; it never does these itself.
ELSEWHERE = {
    "history": "undoing changes or going back to an earlier version",
    "people": "inviting people, changing what they may do, or removing them",
    "setup": "adding or changing types of entry and kinds of fact",
    "assistant": "the AI key, the model in use, or the daily allowance",
    "media": "pictures and other files",
}


class TimedFact(DraftFact):
    starts: StoryTime | None = Field(default=None, description="When it starts being true")
    ends: StoryTime | None = Field(default=None, description="When it stops being true")


class NewEvent(BaseModel):
    ref: str = Field(description="A short handle for this event, used by other times")
    name: str
    kind: str = Field(default="happening", description="A word for what kind of event it is")
    participants: list[str] = Field(
        default_factory=list, description="Ids of existing entries, or refs of new ones"
    )
    when: StoryTime | None = None


class EventTime(BaseModel):
    event: str = Field(description="The id of an existing event, exactly as listed")
    when: StoryTime


class FactTime(BaseModel):
    fact: str = Field(description="The id of an existing fact, exactly as listed")
    starts: StoryTime | None = None
    ends: StoryTime | None = None


class Secrecy(BaseModel):
    fact: str = Field(description="The id of an existing fact, exactly as listed")
    visibility: str = Field(
        description="secret (only those who believe it know), common (everyone knows), or ordinary"
    )


class ChangePlan(BaseModel):
    """Structured model output. Untrusted: resolved and typed before it becomes operations."""

    understood: str = Field(description="One sentence restating what will change")
    questions: list[str] = Field(
        default_factory=list,
        description="Asked instead of guessing when the instruction is ambiguous",
    )
    new_entities: list[DraftEntity] = Field(default_factory=list)
    entity_changes: list[EntityChange] = Field(default_factory=list)
    add_facts: list[TimedFact] = Field(default_factory=list)

    @field_validator("add_facts", mode="before")
    @classmethod
    def _plain_facts(cls, value: object) -> object:
        # A fact drafted without a time is still a fact here.
        if isinstance(value, list):
            return [
                item.model_dump()
                if isinstance(item, DraftFact) and not isinstance(item, TimedFact)
                else item
                for item in value
            ]
        return value

    end_facts: list[RecordRef] = Field(default_factory=list)
    add_beliefs: list[DraftBelief] = Field(default_factory=list)
    end_beliefs: list[RecordRef] = Field(default_factory=list)
    archive_entities: list[RecordRef] = Field(default_factory=list)
    new_events: list[NewEvent] = Field(default_factory=list)
    event_times: list[EventTime] = Field(default_factory=list)
    fact_times: list[FactTime] = Field(default_factory=list)
    secrecy: list[Secrecy] = Field(default_factory=list)
    suggested_types: list[SuggestedType] = Field(default_factory=list)
    suggested_fact_kinds: list[SuggestedFactKind] = Field(default_factory=list)
    notes: list[str] = Field(
        default_factory=list,
        description="Anything else in the story this affects that the author should know",
    )
    elsewhere: list[Elsewhere] = Field(
        default_factory=list,
        description="Requests that are not changes to the story bible, and where they are done",
    )
    back_to_version: int | None = Field(
        default=None,
        description=(
            "Only when the author asks to undo or go back: the number of the last version to "
            "keep. Everything after it would be undone."
        ),
    )
    invite: Invite | None = Field(
        default=None, description="Only when the author asks to invite someone"
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
    suggestions: list[SchemaSuggestion] = field(default_factory=list)
    # (place, what the author wants done there), for requests that are not story changes.
    elsewhere: list[tuple[str, str]] = field(default_factory=list)
    # The version to go back to, when the author asked to undo. Nothing is undone here:
    # the author is shown what going back would change and decides.
    back_to_version: int | None = None
    # (role, email or None) for an invitation the author asked for. Nothing is sent or made here.
    invite: tuple[str, str | None] | None = None


def _fact_extras(fact: Fact, unit: str, event_names: dict[str, str]) -> str:
    extras = []
    if fact.visibility is not FactVisibility.ORDINARY:
        extras.append(fact.visibility.value)
    if fact.starts is not None:
        extras.append("from " + describe(fact.starts, unit=unit, event_name=event_names.get))
    if fact.ends is not None:
        extras.append("until " + describe(fact.ends, unit=unit, event_name=event_names.get))
    return f" ({'; '.join(extras)})" if extras else ""


def _value(fact_object: object, names: dict[str, str]) -> str:
    target = getattr(fact_object, "entity_id", None)
    if target:
        return f"{names.get(target, target)} ({target})"
    return str(getattr(fact_object, "value", ""))


@dataclass
class _Timed:
    """The story-time and secrecy part of a change, and how to tell values apart in time."""

    asserts: list[CanonOperation]
    new_events: list[CanonOperation] = field(default_factory=list)
    event_changes: list[CanonOperation] = field(default_factory=list)
    fact_changes: list[CanonOperation] = field(default_factory=list)
    times: dict[str, Any] = field(default_factory=dict)
    known: dict[str, Any] = field(default_factory=dict)
    amended: dict[str, dict[str, Any]] = field(default_factory=dict)
    kept_both: bool = False

    def apart(self, old: Fact, new: AssertFactOp) -> bool:
        """Whether an old value and its replacement are each true at different times."""
        resolved = resolve_all({**self.known, **self.times})
        lookup = anchor_lookup(resolved)
        change = self.amended.get(old.id, {})
        old_span = span_of(
            resolve(change.get("starts", old.starts), lookup),
            resolve(change.get("ends", old.ends), lookup),
        )
        new_span = span_of(resolve(new.starts, lookup), resolve(new.ends, lookup))
        if not old_span.stated or not new_span.stated or spans_overlap(old_span, new_span):
            return False
        self.kept_both = True
        return True


class CanonChangeWorkflow(CanonCreationWorkflow):
    """Any instruction about the story → one reviewable proposal of everything it implies."""

    async def _unit(self, workspace_id: str, project_id: str) -> str:
        if self._clocks is None:
            return "year"
        unit = await self._clocks.get_unit(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id
        )
        return unit or "year"

    async def _events(self, workspace_id: str, project_id: str) -> list[Event]:
        events = await self._reader.list_events(
            workspace_id=workspace_id, project_id=project_id, limit=MAX_CONTEXT_EVENTS
        )
        return [event for event in events if event.retired_revision is None]

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
        unit = await self._unit(workspace_id, project_id)
        events = await self._events(workspace_id, project_id)
        event_names = {event.id: event.name for event in events}
        if facts:
            lines.append("Facts (true in the story):")
            lines.extend(
                f"- {fact.id}: {names.get(fact.proposition.subject_id, fact.proposition.subject_id)}"
                f" {fact.proposition.predicate} {_value(fact.proposition.object, names)}"
                + _fact_extras(fact, unit, event_names)
                for fact in facts
            )
        lines.append(f"Story time is counted in {unit}s.")
        if events:
            lines.append("Events:")
            lines.extend(
                f"- {event.id}: {event.name}"
                + (
                    f" (when: {describe(event.when, unit=unit, event_name=event_names.get)})"
                    if event.when is not None
                    else ""
                )
                for event in events
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
        session_id: str | None = None,
        recent_versions: list[str] | None = None,
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
                "- some requests are not changes to the story bible. Do not attempt them; "
                "list each in `elsewhere` with the place it is done: "
                + "; ".join(f"{place} ({what})" for place, what in ELSEWHERE.items())
                + ".\n"
                "- when the author asks to undo something or go back, also set "
                "`back_to_version` to the last version to keep, worked out from the list of "
                "recent versions. If you cannot tell which version they mean, ask.\n"
                "- when the author asks to invite someone, also set `invite` with the role "
                "they should have (reader unless the author says they may make changes) and "
                "an email address only if the author gave one.\n"
                "- story time: to say when something happened, use new_events (for something "
                "new that happened) or event_times (for a listed event). A time is at a point, "
                'between two points, or relative to an event ("three years before the war" is '
                "relative, anchor the war, offset 3, direction before). Keep the author's words "
                "in `words`. Never invent a date the author did not give.\n"
                "- to say when a fact is true, use fact_times for a listed fact, or starts/ends "
                'on a new fact. When a one-at-a-time fact changes at a stated time ("she moved '
                'to Saltmere in year 10"), end the old one with fact_times ends and give the '
                "new one starts, instead of ending the old fact.\n"
                '- "X doesn\'t know this" or "this is a secret": put the fact in secrecy '
                'as secret, and end any belief by which X knows it. "Everyone knows": common.\n'
                "- `understood` restates the change in one plain sentence.\n" + PROMPT_RULE
            ),
            user=(
                f"{story}\n\n"
                + (
                    "Recent versions, newest first:\n" + "\n".join(recent_versions) + "\n\n"
                    if recent_versions
                    else ""
                )
                + f"Instruction:\n{instruction}"
            ),
            response_format=ChangePlan,
            prompt_version=CHANGE_PROMPT_VERSION,
            credential=credential,
            provider_base_url=provider_base_url,
            usage_sink=usage_sink,
        )

        session_id = session_id or f"chg_{uuid7()}"
        understood = plan.understood.strip()[:500]
        questions = [q.strip()[:300] for q in plan.questions if q.strip()][:MAX_QUESTIONS]
        operations, discarded, notes = self._change_operations(
            plan,
            entities=entities,
            facts=facts,
            beliefs=beliefs,
            events=await self._events(workspace_id, project_id),
            predicates=predicates,
            entity_types=entity_types,
            session_id=session_id,
            project_id=project_id,
        )
        notes = [*notes, *(note.strip()[:400] for note in plan.notes if note.strip())][:12]
        suggestions = clean_suggestions(
            plan.suggested_types,
            plan.suggested_fact_kinds,
            entity_types=entity_types,
            predicates=set(predicates),
        )
        elsewhere: list[tuple[str, str]] = []
        for item in plan.elsewhere[:4]:
            place = item.place.strip().lower()
            if place in ELSEWHERE and all(place != seen for seen, _ in elsewhere):
                elsewhere.append((place, item.what.strip()[:300]))
        back_to = plan.back_to_version
        if back_to is None or not 0 <= back_to < expected_revision:
            back_to = None
        elif all(place != "history" for place, _ in elsewhere):
            elsewhere.append(("history", f"Go back to version {back_to}."))
        invite: tuple[str, str | None] | None = None
        if plan.invite is not None:
            role = {"editor": "editor", "reader": "viewer", "viewer": "viewer"}.get(
                plan.invite.role.strip().lower(), "viewer"
            )
            email = (plan.invite.email or "").strip()
            invite = (role, email if _EMAIL.match(email) else None)
            if all(place != "people" for place, _ in elsewhere):
                elsewhere.append(("people", "Invite someone to this library."))
        if not operations:
            if questions or elsewhere or suggestions:
                # Ambiguous, or not a change to the story: ask or point the way.
                # Nothing is proposed.
                return ChangeResult(
                    session_id=session_id,
                    understood=understood,
                    questions=questions,
                    notes=notes,
                    discarded=discarded,
                    suggestions=suggestions,
                    elsewhere=elsewhere,
                    back_to_version=back_to,
                    invite=invite,
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
            suggestions=suggestions,
            elsewhere=elsewhere,
            back_to_version=back_to,
            invite=invite,
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
        events: list[Event] | None = None,
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
                facts=list[DraftFact](plan.add_facts[:MAX_CHANGES]),
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
        asserts: list[CanonOperation] = [op for op in drafted if not isinstance(op, CreateEntityOp)]

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

        timed = self._time_operations(
            plan,
            creates=creates,
            asserts=asserts,
            active=active,
            open_facts=open_facts,
            events=events or [],
            evidence=evidence,
            discarded=discarded,
        )
        asserts = timed.asserts

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
                if same and fact.id not in ending and not timed.apart(fact, op):
                    # Each true for its own stretch of story time: both stand.
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
        if timed.kept_both:
            notes.append(
                "Both values are kept, each for its own stretch of story time; "
                "the timeline shows when each is true."
            )
        # Events before the facts that may be timed by them, and an old value's end
        # before the new value that follows it.
        operations = [
            *creates,
            *updates,
            *closes,
            *timed.new_events,
            *timed.event_changes,
            *timed.fact_changes,
            *asserts,
            *retires,
        ]
        return operations[: MAX_CHANGES * 2], discarded, notes

    def _time_operations(
        self,
        plan: ChangePlan,
        *,
        creates: Sequence[CanonOperation],
        asserts: list[CanonOperation],
        active: dict[str, CanonEntity],
        open_facts: dict[str, Fact],
        events: list[Event],
        evidence: list[EvidenceRef],
        discarded: list[DiscardedItem],
    ) -> _Timed:
        """Events placed in time, facts bounded in time, and secrets (ADR-020)."""
        timed = _Timed(asserts=list(asserts))
        known_events = {event.id: event for event in events}
        anchors: dict[str, str] = {}
        for new_event in plan.new_events[:MAX_CHANGES]:
            if new_event.ref.strip() and new_event.name.strip():
                anchors[new_event.ref.strip()] = format_canon_id("event", str(uuid7()))
        created_by_name = {
            normalize_name(op.name): op.id for op in creates if isinstance(op, CreateEntityOp)
        }
        new_refs = {
            entity.ref: created_by_name.get(normalize_name(entity.name))
            for entity in plan.new_entities
        }

        def when(value: StoryTime | None, what: str) -> Any:
            if value is None:
                return None
            converted, reason = to_world_time(value, anchors=anchors)
            if converted is None:
                discarded.append(DiscardedItem("time", f"{what}: {reason}"))
                return False
            anchor = getattr(converted, "anchor_event_id", None)
            if anchor is not None and anchor not in known_events and anchor not in anchors.values():
                discarded.append(DiscardedItem("time", f"{what}: {anchor!r} is not a known event"))
                return False
            return converted

        for item in plan.new_events[:MAX_CHANGES]:
            event_id = anchors.get(item.ref.strip())
            if event_id is None:
                discarded.append(DiscardedItem("event", "an event needs a name and a ref"))
                continue
            participants = []
            for ref in item.participants[:20]:
                entity_id = ref if ref in active else new_refs.get(ref)
                if entity_id is None:
                    discarded.append(DiscardedItem("event", f"{item.name}: {ref!r} is not known"))
                    continue
                participants.append(EventParticipant(entity_id=entity_id, role="participant"))
            placed = when(item.when, item.name)
            timed.new_events.append(
                CreateEventOp(
                    id=event_id,
                    kind=(item.kind.strip() or "happening")[:60],
                    name=item.name.strip()[:MAX_NAME_LENGTH],
                    participants=participants,
                    when=placed or None,
                    evidence=evidence,
                )
            )
            timed.times[event_id] = placed or None
        for event_time in plan.event_times[:MAX_CHANGES]:
            if event_time.event not in known_events:
                discarded.append(
                    DiscardedItem("event", f"{event_time.event!r} is not a listed event")
                )
                continue
            placed = when(event_time.when, known_events[event_time.event].name)
            if placed is False:
                continue
            timed.event_changes.append(
                AmendEventOp(id=event_time.event, when=placed, evidence=evidence)
            )
            timed.times[event_time.event] = placed

        changes: dict[str, dict[str, Any]] = {}
        for fact_time in plan.fact_times[:MAX_CHANGES]:
            if fact_time.fact not in open_facts:
                discarded.append(DiscardedItem("fact", f"{fact_time.fact!r} is not a current fact"))
                continue
            starts, ends = when(fact_time.starts, "a fact"), when(fact_time.ends, "a fact")
            if starts is False or ends is False:
                continue
            change = changes.setdefault(fact_time.fact, {})
            if starts is not None:
                change["starts"] = starts
            if ends is not None:
                change["ends"] = ends
        for secrecy in plan.secrecy[:MAX_CHANGES]:
            visibility = secrecy.visibility.strip().lower()
            if secrecy.fact not in open_facts:
                discarded.append(DiscardedItem("fact", f"{secrecy.fact!r} is not a current fact"))
            elif visibility not in {v.value for v in FactVisibility}:
                discarded.append(DiscardedItem("fact", f"{visibility!r} is not secret or common"))
            elif FactVisibility(visibility) is not open_facts[secrecy.fact].visibility:
                changes.setdefault(secrecy.fact, {})["visibility"] = FactVisibility(visibility)
        for fact_id, change in changes.items():
            if change:
                timed.fact_changes.append(AmendFactOp(id=fact_id, evidence=evidence, **change))
                timed.amended[fact_id] = change

        for index, added in enumerate(plan.add_facts[:MAX_CHANGES]):
            if added.starts is None and added.ends is None:
                continue
            target = f"tmp:fact-{index}"
            for position, op in enumerate(timed.asserts):
                if isinstance(op, AssertFactOp) and op.id == target:
                    starts = when(added.starts, added.predicate)
                    ends = when(added.ends, added.predicate)
                    if starts is False or ends is False:
                        break
                    timed.asserts[position] = op.model_copy(update={"starts": starts, "ends": ends})
        timed.known = {event.id: event.when for event in events}
        return timed


__all__ = [
    "CHANGE_PROMPT_VERSION",
    "MAX_INSTRUCTION_LENGTH",
    "CanonChangeWorkflow",
    "ChangePlan",
    "ChangeResult",
    "EntityChange",
    "RecordRef",
]
