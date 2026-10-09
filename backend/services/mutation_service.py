"""Mutation validation and the ADR-012 canonical commit.

The authoritative validator runs *inside* the canonical PostgreSQL transaction
(see ``CanonRepositoryPort.apply_commit``); the service only orchestrates draft
creation, tempo id resolution, envelope construction, and commit. There is no
code path that writes canon without validation. The graph is a separate,
rebuildable projection.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from backend.domain.canon.assertions import Belief, Fact
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import Event
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.registry import CORE_ENTITY_TYPES
from backend.domain.canon.revisions import OriginKind
from backend.domain.identity import AccessScope
from backend.domain.journal import (
    CommitEnvelope,
    MutationNotFound,
    MutationOperation,
    MutationSet,
    RevisionConflict,
)
from backend.domain.media import AssetLink
from backend.domain.operations import (
    CanonOperation,
    canonicalize_operations,
    operations_checksum,
    parse_operation,
    resolve_temporary_ids,
)
from backend.domain.schemas import SchemaVersionStatus
from backend.domain.tenancy import Permission
from backend.domain.validation import (
    AuthoritativeValidator,
    CanonValidationError,
    DomainValidationReport,
    ValidationContext,
    ValidationFinding,
    ValidationSeverity,
)
from backend.ports import CanonJournalPort, CanonRepositoryPort, SchemaVersionStorePort
from backend.services.schema_compiler import compile_definition


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


def _deterministic_commit_id(mutation_id: str) -> str:
    return f"cmt_{hashlib.sha256(mutation_id.encode()).hexdigest()[:32]}"


class _RepositoryReader:
    """Preview reads for ``validate`` (non-transactional; the commit still revalidates in-tx)."""

    def __init__(self, repository: CanonRepositoryPort, workspace_id: str, project_id: str) -> None:
        self._repository = repository
        self._workspace_id = workspace_id
        self._project_id = project_id

    async def get_entity(self, entity_id: str) -> CanonEntity | None:
        return await self._repository.get_entity(
            workspace_id=self._workspace_id,
            project_id=self._project_id,
            entity_id=entity_id,
        )

    async def count_open_facts(self, subject_id: str, predicate: str) -> int:
        facts = await self._repository.list_facts(
            workspace_id=self._workspace_id,
            project_id=self._project_id,
            include_closed=False,
            subject_id=subject_id,
            predicate=predicate,
            limit=1,
        )
        return len(facts)

    async def get_fact(self, fact_id: str) -> Fact | None:
        return await self._repository.get_fact(
            workspace_id=self._workspace_id,
            project_id=self._project_id,
            fact_id=fact_id,
        )

    async def get_belief(self, belief_id: str) -> Belief | None:
        return await self._repository.get_belief(
            workspace_id=self._workspace_id,
            project_id=self._project_id,
            belief_id=belief_id,
        )

    async def get_event(self, event_id: str) -> Event | None:
        return await self._repository.get_event(
            workspace_id=self._workspace_id,
            project_id=self._project_id,
            event_id=event_id,
        )

    async def get_link(self, link_id: str) -> AssetLink | None:
        return await self._repository.get_link(
            workspace_id=self._workspace_id,
            project_id=self._project_id,
            link_id=link_id,
        )

    async def get_asset(self, asset_id: str) -> None:
        # Preview reader can't reach the media store; in-tx commit revalidates links.
        return None

    async def get_asset_version(self, version_id: str) -> None:
        return None


class MutationService:
    def __init__(
        self,
        journal: CanonJournalPort,
        repository: CanonRepositoryPort,
        *,
        tenancy: object | None = None,
        local_mode: bool = False,
        validator: AuthoritativeValidator | None = None,
        schema_store: SchemaVersionStorePort | None = None,
    ) -> None:
        self._journal = journal
        self._repository = repository
        self._tenancy = tenancy
        self._local_mode = local_mode
        self._validator = validator or AuthoritativeValidator()
        self._schema_store = schema_store

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
        if (
            mutation.workspace_id != workspace_id
            or mutation.project_id != project_id
            or mutation.branch_id != branch_id
            or mutation.expected_head != expected_head
            or mutation.origin != origin
            or mutation.schema_version_id != schema_version_id
        ):
            raise RevisionConflict("idempotency key was reused with different mutation metadata")
        desired_operations = [
            MutationOperation(
                ordinal=index,
                operation_type=operation.op,
                payload=operation.model_dump(mode="json"),
            )
            for index, operation in enumerate(operations)
        ]
        existing_operations = await self._journal.list_operations(mutation_id=mutation.id)
        if existing_operations and existing_operations != desired_operations:
            raise RevisionConflict("idempotency key was reused with different operations")
        await self._journal.record_operations(
            mutation_id=mutation.id,
            operations=desired_operations,
        )
        return mutation

    async def _load_operations(self, mutation_id: str) -> list[CanonOperation]:
        stored = await self._journal.list_operations(mutation_id=mutation_id)
        return [parse_operation(item.payload) for item in stored]

    async def validate(self, scope: AccessScope, mutation_id: str) -> ValidationReport:
        """Non-transactional preview. The commit revalidates authoritatively in-tx."""
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
        reader = _RepositoryReader(self._repository, mutation.workspace_id, mutation.project_id)
        compiled = None
        if mutation.schema_version_id is not None:
            version = (
                await self._schema_store.get(
                    workspace_id=mutation.workspace_id,
                    project_id=mutation.project_id,
                    version_id=mutation.schema_version_id,
                )
                if self._schema_store is not None
                else None
            )
            if version is None or version.status is not SchemaVersionStatus.PUBLISHED:
                return ValidationReport(
                    issues=(
                        ValidationIssue(
                            0,
                            "invalid_schema_version",
                            "mutation schema version is missing, outside this project, or unpublished",
                        ),
                    )
                )
            compiled = compile_definition(
                version.definition,
                workspace_id=mutation.workspace_id,
                project_id=mutation.project_id,
            )
        report = await self._validator.validate(
            operations,
            context=ValidationContext(
                workspace_id=mutation.workspace_id,
                project_id=mutation.project_id,
                branch_id=mutation.branch_id,
                schema_version_id=mutation.schema_version_id,
                head_revision=mutation.expected_head,
                reader=reader,
                allowed_entity_types=frozenset(
                    set(CORE_ENTITY_TYPES) | (set(compiled.entity_types) if compiled else set())
                ),
                entity_attribute_models=compiled.entity_models if compiled else {},
                predicate_specs=compiled.predicate_specs if compiled else {},
            ),
        )
        return ValidationReport(
            issues=tuple(
                ValidationIssue(f.operation_ordinal or 0, f.code, f.message)
                for f in report.blocking
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

        decisions = await self._journal.list_decisions(mutation_id=mutation_id)
        latest_decision = decisions[-1].decision if decisions else None
        if latest_decision == "rejected":
            raise CanonValidationError(
                DomainValidationReport(
                    findings=(
                        ValidationFinding(
                            ValidationSeverity.INTEGRITY,
                            "review_rejected",
                            "the proposal was rejected during review",
                        ),
                    )
                )
            )
        if (
            mutation.origin not in {OriginKind.AUTHOR.value, OriginKind.MIGRATION.value}
            and latest_decision != "approved"
        ):
            raise CanonValidationError(
                DomainValidationReport(
                    findings=(
                        ValidationFinding(
                            ValidationSeverity.INTEGRITY,
                            "review_required",
                            "non-author proposals require an explicit human approval",
                        ),
                    )
                )
            )

        operations = await self._load_operations(mutation_id)
        resolved, _mapping = resolve_temporary_ids(
            operations, new_id=lambda kind, tmp: _deterministic_id(mutation_id, kind, tmp)
        )
        canonical = canonicalize_operations(resolved)
        checksum = operations_checksum(resolved)
        commit_id = _deterministic_commit_id(mutation_id)
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
        # ONE PostgreSQL transaction: lock head, run the authoritative validator,
        # reserve/advance the head, append the immutable envelope, apply operations
        # to normalized canon, record the revision, and enqueue the outbox.
        revision = await self._repository.apply_commit(
            envelope=envelope,
            operations=resolved,
            validator=self._validator,
            origin=mutation.origin,
        )
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
