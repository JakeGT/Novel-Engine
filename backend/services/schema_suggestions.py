"""What the assistant thinks a project's setup is missing.

When an author says something the project's types and kinds of fact cannot
express, the assistant is told not to bend it into the wrong one. It describes
what is missing instead. Those descriptions are model output, so they are cleaned
here into a small, bounded form before anyone sees them, and they change nothing:
an owner decides whether to add them, through the same additive schema path as
adding a type by hand.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from backend.domain.assist import SchemaSuggestion
from backend.domain.canon.propositions import ObjectKind
from backend.domain.schema_defs import (
    IDENTIFIER_RE,
    Cardinality,
    EntityExtension,
    PredicateExtension,
    SchemaDefinition,
)

MAX_SUGGESTIONS_PER_KIND = 5
_TEXT = 240

# What a kind of fact points at, in the words the model is given.
_OBJECT_KINDS = {
    "entry": ObjectKind.ENTITY_REF,
    "text": ObjectKind.STRING,
    "whole_number": ObjectKind.INTEGER,
    "yes_no": ObjectKind.BOOLEAN,
}


class SuggestedType(BaseModel):
    name: str = Field(description="Singular, e.g. Instrument")
    description: str = Field(default="", description="What this type of entry is")
    reason: str = Field(default="", description="What the author said that needs it")


class SuggestedFactKind(BaseModel):
    name: str = Field(description="A short verb phrase, e.g. STUDIED_UNDER")
    description: str = Field(default="", description="What it means, as a phrase")
    subject_types: list[str] = Field(
        default_factory=list, description="Entry types it can be said about"
    )
    points_to: str = Field(default="entry", description="One of: entry, text, whole_number, yes_no")
    object_types: list[str] = Field(
        default_factory=list, description="Entry types it can point to, when points_to is entry"
    )
    one_at_a_time: bool = Field(
        default=False, description="True when an entry can have only one of these at once"
    )
    reason: str = Field(default="", description="What the author said that needs it")


PROMPT_RULE = (
    "- if the author states something that none of the allowed entry types or kinds of fact "
    "can express, do not bend it into the wrong one and never use a kind of fact that is not "
    "listed. Leave that statement out of facts and beliefs, and describe what is missing in "
    "suggested_types or suggested_fact_kinds: its name, what it means, which entry types it "
    "connects, and what the author said that needs it. Suggest only what this text needs, a "
    "few at most. Prefer an existing kind of fact whenever one fits."
)


def _type_name(text: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", text.strip())
    name = "".join(part[:1].upper() + part[1:] for part in parts if part)
    return re.sub(r"^[0-9]+", "", name)[:64]


def _fact_name(text: str) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "_", text.strip()).strip("_").upper()
    return re.sub(r"^[0-9_]+", "", name)[:64]


def clean_suggestions(
    types: list[SuggestedType],
    fact_kinds: list[SuggestedFactKind],
    *,
    entity_types: set[str],
    predicates: set[str],
) -> list[SchemaSuggestion]:
    """Bounded, well-formed suggestions for things the project does not already have."""
    found: list[SchemaSuggestion] = []
    new_types: set[str] = set()
    known_lower = {name.lower() for name in entity_types}
    for item in types:
        name = _type_name(item.name)
        if not IDENTIFIER_RE.match(name) or name.lower() in known_lower | {
            t.lower() for t in new_types
        }:
            continue
        new_types.add(name)
        found.append(
            SchemaSuggestion(
                kind="type",
                name=name,
                description=item.description.strip()[:_TEXT],
                reason=item.reason.strip()[:_TEXT],
            )
        )
        if len(new_types) >= MAX_SUGGESTIONS_PER_KIND:
            break

    # A kind of fact may connect existing types or ones suggested alongside it.
    by_lower = {name.lower(): name for name in entity_types | new_types}

    def resolve(names: list[str]) -> list[str]:
        resolved = [by_lower.get(_type_name(name).lower()) for name in names]
        return sorted({name for name in resolved if name})

    seen: set[str] = set()
    for fact in fact_kinds:
        name = _fact_name(fact.name)
        kind = _OBJECT_KINDS.get(fact.points_to.strip().lower())
        subjects = resolve(fact.subject_types)
        objects = resolve(fact.object_types) if kind is ObjectKind.ENTITY_REF else []
        if (
            not IDENTIFIER_RE.match(name)
            or name in predicates
            or name in seen
            or kind is None
            or not subjects
            or (kind is ObjectKind.ENTITY_REF and not objects)
        ):
            continue
        seen.add(name)
        found.append(
            SchemaSuggestion(
                kind="fact_kind",
                name=name,
                description=fact.description.strip()[:_TEXT],
                reason=fact.reason.strip()[:_TEXT],
                subject_types=subjects,
                points_to=fact.points_to.strip().lower(),
                object_types=objects,
                one_at_a_time=fact.one_at_a_time,
            )
        )
        if len(seen) >= MAX_SUGGESTIONS_PER_KIND:
            break
    return found


def add_to_definition(
    definition: SchemaDefinition, suggestions: list[SchemaSuggestion]
) -> tuple[SchemaDefinition, list[str]]:
    """The definition with the suggestions added, and the names actually added.

    Only ever adds. Anything the definition already has is left exactly as it is.
    """
    have_types = {extension.entity_type for extension in definition.entity_extensions}
    have_facts = {extension.predicate for extension in definition.predicate_extensions}
    types = list(definition.entity_extensions)
    facts = list(definition.predicate_extensions)
    added: list[str] = []
    for item in suggestions:
        if item.kind == "type" and item.name not in have_types:
            types.append(
                EntityExtension(entity_type=item.name, description=item.description or None)
            )
            have_types.add(item.name)
            added.append(item.name)
    for item in suggestions:
        if item.kind == "fact_kind" and item.name not in have_facts:
            facts.append(
                PredicateExtension(
                    predicate=item.name,
                    description=item.description or None,
                    subject_types=item.subject_types,
                    object_kind=_OBJECT_KINDS[item.points_to],
                    object_types=item.object_types,
                    cardinality=Cardinality.ONE if item.one_at_a_time else Cardinality.MANY,
                )
            )
            have_facts.add(item.name)
            added.append(item.name)
    return (
        definition.model_copy(update={"entity_extensions": types, "predicate_extensions": facts}),
        added,
    )
