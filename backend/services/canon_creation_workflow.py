"""Assisted creation: from a description to reviewable canon proposals.

The author describes something in their own words (one character, a place, or a
whole idea involving several things). The model may ask clarifying questions,
then drafts the entries *and the connections between them and what already
exists*. The result is an ordinary model-origin mutation set in the review
queue: nothing here writes canon.

Model output is untrusted. Every drafted item is checked against the project's
published vocabulary and typed before it becomes an operation; anything that
does not fit is discarded with a reason rather than coerced. A drafted entry
whose name matches an existing entry of the same type is linked to that entry
instead of being created again, whatever the model said.
"""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.marks import (
    IMPORTANCE_KEY,
    MARK_KEYS,
    NEEDS_DETAIL_KEY,
    TAGS_KEY,
    clean_importance,
    clean_tags,
    marks,
    needs_detail,
)
from backend.domain.canon.naming import normalize_name, slugify
from backend.domain.canon.propositions import (
    BooleanObject,
    EntityRefObject,
    IntegerObject,
    ObjectKind,
    Proposition,
    PropositionObject,
    StringObject,
)
from backend.domain.canon.registry import Cardinality, PredicateSpec
from backend.domain.canon.revisions import OriginKind
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.operations import (
    AssertBeliefOp,
    AssertFactOp,
    CanonOperation,
    CloseFactOp,
    CreateEntityOp,
    EvidenceRef,
    UpdateEntityOp,
)
from backend.domain.tenancy import Permission
from backend.services.canon_character_workflow import (
    MAX_ANSWER_LENGTH,
    MAX_ANSWERS,
    MAX_PROPOSED_ITEMS,
    CanonCharacterWorkflow,
    CharacterAnswer,
    CharacterProposalResult,
    CharacterQuestionSheet,
    DiscardedItem,
    UsageSink,
)
from backend.services.schema_suggestions import (
    PROMPT_RULE,
    SuggestedFactKind,
    SuggestedType,
    clean_suggestions,
)

logger = logging.getLogger(__name__)

QUESTIONS_PROMPT_VERSION = "creation-questions/1"
DRAFT_PROMPT_VERSION = "creation-draft/1"

MAX_DESCRIPTION_LENGTH = 8000
MAX_NAME_LENGTH = 200
# Existing entries offered to the model so it can link to them instead of duplicating.
MAX_CONTEXT_ENTITIES = 200
MAX_ATTRIBUTES = 20
MAX_ALIASES = 10

_QUESTION_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,60}$")


class CreationBrief(BaseModel):
    """What the author wants to add, in their own words."""

    description: str = ""
    # Both optional: a brief may describe one named thing or a whole idea.
    entity_type: str | None = None
    name: str | None = None
    # Set to fill in an entry that already exists; type and name then come from it.
    entity_id: str | None = None


class DraftAttribute(BaseModel):
    """One lasting trait. A list of pairs, because providers' strict structured
    output does not accept free-form objects."""

    key: str = Field(description="Short trait name, e.g. role, appearance")
    value: str


class DraftEntity(BaseModel):
    ref: str = Field(description="Local handle used by facts and beliefs in this draft")
    type: str = Field(description="One of the allowed entity types")
    name: str
    aliases: list[str] = Field(default_factory=list)
    attributes: list[DraftAttribute] = Field(
        default_factory=list, description="Stable descriptive traits, e.g. role, appearance"
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Up to three short lower-case phrases that tell it apart at a glance",
    )
    importance: str = Field(
        default="", description="main, supporting, minor or background; empty if unclear"
    )
    needs_more: bool = Field(
        default=False, description="True when the author said little about it so far"
    )


class DraftFact(BaseModel):
    subject_ref: str = Field(description="An existing entity id or a draft entity ref")
    predicate: str
    object_ref: str | None = Field(
        default=None, description="Existing entity id or draft ref, for entity predicates"
    )
    object_value: str | None = Field(default=None, description="Value for scalar predicates")
    question_id: str | None = None


