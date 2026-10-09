"""Canon revision history, per-record history, tags, and revision inspection (2H).

All reads go to canonical PostgreSQL (ADR-012). These are inspection views over
committed history; they never mutate canon and never expose secret payloads.
"""

from __future__ import annotations

import json

from backend.domain.canon.assertions import Belief, Fact, FactStatus
from backend.domain.canon.entities import CanonEntity, EntityLifecycle
from backend.domain.canon.events import Event, EventParticipant
from backend.domain.canon.propositions import Proposition
from backend.domain.canon.revisions import CanonRevision
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.history import (
    CanonTag,
    CanonTagHistoryEntry,
    HistoryError,
    RecordHistoryEntry,
    RevertChange,
    RevertProposal,
    RevisionSummary,
)
from backend.domain.identity import AccessScope
from backend.domain.journal import Branch
from backend.domain.operations import parse_operation
from backend.domain.projection import RecordVersion
from backend.domain.review import MutationIntent
from backend.domain.tenancy import Permission
from backend.ports import CanonJournalPort, CanonRepositoryPort, TenancyStorePort
from backend.services.mutation_service import CommitResult, MutationService

_READ_RECORD_TYPES = {"entity", "fact", "belief", "event"}

# Compensating operations for the invertible commit operations (2H revert proposals).
# Operations without a safe automatic inverse are surfaced for manual handling.
_INVERSE_OPERATIONS = {
    "create_entity": "retire_entity",
    "assert_fact": "close_fact",
    "assert_belief": "close_belief",
    "create_event": "retire_event",
    "link_asset": "retire_asset_link",
}


