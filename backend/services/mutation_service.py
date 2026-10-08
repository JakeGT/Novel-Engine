"""Mutation validation and the ADR-012 canonical commit.

The service validates an ordered operation set against the exact schema version
and expected head, then commits it as ONE PostgreSQL transaction through the
canonical repository. Success is decided entirely in PostgreSQL; the graph is a
rebuildable projection applied separately (see ``GraphProjectionService``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from backend.adapters.system import uuid7
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.registry import (
    CORE_ENTITY_TYPES,
    validate_proposition,
)
from backend.domain.canon.revisions import OriginKind
from backend.domain.identity import AccessScope
from backend.domain.journal import (
    CommitEnvelope,
    MutationNotFound,
    MutationOperation,
    MutationSet,
)
from backend.domain.operations import (
    AssertBeliefOp,
    AssertFactOp,
    CanonOperation,
    CreateEntityOp,
    CreateEventOp,
    LinkAssetOp,
    OperationValidationError,
    RetireEntityOp,
    UpdateEntityOp,
    canonicalize_operations,
    operation_reference_ids,
    operations_checksum,
    parse_operation,
    resolve_temporary_ids,
)
from backend.domain.tenancy import Permission
from backend.ports import CanonJournalPort, CanonRepositoryPort

EntityLookup = Callable[[str, str, str], CanonEntity | None]


@dataclass(frozen=True)
class ValidationIssue:
    ordinal: int
    code: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class CommitResult:
    commit_id: str
    revision: int
    checksum: str
    status: str


@dataclass(frozen=True)
class ReconcileResult:
    status: str
    revision: int | None = None


def _deterministic_id(mutation_id: str, kind: str, temporary_id: str) -> str:
    digest = hashlib.sha256(f"{mutation_id}:{temporary_id}".encode()).hexdigest()[:16]
    return format_canon_id(kind, digest)


class MutationService:
    def __init__(
        self,
        journal: CanonJournalPort,
        repository: CanonRepositoryPort,
        *,
        tenancy: object | None = None,
        local_mode: bool = False,
        entity_lookup: EntityLookup | None = None,
    ) -> None:
        self._journal = journal
        self._repository = repository
        self._tenancy = tenancy
        self._local_mode = local_mode
        self._entity_lookup = entity_lookup

    async def _authorize(
        self, scope: AccessScope, workspace_id: str, project_id: str, permission: Permission
    ) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(scope, workspace_id, project_id, permission)  # type: ignore[attr-defined]

    async def create_draft(
        self,
        scope: AccessScope,
        *,
        workspace_id: str,
        project_id: str,
        operations: Sequence[CanonOperation],
        expected_head: int,
        branch_id: str | None = None,
        idempotency_key: str = "",
        origin: str = OriginKind.AUTHOR.value,
        schema_version_id: str | None = None,
        correlation_id: str = "",
    ) -> MutationSet:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if branch_id is None:
            branch = await self._journal.ensure_root_branch(
                workspace_id=workspace_id, project_id=project_id
            )
            branch_id = branch.id
        mutation = await self._journal.create_mutation(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch_id,
            expected_head=expected_head,
            actor_user_id=scope.actor_user_id,
            origin=origin,
            schema_version_id=schema_version_id,
            correlation_id=correlation_id or scope.correlation_id,
            idempotency_key=idempotency_key,
        )
        await self._journal.record_operations(
            mutation_id=mutation.id,
            operations=[
                MutationOperation(
                    ordinal=index,
                    operation_type=operation.op,
                    payload=operation.model_dump(mode="json"),
                )
                for index, operation in enumerate(operations)
            ],
        )
        return mutation

    async def _load_operations(self, mutation_id: str) -> list[CanonOperation]:
        stored = await self._journal.list_operations(mutation_id=mutation_id)
        return [parse_operation(item.payload) for item in stored]

    async def validate(self, scope: AccessScope, mutation_id: str) -> ValidationReport:
        mutation = await self._journal.get_mutation(
            workspace_id=scope.workspace_id or "",
            project_id=scope.project_id or "",
            mutation_id=mutation_id,
        )
        if mutation is None:
            raise MutationNotFound(f"mutation {mutation_id!r} not found")
        await self._authorize(
            scope, mutation.workspace_id, mutation.project_id, Permission.PROJECT_WRITE
        )
        operations = await self._load_operations(mutation_id)

        created_types: dict[str, str] = {}
        seen: set[str] = set()
        issues: list[ValidationIssue] = []
        for index, operation in enumerate(operations):
            if operation.id in seen:
                issues.append(
                    ValidationIssue(index, "duplicate_id", f"duplicate id {operation.id!r}")
                )
            seen.add(operation.id)
            if isinstance(operation, CreateEntityOp):
                created_types[operation.id] = operation.type

        # Resolve pre-existing referenced entities (co-created ids take precedence).
        existing_types: dict[str, str] = {}
        referenced: set[str] = set()
        for operation in operations:
            referenced.update(operation_reference_ids(operation))
            referenced.add(operation.id)
        for entity_id in referenced:
            if entity_id in created_types:
                continue
            if self._entity_lookup is not None:
                entity = self._entity_lookup(mutation.workspace_id, mutation.project_id, entity_id)
            else:
                entity = await self._repository.get_entity(
                    workspace_id=mutation.workspace_id,
                    project_id=mutation.project_id,
                    entity_id=entity_id,
                )
            if entity is not None:
                existing_types[entity_id] = entity.type

        def subject_type(entity_id: str) -> str | None:
            if entity_id in created_types:
                return created_types[entity_id]
            return existing_types.get(entity_id)

        def exists(entity_id: str) -> bool:
            return entity_id in created_types or entity_id in existing_types

        for index, operation in enumerate(operations):
            try:
                self._validate_operation(index, operation, subject_type, exists, issues)
            except Exception as error:  # registry/proposition errors surface as issues
                issues.append(ValidationIssue(index, "invalid_operation", str(error)))
        return ValidationReport(issues=tuple(issues))

    def _validate_operation(
        self,
        index: int,
        operation: CanonOperation,
        subject_type: Callable[[str], str | None],
        exists: Callable[[str], bool],
        issues: list[ValidationIssue],
    ) -> None:
        if isinstance(operation, (CreateEntityOp,)):
            if operation.type not in CORE_ENTITY_TYPES:
                issues.append(
                    ValidationIssue(
                        index, "unknown_type", f"unknown entity type {operation.type!r}"
                    )
                )
        elif isinstance(operation, (UpdateEntityOp, RetireEntityOp)):
            if not exists(operation.id):
                issues.append(
                    ValidationIssue(
                        index, "unknown_entity", f"entity {operation.id!r} does not exist"
                    )
                )
        elif isinstance(operation, AssertFactOp):
            stype = subject_type(operation.proposition.subject_id)
            if stype is None:
                issues.append(
                    ValidationIssue(
                        index,
                        "unknown_subject",
                        f"unknown subject {operation.proposition.subject_id!r}",
                    )
                )
            else:
                validate_proposition(operation.proposition, subject_type=stype)
        elif isinstance(operation, AssertBeliefOp):
            if not exists(operation.holder_entity_id):
                issues.append(
                    ValidationIssue(
                        index, "unknown_holder", f"unknown holder {operation.holder_entity_id!r}"
                    )
                )
            stype = subject_type(operation.proposition.subject_id)
            if stype is not None:
                validate_proposition(operation.proposition, subject_type=stype)
        elif isinstance(operation, CreateEventOp):
            refs = [
                *(p.entity_id for p in operation.participants),
                *operation.location_ids,
                *operation.cause_ids,
                *operation.outcome_ids,
            ]
            for ref in refs:
                if not exists(ref):
                    issues.append(
                        ValidationIssue(
                            index, "unknown_reference", f"unknown event reference {ref!r}"
                        )
                    )
        elif isinstance(operation, LinkAssetOp):
            if not exists(operation.target_id):
                issues.append(
                    ValidationIssue(
                        index, "unknown_target", f"unknown link target {operation.target_id!r}"
                    )
                )

    async def approve(self, scope: AccessScope, mutation_id: str, note: str | None = None) -> None:
        mutation = await self._journal.get_mutation(
            workspace_id=scope.workspace_id or "",
            project_id=scope.project_id or "",
            mutation_id=mutation_id,
        )
        if mutation is None:
            raise MutationNotFound(f"mutation {mutation_id!r} not found")
        await self._authorize(
            scope, mutation.workspace_id, mutation.project_id, Permission.PROJECT_WRITE
        )
        await self._journal.record_decision(
            mutation_id=mutation_id,
            decision="approved",
            actor_user_id=scope.actor_user_id,
            note=note,
        )

    async def commit(self, scope: AccessScope, mutation_id: str) -> CommitResult:
        mutation = await self._journal.get_mutation(
            workspace_id=scope.workspace_id or "",
            project_id=scope.project_id or "",
            mutation_id=mutation_id,
        )
        if mutation is None:
            raise MutationNotFound(f"mutation {mutation_id!r} not found")
        await self._authorize(
            scope, mutation.workspace_id, mutation.project_id, Permission.PROJECT_WRITE
        )

        report = await self.validate(scope, mutation_id)
        if not report.ok:
            raise OperationValidationError(
                "; ".join(f"[{issue.code}] {issue.message}" for issue in report.issues)
            )

        operations = await self._load_operations(mutation_id)
        resolved, _mapping = resolve_temporary_ids(
            operations, new_id=lambda kind, tmp: _deterministic_id(mutation_id, kind, tmp)
        )
        canonical = canonicalize_operations(resolved)
        checksum = operations_checksum(resolved)
        commit_id = f"cmt_{uuid7()}"
        envelope = CommitEnvelope(
            commit_id=commit_id,
            mutation_id=mutation_id,
            workspace_id=mutation.workspace_id,
            project_id=mutation.project_id,
            branch_id=mutation.branch_id,
            revision=mutation.expected_head + 1,
            expected_head=mutation.expected_head,
            schema_version_id=mutation.schema_version_id,
            canonical=canonical,
            checksum=checksum,
            actor_user_id=scope.actor_user_id,
            created_at=datetime.now(UTC),
        )
        # ONE PostgreSQL transaction decides canonical success: reserve/advance the
        # head, append the immutable envelope, apply operations to normalized canon,
        # record the revision, and enqueue the outbox. Neo4j is not involved.
        revision = await self._repository.apply_commit(envelope=envelope, operations=resolved)
        return CommitResult(
            commit_id=commit_id,
            revision=revision,
            checksum=checksum,
            status="committed",
        )

    async def reconcile(self, commit_id: str) -> ReconcileResult:
        # Canonical commits are atomic in PostgreSQL, so an existing envelope is a
        # committed revision; there is no partial canonical state to repair.
        envelope = await self._repository.get_envelope(commit_id)
        if envelope is None:
            raise MutationNotFound(f"commit {commit_id!r} not found")
        return ReconcileResult(status="committed", revision=envelope.revision)


__all__ = [
    "CommitResult",
    "MutationService",
    "ReconcileResult",
    "ValidationIssue",
    "ValidationReport",
]