class DraftBelief(BaseModel):
    holder_ref: str = Field(description="The character who holds the belief")
    about_ref: str | None = Field(default=None, description="Defaults to the holder")
    predicate: str
    object_ref: str | None = None
    object_value: str | None = None
    confidence: float = 0.7
    question_id: str | None = None


class CreationDraft(BaseModel):
    """Structured model output. Untrusted: validated and typed before it becomes operations."""

    entities: list[DraftEntity] = Field(default_factory=list)
    facts: list[DraftFact] = Field(default_factory=list)
    beliefs: list[DraftBelief] = Field(default_factory=list)
    suggested_types: list[SuggestedType] = Field(default_factory=list)
    suggested_fact_kinds: list[SuggestedFactKind] = Field(default_factory=list)


def _typed_object(
    spec: PredicateSpec, target_id: str | None, value: str | None, project_id: str
) -> tuple[PropositionObject | None, str]:
    if spec.object_shape is ObjectKind.ENTITY_REF:
        if target_id is None:
            return None, f"{spec.predicate}: what it points to is not a known entry"
        return EntityRefObject(entity_id=target_id, project_id=project_id), ""
    if value is None or not value.strip():
        return None, f"{spec.predicate}: missing value"
    text = value.strip()
    if spec.object_shape is ObjectKind.STRING:
        return StringObject(value=text), ""
    if spec.object_shape is ObjectKind.INTEGER:
        try:
            return IntegerObject(value=int(text)), ""
        except ValueError:
            return None, f"{spec.predicate}: {text!r} is not a whole number"
    if spec.object_shape is ObjectKind.BOOLEAN:
        if text.lower() in {"true", "yes"}:
            return BooleanObject(value=True), ""
        if text.lower() in {"false", "no"}:
            return BooleanObject(value=False), ""
        return None, f"{spec.predicate}: {text!r} is not yes or no"
    return None, f"{spec.predicate}: {spec.object_shape.value} values need manual entry"


def _subject_update(
    subject: CanonEntity, item: DraftEntity, evidence: list[EvidenceRef]
) -> UpdateEntityOp | None:
    """New details and names for the entry being filled in; never removes what is there."""
    attributes = {
        pair.key.strip(): pair.value.strip()
        for pair in item.attributes[:MAX_ATTRIBUTES]
        if pair.key.strip()
        and pair.value.strip()
        and subject.attributes.get(pair.key.strip()) != pair.value.strip()
    }
    seen = {normalize_name(subject.name), *map(normalize_name, subject.aliases)}
    aliases = []
    for alias in item.aliases[:MAX_ALIASES]:
        if alias.strip() and normalize_name(alias) not in seen:
            seen.add(normalize_name(alias))
            aliases.append(alias.strip())
    # Filling an entry in is what its "fill in later" mark was waiting for. Tags are
    # added to, and an importance the author already has is never overridden.
    merged = {**subject.attributes, **attributes}
    tags = clean_tags([*subject.attributes.get(TAGS_KEY, []), *item.tags])
    if tags:
        merged[TAGS_KEY] = tags
    level = clean_importance(item.importance)
    if level and IMPORTANCE_KEY not in merged:
        merged[IMPORTANCE_KEY] = level
    if attributes:
        merged.pop(NEEDS_DETAIL_KEY, None)
    if merged == subject.attributes and not aliases:
        return None
    return UpdateEntityOp(
        id=subject.id,
        attributes=merged if merged != subject.attributes else None,
        aliases=[*subject.aliases, *aliases] if aliases else None,
        evidence=evidence,
    )


