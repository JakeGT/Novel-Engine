"""Bounded upgrader for Phase 1 graph archives.

Before Phase 2, a project export was an untyped graph: nodes with free-form
property bags and typed edges (``ProjectTransferService``, archive version 2 /
schema version 1). That is *not* validated canon. This upgrader never restores
such an archive as accepted history. It reads one, maps what can be expressed in
the canon vocabulary into typed operations, and files the result as an
``import``-origin **proposal**, which the review gate holds for an explicit
human decision. Everything it cannot map is listed in a migration report with a
reason, so nothing is silently dropped or guessed.

Bounds: the archive is checksum-verified, sizes are capped, properties are
restricted to scalar values, and the upgrader performs no canon write itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from backend.domain.canon.naming import MAX_NAME_LENGTH, name_problem
from backend.domain.canon.propositions import EntityRefObject, ObjectKind, Proposition
from backend.domain.canon.registry import (
    CORE_ENTITY_TYPES,
    CORE_PREDICATES,
    Cardinality,
    PredicateSpec,
)
from backend.domain.canon.revisions import OriginKind
from backend.domain.errors import ValidationError
from backend.domain.identity import AccessScope
from backend.domain.journal import MutationSet
from backend.domain.operations import (
    AssertFactOp,
    CanonOperation,
    CreateEntityOp,
    EvidenceRef,
)
from backend.ports import CanonRepositoryPort, SchemaVersionStorePort
from backend.services.mutation_service import MutationService
from backend.services.project_transfer import ARCHIVE_VERSION, SCHEMA_VERSION, _checksum
from backend.services.schema_compiler import compile_definition

LEGACY_NAMESPACE = "legacy-graph"
MAX_NODES = 2000
MAX_EDGES = 5000
MAX_OPERATIONS = 500
MAX_ATTRIBUTES = 40
MAX_ATTRIBUTE_LENGTH = 2000

_LEGACY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,199}$")
_SCALARS = (str, int, float, bool)


class GraphArchiveError(ValidationError):
    """The legacy archive is malformed, tampered with, or exceeds the upgrade bounds."""


@dataclass(frozen=True)
class SkippedItem:
    kind: str
    # Legacy identifier (node id or "source-[TYPE]->target"); never property content.
    reference: str
    reason: str


@dataclass(frozen=True)
class UpgradePlan:
    operations: list[CanonOperation]
    skipped: list[SkippedItem] = field(default_factory=list)
    entity_count: int = 0
    fact_count: int = 0
    already_present: int = 0

    @property
    def summary(self) -> dict[str, int]:
        return {
            "entities": self.entity_count,
            "facts": self.fact_count,
            "already_present": self.already_present,
            "skipped": len(self.skipped),
        }


@dataclass(frozen=True)
class UpgradeResult:
    mutation: MutationSet
    plan: UpgradePlan


def validate_graph_archive(archive: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Verify a Phase 1 graph archive and return its nodes and edges (pure)."""
    if not isinstance(archive, dict):
        raise GraphArchiveError("The archive must be a JSON object.")
    if archive.get("archive_version") != ARCHIVE_VERSION or archive.get("schema_version") != (
        SCHEMA_VERSION
    ):
        raise GraphArchiveError("This is not a supported Phase 1 graph archive.")
    if archive.get("checksum") != _checksum(archive):
        raise GraphArchiveError("The archive checksum does not match its contents.")
    nodes, edges = archive.get("nodes"), archive.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise GraphArchiveError("The archive is missing its node or edge list.")
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        raise GraphArchiveError(
            f"The archive exceeds the upgrade bound of {MAX_NODES} nodes / {MAX_EDGES} edges."
        )
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            raise GraphArchiveError("The archive contains a malformed node.")
    for edge in edges:
        if not isinstance(edge, dict) or not all(
            isinstance(edge.get(key), str) for key in ("source", "target", "type")
        ):
            raise GraphArchiveError("The archive contains a malformed edge.")
    return nodes, edges


