"""Neo4j canon writer implementing the ADR-011 commit application step.

Applies one immutable commit envelope in a single Neo4j transaction and records a
``CommitMarker``. Re-applying the same tuple is a no-op; a differing checksum
raises ``RevisionConflict``. Canon objects are stored with per-kind labels
(``Entity``/``Fact``/``Belief``/``Event``/``Provenance``) and composite
``(workspace_id, project_id, id)`` identity; complex values are stored as JSON
strings so no nested-map property assumptions leak into the schema.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from backend.domain.commit import ApplyOutcome, CommitMarker
from backend.domain.journal import RevisionConflict
from backend.domain.operations import (
    AmendEventOp,
    AssertBeliefOp,
    AssertFactOp,
    CanonOperation,
    CloseBeliefOp,
    CloseFactOp,
    CreateEntityOp,
    CreateEventOp,
    LinkAssetOp,
    RetireAssetLinkOp,
    RetireEntityOp,
    RetireEventOp,
    UpdateEntityOp,
)
from backend.graph.manager import GraphManager

_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class CanonWriteError(RuntimeError):
    """The graph writer could not apply an operation against expected state."""


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


class Neo4jCanonWriter:
    def __init__(self, manager: GraphManager) -> None:
        self._manager = manager

    def _provenance_id(self, commit_id: str) -> str:
        return f"prov_{commit_id}"

    async def apply_commit(
        self,
        *,
        commit_id: str,
        revision: int,
        checksum: str,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        operations: Sequence[CanonOperation],
    ) -> ApplyOutcome:
        with self._manager.driver.session() as session:
            with session.begin_transaction() as tx:
                existing = tx.run(
                    "MATCH (m:CommitMarker {commit_id: $commit_id}) RETURN m.checksum AS checksum",
                    commit_id=commit_id,
                ).single()
                if existing is not None:
                    if existing["checksum"] != checksum:
                        raise RevisionConflict(
                            f"commit {commit_id!r} already applied with a different checksum"
                        )
                    return ApplyOutcome.ALREADY_APPLIED

                ctx: dict[str, Any] = {
                    "workspace_id": workspace_id,
                    "project_id": project_id,
                    "branch_id": branch_id,
                    "revision": revision,
                    "commit_id": commit_id,
                    "provenance_id": self._provenance_id(commit_id),
                }
                tx.run(
                    """
                    MERGE (p:Provenance {workspace_id: $workspace_id, project_id: $project_id, id: $provenance_id})
                    SET p.branch_id = $branch_id, p.commit_id = $commit_id, p.revision = $revision
                    """,
                    **ctx,
                )
                for operation in operations:
                    self._apply_operation(tx, operation, ctx)
                tx.run(
                    """
                    MERGE (m:CommitMarker {commit_id: $commit_id})
                    SET m.checksum = $checksum, m.revision = $revision,
                        m.workspace_id = $workspace_id, m.project_id = $project_id,
                        m.branch_id = $branch_id
                    """,
                    checksum=checksum,
                    **ctx,
                )
        return ApplyOutcome.APPLIED

    def _apply_operation(self, tx: Any, operation: CanonOperation, ctx: dict[str, Any]) -> None:
        if isinstance(operation, CreateEntityOp):
            label = operation.type
            if not _LABEL.match(label):
                raise CanonWriteError(f"invalid entity type label {label!r}")
            tx.run(
                f"""
                MERGE (n:Entity {{workspace_id: $workspace_id, project_id: $project_id, id: $id}})
                ON CREATE SET n.created_revision = $revision
                SET n.branch_id = $branch_id, n.kind = 'entity', n.type = $type,
                    n.name = $name, n.slug = $slug, n.attributes = $attributes,
                    n.lifecycle = 'active', n.commit_id = $commit_id, n.revision = $revision
                SET n:{label}
                """,
                id=operation.id,
                type=operation.type,
                name=operation.name,
                slug=operation.slug,
                attributes=_dumps(operation.attributes),
                **ctx,
            )
        elif isinstance(operation, UpdateEntityOp):
            self._require(
                tx.run(
                    """
                    MATCH (n:Entity {workspace_id: $workspace_id, project_id: $project_id, id: $id})
                    SET n.commit_id = $commit_id, n.revision = $revision
                    FOREACH (_ IN CASE WHEN $name IS NULL THEN [] ELSE [1] END | SET n.name = $name)
                    FOREACH (_ IN CASE WHEN $slug IS NULL THEN [] ELSE [1] END | SET n.slug = $slug)
                    FOREACH (_ IN CASE WHEN $attributes IS NULL THEN [] ELSE [1] END | SET n.attributes = $attributes)
                    RETURN n.id AS id
                    """,
                    id=operation.id,
                    name=operation.name,
                    slug=operation.slug,
                    attributes=_dumps(operation.attributes),
                    **ctx,
                ),
                operation.id,
            )
        elif isinstance(operation, RetireEntityOp):
            self._require(
                tx.run(
                    """
                    MATCH (n:Entity {workspace_id: $workspace_id, project_id: $project_id, id: $id})
                    SET n.lifecycle = 'retired', n.retired_revision = $revision,
                        n.commit_id = $commit_id, n.revision = $revision
                    RETURN n.id AS id
                    """,
                    id=operation.id,
                    **ctx,
                ),
                operation.id,
            )
        elif isinstance(operation, AssertFactOp):
            tx.run(
                """
                MERGE (f:Fact {workspace_id: $workspace_id, project_id: $project_id, id: $id})
                ON CREATE SET f.created_revision = $revision
                SET f.branch_id = $branch_id, f.subject_id = $subject_id, f.predicate = $predicate,
                    f.proposition = $proposition, f.status = 'asserted', f.confidence = $confidence,
                    f.provenance_id = $provenance_id, f.valid_from = $valid_from,
                    f.valid_until = $valid_until, f.commit_id = $commit_id, f.revision = $revision
                WITH f
                OPTIONAL MATCH (s:Entity {workspace_id: $workspace_id, project_id: $project_id, id: $subject_id})
                FOREACH (_ IN CASE WHEN s IS NULL THEN [] ELSE [1] END | MERGE (s)-[:ASSERTS]->(f))
                """,
                id=operation.id,
                subject_id=operation.proposition.subject_id,
                predicate=operation.proposition.predicate,
                proposition=_dumps(operation.proposition.model_dump(mode="json")),
                confidence=operation.confidence,
                valid_from=operation.valid_from.isoformat() if operation.valid_from else None,
                valid_until=operation.valid_until.isoformat() if operation.valid_until else None,
                **ctx,
            )
        elif isinstance(operation, CloseFactOp):
            self._require(
                tx.run(
                    """
                    MATCH (f:Fact {workspace_id: $workspace_id, project_id: $project_id, id: $id})
                    SET f.status = 'closed', f.closed_revision = $revision,
                        f.commit_id = $commit_id, f.revision = $revision
                    RETURN f.id AS id
                    """,
                    id=operation.id,
                    **ctx,
                ),
                operation.id,
            )
        elif isinstance(operation, AssertBeliefOp):
            tx.run(
                """
                MERGE (b:Belief {workspace_id: $workspace_id, project_id: $project_id, id: $id})
                ON CREATE SET b.created_revision = $revision
                SET b.branch_id = $branch_id, b.holder_entity_id = $holder_entity_id,
                    b.proposition = $proposition, b.confidence = $confidence,
                    b.acquisition_provenance_id = $provenance_id, b.status = 'asserted',
                    b.commit_id = $commit_id, b.revision = $revision
                WITH b
                OPTIONAL MATCH (h:Entity {workspace_id: $workspace_id, project_id: $project_id, id: $holder_entity_id})
                FOREACH (_ IN CASE WHEN h IS NULL THEN [] ELSE [1] END | MERGE (h)-[:BELIEVES]->(b))
                """,
                id=operation.id,
                holder_entity_id=operation.holder_entity_id,
                proposition=_dumps(operation.proposition.model_dump(mode="json")),
                confidence=operation.confidence,
                **ctx,
            )
        elif isinstance(operation, CloseBeliefOp):
            self._require(
                tx.run(
                    """
                    MATCH (b:Belief {workspace_id: $workspace_id, project_id: $project_id, id: $id})
                    SET b.status = 'closed', b.closed_revision = $revision,
                        b.commit_id = $commit_id, b.revision = $revision
                    RETURN b.id AS id
                    """,
                    id=operation.id,
                    **ctx,
                ),
                operation.id,
            )
        elif isinstance(operation, CreateEventOp):
            participants = [
                {"entity_id": p.entity_id, "role": p.role} for p in operation.participants
            ]
            tx.run(
                """
                MERGE (e:Event {workspace_id: $workspace_id, project_id: $project_id, id: $id})
                ON CREATE SET e.created_revision = $revision
                SET e.branch_id = $branch_id, e.event_kind = $kind, e.name = $name,
                    e.participants = $participants, e.participant_edges = $participant_edges,
                    e.locations = $locations, e.causes = $causes, e.outcomes = $outcomes,
                    e.world_position = $world_position, e.provenance_id = $provenance_id,
                    e.commit_id = $commit_id, e.revision = $revision
                WITH e
                UNWIND $participant_edges AS pe
                OPTIONAL MATCH (p:Entity {workspace_id: $workspace_id, project_id: $project_id, id: pe.entity_id})
                FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END |
                    MERGE (p)-[r:PARTICIPATED_IN]->(e) SET r.role = pe.role)
                """,
                id=operation.id,
                kind=operation.kind,
                name=operation.name,
                participants=_dumps(participants),
                participant_edges=participants,
                locations=_dumps(operation.location_ids),
                causes=_dumps(operation.cause_ids),
                outcomes=_dumps(operation.outcome_ids),
                world_position=operation.world_position,
                **ctx,
            )
        elif isinstance(operation, AmendEventOp):
            self._require(
                tx.run(
                    """
                    MATCH (e:Event {workspace_id: $workspace_id, project_id: $project_id, id: $id})
                    SET e.commit_id = $commit_id, e.revision = $revision
                    FOREACH (_ IN CASE WHEN $name IS NULL THEN [] ELSE [1] END | SET e.name = $name)
                    FOREACH (_ IN CASE WHEN $world_position IS NULL THEN [] ELSE [1] END |
                        SET e.world_position = $world_position)
                    RETURN e.id AS id
                    """,
                    id=operation.id,
                    name=operation.name,
                    world_position=operation.world_position,
                    **ctx,
                ),
                operation.id,
            )
        elif isinstance(operation, RetireEventOp):
            self._require(
                tx.run(
                    """
                    MATCH (e:Event {workspace_id: $workspace_id, project_id: $project_id, id: $id})
                    SET e.lifecycle = 'retired', e.retired_revision = $revision,
                        e.commit_id = $commit_id, e.revision = $revision
                    RETURN e.id AS id
                    """,
                    id=operation.id,
                    **ctx,
                ),
                operation.id,
            )
        elif isinstance(operation, LinkAssetOp):
            tx.run(
                """
                MERGE (a:Asset {workspace_id: $workspace_id, project_id: $project_id, id: $asset_id})
                MERGE (t:Entity {workspace_id: $workspace_id, project_id: $project_id, id: $target_id})
                MERGE (a)-[r:EVIDENCE_FOR {link_id: $id}]->(t)
                SET r.role = $role, r.commit_id = $commit_id, r.revision = $revision
                """,
                id=operation.id,
                asset_id=operation.asset_id,
                target_id=operation.target_id,
                role=operation.role,
                **ctx,
            )
        elif isinstance(operation, RetireAssetLinkOp):
            tx.run(
                """
                MATCH (a:Asset {workspace_id: $workspace_id, project_id: $project_id, id: $asset_id})
                      -[r:EVIDENCE_FOR {link_id: $id}]->
                      (:Entity {workspace_id: $workspace_id, project_id: $project_id})
                DELETE r
                """,
                id=operation.id,
                asset_id=operation.asset_id,
                **ctx,
            )
        else:  # pragma: no cover - exhaustive union
            raise CanonWriteError(f"unsupported operation {operation!r}")

    @staticmethod
    def _require(result: Any, target_id: str) -> None:
        if result.single() is None:
            raise CanonWriteError(f"operation target {target_id!r} was not found")

    async def get_marker(self, commit_id: str) -> CommitMarker | None:
        with self._manager.driver.session() as session:
            record = session.run(
                """
                MATCH (m:CommitMarker {commit_id: $commit_id})
                RETURN m.commit_id AS commit_id, m.workspace_id AS workspace_id,
                       m.project_id AS project_id, m.branch_id AS branch_id,
                       m.revision AS revision, m.checksum AS checksum
                """,
                commit_id=commit_id,
            ).single()
        if record is None:
            return None
        return CommitMarker(**dict(record))

    async def health(self) -> bool:
        try:
            self._manager.driver.verify_connectivity()
            return True
        except Exception:
            return False