class CanonCreationWorkflow(CanonCharacterWorkflow):
    """Describe → (questions) → draft entries and links → review queue."""

    async def _check_brief(
        self, brief: CreationBrief, entity_types: set[str], workspace_id: str, project_id: str
    ) -> tuple[CreationBrief, CanonEntity | None]:
        description = brief.description.strip()
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise ValidationError("The description is too long (up to 8000 characters).")
        if brief.entity_id:
            subject = await self._reader.get_entity(
                workspace_id=workspace_id, project_id=project_id, entity_id=brief.entity_id
            )
            if subject is None or not subject.is_active:
                raise NotFoundError("Entry not found.")
            return (
                CreationBrief(
                    description=description,
                    entity_type=subject.type,
                    name=subject.name,
                    entity_id=subject.id,
                ),
                subject,
            )
        if not description:
            raise ValidationError("Describe what you are adding.")
        name = (brief.name or "").strip() or None
        if name is not None and len(name) > MAX_NAME_LENGTH:
            raise ValidationError("The name is too long.")
        entity_type = (brief.entity_type or "").strip() or None
        if entity_type is not None and entity_type not in entity_types:
            raise ValidationError(f"Unknown entry type {entity_type!r}.")
        return CreationBrief(description=description, entity_type=entity_type, name=name), None

    async def _existing(self, workspace_id: str, project_id: str) -> list[CanonEntity]:
        return list(
            await self._reader.list_entities(
                workspace_id=workspace_id, project_id=project_id, limit=MAX_CONTEXT_ENTITIES
            )
        )

    async def _brief_text(
        self,
        brief: CreationBrief,
        existing: list[CanonEntity],
        subject: CanonEntity | None,
        workspace_id: str,
        project_id: str,
    ) -> str:
        lines = []
        if subject is not None:
            lines.append(
                f"The author is filling in an existing entry: {subject.type} “{subject.name}” "
                f"(id {subject.id}). Use that id for it; do not create it again."
            )
            if subject.aliases:
                lines.append(f"Also known as: {', '.join(subject.aliases)}")
            if subject.attributes:
                lines.append(f"Already recorded details: {subject.attributes}")
            names = {entity.id: entity.name for entity in existing}
            facts = await self._reader.list_facts(
                workspace_id=workspace_id, project_id=project_id, subject_id=subject.id, limit=50
            )
            for fact in facts:
                obj = fact.proposition.object
                target = getattr(obj, "entity_id", None)
                value = names.get(target, target) if target else getattr(obj, "value", "")
                lines.append(f"Already recorded: {fact.proposition.predicate} -> {value}")
        elif brief.entity_type or brief.name:
            lines.append(
                "The author is adding: "
                + " ".join(
                    part for part in (brief.entity_type, brief.name and f"“{brief.name}”") if part
                )
            )
        if brief.description:
            lines.append(f"Their description:\n{brief.description}")
        if existing:
            lines.append("Entries that already exist (link to these ids; never recreate them):")
            lines.extend(
                f"- {entity.id}: {entity.name} ({entity.type})"
                + (f", also {', '.join(entity.aliases)}" if entity.aliases else "")
                for entity in existing
                if subject is None or entity.id != subject.id
            )
        else:
            lines.append("Nothing exists in this story yet.")
        return "\n".join(lines)

    async def brief_questions(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        brief: CreationBrief,
        *,
        credential: str | None = None,
        provider_base_url: str | None = None,
        usage_sink: UsageSink | None = None,
    ) -> CharacterQuestionSheet:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        _, entity_types = await self._vocabulary(workspace_id, project_id)
        brief, subject = await self._check_brief(brief, entity_types, workspace_id, project_id)
        existing = await self._existing(workspace_id, project_id)
        sheet: CharacterQuestionSheet = await self._generate(
            system=(
                "You help an author build the story bible for a novel. They have described "
                "something they want to add. Ask five to eight short, specific questions whose "
                "answers would establish lasting facts about it: what it is like, and above all "
                "how it connects to other things in the story (who knows whom, who belongs to "
                "what, where things are, who owns what). Ask about connections to the existing "
                "entries by name where that is plausible. Do not ask about anything the "
                "description already settles, and do not ask about plot or prose."
            ),
            user=await self._brief_text(brief, existing, subject, workspace_id, project_id),
            response_format=CharacterQuestionSheet,
            prompt_version=QUESTIONS_PROMPT_VERSION,
            credential=credential,
            provider_base_url=provider_base_url,
            usage_sink=usage_sink,
        )
        # Question ids become evidence locations, so they must be plain identifiers.
        questions = [
            question.model_copy(
                update={"id": question.id if _QUESTION_ID.match(question.id) else f"q{index + 1}"}
            )
            for index, question in enumerate(sheet.questions[:MAX_ANSWERS])
        ]
        return CharacterQuestionSheet(questions=questions)

    async def propose_from_brief(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        brief: CreationBrief,
        *,
        answers: list[CharacterAnswer],
        expected_revision: int,
        session_id: str | None = None,
        credential: str | None = None,
        provider_base_url: str | None = None,
        usage_sink: UsageSink | None = None,
    ) -> CharacterProposalResult:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if len(answers) > MAX_ANSWERS:
            raise ValidationError(f"Provide at most {MAX_ANSWERS} answers.")
        for answer in answers:
            if not _QUESTION_ID.match(answer.question_id):
                raise ValidationError(f"Invalid question id {answer.question_id!r}.")
            if not answer.answer.strip() or len(answer.answer) > MAX_ANSWER_LENGTH:
                raise ValidationError("Each answer must be non-empty and bounded.")
        predicates, entity_types = await self._vocabulary(workspace_id, project_id)
        brief, subject = await self._check_brief(brief, entity_types, workspace_id, project_id)
        if subject is not None and not brief.description and not answers:
            raise ValidationError("Add a description or answer at least one question.")
        existing = await self._existing(workspace_id, project_id)
        if subject is not None and all(entity.id != subject.id for entity in existing):
            existing.append(subject)

        predicate_lines = "\n".join(
            f"- {spec.predicate}: subject is one of {sorted(spec.subject_types)}; object is "
            f"{spec.object_shape.value}"
            + (f" of type {sorted(spec.object_types)}" if spec.object_types else "")
            for spec in predicates.values()
        )
        interview = "\n\n".join(
            f"[{answer.question_id}] Q: {answer.question}\nA: {answer.answer}" for answer in answers
        )
        draft: CreationDraft = await self._generate(
            system=(
                "Turn an author's description (and their answers, if any) into structured "
                "story-bible entries and the connections between them. Only state what the "
                "author actually wrote; never invent names, events or relationships. Rules:\n"
                "- create an entity for each distinct person, place, group or thing the author "
                "mentions that does not already exist, including one referred to only by a "
                "description (“her professor”, “the old lighthouse”). Name such an entry by "
                "that description (“Mara’s professor”) so the author can name it properly "
                "later, and connect it with a fact. Give each a short unique ref.\n"
                "- when the author refers to an entry that already exists, do not create it: "
                "use its id directly as the ref in facts.\n"
                "- when the author is filling in an existing entry, include it once in entities "
                "with its exact name and type, carrying any new attributes or aliases.\n"
                "- be generous with entries: anyone or anything the description or an answer "
                "mentions, even in passing, gets an entity with its name and type, so the "
                "author can fill it in later. Set needs_more to true on any entity the author "
                "said little about.\n"
                "- give each entity up to three short tags, in lower case, that tell it apart "
                "at a glance: its role in the story (protagonist, antagonist, mentor, rival) "
                "and how it relates to the main characters (“John’s professor”, “family of "
                "the protagonist”). Use only what the author’s words support.\n"
                "- set importance to main (the story is about them), supporting, minor or "
                "background, when the author’s words make it clear; otherwise leave it empty.\n"
                f"- entity types must be one of: {sorted(entity_types)}\n"
                "- put lasting descriptive traits (role, appearance, temperament) in the "
                "entity's attributes as short key/value text.\n"
                "- connect things with facts wherever the author states a relationship. Use "
                "only these predicates, respecting subject and object types:\n"
                f"{predicate_lines}\n"
                "- facts are objective truths. A belief is what a character thinks, which may "
                "be false; never mark a belief as true or false.\n"
                "- set question_id to the id of the answer an item came from, when it did.\n"
                + PROMPT_RULE
            ),
            user=await self._brief_text(brief, existing, subject, workspace_id, project_id)
            + (f"\n\nAnswers:\n{interview}" if interview else ""),
            response_format=CreationDraft,
            prompt_version=DRAFT_PROMPT_VERSION,
            credential=credential,
            provider_base_url=provider_base_url,
            usage_sink=usage_sink,
        )

        # An open conversation keeps its id, so the evidence points at the whole of it.
        session_id = session_id or f"brf_{uuid7()}"
        operations, discarded = self._draft_operations(
            draft,
            brief=brief,
            subject=subject,
            existing=existing,
            predicates=predicates,
            entity_types=entity_types,
            session_id=session_id,
            answered={answer.question_id for answer in answers},
            project_id=project_id,
        )
        operations = await self._replace_single_valued(
            operations, predicates, workspace_id, project_id
        )
        if not operations:
            raise ValidationError(
                "Nothing new could be drafted from this description. Everything it mentions "
                "may already exist, or it may need more detail."
            )
        mutation = await self._mutation.propose(
            scope,
            workspace_id=workspace_id,
            project_id=project_id,
            operations=operations,
            expected_head=expected_revision,
            origin=OriginKind.MODEL.value,
        )
        # MutationService resolves a set through the caller's project scope.
        review_scope = AccessScope(
            actor_user_id=scope.actor_user_id,
            auth_mode=scope.auth_mode,
            correlation_id=scope.correlation_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        mutation = await self._mutation.submit(
            review_scope, mutation.id, "assisted creation awaiting review"
        )
        validation = await self._mutation.validate(review_scope, mutation.id)
        log_event(
            logger,
            "creation.workflow.proposed",
            mutation_id=mutation.id,
            operations=len(operations),
            discarded=len(discarded),
            prompt_version=DRAFT_PROMPT_VERSION,
        )
        return CharacterProposalResult(
            mutation=mutation,
            session_id=session_id,
            operation_count=len(operations),
            validation=validation,
            discarded=discarded,
            suggestions=clean_suggestions(
                draft.suggested_types,
                draft.suggested_fact_kinds,
                entity_types=entity_types,
                predicates=set(predicates),
            ),
        )

    async def _replace_single_valued(
        self,
        operations: list[CanonOperation],
        predicates: dict[str, PredicateSpec],
        workspace_id: str,
        project_id: str,
    ) -> list[CanonOperation]:
        """A kind of fact that holds one value at a time: a new value replaces the old.

        The current fact is ended just before the new one is added, so the reviewer sees
        the replacement. A value that is already recorded is not added again.
        """
        settled: list[CanonOperation] = []
        for operation in operations:
            spec = (
                predicates.get(operation.proposition.predicate)
                if isinstance(operation, AssertFactOp)
                else None
            )
            if (
                not isinstance(operation, AssertFactOp)
                or spec is None
                or spec.cardinality is not Cardinality.ONE
                or operation.proposition.subject_id.startswith("tmp:")
            ):
                settled.append(operation)
                continue
            current = await self._reader.list_facts(
                workspace_id=workspace_id,
                project_id=project_id,
                subject_id=operation.proposition.subject_id,
                predicate=operation.proposition.predicate,
                limit=50,
            )
            if any(fact.proposition.object == operation.proposition.object for fact in current):
                continue
            settled.extend(CloseFactOp(id=fact.id, evidence=operation.evidence) for fact in current)
            settled.append(operation)
        return settled

    @staticmethod
    def _draft_operations(
        draft: CreationDraft,
        *,
        brief: CreationBrief,
        subject: CanonEntity | None,
        existing: list[CanonEntity],
        predicates: dict[str, PredicateSpec],
        entity_types: set[str],
        session_id: str,
        answered: set[str],
        project_id: str,
    ) -> tuple[list[CanonOperation], list[DiscardedItem]]:
        operations: list[CanonOperation] = []
        discarded: list[DiscardedItem] = []

        def evidence(question_id: str | None) -> list[EvidenceRef]:
            if question_id is None or question_id not in answered:
                return [EvidenceRef(source_artefact_id=session_id)]
            return [EvidenceRef(source_artefact_id=session_id, location=f"question:{question_id}")]

        active = [entity for entity in existing if entity.is_active]
        # ref → (entity id, entity type). Existing ids resolve to themselves.
        known: dict[str, tuple[str, str]] = {
            entity.id: (entity.id, entity.type) for entity in active
        }
        # (type, normalized name or alias) → existing entity, for duplicate avoidance.
        by_name: dict[tuple[str, str], CanonEntity] = {}
        for entity in active:
            for label in (entity.name, *entity.aliases):
                by_name.setdefault((entity.type, normalize_name(label)), entity)

        entities = list(draft.entities[:MAX_PROPOSED_ITEMS])
        # The thing the author named must not be silently dropped by the model.
        if subject is None and brief.name and brief.entity_type:
            wanted = normalize_name(brief.name)
            if not any(normalize_name(item.name) == wanted for item in entities):
                entities.insert(
                    0, DraftEntity(ref="__subject__", type=brief.entity_type, name=brief.name)
                )

        drafted: set[tuple[str, str]] = set()
        for index, item in enumerate(entities):
            name = item.name.strip()
            if not name or len(name) > MAX_NAME_LENGTH:
                discarded.append(DiscardedItem("entity", "missing or over-long name"))
                continue
            if item.type not in entity_types:
                discarded.append(DiscardedItem("entity", f"unknown entry type {item.type!r}"))
                continue
            if item.ref in known:
                discarded.append(DiscardedItem("entity", f"{name}: duplicate reference"))
                continue
            key = (item.type, normalize_name(name))
            match = by_name.get(key)
            if match is not None:
                # Already in the story: link to it rather than creating a twin.
                known[item.ref] = (match.id, match.type)
                if subject is not None and match.id == subject.id:
                    update = _subject_update(subject, item, evidence(None))
                    if update is not None:
                        operations.append(update)
                continue
            if key in drafted:
                discarded.append(DiscardedItem("entity", f"{name}: drafted twice"))
                continue
            drafted.add(key)
            temporary = f"tmp:{slugify(name) or 'entry'}-{index}"
            known[item.ref] = (temporary, item.type)
            seen = {normalize_name(name)}
            aliases = []
            for alias in item.aliases[:MAX_ALIASES]:
                if alias.strip() and normalize_name(alias) not in seen:
                    seen.add(normalize_name(alias))
                    aliases.append(alias.strip())
            attributes = {
                pair.key.strip(): pair.value.strip()
                for pair in item.attributes[:MAX_ATTRIBUTES]
                if pair.key.strip() and pair.value.strip() and pair.key.strip() not in MARK_KEYS
            }
            # An entry made from a passing mention is marked to be filled in later.
            attributes.update(
                marks(
                    tags=item.tags,
                    importance=item.importance,
                    needs_detail=item.needs_more or not attributes,
                )
            )
            operations.append(
                CreateEntityOp(
                    id=temporary,
                    type=item.type,
                    name=name,
                    aliases=aliases,
                    attributes=attributes,
                    evidence=evidence(None),
                )
            )

        def proposition(
            subject_ref: str | None, predicate: str, object_ref: str | None, value: str | None
        ) -> tuple[Proposition | None, str]:
            spec = predicates.get(predicate)
            if spec is None:
                return None, f"unsupported kind of fact {predicate!r}"
            subject = known.get(subject_ref or "")
            if subject is None:
                return None, f"{predicate}: what it is about is not a known entry"
            if subject[1] not in spec.subject_types:
                return None, f"{predicate}: cannot be about a {subject[1]}"
            target = known.get(object_ref or "")
            if spec.object_shape is ObjectKind.ENTITY_REF and target is not None:
                if spec.object_types and target[1] not in spec.object_types:
                    return None, f"{predicate}: cannot point to a {target[1]}"
                if target[0] == subject[0]:
                    return None, f"{predicate}: an entry cannot point to itself"
            obj, reason = _typed_object(spec, target[0] if target else None, value, project_id)
            if obj is None:
                return None, reason
            return Proposition(subject_id=subject[0], predicate=predicate, object=obj), ""

        stated: set[str] = set()
        single: set[tuple[str, str]] = set()
        for index, fact in enumerate(draft.facts[:MAX_PROPOSED_ITEMS]):
            built, reason = proposition(
                fact.subject_ref, fact.predicate, fact.object_ref, fact.object_value
            )
            if built is None:
                discarded.append(DiscardedItem("fact", reason))
                continue
            signature = built.model_dump_json()
            if signature in stated:
                continue
            stated.add(signature)
            spec = predicates[built.predicate]
            if spec.cardinality is Cardinality.ONE:
                # Two values at once for something that holds one: the first is kept.
                if (built.subject_id, built.predicate) in single:
                    discarded.append(
                        DiscardedItem("fact", f"{built.predicate}: only one at a time")
                    )
                    continue
                single.add((built.subject_id, built.predicate))
            operations.append(
                AssertFactOp(
                    id=f"tmp:fact-{index}", proposition=built, evidence=evidence(fact.question_id)
                )
            )

        for index, belief in enumerate(draft.beliefs[:MAX_PROPOSED_ITEMS]):
            holder = known.get(belief.holder_ref)
            if holder is None or holder[1] != "Character":
                discarded.append(
                    DiscardedItem("belief", "only a known character can hold a belief")
                )
                continue
            built, reason = proposition(
                belief.about_ref or belief.holder_ref,
                belief.predicate,
                belief.object_ref,
                belief.object_value,
            )
            if built is None:
                discarded.append(DiscardedItem("belief", reason))
                continue
            operations.append(
                AssertBeliefOp(
                    id=f"tmp:belief-{index}",
                    holder_entity_id=holder[0],
                    proposition=built,
                    confidence=min(1.0, max(0.0, belief.confidence)),
                    source_ref=session_id,
                    evidence=evidence(belief.question_id),
                )
            )
        # Filling an entry in, in any way, is what its "fill in later" mark was waiting for.
        if subject is not None and needs_detail(subject.attributes) and operations:
            for position, operation in enumerate(operations):
                if isinstance(operation, UpdateEntityOp) and operation.id == subject.id:
                    current = (
                        subject.attributes if operation.attributes is None else operation.attributes
                    )
                    cleared = {k: v for k, v in current.items() if k != NEEDS_DETAIL_KEY}
                    operations[position] = operation.model_copy(update={"attributes": cleared})
                    break
            else:
                cleared = {k: v for k, v in subject.attributes.items() if k != NEEDS_DETAIL_KEY}
                operations.append(
                    UpdateEntityOp(id=subject.id, attributes=cleared, evidence=evidence(None))
                )
        return operations, discarded


__all__ = [
    "DRAFT_PROMPT_VERSION",
    "MAX_DESCRIPTION_LENGTH",
    "QUESTIONS_PROMPT_VERSION",
    "CanonCreationWorkflow",
    "CreationBrief",
    "CreationDraft",
    "DraftAttribute",
    "DraftBelief",
    "DraftEntity",
    "DraftFact",
]