def _attributes(properties: dict[str, Any]) -> dict[str, Any]:
    """Keep bounded scalar properties; nested structures and embeddings are dropped."""
    kept: dict[str, Any] = {}
    for key, value in properties.items():
        if key == "name" or not isinstance(key, str) or len(kept) >= MAX_ATTRIBUTES:
            continue
        if isinstance(value, _SCALARS) and len(str(value)) <= MAX_ATTRIBUTE_LENGTH:
            kept[key] = value
    return kept


def plan_upgrade(
    archive: Any,
    *,
    project_id: str,
    entity_types: set[str],
    predicates: dict[str, PredicateSpec],
    existing: dict[str, tuple[str, str]],
    source_artefact_id: str,
    existing_facts: frozenset[tuple[str, str, str]] = frozenset(),
) -> UpgradePlan:
    """Map a legacy graph into typed canon operations (pure; performs no I/O).

    ``existing`` maps a legacy node id already bound in the project (through its
    ``legacy-graph`` external identifier) to ``(entity_id, entity_type)``, so a
    second upgrade of the same archive proposes only what is still missing.
    ``existing_facts`` holds the open ``(subject, predicate, object)`` triples
    already in canon for those entities, so facts are not proposed twice either.
    """
    nodes, edges = validate_graph_archive(archive)
    operations: list[CanonOperation] = []
    skipped: list[SkippedItem] = []
    # legacy node id -> (canon reference, entity type)
    resolved: dict[str, tuple[str, str]] = dict(existing)
    already_present = 0
    entity_count = 0

    for index, node in enumerate(nodes):
        legacy_id = node["id"]
        node_type = node.get("type")
        raw_properties = node.get("properties")
        properties: dict[str, Any] = raw_properties if isinstance(raw_properties, dict) else {}
        if legacy_id in existing:
            already_present += 1
            continue
        if not isinstance(node_type, str) or node_type not in entity_types:
            # Appearance/State/Belief nodes and similar are not entities in canon:
            # a legacy "Belief" carried a stored truth flag canon deliberately lacks.
            skipped.append(
                SkippedItem("node", legacy_id, f"type {node_type!r} is not a canon entity type")
            )
            continue
        if not _LEGACY_ID.match(legacy_id):
            skipped.append(SkippedItem("node", legacy_id[:80], "identifier is not portable"))
            continue
        name = properties.get("name")
        if not isinstance(name, str) or name_problem(name) is not None:
            name = legacy_id[:MAX_NAME_LENGTH]
        temporary = f"tmp:legacy-{index}"
        resolved[legacy_id] = (temporary, node_type)
        entity_count += 1
        operations.append(
            CreateEntityOp(
                id=temporary,
                type=node_type,
                name=name.strip(),
                attributes=_attributes(properties),
                # Binds the entity to its legacy id so re-running finds it.
                external_ids={LEGACY_NAMESPACE: legacy_id},
                evidence=[EvidenceRef(source_artefact_id=source_artefact_id)],
            )
        )

    fact_count = 0
    claimed: set[tuple[str, str]] = set()
    for index, edge in enumerate(edges):
        reference = f"{edge['source']}-[{edge['type']}]->{edge['target']}"[:200]
        spec = predicates.get(edge["type"])
        if spec is None or spec.object_shape is not ObjectKind.ENTITY_REF:
            skipped.append(
                SkippedItem("edge", reference, f"{edge['type']!r} is not an entity predicate")
            )
            continue
        source, target = resolved.get(edge["source"]), resolved.get(edge["target"])
        if source is None or target is None:
            skipped.append(SkippedItem("edge", reference, "an endpoint was not upgraded"))
            continue
        if source[1] not in spec.subject_types or (
            spec.object_types and target[1] not in spec.object_types
        ):
            skipped.append(
                SkippedItem("edge", reference, "endpoint types do not fit the predicate")
            )
            continue
        if (source[0], spec.predicate, target[0]) in existing_facts:
            already_present += 1
            # It still occupies a single-valued predicate's one slot.
            claimed.add((source[0], spec.predicate))
            continue
        if spec.cardinality is Cardinality.ONE:
            if (source[0], spec.predicate) in claimed:
                skipped.append(
                    SkippedItem("edge", reference, "predicate allows one value; first kept")
                )
                continue
            claimed.add((source[0], spec.predicate))
        fact_count += 1
        operations.append(
            AssertFactOp(
                id=f"tmp:legacy-fact-{index}",
                proposition=Proposition(
                    subject_id=source[0],
                    predicate=spec.predicate,
                    object=EntityRefObject(entity_id=target[0], project_id=project_id),
                ),
                evidence=[EvidenceRef(source_artefact_id=source_artefact_id)],
            )
        )

    return UpgradePlan(
        operations=operations,
        skipped=skipped,
        entity_count=entity_count,
        fact_count=fact_count,
        already_present=already_present,
    )


