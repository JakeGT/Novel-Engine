"""Merging two entries by hand (AST-EXP-017): a preview, then a change to review.

The author picks the entry to keep and the one to merge into it. The preview says, in
plain sentences, everything the merge would move or end; asking for the merge puts one
reviewed change in the review queue. It is high-impact (an entry is retired), so it is
saved only once the author approves it, and it can be undone in one step from history.
No model is called.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.domain.canon.marks import details
from backend.domain.canon.phrasing import phrase
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.canon.registry import CORE_PREDICATES, PredicateSpec
from backend.domain.canon.revisions import OriginKind
from backend.domain.errors import ValidationError
from backend.domain.identity import AccessScope
from backend.domain.journal import MutationSet
from backend.domain.merge import (
    MergePreview,
    MergeRecords,
    MergeRefused,
    check_pair,
    expand,
    plan_merge,
    read_records,
)
from backend.domain.operations import EvidenceRef, MergeEntitiesOp
from backend.domain.tenancy import Permission
from backend.services.mutation_service import MutationService, ValidationReport, story_reader
from backend.services.schema_compiler import compile_definition


@dataclass(frozen=True)
class EndedItem:
    sentence: str
    reason: str


@dataclass(frozen=True)
class MergeSummary:
    """What a merge would do, in plain words for the author."""

    keep_id: str
    keep_name: str
    merged_id: str
    merged_name: str
    entry_type: str
    name: str
    aliases: list[str]
    details: dict[str, Any]
    # Other names left out, as an entry keeps at most MAX_ALIASES of them.
    aliases_cut: int = 0
    facts_moved: list[str] = field(default_factory=list)
    facts_ended: list[EndedItem] = field(default_factory=list)
    beliefs_moved: list[str] = field(default_factory=list)
    beliefs_ended: list[EndedItem] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    plan_items: list[str] = field(default_factory=list)
    pictures_moved: int = 0
    pictures_ended: int = 0
    still_naming: list[str] = field(default_factory=list)
    # What characters said that will name the kept entry instead (AST-EXP-017).
    claims_moved: list[str] = field(default_factory=list)
    change_count: int = 0


async def story_vocabulary(
    schema_store: Any, workspace_id: str, project_id: str
) -> dict[str, PredicateSpec]:
    """The kinds of fact the story's published setup allows."""
    predicates = dict(CORE_PREDICATES)
    if schema_store is not None:
        published = await schema_store.get_published(
            workspace_id=workspace_id, project_id=project_id
        )
        if published is not None:
            compiled = compile_definition(
                published.definition, workspace_id=workspace_id, project_id=project_id
            )
            predicates.update(compiled.predicate_specs)
    return predicates


def _sentence(names: dict[str, str], holder: str | None, proposition: Proposition) -> str:
    obj = proposition.object
    target = (
        names.get(obj.entity_id, "an entry")
        if isinstance(obj, EntityRefObject)
        else str(getattr(obj, "value", ""))
    )
    words = (
        f"{names.get(proposition.subject_id, 'an entry')} {phrase(proposition.predicate)} {target}"
    )
    if holder is not None:
        return f"{names.get(holder, 'someone')} believes {words}"
    return words.strip()


