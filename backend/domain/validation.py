"""Authoritative canon validation (ADR-012/ADR-013).

All canon validation that can block a commit runs inside the canonical
PostgreSQL transaction, against the branch head being advanced. Findings are
split into permanently blocking integrity findings, blocking author-rule
findings (only when a rule is published as blocking and deterministically
evaluable), and advisory findings. There is no suppression path: the canonical
repository invokes this validator on every commit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.registry import (
    CORE_ENTITY_TYPES,
    predicate_spec,
    validate_proposition,
)
from backend.domain.media import MediaAsset, MediaAssetVersion, is_link_role
from backend.domain.operations import (
    AssertBeliefOp,
    AssertFactOp,
    CanonOperation,
    CreateEntityOp,
    CreateEventOp,
    LinkAssetOp,
    RetireEntityOp,
    UpdateEntityOp,
    operation_reference_ids,
)


class ValidationSeverity(StrEnum):
    INTEGRITY = "integrity"
    BLOCKING_RULE = "blocking_rule"
    ADVISORY = "advisory"


BLOCKING_SEVERITIES = frozenset({ValidationSeverity.INTEGRITY, ValidationSeverity.BLOCKING_RULE})


@dataclass(frozen=True)
class ValidationFinding:
    severity: ValidationSeverity
    code: str
    message: str
    operation_ordinal: int | None = None


@dataclass(frozen=True)
class DomainValidationReport:
    findings: tuple[ValidationFinding, ...] = field(default_factory=tuple)

    @property
    def blocking(self) -> tuple[ValidationFinding, ...]:
        return tuple(f for f in self.findings if f.severity in BLOCKING_SEVERITIES)

    @property
    def advisory(self) -> tuple[ValidationFinding, ...]:
        return tuple(f for f in self.findings if f.severity is ValidationSeverity.ADVISORY)

    @property
    def ok(self) -> bool:
        return not self.blocking


class CanonValidationError(RuntimeError):
    """A commit was rejected by the authoritative validator (transaction rolls back)."""

    def __init__(self, report: DomainValidationReport) -> None:
        self.report = report
        detail = "; ".join(f"[{f.code}] {f.message}" for f in report.blocking) or "invalid canon"
        super().__init__(detail)


class CanonTxReader(Protocol):
    """Reads bound to the canonical transaction running the validation."""

    async def get_entity(self, entity_id: str) -> CanonEntity | None: ...

    async def count_open_facts(self, subject_id: str, predicate: str) -> int: ...

    async def get_asset(self, asset_id: str) -> MediaAsset | None: ...

    async def get_asset_version(self, version_id: str) -> MediaAssetVersion | None: ...


@dataclass(frozen=True)
class ValidationContext:
    workspace_id: str
    project_id: str
    branch_id: str
    schema_version_id: str | None
    head_revision: int
    reader: CanonTxReader
    allowed_entity_types: frozenset[str] = field(
        default_factory=lambda: frozenset(CORE_ENTITY_TYPES)
    )


class AuthoritativeValidator:
    """Pure, deterministic canon validation over an ordered operation set.

    Depends only on the domain registry and a transaction-bound reader, so it can
    run inside the canonical PostgreSQL transaction without service coupling.
    """

    async def validate(
        self, operations: Sequence[CanonOperation], *, context: ValidationContext
    ) -> DomainValidationReport:
        findings: list[ValidationFinding] = []
        created_types: dict[str, str] = {}
        seen: set[str] = set()
        for index, operation in enumerate(operations):
            if operation.id in seen:
                findings.append(
                    ValidationFinding(
                        ValidationSeverity.INTEGRITY,
                        "duplicate_id",
                        f"duplicate id {operation.id!r}",
                        index,
                    )
                )
            seen.add(operation.id)
            if isinstance(operation, CreateEntityOp):
                created_types[operation.id] = operation.type

        existing: dict[str, CanonEntity] = {}
        referenced: set[str] = set()
        for operation in operations:
            referenced.update(operation_reference_ids(operation))
            referenced.add(operation.id)
        for entity_id in referenced:
            if entity_id in created_types or entity_id in existing:
                continue
            entity = await context.reader.get_entity(entity_id)
            if entity is not None:
                existing[entity_id] = entity

        def subject_type(entity_id: str) -> str | None:
            if entity_id in created_types:
                return created_types[entity_id]
            entity = existing.get(entity_id)
            return entity.type if entity is not None else None

        def exists(entity_id: str) -> bool:
            return entity_id in created_types or entity_id in existing

        for index, operation in enumerate(operations):
            await self._validate_operation(
                index, operation, context, subject_type, exists, findings
            )

        return DomainValidationReport(findings=tuple(findings))

    async def _validate_operation(
        self,
        index: int,
        operation: CanonOperation,
        context: ValidationContext,
        subject_type,
        exists,
        findings: list[ValidationFinding],
    ) -> None:
        def blocking(code: str, message: str) -> None:
            findings.append(ValidationFinding(ValidationSeverity.INTEGRITY, code, message, index))

        if isinstance(operation, CreateEntityOp):
            if operation.type not in context.allowed_entity_types:
                blocking("unknown_type", f"unknown entity type {operation.type!r}")
        elif isinstance(operation, (UpdateEntityOp, RetireEntityOp)):
            if not exists(operation.id):
                blocking("unknown_entity", f"entity {operation.id!r} does not exist")
        elif isinstance(operation, AssertFactOp):
            stype = subject_type(operation.proposition.subject_id)
            if stype is None:
                blocking("unknown_subject", f"unknown subject {operation.proposition.subject_id!r}")
            else:
                try:
                    validate_proposition(operation.proposition, subject_type=stype)
                except Exception as error:
                    blocking("invalid_proposition", str(error))
            spec = predicate_spec(operation.proposition.predicate)
            if spec is not None and spec.cardinality.value == "one":
                open_count = await context.reader.count_open_facts(
                    operation.proposition.subject_id, operation.proposition.predicate
                )
                if open_count > 0:
                    blocking(
                        "cardinality_conflict",
                        f"predicate {operation.proposition.predicate!r} already asserted for "
                        f"{operation.proposition.subject_id!r}",
                    )
        elif isinstance(operation, AssertBeliefOp):
            holder = subject_type(operation.holder_entity_id)
            if holder is None:
                blocking("unknown_holder", f"unknown holder {operation.holder_entity_id!r}")
            elif holder != "Character":
                blocking("invalid_holder", "belief holder must be a Character")
            if not operation.source_ref:
                blocking("missing_source", "belief requires an acquisition source_ref")
            stype = subject_type(operation.proposition.subject_id)
            if stype is not None:
                try:
                    validate_proposition(operation.proposition, subject_type=stype)
                except Exception as error:
                    blocking("invalid_proposition", str(error))
        elif isinstance(operation, CreateEventOp):
            for participant in operation.participants:
                if not exists(participant.entity_id):
                    blocking(
                        "unknown_reference",
                        f"unknown event participant {participant.entity_id!r}",
                    )
                elif not participant.role:
                    blocking("missing_role", "event participant role is required")
            for ref in (*operation.location_ids, *operation.cause_ids, *operation.outcome_ids):
                if not exists(ref):
                    blocking("unknown_reference", f"unknown event reference {ref!r}")
        elif isinstance(operation, LinkAssetOp):
            if not is_link_role(operation.role):
                blocking("invalid_role", f"unknown link role {operation.role!r}")
            asset = await context.reader.get_asset(operation.asset_id)
            if asset is None:
                blocking("unknown_asset", f"unknown asset {operation.asset_id!r}")
            elif not asset.is_active:
                blocking("inactive_asset", f"asset {operation.asset_id!r} is not active")
            if operation.asset_version_id is not None:
                version = await context.reader.get_asset_version(operation.asset_version_id)
                if version is None:
                    blocking(
                        "unknown_asset_version",
                        f"unknown asset version {operation.asset_version_id!r}",
                    )
                elif version.asset_id != operation.asset_id:
                    blocking(
                        "asset_version_mismatch",
                        "asset version does not belong to the linked asset",
                    )
            if operation.target_record_type != "entity":
                blocking(
                    "unsupported_target",
                    f"unsupported link target type {operation.target_record_type!r}",
                )
            elif not exists(operation.target_id):
                blocking("unknown_target", f"unknown link target {operation.target_id!r}")


__all__ = [
    "AuthoritativeValidator",
    "CanonTxReader",
    "CanonValidationError",
    "DomainValidationReport",
    "ValidationContext",
    "ValidationFinding",
    "ValidationSeverity",
]