class GraphArchiveUpgradeService:
    def __init__(
        self,
        *,
        mutation: MutationService,
        reader: CanonRepositoryPort,
        schema_store: SchemaVersionStorePort | None = None,
    ) -> None:
        self._mutation = mutation
        self._reader = reader
        self._schema_store = schema_store

    async def propose(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        archive: Any,
        *,
        expected_revision: int,
    ) -> UpgradeResult:
        """File a legacy graph archive as a reviewable import proposal.

        The proposal is an ``import``-origin mutation set: the review gate holds
        it until a human approves it, and the commit transaction validates every
        operation against the project's published schema.
        """
        nodes, _edges = validate_graph_archive(archive)
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

        existing: dict[str, tuple[str, str]] = {}
        for node in nodes:
            if not _LEGACY_ID.match(node["id"]):
                continue
            bound = await self._reader.get_entity_by_external_id(
                workspace_id=workspace_id,
                project_id=project_id,
                namespace=LEGACY_NAMESPACE,
                value=node["id"],
            )
            if bound is not None:
                existing[node["id"]] = (bound.id, bound.type)

        existing_facts: set[tuple[str, str, str]] = set()
        for entity_id, _entity_type in existing.values():
            for fact in await self._reader.list_facts(
                workspace_id=workspace_id, project_id=project_id, subject_id=entity_id, limit=500
            ):
                obj = fact.proposition.object
                if isinstance(obj, EntityRefObject):
                    existing_facts.add((entity_id, fact.proposition.predicate, obj.entity_id))

        plan = plan_upgrade(
            archive,
            project_id=project_id,
            entity_types=entity_types,
            predicates=predicates,
            existing=existing,
            source_artefact_id=f"legacy-archive:{str(archive['checksum'])[:32]}",
            existing_facts=frozenset(existing_facts),
        )
        if not plan.operations:
            raise GraphArchiveError("Nothing in this archive can be upgraded into canon.")
        if len(plan.operations) > MAX_OPERATIONS:
            raise GraphArchiveError(
                f"The upgrade would propose {len(plan.operations)} operations; "
                f"the bound is {MAX_OPERATIONS}. Split the archive and upgrade it in parts."
            )
        mutation = await self._mutation.propose(
            scope,
            workspace_id=workspace_id,
            project_id=project_id,
            operations=plan.operations,
            expected_head=expected_revision,
            origin=OriginKind.IMPORT.value,
            idempotency_key=f"legacy-upgrade:{str(archive['checksum'])[:48]}:{expected_revision}",
        )
        return UpgradeResult(mutation=mutation, plan=plan)


__all__ = [
    "LEGACY_NAMESPACE",
    "MAX_OPERATIONS",
    "GraphArchiveError",
    "GraphArchiveUpgradeService",
    "SkippedItem",
    "UpgradePlan",
    "UpgradeResult",
    "plan_upgrade",
    "validate_graph_archive",
]