def summarize(records: MergeRecords, preview: MergePreview, names: dict[str, str]) -> MergeSummary:
    """The preview as sentences, read with the names of the story as it is now."""
    facts = {fact.id: fact for fact in records.facts}
    beliefs = {belief.id: belief for belief in records.beliefs}
    events = {event.id: event for event in records.events}
    plans = {item.id: item for item in records.plan_items}
    return MergeSummary(
        keep_id=records.keep.id,
        keep_name=records.keep.name,
        merged_id=records.merged.id,
        merged_name=records.merged.name,
        entry_type=records.keep.type,
        name=preview.name,
        aliases=list(preview.aliases),
        details=details(preview.attributes),
        aliases_cut=preview.aliases_cut,
        facts_moved=[_sentence(names, None, facts[i].proposition) for i in preview.facts_moved],
        facts_ended=[
            EndedItem(_sentence(names, None, facts[i].proposition), reason)
            for i, reason in preview.facts_ended
        ],
        beliefs_moved=[
            _sentence(names, beliefs[i].holder_entity_id, beliefs[i].proposition)
            for i in preview.beliefs_moved
        ],
        beliefs_ended=[
            EndedItem(_sentence(names, beliefs[i].holder_entity_id, beliefs[i].proposition), why)
            for i, why in preview.beliefs_ended
        ],
        events=[events[i].name for i in preview.events],
        plan_items=[plans[i].title for i in preview.plan_items],
        pictures_moved=len(preview.links_moved),
        pictures_ended=len(preview.links_ended),
        still_naming=[
            f"what is said in “{events[record_id].name}”"
            for record_type, record_id in preview.still_naming
            if record_type == "event" and record_id in events
        ],
        claims_moved=[
            f"what is said in “{events[record_id].name}”"
            for record_id in preview.claims_moved
            if record_id in events
        ],
        change_count=preview.change_count + 1,
    )


class MergeService:
    def __init__(
        self,
        *,
        mutation: MutationService,
        reader: Any,
        schema_store: Any = None,
        tenancy: Any = None,
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
        await self._tenancy.require_project(scope, workspace_id, project_id, permission)

    async def _records(
        self, workspace_id: str, project_id: str, entity_id: str, into: str
    ) -> MergeRecords:
        source = story_reader(self._reader, workspace_id, project_id)
        keep = await source.get_entity(into)
        merged = await source.get_entity(entity_id)
        try:
            check_pair(keep, merged)
        except MergeRefused as refused:
            raise ValidationError(str(refused)) from refused
        records = await read_records(source, into, entity_id)
        if records is None:
            raise ValidationError("Both entries must be in the story to be merged.")
        return records

    async def _names(self, workspace_id: str, project_id: str) -> dict[str, str]:
        entities = await self._reader.list_entities(
            workspace_id=workspace_id, project_id=project_id, include_retired=True, limit=10_000
        )
        return {entity.id: entity.name for entity in entities}

    async def preview(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        entity_id: str,
        into: str,
        name: str | None = None,
        merge: MergeEntitiesOp | None = None,
    ) -> MergeSummary:
        """What merging ``entity_id`` into ``into`` would do now. Changes nothing."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        records = await self._records(workspace_id, project_id, entity_id, into)
        if merge is None:
            predicates = await story_vocabulary(self._schema_store, workspace_id, project_id)
            merge = plan_merge(records, predicates, name=name)
        _changes, preview = expand(merge, records, ordinal=0)
        return summarize(records, preview, await self._names(workspace_id, project_id))

    async def propose(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        entity_id: str,
        into: str,
        expected_revision: int,
        name: str | None = None,
    ) -> tuple[MutationSet, ValidationReport]:
        """One reviewed change merging the two; it waits for the author's approval."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        records = await self._records(workspace_id, project_id, entity_id, into)
        predicates = await story_vocabulary(self._schema_store, workspace_id, project_id)
        if name is not None and len(name.strip()) > 200:
            raise ValidationError("The name is too long.")
        merge = plan_merge(
            records,
            predicates,
            name=name,
            evidence=[EvidenceRef(source_artefact_id=f"merge:{records.merged.id}")],
        )
        mutation = await self._mutation.propose(
            scope,
            workspace_id=workspace_id,
            project_id=project_id,
            operations=[merge],
            expected_head=expected_revision,
            origin=OriginKind.AUTHOR.value,
        )
        review_scope = AccessScope(
            actor_user_id=scope.actor_user_id,
            auth_mode=scope.auth_mode,
            correlation_id=scope.correlation_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        mutation = await self._mutation.submit(review_scope, mutation.id, "merge awaiting review")
        return mutation, await self._mutation.validate(review_scope, mutation.id)


__all__ = ["EndedItem", "MergeService", "MergeSummary", "story_vocabulary", "summarize"]
