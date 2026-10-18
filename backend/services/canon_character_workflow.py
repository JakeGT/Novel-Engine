"""Guided character workflow over canonical proposals (2F).

The model interviews the author about a canon ``Character`` and turns the
answers into a *proposal*: an ordinary model-origin mutation set that waits in
the review queue. Nothing here writes canon. Every proposed operation is typed,
cites the questionnaire answer it came from, and reaches canon only after a
human reviews it through the mutation review path and the commit transaction
validates it. Model output that cannot be expressed as a valid typed operation
is discarded with a reason instead of being coerced.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.domain.canon.entities import CanonEntity
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
from backend.domain.canon.registry import CORE_ENTITY_TYPES, CORE_PREDICATES, PredicateSpec
from backend.domain.canon.revisions import OriginKind
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.journal import MutationSet
from backend.domain.operations import (
    AssertBeliefOp,
    AssertFactOp,
    CanonOperation,
    CreateEntityOp,
    EvidenceRef,
    UpdateEntityOp,
)
from backend.domain.prompts import Messages, prompt_version
from backend.domain.tenancy import Permission
from backend.ports import AIGateway, AIUsage, CanonRepositoryPort, SchemaVersionStorePort
from backend.services.mutation_service import MutationService, ValidationReport
from backend.services.schema_compiler import compile_definition

logger = logging.getLogger(__name__)

UsageSink = Callable[[AIUsage, str], Awaitable[None]]

QUESTIONS_PROMPT_VERSION = prompt_version("character-questions")
PROPOSAL_PROMPT_VERSION = prompt_version("character-proposal")

MAX_CONTEXT_ENTITIES = 60
MAX_ANSWERS = 40
MAX_ANSWER_LENGTH = 4000
MAX_PROPOSED_ITEMS = 60

_QUESTION_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,60}$")


class CharacterQuestion(BaseModel):
    id: str = Field(description="Short unique identifier, e.g. personality_1")
    question: str
    category: str = Field(description="e.g. appearance, personality, relationships, beliefs")


class CharacterQuestionSheet(BaseModel):
    questions: list[CharacterQuestion]


class CharacterAnswer(BaseModel):
    question_id: str
    question: str
    answer: str


class ProposedEntity(BaseModel):
    ref: str = Field(description="Local handle used by facts/beliefs in this proposal")
    type: str = Field(description="One of the allowed entity types")
    name: str


class ProposedFact(BaseModel):
    predicate: str
    object_ref: str | None = Field(
        default=None, description="Existing entity id or a new entity ref, for entity predicates"
    )
    object_value: str | None = Field(default=None, description="Value for scalar predicates")
    question_id: str | None = None


class ProposedBelief(BaseModel):
    about_ref: str | None = Field(
        default=None, description="Entity the belief is about; defaults to the character"
    )
    predicate: str
    object_ref: str | None = None
    object_value: str | None = None
    confidence: float = 0.7
    question_id: str | None = None


class CharacterProposal(BaseModel):
    """Structured model output. Untrusted: validated and typed before it becomes operations."""

    attributes: dict[str, str] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    new_entities: list[ProposedEntity] = Field(default_factory=list)
    facts: list[ProposedFact] = Field(default_factory=list)
    beliefs: list[ProposedBelief] = Field(default_factory=list)


@dataclass(frozen=True)
class DiscardedItem:
    kind: str
    reason: str


@dataclass(frozen=True)
class CharacterProposalResult:
    mutation: MutationSet
    session_id: str
    operation_count: int
    validation: ValidationReport
    discarded: list[DiscardedItem] = field(default_factory=list)
    # What the assistant thought the project's setup was missing (cleaned; changes nothing).
    suggestions: list[Any] = field(default_factory=list)


class CanonCharacterWorkflow:
    def __init__(
        self,
        *,
        ai: AIGateway,
        mutation: MutationService,
        reader: CanonRepositoryPort,
        schema_store: SchemaVersionStorePort | None = None,
        tenancy: object | None = None,
        local_mode: bool = False,
        model: str | None = None,
        clocks: object | None = None,
    ) -> None:
        self._ai = ai
        # The unit each story counts time in; only the change workflow reads it.
        self._clocks = clocks
        self._mutation = mutation
        self._reader = reader
        self._schema_store = schema_store
        self._tenancy = tenancy
        self._local_mode = local_mode
        self._model = model

    async def _authorize(
        self, scope: AccessScope, workspace_id: str, project_id: str, permission: Permission
    ) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(scope, workspace_id, project_id, permission)  # type: ignore[attr-defined]

    async def _character(self, workspace_id: str, project_id: str, entity_id: str) -> CanonEntity:
        entity = await self._reader.get_entity(
            workspace_id=workspace_id, project_id=project_id, entity_id=entity_id
        )
        if entity is None or not entity.is_active or entity.type != "Character":
            raise NotFoundError("Character not found.")
        return entity

    async def _vocabulary(
        self, workspace_id: str, project_id: str
    ) -> tuple[dict[str, PredicateSpec], set[str]]:
        """Predicates and entity types the project's published schema allows."""
        predicates = dict(CORE_PREDICATES)
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

    async def _context(
        self, workspace_id: str, project_id: str, character: CanonEntity
    ) -> tuple[str, list[CanonEntity]]:
        """Bounded canon context: the character's open facts and the project's entities.

        This is objective canon only. POV-filtered context arrives with Phase 3;
        the questionnaire is an author-facing tool, not point-of-view prose.
        """
        entities = await self._reader.list_entities(
            workspace_id=workspace_id, project_id=project_id, limit=MAX_CONTEXT_ENTITIES
        )
        facts = await self._reader.list_facts(
            workspace_id=workspace_id, project_id=project_id, subject_id=character.id, limit=50
        )
        lines = [f"Character: {character.name} (id {character.id})"]
        if character.aliases:
            lines.append(f"Also known as: {', '.join(character.aliases)}")
        if character.attributes:
            lines.append(f"Known attributes: {character.attributes}")
        for fact in facts:
            obj = fact.proposition.object
            value = getattr(obj, "entity_id", None) or getattr(obj, "value", "")
            lines.append(f"Fact: {fact.proposition.predicate} -> {value}")
        lines.append("Existing entities (reuse these ids instead of inventing duplicates):")
        lines.extend(
            f"- {entity.id}: {entity.name} ({entity.type})"
            for entity in entities
            if entity.id != character.id
        )
        return "\n".join(lines), entities

    async def _generate(
        self,
        *,
        messages: Messages,
        response_format: type[BaseModel],
        prompt_version: str,
        credential: str | None,
        provider_base_url: str | None,
        usage_sink: UsageSink | None,
    ) -> Any:
        started = time.perf_counter()
        outcome = "error"
        try:
            response = await self._ai.generate_structured(
                model=self._model,
                messages=messages,
                response_format=response_format,
                api_key=credential,
                base_url=provider_base_url,
            )
            outcome = "ok"
        finally:
            # Never logs prompt or answer text: only the versioned contract and outcome.
            log_event(
                logger,
                "character.workflow.model_call",
                prompt_version=prompt_version,
                outcome=outcome,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
        if usage_sink is not None:
            await usage_sink(response.usage, response.model)
        log_event(
            logger,
            "character.workflow.model_usage",
            prompt_version=prompt_version,
            model=response.model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
        )
        return response.parsed

    async def generate_questions(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        entity_id: str,
        *,
        credential: str | None = None,
        provider_base_url: str | None = None,
        usage_sink: UsageSink | None = None,
    ) -> CharacterQuestionSheet:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        character = await self._character(workspace_id, project_id, entity_id)
        context, _ = await self._context(workspace_id, project_id, character)
        sheet: CharacterQuestionSheet = await self._generate(
            messages=character_questions_messages(context),
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

    async def propose_from_answers(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        entity_id: str,
        *,
        answers: list[CharacterAnswer],
        expected_revision: int,
        credential: str | None = None,
        provider_base_url: str | None = None,
        usage_sink: UsageSink | None = None,
    ) -> CharacterProposalResult:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if not answers or len(answers) > MAX_ANSWERS:
            raise ValidationError(f"Provide between 1 and {MAX_ANSWERS} answers.")
        for answer in answers:
            if not _QUESTION_ID.match(answer.question_id):
                raise ValidationError(f"Invalid question id {answer.question_id!r}.")
            if not answer.answer.strip() or len(answer.answer) > MAX_ANSWER_LENGTH:
                raise ValidationError("Each answer must be non-empty and bounded.")
        character = await self._character(workspace_id, project_id, entity_id)
        predicates, entity_types = await self._vocabulary(workspace_id, project_id)
        context, _ = await self._context(workspace_id, project_id, character)

        proposal: CharacterProposal = await self._generate(
            messages=character_proposal_messages(context, predicates, entity_types, answers),
            response_format=CharacterProposal,
            prompt_version=PROPOSAL_PROMPT_VERSION,
            credential=credential,
            provider_base_url=provider_base_url,
            usage_sink=usage_sink,
        )

        session_id = f"qnr_{uuid7()}"
        operations, discarded = await self._to_operations(
            proposal,
            character=character,
            predicates=predicates,
            entity_types=entity_types,
            session_id=session_id,
            answered={answer.question_id for answer in answers},
            workspace_id=workspace_id,
            project_id=project_id,
        )
        if not operations:
            raise ValidationError("The model produced no usable proposals from these answers.")
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
            review_scope, mutation.id, "guided character proposal awaiting review"
        )
        validation = await self._mutation.validate(review_scope, mutation.id)
        # Kept for the author when they chose to have suggestions like this kept.
        mutation = await self._mutation.keep_if_allowed(
            review_scope, mutation, validation=validation
        )
        log_event(
            logger,
            "character.workflow.proposed",
            mutation_id=mutation.id,
            operations=len(operations),
            discarded=len(discarded),
            prompt_version=PROPOSAL_PROMPT_VERSION,
        )
        return CharacterProposalResult(
            mutation=mutation,
            session_id=session_id,
            operation_count=len(operations),
            validation=validation,
            discarded=discarded,
        )

    async def _to_operations(
        self,
        proposal: CharacterProposal,
        *,
        character: CanonEntity,
        predicates: dict[str, PredicateSpec],
        entity_types: set[str],
        session_id: str,
        answered: set[str],
        workspace_id: str,
        project_id: str,
    ) -> tuple[list[CanonOperation], list[DiscardedItem]]:
        operations: list[CanonOperation] = []
        discarded: list[DiscardedItem] = []

        def evidence(question_id: str | None) -> list[EvidenceRef]:
            if question_id is None or question_id not in answered:
                return [EvidenceRef(source_artefact_id=session_id)]
            return [EvidenceRef(source_artefact_id=session_id, location=f"question:{question_id}")]

        # New entities become temporary ids that later operations may reference.
        references: dict[str, str] = {}
        for index, item in enumerate(proposal.new_entities[:MAX_PROPOSED_ITEMS]):
            if item.type not in entity_types:
                discarded.append(DiscardedItem("entity", f"unknown entity type {item.type!r}"))
                continue
            if not item.name.strip() or item.ref in references:
                discarded.append(DiscardedItem("entity", "missing name or duplicate ref"))
                continue
            temporary = f"tmp:{slugify(item.name) or 'entity'}-{index}"
            references[item.ref] = temporary
            operations.append(
                CreateEntityOp(
                    id=temporary, type=item.type, name=item.name.strip(), evidence=evidence(None)
                )
            )

        async def resolve(ref: str | None) -> str | None:
            if ref is None:
                return None
            if ref in references:
                return references[ref]
            if ref == character.id:
                return character.id
            existing = await self._reader.get_entity(
                workspace_id=workspace_id, project_id=project_id, entity_id=ref
            )
            return existing.id if existing is not None and existing.is_active else None

        async def typed_object(
            spec: PredicateSpec, object_ref: str | None, object_value: str | None
        ) -> tuple[PropositionObject | None, str]:
            if spec.object_shape is ObjectKind.ENTITY_REF:
                target = await resolve(object_ref)
                if target is None:
                    return None, f"{spec.predicate}: object does not resolve to an entity"
                return EntityRefObject(entity_id=target, project_id=project_id), ""
            if object_value is None or not object_value.strip():
                return None, f"{spec.predicate}: missing value"
            value = object_value.strip()
            if spec.object_shape is ObjectKind.STRING:
                return StringObject(value=value), ""
            if spec.object_shape is ObjectKind.INTEGER:
                try:
                    return IntegerObject(value=int(value)), ""
                except ValueError:
                    return None, f"{spec.predicate}: {value!r} is not an integer"
            if spec.object_shape is ObjectKind.BOOLEAN:
                if value.lower() in {"true", "yes"}:
                    return BooleanObject(value=True), ""
                if value.lower() in {"false", "no"}:
                    return BooleanObject(value=False), ""
                return None, f"{spec.predicate}: {value!r} is not a boolean"
            return None, f"{spec.predicate}: {spec.object_shape.value} values need manual entry"

        attributes = {
            key.strip(): value.strip()
            for key, value in proposal.attributes.items()
            if key.strip() and value.strip()
        }
        known = {normalize_name(character.name), *map(normalize_name, character.aliases)}
        new_aliases = []
        for alias in proposal.aliases:
            if alias.strip() and normalize_name(alias) not in known:
                known.add(normalize_name(alias))
                new_aliases.append(alias.strip())
        if attributes or new_aliases:
            operations.append(
                UpdateEntityOp(
                    id=character.id,
                    attributes={**character.attributes, **attributes} if attributes else None,
                    aliases=[*character.aliases, *new_aliases] if new_aliases else None,
                    evidence=evidence(None),
                )
            )

        for index, fact in enumerate(proposal.facts[:MAX_PROPOSED_ITEMS]):
            spec = predicates.get(fact.predicate)
            if spec is None or "Character" not in spec.subject_types:
                discarded.append(DiscardedItem("fact", f"unsupported predicate {fact.predicate!r}"))
                continue
            obj, reason = await typed_object(spec, fact.object_ref, fact.object_value)
            if obj is None:
                discarded.append(DiscardedItem("fact", reason))
                continue
            operations.append(
                AssertFactOp(
                    id=f"tmp:fact-{index}",
                    proposition=Proposition(
                        subject_id=character.id, predicate=fact.predicate, object=obj
                    ),
                    evidence=evidence(fact.question_id),
                )
            )

        for index, belief in enumerate(proposal.beliefs[:MAX_PROPOSED_ITEMS]):
            spec = predicates.get(belief.predicate)
            if spec is None:
                discarded.append(
                    DiscardedItem("belief", f"unsupported predicate {belief.predicate!r}")
                )
                continue
            subject = await resolve(belief.about_ref) if belief.about_ref else character.id
            if subject is None:
                discarded.append(DiscardedItem("belief", "subject does not resolve to an entity"))
                continue
            obj, reason = await typed_object(spec, belief.object_ref, belief.object_value)
            if obj is None:
                discarded.append(DiscardedItem("belief", reason))
                continue
            operations.append(
                AssertBeliefOp(
                    id=f"tmp:belief-{index}",
                    holder_entity_id=character.id,
                    proposition=Proposition(
                        subject_id=subject, predicate=belief.predicate, object=obj
                    ),
                    confidence=min(1.0, max(0.0, belief.confidence)),
                    source_ref=session_id,
                    evidence=evidence(belief.question_id),
                )
            )
        return operations, discarded


def character_questions_messages(context: str) -> Messages:
    """The ``character-questions`` prompt: ``context`` is the character and the entries."""
    system = (
        "You help an author develop a character for a novel. Ask specific, concrete "
        "questions whose answers establish stable facts: appearance, personality, "
        "relationships, where they are, what they own, what groups they belong to, "
        "and what they believe (including things they may be wrong about). Do not ask "
        "about anything the context already settles."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": context}]


def character_proposal_messages(
    context: str,
    predicates: dict[str, PredicateSpec],
    entity_types: set[str],
    answers: list[CharacterAnswer],
) -> Messages:
    """The ``character-proposal`` prompt."""
    predicate_lines = "\n".join(
        f"- {spec.predicate}: object is {spec.object_shape.value}"
        + (f" of type {sorted(spec.object_types)}" if spec.object_types else "")
        for spec in predicates.values()
        if "Character" in spec.subject_types
    )
    interview = "\n\n".join(
        f"[{answer.question_id}] Q: {answer.question}\nA: {answer.answer}" for answer in answers
    )
    system = (
        "Convert an author's answers about one character into structured proposals. "
        "Only state what the answers actually say. Rules:\n"
        "- facts are objective truths about the character; beliefs are what the "
        "character thinks, which may be false. Never mark a belief as true or false.\n"
        "- use only these predicates for the character:\n"
        f"{predicate_lines}\n"
        f"- new entities may only use these types: {sorted(entity_types)}\n"
        "- reuse an existing entity id when the answer refers to a known entity.\n"
        "- set question_id to the id of the answer each item came from.\n"
        "- put stable descriptive traits (appearance, temperament) in attributes."
    )
    user = f"{context}\n\nAnswers:\n{interview}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


__all__ = [
    "PROPOSAL_PROMPT_VERSION",
    "QUESTIONS_PROMPT_VERSION",
    "CanonCharacterWorkflow",
    "CharacterAnswer",
    "CharacterProposal",
    "CharacterProposalResult",
    "CharacterQuestion",
    "CharacterQuestionSheet",
    "DiscardedItem",
    "ProposedBelief",
    "ProposedEntity",
    "ProposedFact",
    "character_proposal_messages",
    "character_questions_messages",
]
