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
from datetime import datetime
from typing import Any

from backend.domain.canon.assertions import Belief, Fact, FactStatus
from backend.domain.canon.entities import CanonEntity, EntityLifecycle
from backend.domain.canon.events import Event, EventParticipant
from backend.domain.canon.propositions import EntityRefObject, Proposition
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
# A predicate becomes a relationship type only when it is a plain identifier, so
# no project-supplied text is ever interpolated into Cypher unchecked.
_PREDICATE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


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
        schema_version_id: str | None = None,
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
                    "schema_version_id": schema_version_id,
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
                    n.aliases = $aliases, n.external_ids = $external_ids,
                    n.lifecycle = 'active', n.commit_id = $commit_id, n.revision = $revision,
                    n.schema_version_id = $schema_version_id
                SET n:{label}
                """,
                id=operation.id,
                type=operation.type,
                name=operation.name,
                slug=operation.slug,
                attributes=_dumps(operation.attributes),
                aliases=list(operation.aliases),
                external_ids=_dumps(operation.external_ids),
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
                    FOREACH (_ IN CASE WHEN $aliases IS NULL THEN [] ELSE [1] END | SET n.aliases = $aliases)
                    FOREACH (_ IN CASE WHEN $external_ids IS NULL THEN [] ELSE [1] END | SET n.external_ids = $external_ids)
                    RETURN n.id AS id
                    """,
                    id=operation.id,
                    name=operation.name,
                    slug=operation.slug,
                    attributes=_dumps(operation.attributes),
                    aliases=operation.aliases,
                    external_ids=_dumps(operation.external_ids),
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
            obj = operation.proposition.object
            predicate = operation.proposition.predicate
            if isinstance(obj, EntityRefObject) and _PREDICATE.match(predicate):
                # Traversable shortcut for an open entity-to-entity fact. It is a
                # pure function of the reified Fact node: created with it, removed
                # when it closes, and rebuilt from the journal like everything else.
                tx.run(
                    f"""
                    MATCH (s:Entity {{workspace_id: $workspace_id, project_id: $project_id, id: $subject_id}})
                    MATCH (o:Entity {{workspace_id: $workspace_id, project_id: $project_id, id: $object_id}})
                    MERGE (s)-[r:{predicate} {{fact_id: $id}}]->(o)
                    SET r.projection = 'fact', r.predicate = $predicate,
                        r.commit_id = $commit_id, r.revision = $revision
                    """,
                    id=operation.id,
                    subject_id=operation.proposition.subject_id,
                    object_id=obj.entity_id,
                    predicate=predicate,
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
            tx.run(
                """
                MATCH (f:Fact {workspace_id: $workspace_id, project_id: $project_id, id: $id})
                MATCH (:Entity {workspace_id: $workspace_id, project_id: $project_id, id: f.subject_id})
                      -[r]->(:Entity)
                WHERE r.projection = 'fact' AND r.fact_id = $id
                DELETE r
                """,
                id=operation.id,
                **ctx,
            )
        elif isinstance(operation, AssertBeliefOp):
            tx.run(
                """
                MERGE (b:Belief {workspace_id: $workspace_id, project_id: $project_id, id: $id})
                ON CREATE SET b.created_revision = $revision
                SET b.branch_id = $branch_id, b.holder_entity_id = $holder_entity_id,
                    b.proposition = $proposition, b.confidence = $confidence,
                    b.acquisition_provenance_id = $provenance_id, b.status = 'asserted',
                    b.source_ref = $source_ref,
                    b.commit_id = $commit_id, b.revision = $revision
                WITH b
                OPTIONAL MATCH (h:Entity {workspace_id: $workspace_id, project_id: $project_id, id: $holder_entity_id})
                FOREACH (_ IN CASE WHEN h IS NULL THEN [] ELSE [1] END | MERGE (h)-[:BELIEVES]->(b))
                """,
                id=operation.id,
                holder_entity_id=operation.holder_entity_id,
                proposition=_dumps(operation.proposition.model_dump(mode="json")),
                confidence=operation.confidence,
                source_ref=operation.source_ref,
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
                    e.participants = $participants,
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
                SET r.role = $role, r.asset_version_id = $asset_version_id,
                    r.commit_id = $commit_id, r.revision = $revision
                """,
                id=operation.id,
                asset_id=operation.asset_id,
                asset_version_id=operation.asset_version_id,
                target_id=operation.target_id,
                role=operation.role,
                **ctx,
            )
        elif isinstance(operation, RetireAssetLinkOp):
            tx.run(
                """
                MATCH (a:Asset {workspace_id: $workspace_id, project_id: $project_id})
                      -[r:EVIDENCE_FOR {link_id: $id}]->
                      (:Entity {workspace_id: $workspace_id, project_id: $project_id})
                DELETE r
                """,
                id=operation.id,
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

    _ENTITY_RETURN = (
        "n.id AS id, n.workspace_id AS workspace_id, n.project_id AS project_id, "
        "n.branch_id AS branch_id, n.type AS type, n.name AS name, n.slug AS slug, "
        "n.lifecycle AS lifecycle, n.schema_version_id AS schema_version_id, "
        "n.created_revision AS created_revision, n.retired_revision AS retired_revision, "
        "n.attributes AS attributes, n.aliases AS aliases, n.external_ids AS external_ids"
    )

    @staticmethod
    def _to_entity(record: Any) -> CanonEntity:
        return CanonEntity(
            id=record["id"],
            workspace_id=record["workspace_id"],
            project_id=record["project_id"],
            branch_id=record["branch_id"] or "",
            type=record["type"],
            name=record["name"],
            slug=record["slug"],
            lifecycle=EntityLifecycle(record["lifecycle"] or "active"),
            schema_version_id=record["schema_version_id"] or "",
            created_revision=record["created_revision"] or 0,
            retired_revision=record["retired_revision"],
            attributes=json.loads(record["attributes"]) if record["attributes"] else {},
            aliases=list(record["aliases"] or []),
            external_ids=json.loads(record["external_ids"]) if record["external_ids"] else {},
        )

    async def list_fact_edges(
        self, *, workspace_id: str, project_id: str
    ) -> list[tuple[str, str, str, str]]:
        """Projected ``(fact_id, subject_id, predicate, object_id)`` relationship shortcuts."""
        with self._manager.driver.session() as session:
            records = session.run(
                """
                MATCH (s:Entity {workspace_id: $workspace_id, project_id: $project_id})
                      -[r]->(o:Entity {workspace_id: $workspace_id, project_id: $project_id})
                WHERE r.projection = 'fact'
                RETURN r.fact_id AS fact_id, s.id AS subject_id, type(r) AS predicate,
                       o.id AS object_id
                ORDER BY fact_id
                """,
                workspace_id=workspace_id,
                project_id=project_id,
            )
            return [
                (record["fact_id"], record["subject_id"], record["predicate"], record["object_id"])
                for record in records
            ]

    async def get_entity(
        self, *, workspace_id: str, project_id: str, entity_id: str
    ) -> CanonEntity | None:
        with self._manager.driver.session() as session:
            record = session.run(
                f"""
                MATCH (n:Entity {{workspace_id: $workspace_id, project_id: $project_id, id: $id}})
                WHERE n.kind = 'entity'
                RETURN {self._ENTITY_RETURN}
                """,
                workspace_id=workspace_id,
                project_id=project_id,
                id=entity_id,
            ).single()
        return self._to_entity(record) if record is not None else None

    async def list_entities(
        self,
        *,
        workspace_id: str,
        project_id: str,
        entity_type: str | None = None,
        include_retired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[CanonEntity]:
        type_clause = "AND n.type = $type" if entity_type else ""
        retired_clause = "" if include_retired else "AND coalesce(n.lifecycle, 'active') = 'active'"
        with self._manager.driver.session() as session:
            records = session.run(
                f"""
                MATCH (n:Entity {{workspace_id: $workspace_id, project_id: $project_id}})
                WHERE n.kind = 'entity' {type_clause} {retired_clause}
                RETURN {self._ENTITY_RETURN}
                ORDER BY n.name, n.id
                SKIP $offset LIMIT $limit
                """,
                workspace_id=workspace_id,
                project_id=project_id,
                type=entity_type,
                offset=offset,
                limit=limit,
            )
            return [self._to_entity(record) for record in records]

    async def search_entities(
        self,
        *,
        workspace_id: str,
        project_id: str,
        query: str,
        entity_type: str | None = None,
        include_retired: bool = False,
        limit: int = 50,
    ) -> list[CanonEntity]:
        type_clause = "AND n.type = $type" if entity_type else ""
        retired_clause = "" if include_retired else "AND coalesce(n.lifecycle, 'active') = 'active'"
        with self._manager.driver.session() as session:
            records = session.run(
                f"""
                MATCH (n:Entity {{workspace_id: $workspace_id, project_id: $project_id}})
                WHERE n.kind = 'entity' AND toLower(n.name) CONTAINS toLower($needle)
                      {type_clause} {retired_clause}
                RETURN {self._ENTITY_RETURN}
                ORDER BY n.name, n.id
                LIMIT $limit
                """,
                workspace_id=workspace_id,
                project_id=project_id,
                type=entity_type,
                needle=query,
                limit=limit,
            )
            return [self._to_entity(record) for record in records]

    async def discard(self, *, workspace_id: str, project_id: str, branch_id: str) -> int:
        with self._manager.driver.session() as session:
            record = session.run(
                """
                MATCH (n) WHERE n.workspace_id = $w AND n.project_id = $p
                      AND coalesce(n.branch_id, '') = $b
                      AND (n:Entity OR n:Fact OR n:Belief OR n:Event OR n:Provenance)
                WITH collect(n) AS nodes, count(n) AS deleted
                FOREACH (x IN nodes | DETACH DELETE x)
                RETURN deleted
                """,
                w=workspace_id,
                p=project_id,
                b=branch_id,
            ).single()
            session.run(
                """
                MATCH (m:CommitMarker {workspace_id: $w, project_id: $p, branch_id: $b})
                DELETE m
                """,
                w=workspace_id,
                p=project_id,
                b=branch_id,
            )
        return int(record["deleted"]) if record is not None else 0

    async def purge(self, *, workspace_id: str, project_id: str | None = None) -> int:
        """Erase every projected canon node for a deleted project or workspace."""
        project_clause = "AND n.project_id = $p" if project_id is not None else ""
        with self._manager.driver.session() as session:
            result = session.run(
                f"""
                MATCH (n) WHERE n.workspace_id = $w {project_clause}
                      AND (n:Entity OR n:Fact OR n:Belief OR n:Event OR n:Provenance
                           OR n:Asset OR n:CommitMarker)
                DETACH DELETE n
                """,
                w=workspace_id,
                p=project_id,
            )
            return int(result.consume().counters.nodes_deleted)

    async def health(self) -> bool:
        try:
            self._manager.driver.verify_connectivity()
            return True
        except Exception:
            return False

    _FACT_RETURN = (
        "f.id AS id, f.workspace_id AS workspace_id, f.project_id AS project_id, "
        "f.branch_id AS branch_id, f.proposition AS proposition, f.status AS status, "
        "f.confidence AS confidence, f.provenance_id AS provenance_id, "
        "f.created_revision AS created_revision, f.closed_revision AS closed_revision, "
        "f.valid_from AS valid_from, f.valid_until AS valid_until"
    )

    @staticmethod
    def _to_fact(record: Any) -> Fact:
        return Fact(
            id=record["id"],
            workspace_id=record["workspace_id"],
            project_id=record["project_id"],
            branch_id=record["branch_id"] or "",
            proposition=Proposition.model_validate_json(record["proposition"]),
            status=FactStatus(record["status"] or "asserted"),
            confidence=record["confidence"] if record["confidence"] is not None else 1.0,
            provenance_id=record["provenance_id"] or "",
            created_revision=record["created_revision"] or 0,
            closed_revision=record["closed_revision"],
            valid_from=datetime.fromisoformat(record["valid_from"])
            if record["valid_from"]
            else None,
            valid_until=(
                datetime.fromisoformat(record["valid_until"]) if record["valid_until"] else None
            ),
        )

    async def get_fact(self, *, workspace_id: str, project_id: str, fact_id: str) -> Fact | None:
        with self._manager.driver.session() as session:
            record = session.run(
                f"""
                MATCH (f:Fact {{workspace_id: $workspace_id, project_id: $project_id, id: $id}})
                RETURN {self._FACT_RETURN}
                """,
                workspace_id=workspace_id,
                project_id=project_id,
                id=fact_id,
            ).single()
        return self._to_fact(record) if record is not None else None

    async def list_facts(
        self,
        *,
        workspace_id: str,
        project_id: str,
        include_closed: bool = False,
        subject_id: str | None = None,
        predicate: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Fact]:
        closed_clause = "" if include_closed else "AND coalesce(f.status, 'asserted') = 'asserted'"
        subject_clause = "AND f.subject_id = $subject_id" if subject_id else ""
        predicate_clause = "AND f.predicate = $predicate" if predicate else ""
        with self._manager.driver.session() as session:
            records = session.run(
                f"""
                MATCH (f:Fact {{workspace_id: $workspace_id, project_id: $project_id}})
                WHERE true {closed_clause} {subject_clause} {predicate_clause}
                RETURN {self._FACT_RETURN}
                ORDER BY f.created_revision, f.id
                SKIP $offset LIMIT $limit
                """,
                workspace_id=workspace_id,
                project_id=project_id,
                subject_id=subject_id,
                predicate=predicate,
                offset=offset,
                limit=limit,
            )
            return [self._to_fact(record) for record in records]

    _BELIEF_RETURN = (
        "b.id AS id, b.workspace_id AS workspace_id, b.project_id AS project_id, "
        "b.branch_id AS branch_id, b.holder_entity_id AS holder_entity_id, "
        "b.proposition AS proposition, b.confidence AS confidence, "
        "b.acquisition_provenance_id AS acquisition_provenance_id, "
        "b.source_ref AS source_ref, "
        "b.created_revision AS created_revision, b.closed_revision AS closed_revision"
    )

    @staticmethod
    def _to_belief(record: Any) -> Belief:
        return Belief(
            id=record["id"],
            workspace_id=record["workspace_id"],
            project_id=record["project_id"],
            branch_id=record["branch_id"] or "",
            holder_entity_id=record["holder_entity_id"],
            proposition=Proposition.model_validate_json(record["proposition"]),
            confidence=record["confidence"] if record["confidence"] is not None else 1.0,
            acquisition_provenance_id=record["acquisition_provenance_id"] or "",
            source_ref=record["source_ref"],
            created_revision=record["created_revision"] or 0,
            closed_revision=record["closed_revision"],
        )

    async def get_belief(
        self, *, workspace_id: str, project_id: str, belief_id: str
    ) -> Belief | None:
        with self._manager.driver.session() as session:
            record = session.run(
                f"""
                MATCH (b:Belief {{workspace_id: $workspace_id, project_id: $project_id, id: $id}})
                RETURN {self._BELIEF_RETURN}
                """,
                workspace_id=workspace_id,
                project_id=project_id,
                id=belief_id,
            ).single()
        return self._to_belief(record) if record is not None else None

    async def list_beliefs(
        self,
        *,
        workspace_id: str,
        project_id: str,
        holder_entity_id: str | None = None,
        include_closed: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Belief]:
        closed_clause = "" if include_closed else "AND coalesce(b.status, 'asserted') = 'asserted'"
        holder_clause = "AND b.holder_entity_id = $holder_entity_id" if holder_entity_id else ""
        with self._manager.driver.session() as session:
            records = session.run(
                f"""
                MATCH (b:Belief {{workspace_id: $workspace_id, project_id: $project_id}})
                WHERE true {closed_clause} {holder_clause}
                RETURN {self._BELIEF_RETURN}
                ORDER BY b.created_revision, b.id
                SKIP $offset LIMIT $limit
                """,
                workspace_id=workspace_id,
                project_id=project_id,
                holder_entity_id=holder_entity_id,
                offset=offset,
                limit=limit,
            )
            return [self._to_belief(record) for record in records]

    _EVENT_RETURN = (
        "e.id AS id, e.workspace_id AS workspace_id, e.project_id AS project_id, "
        "e.branch_id AS branch_id, e.event_kind AS kind, e.name AS name, "
        "e.participants AS participants, e.locations AS locations, e.causes AS causes, "
        "e.outcomes AS outcomes, e.world_position AS world_position, "
        "e.provenance_id AS provenance_id, e.created_revision AS created_revision, "
        "e.retired_revision AS retired_revision"
    )

    @staticmethod
    def _to_event(record: Any) -> Event:
        def _load(value: Any, default: Any) -> Any:
            return json.loads(value) if value else default

        return Event(
            id=record["id"],
            workspace_id=record["workspace_id"],
            project_id=record["project_id"],
            branch_id=record["branch_id"] or "",
            kind=record["kind"],
            name=record["name"],
            participants=[EventParticipant(**p) for p in _load(record["participants"], [])],
            location_ids=_load(record["locations"], []),
            cause_ids=_load(record["causes"], []),
            outcome_ids=_load(record["outcomes"], []),
            world_position=record["world_position"] or None,
            provenance_id=record["provenance_id"] or "",
            created_revision=record["created_revision"] or 0,
            retired_revision=record["retired_revision"],
        )

    async def get_event(self, *, workspace_id: str, project_id: str, event_id: str) -> Event | None:
        with self._manager.driver.session() as session:
            record = session.run(
                f"""
                MATCH (e:Event {{workspace_id: $workspace_id, project_id: $project_id, id: $id}})
                RETURN {self._EVENT_RETURN}
                """,
                workspace_id=workspace_id,
                project_id=project_id,
                id=event_id,
            ).single()
        return self._to_event(record) if record is not None else None

    async def list_events(
        self,
        *,
        workspace_id: str,
        project_id: str,
        include_retired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Event]:
        retired_clause = "" if include_retired else "AND coalesce(e.lifecycle, 'active') = 'active'"
        with self._manager.driver.session() as session:
            records = session.run(
                f"""
                MATCH (e:Event {{workspace_id: $workspace_id, project_id: $project_id}})
                WHERE true {retired_clause}
                RETURN {self._EVENT_RETURN}
                ORDER BY e.created_revision, e.id
                SKIP $offset LIMIT $limit
                """,
                workspace_id=workspace_id,
                project_id=project_id,
                offset=offset,
                limit=limit,
            )
            return [self._to_event(record) for record in records]
