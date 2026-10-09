"""Mutation validation and the ADR-012 canonical commit.

The authoritative validator runs *inside* the canonical PostgreSQL transaction
(see ``CanonRepositoryPort.apply_commit``); the service only orchestrates draft
creation, tempo id resolution, envelope construction, and commit. There is no
code path that writes canon without validation. The graph is a separate,
rebuildable projection.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from backend.core.logging import log_event
from backend.domain.canon.assertions import Belief, Fact
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import Event
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.registry import CORE_ENTITY_TYPES
from backend.domain.canon.revisions import OriginKind
from backend.domain.errors import ValidationError as DomainValidationError
from backend.domain.identity import AccessScope
from backend.domain.journal import (
    CommitEnvelope,
    MutationNotFound,
    MutationOperation,
    MutationSet,
    MutationStatus,
    MutationTransition,
    OperationDecision,
    ReviewDecision,
    RevisionConflict,
)
from backend.domain.media import AssetLink, MediaAsset, MediaAssetVersion
from backend.domain.operations import (
    CanonOperation,
    canonicalize_operations,
    operations_checksum,
    parse_operation,
    resolve_temporary_ids,
)
from backend.domain.review import (
    MutationIntent,
    OperationDecisionKind,
    ReviewClassification,
    classify_review,
    effective_operations,
    is_editable,
    latest_operation_decisions,
    review_gate,
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
from backend.ports import (
    CanonJournalPort,
    CanonRepositoryPort,
    MediaStorePort,
    SchemaVersionStorePort,
)
from backend.services.schema_compiler import compile_definition

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationIssue:
    ordinal: int
    code: str
    message: str
    # ``blocking`` findings stop a commit; ``advisory`` findings only inform review.
    severity: str = "blocking"


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not any(issue.severity == "blocking" for issue in self.issues)


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


@dataclass(frozen=True)
class MutationReview:
    """Everything a reviewer needs to decide on one mutation set."""

    mutation: MutationSet
    operations: list[MutationOperation]
    operation_decisions: dict[int, OperationDecision]
    decisions: list[ReviewDecision]
    transitions: list[MutationTransition]
    classification: ReviewClassification
    head_revision: int
    targets_before: dict[int, dict | None]
    commit_id: str | None = None
    committed_revision: int | None = None


class ReviewRequiredError(CanonValidationError):
    """The proposal needs an explicit approval; it is now waiting in the review queue."""

    def __init__(self, mutation_id: str, reasons: tuple[str, ...], message: str) -> None:
        self.mutation_id = mutation_id
        self.reasons = reasons
        super().__init__(
            DomainValidationReport(
                findings=(
                    ValidationFinding(ValidationSeverity.INTEGRITY, "review_required", message),
                )
            )
        )


# Origins a client may name on a proposal. Migration/system/derived provenance is
# reserved for trusted internal writers and can never be claimed over the API.
PROPOSAL_ORIGINS = frozenset(
    {OriginKind.AUTHOR.value, OriginKind.MODEL.value, OriginKind.IMPORT.value}
)

# Operations whose target already exists, so a reviewer can compare before/after.
_TARGET_RECORD = {
    "update_entity": "entity",
    "retire_entity": "entity",
    "close_fact": "fact",
    "close_belief": "belief",
    "amend_event": "event",
    "retire_event": "event",
    "retire_asset_link": "asset_link",
}


def _deterministic_id(mutation_id: str, kind: str, temporary_id: str) -> str:
    digest = hashlib.sha256(f"{mutation_id}:{temporary_id}".encode()).hexdigest()[:16]
    return format_canon_id(kind, digest)


def _deterministic_commit_id(mutation_id: str) -> str:
    return f"cmt_{hashlib.sha256(mutation_id.encode()).hexdigest()[:32]}"


class _RepositoryReader:
    """Preview reads for ``validate`` (non-transactional; the commit still revalidates in-tx)."""

    def __init__(
        self,
        repository: CanonRepositoryPort,
        workspace_id: str,
        project_id: str,
        media: MediaStorePort | None = None,
    ) -> None:
        self._repository = repository
        self._workspace_id = workspace_id
        self._project_id = project_id
        self._media = media

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

    async def get_asset(self, asset_id: str) -> MediaAsset | None:
        if self._media is None:
            return None
        return await self._media.get_asset(
            workspace_id=self._workspace_id, project_id=self._project_id, asset_id=asset_id
        )

    async def get_asset_version(self, version_id: str) -> MediaAssetVersion | None:
        if self._media is None:
            return None
        return await self._media.get_version(
            workspace_id=self._workspace_id, project_id=self._project_id, version_id=version_id
        )

    async def find_entity_by_slug(self, slug: str) -> CanonEntity | None:
        return await self._repository.get_entity_by_slug(
            workspace_id=self._workspace_id, project_id=self._project_id, slug=slug
        )

    async def find_entity_by_external_id(self, namespace: str, value: str) -> CanonEntity | None:
        return await self._repository.get_entity_by_external_id(
            workspace_id=self._workspace_id,
            project_id=self._project_id,
            namespace=namespace,
            value=value,
        )

    async def find_entities_by_name(self, entity_type: str, name: str) -> list[CanonEntity]:
        return await self._repository.find_entities_by_name(
            workspace_id=self._workspace_id,
            project_id=self._project_id,
            entity_type=entity_type,
            name=name,
        )

    async def count_active_references(self, entity_id: str) -> int:
        return await self._repository.count_active_references(
            workspace_id=self._workspace_id, project_id=self._project_id, entity_id=entity_id
        )


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
        media: MediaStorePort | None = None,
    ) -> None:
        self._media = media
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
        intent: str = MutationIntent.EDIT.value,
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
            intent=intent,
        )
        if (
            mutation.workspace_id != workspace_id
            or mutation.project_id != project_id
            or mutation.branch_id != branch_id
            or mutation.expected_head != expected_head
            or mutation.origin != origin
            or mutation.schema_version_id != schema_version_id
            or mutation.intent != intent
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
        if existing_operations:
            return mutation
        await self._journal.record_operations(
            mutation_id=mutation.id,
            operations=desired_operations,
        )
        return mutation

    async def propose(
        self,
        scope: AccessScope,
        *,
        workspace_id: str,
        project_id: str,
        operations: Sequence[CanonOperation],
        expected_head: int,
        origin: str = OriginKind.AUTHOR.value,
        idempotency_key: str = "",
    ) -> MutationSet:
        """Create a reviewable proposal bound to the project's published schema version."""
        if origin not in PROPOSAL_ORIGINS:
            raise DomainValidationError(f"Proposals may not use origin {origin!r}.")
        if not operations:
            raise DomainValidationError("A proposal needs at least one operation.")
        schema_version_id: str | None = None
        if self._schema_store is not None:
            published = await self._schema_store.get_published(
                workspace_id=workspace_id, project_id=project_id
            )
            if published is None:
                raise DomainValidationError("No published schema version exists for this project.")
            schema_version_id = published.id
        return await self.create_draft(
            scope,
            workspace_id=workspace_id,
            project_id=project_id,
            operations=operations,
            expected_head=expected_head,
            idempotency_key=idempotency_key,
            origin=origin,
            schema_version_id=schema_version_id,
        )

    async def _effective(self, mutation_id: str) -> list[MutationOperation]:
        """Current operation revisions that reviewers have not rejected."""
        return effective_operations(
            await self._journal.list_operations(mutation_id=mutation_id),
            await self._journal.list_operation_decisions(mutation_id=mutation_id),
        )

    async def _require(
        self, scope: AccessScope, mutation_id: str, permission: Permission
    ) -> MutationSet:
        mutation = await self._journal.get_mutation(
            workspace_id=scope.workspace_id or "",
            project_id=scope.project_id or "",
            mutation_id=mutation_id,
        )
        if mutation is None:
            raise MutationNotFound(f"mutation {mutation_id!r} not found")
        await self._authorize(scope, mutation.workspace_id, mutation.project_id, permission)
        return mutation

    async def list_mutations(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MutationSet]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._journal.list_mutations(
            workspace_id=workspace_id,
            project_id=project_id,
            status=status,
            limit=limit,
            offset=offset,
        )

    async def _target_before(self, mutation: MutationSet, operation: MutationOperation) -> dict:
        record_type = _TARGET_RECORD[operation.operation_type]
        target_id = str(operation.payload.get("id", ""))
        scope = {"workspace_id": mutation.workspace_id, "project_id": mutation.project_id}
        record: object | None
        if record_type == "entity":
            record = await self._repository.get_entity(**scope, entity_id=target_id)
        elif record_type == "fact":
            record = await self._repository.get_fact(**scope, fact_id=target_id)
        elif record_type == "belief":
            record = await self._repository.get_belief(**scope, belief_id=target_id)
        elif record_type == "event":
            record = await self._repository.get_event(**scope, event_id=target_id)
        else:
            record = await self._repository.get_link(**scope, link_id=target_id)
        if record is None:
            return {}
        return record.model_dump(mode="json")  # type: ignore[attr-defined,no-any-return]

    async def review(self, scope: AccessScope, mutation_id: str) -> MutationReview:
        mutation = await self._require(scope, mutation_id, Permission.PROJECT_READ)
        operations = await self._journal.list_operations(mutation_id=mutation_id)
        operation_decisions = await self._journal.list_operation_decisions(mutation_id=mutation_id)
        accepted = effective_operations(operations, operation_decisions)
        targets_before: dict[int, dict | None] = {}
        for operation in operations:
            if operation.operation_type in _TARGET_RECORD:
                targets_before[operation.ordinal] = await self._target_before(mutation, operation)
        commit_id: str | None = None
        committed_revision: int | None = None
        if mutation.status is MutationStatus.COMMITTED:
            envelope = await self._repository.get_envelope(_deterministic_commit_id(mutation_id))
            if envelope is not None:
                commit_id, committed_revision = envelope.commit_id, envelope.revision
        return MutationReview(
            mutation=mutation,
            operations=operations,
            operation_decisions=latest_operation_decisions(operation_decisions, operations),
            decisions=await self._journal.list_decisions(mutation_id=mutation_id),
            transitions=await self._journal.list_transitions(mutation_id=mutation_id),
            classification=classify_review(
                origin=mutation.origin,
                intent=mutation.intent,
                operation_types=[item.operation_type for item in accepted],
            ),
            head_revision=await self._repository.get_head(
                workspace_id=mutation.workspace_id,
                project_id=mutation.project_id,
                branch_id=mutation.branch_id,
            ),
            targets_before=targets_before,
            commit_id=commit_id,
            committed_revision=committed_revision,
        )

    async def validate(self, scope: AccessScope, mutation_id: str) -> ValidationReport:
        """Non-transactional preview. The commit revalidates authoritatively in-tx."""
        mutation = await self._require(scope, mutation_id, Permission.PROJECT_WRITE)
        accepted = await self._effective(mutation_id)
        ordinals = [item.ordinal for item in accepted]
        operations = [parse_operation(item.payload) for item in accepted]
        issues: list[ValidationIssue] = []
        head = await self._repository.get_head(
            workspace_id=mutation.workspace_id,
            project_id=mutation.project_id,
            branch_id=mutation.branch_id,
        )
        if mutation.status is not MutationStatus.COMMITTED and head != mutation.expected_head:
            issues.append(
                ValidationIssue(
                    -1,
                    "stale_head",
                    f"proposal targets revision {mutation.expected_head} but canon is at {head}",
                )
            )
        if not operations:
            issues.append(ValidationIssue(-1, "empty_mutation", "no accepted operations remain"))
        reader = _RepositoryReader(
            self._repository, mutation.workspace_id, mutation.project_id, self._media
        )
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
                        *issues,
                        ValidationIssue(
                            -1,
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
        issues.extend(
            ValidationIssue(
                ordinals[f.operation_ordinal] if f.operation_ordinal is not None else -1,
                f.code,
                f.message,
                "advisory" if f.severity is ValidationSeverity.ADVISORY else "blocking",
            )
            for f in report.findings
        )
        return ValidationReport(issues=tuple(issues))

    async def submit(
        self, scope: AccessScope, mutation_id: str, reason: str | None = None
    ) -> MutationSet:
        """Send a draft to the review queue."""
        mutation = await self._require(scope, mutation_id, Permission.PROJECT_WRITE)
        if mutation.status is MutationStatus.NEEDS_REVIEW:
            return mutation
        return await self._journal.transition_mutation(
            mutation_id=mutation_id,
            to_status=MutationStatus.NEEDS_REVIEW,
            actor_user_id=scope.actor_user_id,
            reason=reason or "submitted for review",
            correlation_id=scope.correlation_id,
        )

    async def revise_operation(
        self,
        scope: AccessScope,
        mutation_id: str,
        ordinal: int,
        operation: CanonOperation,
        reason: str | None = None,
    ) -> MutationOperation:
        """Edit one proposed operation as a new revision; prior review evidence is kept."""
        await self._require(scope, mutation_id, Permission.PROJECT_WRITE)
        return await self._journal.revise_operation(
            mutation_id=mutation_id,
            ordinal=ordinal,
            operation_type=operation.op,
            payload=operation.model_dump(mode="json"),
            actor_user_id=scope.actor_user_id,
            reason=reason or "edited during review",
        )

    async def decide_operation(
        self,
        scope: AccessScope,
        mutation_id: str,
        ordinal: int,
        decision: str,
        note: str | None = None,
    ) -> OperationDecision:
        await self._require(scope, mutation_id, Permission.PROJECT_WRITE)
        kind = OperationDecisionKind(decision)
        return await self._journal.record_operation_decision(
            mutation_id=mutation_id,
            ordinal=ordinal,
            decision=kind.value,
            actor_user_id=scope.actor_user_id,
            note=note,
        )

    async def approve(self, scope: AccessScope, mutation_id: str, note: str | None = None) -> None:
        mutation = await self._require(scope, mutation_id, Permission.PROJECT_WRITE)
        if not is_editable(mutation.status):
            raise RevisionConflict(
                f"mutation in state {mutation.status.value!r} cannot be approved"
            )
        if not await self._effective(mutation_id):
            raise CanonValidationError(
                DomainValidationReport(
                    findings=(
                        ValidationFinding(
                            ValidationSeverity.INTEGRITY,
                            "empty_mutation",
                            "no accepted operations remain to approve",
                        ),
                    )
                )
            )
        await self._journal.record_decision(
            mutation_id=mutation_id,
            decision="approved",
            actor_user_id=scope.actor_user_id,
            note=note,
            transition_to=MutationStatus.APPROVED,
            correlation_id=scope.correlation_id,
        )

    async def reject(self, scope: AccessScope, mutation_id: str, note: str | None = None) -> None:
        mutation = await self._require(scope, mutation_id, Permission.PROJECT_WRITE)
        if not is_editable(mutation.status):
            raise RevisionConflict(
                f"mutation in state {mutation.status.value!r} cannot be rejected"
            )
        await self._journal.record_decision(
            mutation_id=mutation_id,
            decision="rejected",
            actor_user_id=scope.actor_user_id,
            note=note,
            transition_to=MutationStatus.REJECTED,
            correlation_id=scope.correlation_id,
        )

    async def retarget(
        self, scope: AccessScope, mutation_id: str, expected_head: int
    ) -> MutationSet:
        """Recover a stale proposal by pointing it at the current head (re-review required)."""
        await self._require(scope, mutation_id, Permission.PROJECT_WRITE)
        return await self._journal.retarget_mutation(
            mutation_id=mutation_id,
            expected_head=expected_head,
            actor_user_id=scope.actor_user_id,
            reason=f"retargeted to revision {expected_head}",
        )

    async def commit_author_change(
        self, scope: AccessScope, mutation_id: str, *, acknowledge_high_impact: bool = False
    ) -> CommitResult:
        """Commit an author-originated change made through a dedicated product action.

        A high-impact change (destructive, bulk, or a revert) commits only when the
        author explicitly acknowledged it; that acknowledgement is recorded as the
        approval decision. Without it the proposal is parked in the review queue.
        Protected origins can never be self-acknowledged through this path.
        """
        mutation = await self._require(scope, mutation_id, Permission.PROJECT_WRITE)
        if (
            acknowledge_high_impact
            and mutation.origin == OriginKind.AUTHOR.value
            and mutation.status in {MutationStatus.DRAFT, MutationStatus.NEEDS_REVIEW}
        ):
            classification = classify_review(
                origin=mutation.origin,
                intent=mutation.intent,
                operation_types=[
                    item.operation_type for item in await self._effective(mutation_id)
                ],
            )
            if classification.requires_review:
                names = ", ".join(reason.value for reason in classification.reasons)
                await self.approve(
                    scope, mutation_id, note=f"author acknowledged high-impact change ({names})"
                )
        return await self.commit(scope, mutation_id)

    async def commit(self, scope: AccessScope, mutation_id: str) -> CommitResult:
        mutation = await self._require(scope, mutation_id, Permission.PROJECT_WRITE)
        accepted = await self._effective(mutation_id)

        if mutation.status is not MutationStatus.COMMITTED:
            gate = review_gate(
                origin=mutation.origin,
                intent=mutation.intent,
                status=mutation.status,
                operation_types=[item.operation_type for item in accepted],
            )
            if gate.code == "review_required":
                if mutation.status is MutationStatus.DRAFT:
                    await self._journal.transition_mutation(
                        mutation_id=mutation_id,
                        to_status=MutationStatus.NEEDS_REVIEW,
                        actor_user_id=scope.actor_user_id,
                        reason=gate.message,
                        correlation_id=scope.correlation_id,
                    )
                raise ReviewRequiredError(
                    mutation_id, tuple(reason.value for reason in gate.reasons), gate.message
                )
            if not gate.ok:
                raise CanonValidationError(
                    DomainValidationReport(
                        findings=(
                            ValidationFinding(
                                ValidationSeverity.INTEGRITY, gate.code or "", gate.message
                            ),
                        )
                    )
                )

        operations = [parse_operation(item.payload) for item in accepted]
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
        # ONE PostgreSQL transaction: lock head, evaluate the review gate, run the
        # authoritative validator, reserve/advance the head, append the immutable
        # envelope, apply operations to normalized canon, record the revision and
        # state transitions, and enqueue the outbox.
        started = time.perf_counter()
        try:
            revision = await self._repository.apply_commit(
                envelope=envelope,
                operations=resolved,
                validator=self._validator,
                origin=mutation.origin,
            )
        except Exception as error:
            log_event(
                logger,
                "canon.commit.failed",
                level=logging.WARNING,
                mutation_id=mutation_id,
                origin=mutation.origin,
                error_type=type(error).__name__,
                duration_ms=round((time.perf_counter() - started) * 1000, 3),
            )
            raise
        log_event(
            logger,
            "canon.commit",
            mutation_id=mutation_id,
            revision=revision,
            operations=len(resolved),
            origin=mutation.origin,
            duration_ms=round((time.perf_counter() - started) * 1000, 3),
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
    "MutationReview",
    "MutationService",
    "ReconcileResult",
    "ReviewRequiredError",
    "ValidationIssue",
    "ValidationReport",
]
