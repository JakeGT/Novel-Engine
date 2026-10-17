"""Story rules: keep them, check the story against them, and say when a change breaks one.

Nothing here calls a model. A rule that a set of changes would newly break is
reported next to those changes. A rule warns by default; the author may set it to
block (ADR-013, CON-015), and then the commit is refused, but only when the rule was
worked out over the whole story as it stands inside the commit: a rule that could
not see everything it needed only ever warns.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.registry import CORE_ENTITY_TYPES, CORE_PREDICATES
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.operations import CanonOperation
from backend.domain.rules import (
    MAX_EXAMPLES,
    MAX_RULES,
    MAX_SENTENCE_LENGTH,
    MAX_TITLE_LENGTH,
    MAX_VIOLATIONS,
    ExampleResult,
    FactIndex,
    RuleBody,
    RuleExample,
    RuleStatus,
    StoryRule,
    Violation,
    World,
    affected_entries,
    apply_operations,
    body_problems,
    changes_world,
    read_rule,
    run_examples,
    violations,
    world_from_canon,
)
from backend.domain.tenancy import Permission
from backend.domain.validation import RuleEnforcement, RuleEvaluation, ValidationContext
from backend.ports import CanonRepositoryPort, SchemaVersionStorePort
from backend.services.schema_compiler import compile_definition

logger = logging.getLogger(__name__)

MAX_ENTITIES = 2000
MAX_FACTS = 10_000


@dataclass(frozen=True)
class RuleReading:
    """A rule as an author sees it: its words, and who breaks it right now."""

    rule: StoryRule
    reading: str
    broken_by: list[Violation]
    examples: list[ExampleResult]


@dataclass(frozen=True)
class RuleCheck:
    """What a rule would do, before it is kept."""

    reading: str
    problems: list[str]
    examples: list[ExampleResult]
    broken_by: list[Violation]


class RuleService:
    def __init__(
        self,
        store: object,
        *,
        reader: CanonRepositoryPort,
        schema_store: SchemaVersionStorePort | None = None,
        tenancy: object | None = None,
        local_mode: bool = False,
    ) -> None:
        self._store = store
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

    async def vocabulary(self, workspace_id: str, project_id: str) -> tuple[set[str], set[str]]:
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

    async def world(self, workspace_id: str, project_id: str) -> World:
        entities = await self._reader.list_entities(
            workspace_id=workspace_id, project_id=project_id, limit=MAX_ENTITIES
        )
        facts = await self._reader.list_facts(
            workspace_id=workspace_id, project_id=project_id, limit=MAX_FACTS
        )
        return world_from_canon(entities, facts)

    @staticmethod
    def _names(world: World) -> dict[str, str]:
        return {entry.id: entry.name for entry in world.entries.values()}

    async def _rule(self, workspace_id: str, project_id: str, rule_id: str) -> StoryRule:
        rule = await self._store.get_rule(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id, rule_id=rule_id
        )
        if rule is None:
            raise NotFoundError("That rule was not found.")
        return rule  # type: ignore[no-any-return]

    def _reading(self, rule: StoryRule, world: World) -> RuleReading:
        return RuleReading(
            rule=rule,
            reading=read_rule(rule.body, self._names(world)),
            broken_by=(
                violations(rule.id, rule.body, world) if rule.status is RuleStatus.ON else []
            ),
            examples=run_examples(rule.body, rule.examples),
        )

    # ---------------------------------------------------------------- read
    async def rules(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> list[RuleReading]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        rules = await self._store.list_rules(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id
        )
        if not rules:
            return []
        world = await self.world(workspace_id, project_id)
        return [self._reading(rule, world) for rule in rules]

    async def check(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        body: RuleBody,
        examples: Sequence[RuleExample],
    ) -> RuleCheck:
        """What this rule would say, and whether its examples agree with it. Keeps nothing."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self.checked(workspace_id, project_id, body, examples)

    async def checked(
        self,
        workspace_id: str,
        project_id: str,
        body: RuleBody,
        examples: Sequence[RuleExample],
    ) -> RuleCheck:
        world = await self.world(workspace_id, project_id)
        predicates, entity_types = await self.vocabulary(workspace_id, project_id)
        problems = body_problems(
            body, predicates=predicates, entity_types=entity_types, entity_ids=set(world.entries)
        )
        return RuleCheck(
            reading=read_rule(body, self._names(world)),
            problems=problems,
            examples=run_examples(body, list(examples)[:MAX_EXAMPLES]),
            broken_by=[] if problems else violations("new", body, world),
        )

    # --------------------------------------------------------------- write
    async def _accepted(
        self,
        workspace_id: str,
        project_id: str,
        body: RuleBody,
        examples: Sequence[RuleExample],
    ) -> None:
        found = await self.checked(workspace_id, project_id, body, examples)
        if found.problems:
            raise ValidationError(" ".join(found.problems))
        disagree = [item for item in found.examples if not item.agrees]
        if disagree:
            first = disagree[0]
            raise ValidationError(
                f"The rule does not do what its example says: “{first.description}” should be "
                f"{first.expect}, but the rule would leave it {first.outcome}. Change the rule "
                "or the example."
            )

    @staticmethod
    def _text(title: str, sentence: str) -> tuple[str, str]:
        title = " ".join(title.split())
        sentence = " ".join(sentence.split())
        if not title or len(title) > MAX_TITLE_LENGTH:
            raise ValidationError("Give the rule a short name.")
        if len(sentence) > MAX_SENTENCE_LENGTH:
            raise ValidationError("Say the rule in a sentence or two.")
        return title, sentence

    async def add(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        title: str,
        sentence: str,
        body: RuleBody,
        examples: Sequence[RuleExample],
    ) -> RuleReading:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        title, sentence = self._text(title, sentence)
        existing = await self._store.list_rules(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id
        )
        if len(existing) >= MAX_RULES:
            raise ValidationError("This story has as many rules as it can hold.")
        await self._accepted(workspace_id, project_id, body, examples)
        rule_id = format_canon_id("story_invariant", str(uuid7()))
        await self._store.add_rule(  # type: ignore[attr-defined]
            workspace_id=workspace_id,
            project_id=project_id,
            rule_id=rule_id,
            title=title,
            sentence=sentence,
            body=body,
            examples=list(examples)[:MAX_EXAMPLES],
            actor_user_id=scope.actor_user_id,
        )
        log_event(logger, "rule.added", rule_id=rule_id, examples=len(examples))
        rule = await self._rule(workspace_id, project_id, rule_id)
        return self._reading(rule, await self.world(workspace_id, project_id))

    async def change(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        rule_id: str,
        *,
        title: str | None = None,
        sentence: str | None = None,
        status: RuleStatus | None = None,
        body: RuleBody | None = None,
        examples: Sequence[RuleExample] | None = None,
        enforcement: RuleEnforcement | None = None,
    ) -> RuleReading:
        """Rename, pause, reword, or choose whether it warns or blocks.

        New wording is a new version; the old one is kept. Setting a rule to block is
        its own explicit choice (ADR-013), never a side effect of rewording.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        rule = await self._rule(workspace_id, project_id, rule_id)
        if title is not None or sentence is not None:
            title, sentence = self._text(
                title if title is not None else rule.title,
                sentence if sentence is not None else rule.sentence,
            )
        if body is not None or examples is not None:
            body = body if body is not None else rule.body
            examples = list(examples) if examples is not None else rule.examples
            await self._accepted(workspace_id, project_id, body, examples)
            if body == rule.body and list(examples) == rule.examples:
                body = None
        await self._store.change_rule(  # type: ignore[attr-defined]
            workspace_id=workspace_id,
            project_id=project_id,
            rule_id=rule_id,
            title=title,
            sentence=sentence,
            status=status,
            body=body,
            examples=list(examples)[:MAX_EXAMPLES] if body is not None and examples else None,
            actor_user_id=scope.actor_user_id,
            enforcement=enforcement,
        )
        if enforcement is not None and enforcement is not rule.enforcement:
            log_event(logger, "rule.enforcement", rule_id=rule_id, enforcement=enforcement.value)
        rule = await self._rule(workspace_id, project_id, rule_id)
        return self._reading(rule, await self.world(workspace_id, project_id))

    async def remove(
        self, scope: AccessScope, workspace_id: str, project_id: str, rule_id: str
    ) -> None:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        removed = await self._store.remove_rule(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id, rule_id=rule_id
        )
        if not removed:
            raise NotFoundError("That rule was not found.")

    # ------------------------------------------------- the validation seam
    async def _world_for(self, context: ValidationContext) -> tuple[World, bool]:
        """The story as the commit sees it, and whether that is all of it.

        Read through the commit's own reader, so a rule sees the line of the story
        being changed exactly as it stands under the commit's lock. If the story is
        larger than a rule may read, or the reader cannot give the whole story, the
        result is incomplete and cannot block.
        """
        reader = context.reader
        entities_of = getattr(reader, "active_entities", None)
        facts_of = getattr(reader, "asserted_facts", None)
        if entities_of is None or facts_of is None:
            return await self.world(context.workspace_id, context.project_id), False
        entities = await entities_of(MAX_ENTITIES + 1)
        facts = await facts_of(MAX_FACTS + 1)
        complete = len(entities) <= MAX_ENTITIES and len(facts) <= MAX_FACTS
        return world_from_canon(entities[:MAX_ENTITIES], facts[:MAX_FACTS]), complete

    async def evaluate(
        self, operations: Sequence[CanonOperation], *, context: ValidationContext
    ) -> Sequence[RuleEvaluation]:
        """Rules these changes would newly break: a warning, or a block when set to block.

        A rule the story already breaks is not blamed on an unrelated change: only
        what is broken afterwards and was not before is reported.
        """
        # Only adding, changing or removing entries and facts can alter what a rule
        # sees; anything else (a plan, a picture) leaves every rule as it was.
        if not changes_world(operations):
            return []
        rules = [
            rule
            for rule in await self._store.list_rules(  # type: ignore[attr-defined]
                workspace_id=context.workspace_id, project_id=context.project_id
            )
            if rule.status is RuleStatus.ON
        ]
        if not rules:
            return []
        before, complete = await self._world_for(context)
        after = apply_operations(before, operations)
        # Every entry, not a shown handful: a rule blocks only on the whole answer.
        # Entries these changes cannot reach keep their standing, so only the
        # reachable ones are worked out, before and after.
        before_index, after_index = FactIndex(before), FactIndex(after)
        reachable = affected_entries(before, after, operations, indexes=(before_index, after_index))
        found: list[RuleEvaluation] = []
        for rule in rules:
            already = {
                item.entity_id
                for item in violations(
                    rule.id, rule.body, before, limit=None, index=before_index, only=reachable
                )
            }
            blocks = rule.enforcement is RuleEnforcement.BLOCKING and complete
            newly = [
                violation
                for violation in violations(
                    rule.id, rule.body, after, limit=None, index=after_index, only=reachable
                )
                if violation.entity_id not in already
            ]
            # All of them decide; only so many are worth saying one by one.
            for violation in newly[:MAX_VIOLATIONS]:
                if blocks:
                    message = (
                        f"This breaks your rule “{rule.title}”, which you set to block: it would "
                        f"no longer hold for {violation.entity_name}. Skip or change this, or "
                        "set the rule to warn."
                    )
                elif rule.enforcement is RuleEnforcement.BLOCKING:
                    message = (
                        f"This may break your rule “{rule.title}”: it would no longer hold for "
                        f"{violation.entity_name}. The story is too large to check this rule "
                        "completely, so it warns instead of blocking."
                    )
                else:
                    message = (
                        f"This would break your rule “{rule.title}”: it would no longer "
                        f"hold for {violation.entity_name}."
                    )
                found.append(
                    RuleEvaluation(
                        rule_id=rule.id,
                        message=message,
                        operation_ordinal=_blamed(operations, violation.entity_id, before),
                        enforcement=rule.enforcement,
                        deterministic=True,
                        complete=complete,
                        rule_version=rule.version,
                    )
                )
        return found


def _blamed(operations: Sequence[CanonOperation], entity_id: str, before: World) -> int | None:
    """The last change that touches the entry the rule broke for, if one plainly does."""
    blamed: int | None = None
    for ordinal, operation in enumerate(operations):
        touched = {operation.id}
        proposition = getattr(operation, "proposition", None)
        if proposition is not None:
            touched.add(proposition.subject_id)
            other = getattr(proposition.object, "entity_id", None)
            if other is not None:
                touched.add(other)
        # Ending a fact touches both of the entries it was between.
        ended = before.facts.get(operation.id)
        if ended is not None:
            touched.add(ended.subject_id)
            if ended.object_id is not None:
                touched.add(ended.object_id)
        if entity_id in touched:
            blamed = ordinal
    return blamed
