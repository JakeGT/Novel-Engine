"""Merging two entries into one (AST-EXP-017).

A merge is one change the author reviews: "Mara V. is Mara: keep Mara". When it is kept
it becomes the ordinary changes it implies, worked out here from the story as it is at
that moment:

- the kept entry takes the merged one's names as other names, and any of its details
  it does not already have (the kept entry's own details win);
- every current fact and belief naming the merged entry is ended and stated again
  about the kept one, with its story time, secrecy and confidence; one that would only
  repeat something the kept entry already has, or would make an entry point at itself,
  is ended and not stated again; one the merge was told to drop (a value the kept entry
  already has for something that holds one value at a time) is ended, while the kept
  entry still has a fact of that kind when the merge is kept;
- events (who takes part, where, and what causes or follows them), plan items and
  pictures that name the merged entry name the kept one;
- the merged entry is retired, marked as merged into the kept one, so undoing the merge
  brings it back (``restore_entity``);
- remembered name answers that name the merged entry are read as naming the kept one
  (``follow_merges``), without rewriting them, so undo needs nothing more.

Expanding at commit, rather than when the merge is suggested, means a fact added after
the suggestion still moves, and every reader of the change log (history, undo, what-ifs,
exports, the graph) sees only changes it already understands. The same function gives
the preview a reviewer reads, so what is shown is what is kept. Everything here is pure
apart from ``read_records`` and ``merged_into``, which only read.

Claims (things a character says) that name the merged entry are not rewritten: what was
said keeps its words. They are listed in the preview as still naming it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from backend.domain.canon.assertions import Belief, Fact
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import Event, EventParticipant
from backend.domain.canon.marks import (
    IMPORTANCE_KEY,
    MARK_KEYS,
    NEEDS_DETAIL_KEY,
    TAGS_KEY,
    TRUST_KEY,
    clean_tags,
    details,
)
from backend.domain.canon.naming import normalize_name
from backend.domain.canon.plans import PlanItem, PlanRef
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.canon.registry import Cardinality, PredicateSpec
from backend.domain.media import AssetLink
from backend.domain.operations import (
    AmendEventOp,
    AssertBeliefOp,
    AssertFactOp,
    CanonOperation,
    CloseBeliefOp,
    CloseFactOp,
    EvidenceRef,
    LinkAssetOp,
    MergeEntitiesOp,
    RetireAssetLinkOp,
    RetireEntityOp,
    UpdateEntityOp,
    UpdatePlanItemOp,
)

# Every record of each kind is read when a merge is worked out; a story larger than this
# cannot be merged in one step and says so rather than leaving records behind.
MAX_RECORDS = 100_000
MAX_ALIASES = 40
# How many merges in a row a remembered name answer is followed through.
MAX_MERGE_HOPS = 10


class MergeSource(Protocol):
    """What working out a merge reads: the story at one version, in one line of it."""

    async def get_entity(self, entity_id: str) -> CanonEntity | None: ...

    async def asserted_facts(self, limit: int) -> list[Fact]: ...

    async def current_beliefs(self, limit: int) -> list[Belief]: ...

    async def live_events(self, limit: int) -> list[Event]: ...

    async def live_plan_items(self, limit: int) -> list[PlanItem]: ...

    async def live_asset_links(self, limit: int) -> list[AssetLink]: ...


@dataclass(frozen=True)
class MergeRecords:
    """The two entries and every current record naming either of them, by id order."""

    keep: CanonEntity
    merged: CanonEntity
    facts: tuple[Fact, ...] = ()
    beliefs: tuple[Belief, ...] = ()
    events: tuple[Event, ...] = ()
    plan_items: tuple[PlanItem, ...] = ()
    links: tuple[AssetLink, ...] = ()


@dataclass
class MergePreview:
    """What a merge would do, by record id; names are looked up by whoever shows it."""

    keep_id: str
    merged_id: str
    name: str
    aliases: list[str]
    attributes: dict[str, Any]
    # Other names left out because an entry keeps at most MAX_ALIASES of them.
    aliases_cut: int = 0
    facts_moved: list[str] = field(default_factory=list)
    # (fact id, why it is ended rather than moved)
    facts_ended: list[tuple[str, str]] = field(default_factory=list)
    beliefs_moved: list[str] = field(default_factory=list)
    beliefs_ended: list[tuple[str, str]] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    plan_items: list[str] = field(default_factory=list)
    links_moved: list[str] = field(default_factory=list)
    links_ended: list[tuple[str, str]] = field(default_factory=list)
    # Claims (what a character said) rewritten to name the kept entry, by event id.
    claims_moved: list[str] = field(default_factory=list)
    # Records that keep naming the merged entry: a claim that would name the kept entry
    # twice keeps its words.
    still_naming: list[tuple[str, str]] = field(default_factory=list)

    @property
    def change_count(self) -> int:
        return (
            len(self.facts_moved)
            + len(self.facts_ended)
            + len(self.beliefs_moved)
            + len(self.beliefs_ended)
            + len(self.events)
            + len(self.plan_items)
            + len(self.links_moved)
            + len(self.links_ended)
            + len([i for i in self.claims_moved if i not in self.events])
        )


class MergeRefused(ValueError):
    """Two entries cannot be merged; the message is plain words for the author."""


def _entity_target(proposition: Proposition) -> str | None:
    obj = proposition.object
    return obj.entity_id if isinstance(obj, EntityRefObject) else None


def _names(proposition: Proposition, entity_id: str) -> bool:
    return entity_id in (proposition.subject_id, _entity_target(proposition))


def _event_names(event: Event, entity_id: str) -> bool:
    return (
        any(p.entity_id == entity_id for p in event.participants)
        or entity_id in event.location_ids
        or entity_id in event.cause_ids
        or entity_id in event.outcome_ids
    )


def _claim_names(event: Event, entity_id: str) -> bool:
    return event.claim is not None and _names(event.claim.proposition, entity_id)


async def read_records(source: MergeSource, keep_id: str, merged_id: str) -> MergeRecords | None:
    """Both entries and what names them, or None when either is not a current entry."""
    keep = await source.get_entity(keep_id)
    merged = await source.get_entity(merged_id)
    if keep is None or merged is None or not keep.is_active or not merged.is_active:
        return None
    pair = {keep_id, merged_id}
    facts = [
        fact
        for fact in await source.asserted_facts(MAX_RECORDS)
        if fact.closed_revision is None
        and (fact.proposition.subject_id in pair or _entity_target(fact.proposition) in pair)
    ]
    beliefs = [
        belief
        for belief in await source.current_beliefs(MAX_RECORDS)
        if belief.closed_revision is None
        and (
            belief.holder_entity_id in pair
            or belief.proposition.subject_id in pair
            or _entity_target(belief.proposition) in pair
        )
    ]
    events = [
        event
        for event in await source.live_events(MAX_RECORDS)
        if event.retired_revision is None
        and (_event_names(event, merged_id) or _claim_names(event, merged_id))
    ]
    plan_items = [
        item
        for item in await source.live_plan_items(MAX_RECORDS)
        if item.retired_revision is None and any(ref.entity_id == merged_id for ref in item.refs)
    ]
    links = [
        link
        for link in await source.live_asset_links(MAX_RECORDS)
        if link.retired_revision is None
        and (
            (link.target_record_type == "entity" and link.target_record_id in pair)
            or merged_id in link.known_by
        )
    ]
    return MergeRecords(
        keep=keep,
        merged=merged,
        facts=tuple(sorted(facts, key=lambda record: record.id)),
        beliefs=tuple(sorted(beliefs, key=lambda record: record.id)),
        events=tuple(sorted(events, key=lambda record: record.id)),
        plan_items=tuple(sorted(plan_items, key=lambda record: record.id)),
        links=tuple(sorted(links, key=lambda record: record.id)),
    )


def check_pair(keep: CanonEntity | None, merged: CanonEntity | None) -> None:
    """Refuse a merge that cannot be made, in plain words."""
    if keep is None or merged is None or not keep.is_active or not merged.is_active:
        raise MergeRefused("Both entries must be in the story to be merged.")
    if keep.id == merged.id:
        raise MergeRefused("An entry cannot be merged with itself.")
    if keep.type != merged.type:
        raise MergeRefused(
            f"Only entries of the same type can be merged: “{merged.name}” is a "
            f"{merged.type.lower()} and “{keep.name}” is a {keep.type.lower()}."
        )


def kept_entry(
    keep: CanonEntity, merged: CanonEntity, *, name: str | None = None
) -> tuple[str, list[str], dict[str, Any]]:
    """The kept entry's name, other names and details once the other is merged in.

    The merged entry's name and other names become other names (never repeating the
    kept name), at most MAX_ALIASES of them; its details fill in only what the kept
    entry does not already say; tags are joined; the kept entry's importance and trust
    stand, else the merged one's; "fill in later" goes once either entry has details.
    """
    final_name, aliases, attributes = _kept_entry(keep, merged, name=name)
    return final_name, aliases[:MAX_ALIASES], attributes


def _kept_entry(
    keep: CanonEntity, merged: CanonEntity, *, name: str | None = None
) -> tuple[str, list[str], dict[str, Any]]:
    """``kept_entry`` with every other name, before any are left out."""
    final_name = (name or "").strip() or keep.name
    taken = {normalize_name(final_name)}
    aliases: list[str] = []
    for label in (keep.name, *keep.aliases, merged.name, *merged.aliases):
        key = normalize_name(label)
        if label.strip() and key not in taken:
            taken.add(key)
            aliases.append(label.strip())
    attributes: dict[str, Any] = {
        **details(merged.attributes),
        **{key: value for key, value in keep.attributes.items() if key not in MARK_KEYS},
    }
    tags = clean_tags([*keep.attributes.get(TAGS_KEY, []), *merged.attributes.get(TAGS_KEY, [])])
    if tags:
        attributes[TAGS_KEY] = tags
    level = keep.attributes.get(IMPORTANCE_KEY) or merged.attributes.get(IMPORTANCE_KEY)
    if level:
        attributes[IMPORTANCE_KEY] = level
    trust = keep.attributes.get(TRUST_KEY) or merged.attributes.get(TRUST_KEY)
    if trust:
        attributes[TRUST_KEY] = trust
    for key in MARK_KEYS - {TAGS_KEY, IMPORTANCE_KEY, NEEDS_DETAIL_KEY, TRUST_KEY}:
        if key in keep.attributes:
            attributes[key] = keep.attributes[key]
    has_details = bool(details(attributes))
    if not has_details and (
        keep.attributes.get(NEEDS_DETAIL_KEY) or merged.attributes.get(NEEDS_DETAIL_KEY)
    ):
        attributes[NEEDS_DETAIL_KEY] = True
    return final_name, aliases, attributes


def plan_merge(
    records: MergeRecords,
    predicates: dict[str, PredicateSpec],
    *,
    name: str | None = None,
    evidence: Sequence[EvidenceRef] = (),
) -> MergeEntitiesOp:
    """The merge to review: which of the merged entry's facts the kept one replaces.

    A kind of fact that holds one value at a time keeps the kept entry's value: the
    merged entry's different value is ended rather than moved. The kept entry's name
    and details are left to be worked out when the merge is kept, unless a new name is
    asked for.
    """
    check_pair(records.keep, records.merged)
    keep_id, merged_id = records.keep.id, records.merged.id
    held = {
        (fact.proposition.predicate): fact
        for fact in records.facts
        if fact.proposition.subject_id == keep_id
    }
    drop: list[str] = []
    for fact in records.facts:
        if fact.proposition.subject_id != merged_id:
            continue
        spec = predicates.get(fact.proposition.predicate)
        if spec is None or spec.cardinality is not Cardinality.ONE:
            continue
        current = held.get(fact.proposition.predicate)
        if current is not None and _moved(fact.proposition, merged_id, keep_id) != (
            current.proposition
        ):
            drop.append(fact.id)
    cleaned = (name or "").strip()
    return MergeEntitiesOp(
        id=merged_id,
        into=keep_id,
        name=cleaned if cleaned and cleaned != records.keep.name else None,
        drop_facts=drop,
        evidence=list(evidence),
    )


def _swap(entity_id: str, merged_id: str, keep_id: str) -> str:
    return keep_id if entity_id == merged_id else entity_id


def _moved(proposition: Proposition, merged_id: str, keep_id: str) -> Proposition:
    obj = proposition.object
    if isinstance(obj, EntityRefObject) and obj.entity_id == merged_id:
        obj = obj.model_copy(update={"entity_id": keep_id})
    return proposition.model_copy(
        update={"subject_id": _swap(proposition.subject_id, merged_id, keep_id), "object": obj}
    )


def _same_time(left: Any, right: Any) -> bool:
    def dump(value: Any) -> Any:
        return value.model_dump(mode="json") if hasattr(value, "model_dump") else value

    return bool(dump(left) == dump(right))


def expand(
    operation: MergeEntitiesOp, records: MergeRecords, *, ordinal: int
) -> tuple[list[CanonOperation], MergePreview]:
    """The ordinary changes one merge makes, and the preview of them.

    ``ordinal`` is the merge's place in its set, so the new facts, beliefs and picture
    links it states get temporary ids of their own that resolve the same way every time.
    """
    check_pair(records.keep, records.merged)
    keep, merged = records.keep, records.merged
    keep_id, merged_id = keep.id, merged.id
    evidence = list(operation.evidence)
    name, every_alias, attributes = _kept_entry(keep, merged, name=operation.name)
    aliases = every_alias[:MAX_ALIASES]
    cut = len(every_alias) - len(aliases)
    if operation.aliases is not None:
        aliases, cut = list(operation.aliases), 0
    if operation.attributes is not None:
        attributes = dict(operation.attributes)
    preview = MergePreview(
        keep_id=keep_id,
        merged_id=merged_id,
        name=name,
        aliases=aliases,
        attributes=attributes,
        aliases_cut=cut,
    )
    prefix = f"tmp:merge-{ordinal}"
    closes: list[CanonOperation] = []
    asserts: list[CanonOperation] = []
    dropped_facts = set(operation.drop_facts)
    dropped_beliefs = set(operation.drop_beliefs)

    # What the kept entry already has, so nothing is stated twice.
    kept_facts = [
        fact
        for fact in records.facts
        if not _names(fact.proposition, merged_id) and _names(fact.proposition, keep_id)
    ]
    # A fact set to be dropped is dropped only while the kept entry still has a value
    # of that kind: if it has ended since the merge was suggested, the fact moves.
    held_kinds = {
        fact.proposition.predicate for fact in kept_facts if fact.proposition.subject_id == keep_id
    }
    for fact in records.facts:
        if not _names(fact.proposition, merged_id):
            continue
        moved = _moved(fact.proposition, merged_id, keep_id)
        closes.append(CloseFactOp(id=fact.id, evidence=evidence))
        reason = ""
        if fact.id in dropped_facts and fact.proposition.predicate in held_kinds:
            reason = "the kept entry already has a value for this, and holds only one"
        elif moved.subject_id == _entity_target(moved):
            reason = "it would make the entry point at itself"
        elif any(
            other.proposition == moved
            and _same_time(other.starts, fact.starts)
            and _same_time(other.ends, fact.ends)
            for other in kept_facts
        ):
            reason = "the kept entry already has it"
        if reason:
            preview.facts_ended.append((fact.id, reason))
            continue
        kept_facts.append(fact.model_copy(update={"proposition": moved}))
        preview.facts_moved.append(fact.id)
        asserts.append(
            AssertFactOp(
                id=f"{prefix}-fact-{len(preview.facts_moved)}",
                proposition=moved,
                confidence=fact.confidence,
                valid_from=fact.valid_from,
                valid_until=fact.valid_until,
                starts=fact.starts,
                ends=fact.ends,
                visibility=fact.visibility if fact.visibility.value != "ordinary" else None,
                evidence=evidence,
            )
        )

    kept_beliefs = [
        (belief.holder_entity_id, belief.proposition)
        for belief in records.beliefs
        if merged_id
        not in (
            belief.holder_entity_id,
            belief.proposition.subject_id,
            _entity_target(belief.proposition),
        )
    ]
    for belief in records.beliefs:
        if merged_id not in (
            belief.holder_entity_id,
            belief.proposition.subject_id,
            _entity_target(belief.proposition),
        ):
            continue
        holder = _swap(belief.holder_entity_id, merged_id, keep_id)
        moved = _moved(belief.proposition, merged_id, keep_id)
        closes.append(CloseBeliefOp(id=belief.id, evidence=evidence))
        reason = ""
        if belief.id in dropped_beliefs:
            reason = "it was set to be ended"
        elif moved.subject_id == _entity_target(moved):
            reason = "it would make the entry point at itself"
        elif (holder, moved) in kept_beliefs:
            reason = "the kept entry already holds it"
        if reason:
            preview.beliefs_ended.append((belief.id, reason))
            continue
        kept_beliefs.append((holder, moved))
        preview.beliefs_moved.append(belief.id)
        asserts.append(
            AssertBeliefOp(
                id=f"{prefix}-belief-{len(preview.beliefs_moved)}",
                holder_entity_id=holder,
                proposition=moved,
                confidence=belief.confidence,
                source_ref=belief.source_ref,
                evidence=evidence,
            )
        )

    event_changes: list[CanonOperation] = []
    for event in records.events:
        claim = None
        if event.claim is not None and _claim_names(event, merged_id):
            said = _moved(event.claim.proposition, merged_id, keep_id)
            if said.subject_id == _entity_target(said):
                # Saying the kept entry is related to itself means nothing: kept as said.
                preview.still_naming.append(("event", event.id))
            else:
                claim = event.claim.model_copy(update={"proposition": said})
                preview.claims_moved.append(event.id)
        if not _event_names(event, merged_id):
            if claim is not None:
                event_changes.append(AmendEventOp(id=event.id, claim=claim, evidence=evidence))
            continue
        participants: list[EventParticipant] = []
        seen: set[str] = set()
        for participant in event.participants:
            entity_id = _swap(participant.entity_id, merged_id, keep_id)
            if entity_id in seen:
                continue
            seen.add(entity_id)
            participants.append(participant.model_copy(update={"entity_id": entity_id}))
        locations = list(
            dict.fromkeys(_swap(place, merged_id, keep_id) for place in event.location_ids)
        )
        causes = list(dict.fromkeys(_swap(ref, merged_id, keep_id) for ref in event.cause_ids))
        outcomes = list(dict.fromkeys(_swap(ref, merged_id, keep_id) for ref in event.outcome_ids))
        event_changes.append(
            AmendEventOp(
                id=event.id,
                participants=participants if participants != list(event.participants) else None,
                location_ids=locations if locations != list(event.location_ids) else None,
                cause_ids=causes if causes != list(event.cause_ids) else None,
                outcome_ids=outcomes if outcomes != list(event.outcome_ids) else None,
                claim=claim,
                evidence=evidence,
            )
        )
        preview.events.append(event.id)

    plan_changes: list[CanonOperation] = []
    for item in records.plan_items:
        refs: list[PlanRef] = []
        for ref in item.refs:
            moved_ref = ref.model_copy(
                update={"entity_id": _swap(ref.entity_id, merged_id, keep_id)}
            )
            if all(
                (other.entity_id, other.role) != (moved_ref.entity_id, moved_ref.role)
                for other in refs
            ):
                refs.append(moved_ref)
        plan_changes.append(UpdatePlanItemOp(id=item.id, refs=refs, evidence=evidence))
        preview.plan_items.append(item.id)

    link_closes: list[CanonOperation] = []
    link_adds: list[CanonOperation] = []
    kept_links = {
        (link.asset_id, link.asset_version_id, link.role.value)
        for link in records.links
        if link.target_record_type == "entity" and link.target_record_id == keep_id
    }
    for link in records.links:
        on_merged = link.target_record_type == "entity" and link.target_record_id == merged_id
        if not on_merged and merged_id not in link.known_by:
            continue
        link_closes.append(RetireAssetLinkOp(id=link.id, evidence=evidence))
        target = keep_id if on_merged else link.target_record_id
        key = (link.asset_id, link.asset_version_id, link.role.value)
        if on_merged and key in kept_links:
            preview.links_ended.append((link.id, "the kept entry already shows this picture"))
            continue
        if on_merged:
            kept_links.add(key)
        known_by = list(dict.fromkeys(_swap(item, merged_id, keep_id) for item in link.known_by))
        preview.links_moved.append(link.id)
        link_adds.append(
            LinkAssetOp(
                id=f"{prefix}-link-{len(preview.links_moved)}",
                asset_id=link.asset_id,
                asset_version_id=link.asset_version_id,
                target_record_type=link.target_record_type,
                target_id=target,
                role=link.role.value,
                visibility=link.visibility if link.visibility != "open" else None,
                known_by=known_by,
                applies_from=link.applies_from,
                applies_until=link.applies_until,
                shows_version=link.shows_version or None,
                evidence=evidence,
            )
        )

    update = UpdateEntityOp(
        id=keep_id,
        name=name if name != keep.name else None,
        aliases=aliases if aliases != list(keep.aliases) else None,
        attributes=attributes if attributes != dict(keep.attributes) else None,
        evidence=evidence,
    )
    changed_entry = (
        [update]
        if update.name is not None or update.aliases is not None or update.attributes is not None
        else []
    )
    # Ends before new statements (a value held one at a time is free again); the merged
    # entry is retired last, once nothing current names it.
    operations: list[CanonOperation] = [
        *changed_entry,
        *closes,
        *link_closes,
        *event_changes,
        *plan_changes,
        *asserts,
        *link_adds,
        RetireEntityOp(id=merged_id, merged_into=keep_id, evidence=evidence),
    ]
    return operations, preview


def _after(records: MergeRecords, earlier: Sequence[CanonOperation]) -> MergeRecords:
    """The records as the changes before a merge in its set leave them.

    A fact or belief already ended, a picture link or plan item already removed, or an
    event or plan item already given new people earlier in the set is taken as it will
    be, so the merge neither ends something twice nor undoes a change made beside it.
    """
    closed_facts = {op.id for op in earlier if isinstance(op, CloseFactOp)}
    closed_beliefs = {op.id for op in earlier if isinstance(op, CloseBeliefOp)}
    retired_links = {op.id for op in earlier if isinstance(op, RetireAssetLinkOp)}
    removed_plans = {op.id for op in earlier if op.op == "retire_plan_item"}
    removed_events = {op.id for op in earlier if op.op == "retire_event"}
    plan_refs: dict[str, list[PlanRef]] = {}
    people: dict[str, AmendEventOp] = {}
    for op in earlier:
        if isinstance(op, UpdatePlanItemOp) and op.refs is not None:
            plan_refs[op.id] = list(op.refs)
        if isinstance(op, AmendEventOp) and (
            op.participants is not None
            or op.location_ids is not None
            or op.cause_ids is not None
            or op.outcome_ids is not None
            or op.claim is not None
        ):
            people[op.id] = op
    merged_id = records.merged.id
    plan_items = []
    for item in records.plan_items:
        if item.id in removed_plans:
            continue
        if item.id in plan_refs:
            item = item.model_copy(update={"refs": plan_refs[item.id]})
        if any(ref.entity_id == merged_id for ref in item.refs):
            plan_items.append(item)
    events = []
    for event in records.events:
        if event.id in removed_events:
            continue
        change = people.get(event.id)
        if change is not None:
            event = event.model_copy(
                update={
                    "participants": event.participants
                    if change.participants is None
                    else list(change.participants),
                    "location_ids": event.location_ids
                    if change.location_ids is None
                    else list(change.location_ids),
                    "cause_ids": event.cause_ids
                    if change.cause_ids is None
                    else list(change.cause_ids),
                    "outcome_ids": event.outcome_ids
                    if change.outcome_ids is None
                    else list(change.outcome_ids),
                    "claim": event.claim if change.claim is None else change.claim,
                }
            )
        if _event_names(event, merged_id) or _claim_names(event, merged_id):
            events.append(event)
    return MergeRecords(
        keep=records.keep,
        merged=records.merged,
        facts=tuple(fact for fact in records.facts if fact.id not in closed_facts),
        beliefs=tuple(belief for belief in records.beliefs if belief.id not in closed_beliefs),
        events=tuple(events),
        plan_items=tuple(plan_items),
        links=tuple(link for link in records.links if link.id not in retired_links),
    )


async def expand_merges(
    operations: Sequence[CanonOperation], source: MergeSource
) -> tuple[list[CanonOperation], list[int]]:
    """Every merge in a set replaced by the changes it makes, read from ``source``.

    Returns the changes and, for each, the position in ``operations`` it came from, so
    a finding about one of them can be shown against the merge the author reviewed. A
    merge that cannot be worked out (an entry gone, merged twice in one set, kept by two
    merges, or both kept and merged away) is left as it is: the validator then refuses
    it in plain words.
    """
    if not any(isinstance(op, MergeEntitiesOp) for op in operations):
        return list(operations), list(range(len(operations)))
    overlapping = merge_overlaps(operations)
    expanded: list[CanonOperation] = []
    origin: list[int] = []
    for position, operation in enumerate(operations):
        if not isinstance(operation, MergeEntitiesOp) or (
            operation.id in overlapping or operation.into in overlapping
        ):
            expanded.append(operation)
            origin.append(position)
            continue
        records = await read_records(source, operation.into, operation.id)
        try:
            if records is None:
                raise MergeRefused("gone")
            changes, _preview = expand(operation, _after(records, expanded), ordinal=position)
        except MergeRefused:
            expanded.append(operation)
            origin.append(position)
            continue
        expanded.extend(changes)
        origin.extend([position] * len(changes))
    return expanded, origin


def merge_overlaps(operations: Sequence[CanonOperation]) -> set[str]:
    """Entries merged away more than once in a set, both merged away and kept, or kept
    by two merges (each would set the kept entry's names and details over the other's)."""
    merges = [op for op in operations if isinstance(op, MergeEntitiesOp)]
    away = [op.id for op in merges]
    kept = [op.into for op in merges]
    return {entity_id for entity_id in away if away.count(entity_id) > 1 or entity_id in kept} | {
        entity_id for entity_id in kept if kept.count(entity_id) > 1
    }


class _Versions(Protocol):
    async def list_record_versions(
        self,
        *,
        workspace_id: str,
        project_id: str,
        record_type: str | None = None,
        record_id: str | None = None,
        limit: int = 100,
    ) -> Sequence[Any]: ...


async def merged_into(
    versions: _Versions,
    *,
    workspace_id: str,
    project_id: str,
    entity_ids: Iterable[str],
    active: set[str],
) -> dict[str, str]:
    """For each entry no longer in the story because it was merged, the current entry it
    now is (following merges in a row). Only reads; an entry brought back by undoing its
    merge has a later change than the retirement, so it is no longer followed."""
    targets: dict[str, str] = {}
    for entity_id in sorted(set(entity_ids) - active):
        current, seen = entity_id, {entity_id}
        for _ in range(MAX_MERGE_HOPS):
            latest = await versions.list_record_versions(
                workspace_id=workspace_id,
                project_id=project_id,
                record_type="entity",
                record_id=current,
                limit=1,
            )
            if not latest or latest[0].operation_type != "retire_entity":
                break
            kept = (latest[0].change or {}).get("merged_into")
            if not isinstance(kept, str) or kept in seen:
                break
            seen.add(kept)
            current = kept
            if kept in active:
                targets[entity_id] = kept
                break
    return targets


def follow_merges(bindings: Sequence[Any], targets: dict[str, str]) -> list[Any]:
    """Remembered name answers (what a phrase means, or whose viewpoint it holds in) read
    as naming the kept entry when the one they name was merged into it. The stored answer
    is left as it was, so undoing the merge makes it name the merged entry again."""
    if not targets:
        return list(bindings)
    followed: list[Any] = []
    for binding in bindings:
        entity_id = targets.get(binding.entity_id or "", binding.entity_id)
        viewpoint_id = targets.get(binding.viewpoint_id or "", binding.viewpoint_id)
        if entity_id != binding.entity_id or viewpoint_id != binding.viewpoint_id:
            binding = replace(binding, entity_id=entity_id, viewpoint_id=viewpoint_id)
        followed.append(binding)
    return followed


__all__ = [
    "MAX_RECORDS",
    "MergePreview",
    "MergeRecords",
    "MergeRefused",
    "MergeSource",
    "check_pair",
    "expand",
    "expand_merges",
    "follow_merges",
    "kept_entry",
    "merge_overlaps",
    "merged_into",
    "plan_merge",
    "read_records",
]
