"""Canonical equivalence verification (ADR-012).

Three views of one project's canon must agree at the committed head:

1. the *journal* — the immutable commit envelopes, replayed from genesis with no
   database (the definition of accepted canon),
2. the *normalized* PostgreSQL tables every product read uses, and
3. the *graph projection* in Neo4j.

``verify`` replays the journal in memory and compares it record by record with
the normalized tables, then compares the normalized tables with the projection
through the same queries product code runs. It reports disagreement; it never
repairs anything, because canon must not be mutated to match a derived store.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from backend.domain.canon.assertions import Belief, Fact
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import Event
from backend.domain.operations import parse_operation
from backend.domain.projection import RecordVersion
from backend.ports import CanonJournalPort, CanonRepositoryPort, CanonWriterPort
from backend.services.canon_history_service import (
    _replay_belief,
    _replay_entity,
    _replay_event,
    _replay_fact,
)

_PAGE = 500
_RECORD_TYPE = {
    "create_entity": "entity",
    "update_entity": "entity",
    "retire_entity": "entity",
    "assert_fact": "fact",
    "close_fact": "fact",
    "assert_belief": "belief",
    "close_belief": "belief",
    "create_event": "event",
    "amend_event": "event",
    "retire_event": "event",
}

Snapshot = dict[str, dict[str, dict[str, Any]]]


def _entity_view(entity: CanonEntity) -> dict[str, Any]:
    return {
        "type": entity.type,
        "name": entity.name,
        "slug": entity.slug,
        "lifecycle": entity.lifecycle.value,
        "created_revision": entity.created_revision,
        "retired_revision": entity.retired_revision,
        "attributes": entity.attributes,
        "aliases": list(entity.aliases),
        "external_ids": dict(entity.external_ids),
    }


def _fact_view(fact: Fact) -> dict[str, Any]:
    return {
        "proposition": fact.proposition.model_dump(mode="json"),
        "status": fact.status.value,
        "confidence": fact.confidence,
        "created_revision": fact.created_revision,
        "closed_revision": fact.closed_revision,
    }


def _belief_view(belief: Belief) -> dict[str, Any]:
    return {
        "holder_entity_id": belief.holder_entity_id,
        "proposition": belief.proposition.model_dump(mode="json"),
        "confidence": belief.confidence,
        "source_ref": belief.source_ref,
        "created_revision": belief.created_revision,
        "closed_revision": belief.closed_revision,
    }


def _event_view(event: Event) -> dict[str, Any]:
    return {
        "kind": event.kind,
        "name": event.name,
        "participants": [p.model_dump(mode="json") for p in event.participants],
        "location_ids": list(event.location_ids),
        "cause_ids": list(event.cause_ids),
        "outcome_ids": list(event.outcome_ids),
        "world_position": event.world_position,
        "created_revision": event.created_revision,
        "retired_revision": event.retired_revision,
    }


def _fact_edges(facts: dict[str, dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    edges = []
    for fact_id, view in facts.items():
        obj = view["proposition"]["object"]
        if view["status"] == "asserted" and obj.get("kind") == "entity_ref":
            edges.append(
                (
                    fact_id,
                    view["proposition"]["subject_id"],
                    view["proposition"]["predicate"],
                    obj["entity_id"],
                )
            )
    return sorted(edges)


def snapshot_checksum(snapshot: Snapshot) -> str:
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def diff_snapshots(expected: Snapshot, actual: Snapshot, *, label: str) -> list[str]:
    """Human-readable disagreements that name records, never their story content."""
    problems: list[str] = []
    for kind in sorted(set(expected) | set(actual)):
        want, have = expected.get(kind, {}), actual.get(kind, {})
        for record_id in sorted(set(want) - set(have)):
            problems.append(f"{label}: {kind} {record_id} is missing")
        for record_id in sorted(set(have) - set(want)):
            problems.append(f"{label}: {kind} {record_id} is unexpected")
        for record_id in sorted(set(want) & set(have)):
            fields = sorted(
                key
                for key in set(want[record_id]) | set(have[record_id])
                if want[record_id].get(key) != have[record_id].get(key)
            )
            if fields:
                problems.append(f"{label}: {kind} {record_id} differs in {', '.join(fields)}")
    return problems


@dataclass(frozen=True)
class EquivalenceReport:
    head_revision: int
    journal_checksum: str
    canon_checksum: str
    projection_checksum: str | None
    projection_revision: int | None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


class CanonEquivalenceService:
    def __init__(
        self,
        *,
        canon: CanonRepositoryPort,
        journal: CanonJournalPort,
        writer: CanonWriterPort | None = None,
        projection: str = "graph",
    ) -> None:
        self._canon = canon
        self._journal = journal
        self._writer = writer
        self._projection = projection

    async def journal_snapshot(
        self, workspace_id: str, project_id: str, branch_id: str
    ) -> Snapshot:
        """Replay every committed envelope from genesis into an in-memory snapshot."""
        entities: dict[str, CanonEntity] = {}
        facts: dict[str, Fact] = {}
        beliefs: dict[str, Belief] = {}
        events: dict[str, Event] = {}
        after = 0
        while True:
            commits = await self._canon.list_commits(
                workspace_id=workspace_id,
                project_id=project_id,
                branch_id=branch_id,
                after_revision=after,
                limit=_PAGE,
            )
            for commit in commits:
                envelope = await self._canon.get_envelope(commit.commit_id)
                if envelope is None:
                    raise RuntimeError(f"revision {commit.revision} has no envelope")
                for payload in json.loads(envelope.canonical):
                    operation = parse_operation(payload)
                    record_type = _RECORD_TYPE.get(operation.op)
                    if record_type is None:
                        continue
                    version = RecordVersion(
                        workspace_id=workspace_id,
                        project_id=project_id,
                        branch_id=branch_id,
                        record_type=record_type,
                        record_id=operation.id,
                        revision=envelope.revision,
                        operation_type=operation.op,
                        commit_id=envelope.commit_id,
                        change=operation.model_dump(mode="json"),
                        created_at=envelope.created_at,
                    )
                    if record_type == "entity":
                        replayed = _replay_entity(version, entities.get(operation.id))
                        if replayed is not None:
                            entities[operation.id] = replayed
                    elif record_type == "fact":
                        fact = _replay_fact(version, facts.get(operation.id))
                        if fact is not None:
                            facts[operation.id] = fact
                    elif record_type == "belief":
                        belief = _replay_belief(version, beliefs.get(operation.id))
                        if belief is not None:
                            beliefs[operation.id] = belief
                    else:
                        event = _replay_event(version, events.get(operation.id))
                        if event is not None:
                            events[operation.id] = event
                after = commit.revision
            if len(commits) < _PAGE:
                break
        return {
            "entity": {key: _entity_view(value) for key, value in entities.items()},
            "fact": {key: _fact_view(value) for key, value in facts.items()},
            "belief": {key: _belief_view(value) for key, value in beliefs.items()},
            "event": {key: _event_view(value) for key, value in events.items()},
        }

    @staticmethod
    async def _read_snapshot(source: Any, workspace_id: str, project_id: str) -> Snapshot:
        """Read a store through the ordinary paginated product queries."""

        async def pages(method: Any, **filters: Any) -> list[Any]:
            items: list[Any] = []
            offset = 0
            while True:
                page = await method(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    limit=_PAGE,
                    offset=offset,
                    **filters,
                )
                items.extend(page)
                if len(page) < _PAGE:
                    return items
                offset += _PAGE

        return {
            "entity": {
                item.id: _entity_view(item)
                for item in await pages(source.list_entities, include_retired=True)
            },
            "fact": {
                item.id: _fact_view(item)
                for item in await pages(source.list_facts, include_closed=True)
            },
            "belief": {
                item.id: _belief_view(item)
                for item in await pages(source.list_beliefs, include_closed=True)
            },
            "event": {
                item.id: _event_view(item)
                for item in await pages(source.list_events, include_retired=True)
            },
        }

    async def verify(self, workspace_id: str, project_id: str) -> EquivalenceReport:
        branch = await self._journal.ensure_root_branch(
            workspace_id=workspace_id, project_id=project_id
        )
        head = await self._canon.get_head(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
        )
        journal = await self.journal_snapshot(workspace_id, project_id, branch.id)
        canon = await self._read_snapshot(self._canon, workspace_id, project_id)
        problems = diff_snapshots(journal, canon, label="normalized canon vs journal")

        projection_checksum: str | None = None
        projection_revision: int | None = None
        if self._writer is not None:
            watermark = await self._canon.get_watermark(
                projection=self._projection,
                workspace_id=workspace_id,
                project_id=project_id,
                branch_id=branch.id,
            )
            projection_revision = watermark.revision if watermark is not None else 0
            if projection_revision != head:
                # A lagging projection is recoverable, not an integrity failure, but
                # equivalence cannot be claimed for a revision it has not reached.
                problems.append(
                    f"projection is at revision {projection_revision}, canon head is {head}"
                )
            else:
                projected = await self._read_snapshot(self._writer, workspace_id, project_id)
                projection_checksum = snapshot_checksum(projected)
                problems.extend(diff_snapshots(canon, projected, label="projection vs canon"))
                expected_edges = _fact_edges(canon["fact"])
                actual_edges = sorted(
                    await self._writer.list_fact_edges(
                        workspace_id=workspace_id, project_id=project_id
                    )
                )
                for edge in sorted(set(expected_edges) - set(actual_edges)):
                    problems.append(
                        f"projection vs canon: relationship for fact {edge[0]} is missing"
                    )
                for edge in sorted(set(actual_edges) - set(expected_edges)):
                    problems.append(
                        f"projection vs canon: relationship for fact {edge[0]} is unexpected"
                    )
        return EquivalenceReport(
            head_revision=head,
            journal_checksum=snapshot_checksum(journal),
            canon_checksum=snapshot_checksum(canon),
            projection_checksum=projection_checksum,
            projection_revision=projection_revision,
            problems=problems,
        )


__all__ = [
    "CanonEquivalenceService",
    "EquivalenceReport",
    "Snapshot",
    "diff_snapshots",
    "snapshot_checksum",
]