class CanonHistoryService:
    def __init__(
        self,
        *,
        canon: CanonRepositoryPort,
        journal: CanonJournalPort,
        tenancy: object | None = None,
        local_mode: bool = False,
        mutation: MutationService | None = None,
    ) -> None:
        self._canon = canon
        self._journal = journal
        self._tenancy = tenancy
        self._local_mode = local_mode
        self._mutation = mutation

    async def _root_branch(self, workspace_id: str, project_id: str) -> Branch:
        return await self._journal.ensure_root_branch(
            workspace_id=workspace_id, project_id=project_id
        )

    async def _authorize(self, scope: AccessScope, workspace_id: str, project_id: str) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(  # type: ignore[attr-defined]
            scope, workspace_id, project_id, Permission.PROJECT_READ
        )

    async def _authorize_write(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(  # type: ignore[attr-defined]
            scope, workspace_id, project_id, Permission.PROJECT_WRITE
        )

    async def list_revisions(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RevisionSummary]:
        await self._authorize(scope, workspace_id, project_id)
        branch = await self._root_branch(workspace_id, project_id)
        commits = await self._canon.list_commits(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch.id,
            after_revision=0,
            limit=limit + offset,
        )
        commits = sorted(commits, key=lambda c: c.revision, reverse=True)[offset : offset + limit]
        summaries: list[RevisionSummary] = []
        for commit in commits:
            summaries.append(await self._summary(commit))
        return summaries

    async def get_revision(
        self, scope: AccessScope, workspace_id: str, project_id: str, commit_id: str
    ) -> RevisionSummary:
        await self._authorize(scope, workspace_id, project_id)
        envelope = await self._canon.get_envelope(commit_id)
        if envelope is None:
            raise NotFoundError("Revision not found.")
        branch = await self._root_branch(workspace_id, project_id)
        commits = await self._canon.list_commits(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch.id,
            after_revision=0,
            limit=1000,
        )
        for commit in commits:
            if commit.commit_id == commit_id:
                return await self._summary(commit)
        raise NotFoundError("Revision not found.")

    async def _summary(self, commit: CanonRevision) -> RevisionSummary:
        envelope = await self._canon.get_envelope(commit.commit_id)
        operation_count = 0
        if envelope is not None and envelope.canonical:
            operation_count = len(json.loads(envelope.canonical))
        return RevisionSummary(
            revision=commit.revision,
            commit_id=commit.commit_id,
            expected_head=commit.expected_head,
            result_head=commit.result_head,
            schema_version_id=commit.schema_version_id,
            envelope_checksum=commit.envelope_checksum,
            operation_count=operation_count,
            created_at=commit.created_at,
        )

    async def record_history(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        record_type: str,
        record_id: str,
        limit: int = 100,
    ) -> list[RecordHistoryEntry]:
        await self._authorize(scope, workspace_id, project_id)
        if record_type not in _READ_RECORD_TYPES:
            raise ValidationError(f"Unknown record type {record_type!r}.")
        versions = await self._canon.list_record_versions(
            workspace_id=workspace_id,
            project_id=project_id,
            record_type=record_type,
            record_id=record_id,
            limit=limit,
        )
        return [
            RecordHistoryEntry(
                record_type=v.record_type,
                record_id=v.record_id,
                branch_id=v.branch_id,
                revision=v.revision,
                operation_type=v.operation_type,
                commit_id=v.commit_id,
                change=v.change,
                created_at=v.created_at,
            )
            for v in versions
        ]

    async def entity_at_revision(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        entity_id: str,
        revision: int,
    ) -> CanonEntity:
        await self._authorize(scope, workspace_id, project_id)
        versions = await self._canon.list_record_versions(
            workspace_id=workspace_id,
            project_id=project_id,
            record_type="entity",
            record_id=entity_id,
            limit=1000,
        )
        ordered = sorted((v for v in versions if v.revision <= revision), key=lambda v: v.revision)
        if not ordered:
            raise NotFoundError("Entity did not exist at that revision.")
        entity: CanonEntity | None = None
        for version in ordered:
            entity = _replay_entity(version, entity)
        if entity is None:
            raise NotFoundError("Entity did not exist at that revision.")
        return entity

    async def diff_revisions(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        from_revision: int,
        to_revision: int,
        limit: int = 1000,
    ) -> list[RecordHistoryEntry]:
        await self._authorize(scope, workspace_id, project_id)
        if from_revision < 0 or to_revision < from_revision:
            raise ValidationError("Invalid revision range.")
        versions = await self._canon.list_record_versions(
            workspace_id=workspace_id,
            project_id=project_id,
            min_revision=from_revision,
            max_revision=to_revision,
            limit=limit,
        )
        versions = sorted(versions, key=lambda v: (v.revision, v.record_type, v.record_id))
        return [
            RecordHistoryEntry(
                record_type=v.record_type,
                record_id=v.record_id,
                branch_id=v.branch_id,
                revision=v.revision,
                operation_type=v.operation_type,
                commit_id=v.commit_id,
                change=v.change,
                created_at=v.created_at,
            )
            for v in versions
        ]

    async def fact_at_revision(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        fact_id: str,
        revision: int,
    ) -> Fact:
        await self._authorize(scope, workspace_id, project_id)
        versions = await self._canon.list_record_versions(
            workspace_id=workspace_id,
            project_id=project_id,
            record_type="fact",
            record_id=fact_id,
            max_revision=revision,
            limit=1000,
        )
        ordered = sorted(versions, key=lambda v: v.revision)
        fact: Fact | None = None
        for version in ordered:
            fact = _replay_fact(version, fact)
        if fact is None:
            raise NotFoundError("Fact did not exist at that revision.")
        return fact

    async def belief_at_revision(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        belief_id: str,
        revision: int,
    ) -> Belief:
        await self._authorize(scope, workspace_id, project_id)
        versions = await self._canon.list_record_versions(
            workspace_id=workspace_id,
            project_id=project_id,
            record_type="belief",
            record_id=belief_id,
            max_revision=revision,
            limit=1000,
        )
        ordered = sorted(versions, key=lambda v: v.revision)
        belief: Belief | None = None
        for version in ordered:
            belief = _replay_belief(version, belief)
        if belief is None:
            raise NotFoundError("Belief did not exist at that revision.")
        return belief

    async def event_at_revision(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        event_id: str,
        revision: int,
    ) -> Event:
        await self._authorize(scope, workspace_id, project_id)
        versions = await self._canon.list_record_versions(
            workspace_id=workspace_id,
            project_id=project_id,
            record_type="event",
            record_id=event_id,
            max_revision=revision,
            limit=1000,
        )
        ordered = sorted(versions, key=lambda v: v.revision)
        event: Event | None = None
        for version in ordered:
            event = _replay_event(version, event)
        if event is None:
            raise NotFoundError("Event did not exist at that revision.")
        return event

    async def propose_revert(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        from_revision: int,
        to_revision: int | None = None,
    ) -> RevertProposal:
        """Preview a compensating mutation set for the range ``(from_revision, to_revision]``.

        Never mutates or rewrites committed history. Changes with no safe automatic
        inverse are listed for manual handling, and any record modified after
        ``to_revision`` is reported as an intervening conflict rather than reverted.
        """
        await self._authorize(scope, workspace_id, project_id)
        branch = await self._root_branch(workspace_id, project_id)
        head = await self._canon.get_head(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
        )
        if to_revision is None:
            to_revision = head
        if from_revision < 0 or from_revision >= to_revision or to_revision > head:
            raise ValidationError("invalid revert revision range")

        entries = await self._canon.list_record_versions(
            workspace_id=workspace_id,
            project_id=project_id,
            min_revision=from_revision,
            max_revision=to_revision,
            limit=1000,
        )
        later = await self._canon.list_record_versions(
            workspace_id=workspace_id,
            project_id=project_id,
            min_revision=to_revision,
            limit=1000,
        )
        later_keys = {(version.record_type, version.record_id) for version in later}

        operations: list[dict] = []
        changes: list[RevertChange] = []
        conflicts: list[str] = []
        ordered = sorted(
            entries, key=lambda v: (v.revision, v.record_type, v.record_id), reverse=True
        )
        for version in ordered:
            inverse = _INVERSE_OPERATIONS.get(version.operation_type)
            if inverse is None:
                changes.append(
                    RevertChange(
                        revision=version.revision,
                        record_type=version.record_type,
                        record_id=version.record_id,
                        operation_type=version.operation_type,
                        reason="no automatic inverse",
                    )
                )
                continue
            if (version.record_type, version.record_id) in later_keys:
                conflicts.append(
                    f"{version.record_type} {version.record_id} changed after r{to_revision}"
                )
                continue
            operations.append({"op": inverse, "id": version.record_id})
            changes.append(
                RevertChange(
                    revision=version.revision,
                    record_type=version.record_type,
                    record_id=version.record_id,
                    operation_type=version.operation_type,
                    inverse_operation=inverse,
                    reason="compensating",
                )
            )

        return RevertProposal(
            from_revision=from_revision,
            to_revision=to_revision,
            head_revision=head,
            operations=operations,
            changes=changes,
            conflicts=conflicts,
            summary=(
                f"revert r{from_revision + 1}..r{to_revision}: {len(operations)} compensating "
                f"operation(s), {len(changes) - len(operations)} manual, {len(conflicts)} conflict(s)"
            ),
        )

    async def commit_revert(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        from_revision: int,
        expected_head: int,
        to_revision: int | None = None,
        acknowledge_high_impact: bool = False,
    ) -> CommitResult:
        """Apply a proposed revert as a new reviewed commit through the mutation path.

        A revert is always high-impact: it commits only with the author's explicit
        acknowledgement, otherwise it is parked in the review queue.
        """
        if self._mutation is None:
            raise HistoryError("revert commit is not configured")
        proposal = await self.propose_revert(
            scope, workspace_id, project_id, from_revision=from_revision, to_revision=to_revision
        )
        if proposal.conflicts:
            raise ValidationError(
                "revert blocked by intervening conflicts: " + "; ".join(proposal.conflicts)
            )
        if not proposal.operations:
            raise ValidationError("nothing to revert")
        await self._authorize_write(scope, workspace_id, project_id)
        operations = [parse_operation(op) for op in proposal.operations]
        mutation = await self._mutation.create_draft(
            scope,
            workspace_id=workspace_id,
            project_id=project_id,
            operations=operations,
            expected_head=expected_head,
            intent=MutationIntent.REVERT.value,
        )
        return await self._mutation.commit_author_change(
            scope, mutation.id, acknowledge_high_impact=acknowledge_high_impact
        )

    async def list_tags(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> list[CanonTag]:
        await self._authorize(scope, workspace_id, project_id)
        branch = await self._root_branch(workspace_id, project_id)
        return await self._canon.list_tags(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
        )

    async def tag_history(
        self, scope: AccessScope, workspace_id: str, project_id: str, name: str
    ) -> list[CanonTagHistoryEntry]:
        await self._authorize(scope, workspace_id, project_id)
        branch = await self._root_branch(workspace_id, project_id)
        return await self._canon.list_tag_history(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id, name=name
        )

    async def set_tag(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        name: str,
        revision: int | None = None,
    ) -> CanonTag:
        await self._authorize_write(scope, workspace_id, project_id)
        if not name or len(name) > 100:
            raise ValidationError("Tag name must be 1-100 characters.")
        branch = await self._root_branch(workspace_id, project_id)
        if revision is None:
            revision = await self._canon.get_head(
                workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
            )
        commits = await self._canon.list_commits(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch.id,
            after_revision=0,
            limit=1000,
        )
        match = next((c for c in commits if c.revision == revision), None)
        if match is None:
            raise NotFoundError("No committed revision with that number.")
        return await self._canon.create_tag(
            CanonTag(
                workspace_id=workspace_id,
                project_id=project_id,
                branch_id=branch.id,
                name=name,
                revision=match.revision,
                commit_id=match.commit_id,
                created_by_user_id=scope.actor_user_id,
                created_at=match.created_at,
                updated_at=match.created_at,
            )
        )

    async def delete_tag(
        self, scope: AccessScope, workspace_id: str, project_id: str, name: str
    ) -> bool:
        await self._authorize_write(scope, workspace_id, project_id)
        branch = await self._root_branch(workspace_id, project_id)
        return await self._canon.delete_tag(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id, name=name
        )


def _replay_entity(version: RecordVersion, current: CanonEntity | None) -> CanonEntity | None:
    change = version.change
    op = change.get("op")
    if op == "create_entity":
        return CanonEntity(
            id=change["id"],
            workspace_id=version.workspace_id,
            project_id=version.project_id,
            branch_id=version.branch_id,
            type=change["type"],
            name=change["name"],
            slug=change.get("slug"),
            lifecycle=EntityLifecycle.ACTIVE,
            schema_version_id=change.get("schema_version_id") or "",
            created_revision=version.revision,
            attributes=change.get("attributes") or {},
        )
    if current is None:
        return None
    if op == "update_entity":
        return current.model_copy(
            update={
                "name": current.name if change.get("name") is None else change["name"],
                "slug": current.slug if change.get("slug") is None else change["slug"],
                "attributes": current.attributes
                if change.get("attributes") is None
                else change["attributes"],
            }
        )
    if op == "retire_entity":
        return current.model_copy(
            update={"lifecycle": EntityLifecycle.RETIRED, "retired_revision": version.revision}
        )
    return current


def _replay_fact(version: RecordVersion, current: Fact | None) -> Fact | None:
    change = version.change
    op = change.get("op")
    if op == "assert_fact":
        return Fact(
            id=change["id"],
            workspace_id=version.workspace_id,
            project_id=version.project_id,
            branch_id=version.branch_id,
            proposition=Proposition.model_validate(change["proposition"]),
            status=FactStatus.ASSERTED,
            confidence=change.get("confidence", 1.0),
            provenance_id=f"prov_{version.commit_id}",
            created_revision=version.revision,
            valid_from=change.get("valid_from"),
            valid_until=change.get("valid_until"),
        )
    if current is None:
        return None
    if op == "close_fact":
        return current.model_copy(
            update={"status": FactStatus.CLOSED, "closed_revision": version.revision}
        )
    return current


def _replay_belief(version: RecordVersion, current: Belief | None) -> Belief | None:
    change = version.change
    op = change.get("op")
    if op == "assert_belief":
        return Belief(
            id=change["id"],
            workspace_id=version.workspace_id,
            project_id=version.project_id,
            branch_id=version.branch_id,
            holder_entity_id=change["holder_entity_id"],
            proposition=Proposition.model_validate(change["proposition"]),
            confidence=change.get("confidence", 1.0),
            acquisition_provenance_id=f"prov_{version.commit_id}",
            source_ref=change.get("source_ref"),
            created_revision=version.revision,
        )
    if current is None:
        return None
    if op == "close_belief":
        return current.model_copy(update={"closed_revision": version.revision})
    return current


def _replay_event(version: RecordVersion, current: Event | None) -> Event | None:
    change = version.change
    op = change.get("op")
    if op == "create_event":
        return Event(
            id=change["id"],
            workspace_id=version.workspace_id,
            project_id=version.project_id,
            branch_id=version.branch_id,
            kind=change["kind"],
            name=change["name"],
            participants=[
                EventParticipant.model_validate(p) for p in change.get("participants", [])
            ],
            location_ids=list(change.get("location_ids", [])),
            cause_ids=list(change.get("cause_ids", [])),
            outcome_ids=list(change.get("outcome_ids", [])),
            world_position=change.get("world_position"),
            provenance_id=f"prov_{version.commit_id}",
            created_revision=version.revision,
        )
    if current is None:
        return None
    if op == "amend_event":
        update: dict[str, object] = {}
        if change.get("name") is not None:
            update["name"] = change["name"]
        if change.get("world_position") is not None:
            update["world_position"] = change["world_position"]
        return current.model_copy(update=update)
    if op == "retire_event":
        return current.model_copy(update={"retired_revision": version.revision})
    return current


# Re-exported for callers that only need the error type.
__all__ = ["CanonHistoryService", "HistoryError", "TenancyStorePort"]
