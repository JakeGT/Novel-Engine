"""Canon revision history, per-record history, tags, and revision inspection (2H).

All reads go to canonical PostgreSQL (ADR-012). These are inspection views over
committed history; they never mutate canon and never expose secret payloads.
"""

from __future__ import annotations

import json
from typing import Any

from backend.domain.canon.assertions import Belief, Fact, FactStatus, FactVisibility
from backend.domain.canon.entities import CanonEntity, EntityLifecycle
from backend.domain.canon.events import Event, EventClaim, EventParticipant
from backend.domain.canon.plans import PlanItem, apply_plan_update, plan_item_from_create
from backend.domain.canon.propositions import Proposition
from backend.domain.canon.revisions import CanonRevision
from backend.domain.errors import ForbiddenError, NotFoundError, ValidationError
from backend.domain.history import (
    CanonTag,
    CanonTagHistoryEntry,
    HistoryError,
    ProvenanceDecision,
    ProvenanceEntry,
    ProvenanceEvidence,
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
from backend.domain.worldtime import Unknown, parse_world_time
from backend.ports import CanonJournalPort, CanonRepositoryPort, TenancyStorePort
from backend.services.mutation_service import CommitResult, MutationService

_READ_RECORD_TYPES = {"entity", "fact", "belief", "event", "asset_link", "plan_item"}
_MAX_HISTORY = 10_000

# Operations that bring a record into existence, with the operation that removes it.
_CREATION_INVERSE = {
    "create_entity": "retire_entity",
    "assert_fact": "close_fact",
    "assert_belief": "close_belief",
    "create_event": "retire_event",
    "link_asset": "retire_asset_link",
    "create_plan_item": "retire_plan_item",
}
# Retirement has no "un-retire" operation: identity records must be restored by hand.
_NO_INVERSE_REASON = {
    "retire_entity": "a retired entity cannot be reactivated automatically",
    "retire_event": "a retired event cannot be reactivated automatically",
    "retire_plan_item": "a removed plan item cannot be put back automatically",
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

    async def _working_branch(self, workspace_id: str, project_id: str) -> Branch:
        return await self._journal.working_branch(workspace_id=workspace_id, project_id=project_id)

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

    async def _permits(
        self, scope: AccessScope, workspace_id: str, project_id: str, permission: Permission
    ) -> bool:
        if self._local_mode or self._tenancy is None:
            return True
        try:
            await self._tenancy.require_project(  # type: ignore[attr-defined]
                scope, workspace_id, project_id, permission
            )
        except ForbiddenError:
            return False
        return True

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
        branch = await self._working_branch(workspace_id, project_id)
        # Newest first. Revisions on a branch are contiguous from 1 to the head, so
        # the page ``offset`` back from the head is a known window; reading from
        # revision 1 instead would return the oldest commits once a project has
        # more revisions than one page.
        head = await self._canon.get_head(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
        )
        newest = head - offset
        oldest = max(newest - limit, 0)
        commits = (
            await self._canon.list_commits(
                workspace_id=workspace_id,
                project_id=project_id,
                branch_id=branch.id,
                after_revision=oldest,
                limit=newest - oldest,
            )
            if newest > oldest
            else []
        )
        commits = sorted(commits, key=lambda c: c.revision, reverse=True)
        show_actors = await self._permits(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        summaries: list[RevisionSummary] = []
        for commit in commits:
            summaries.append(await self._summary(commit, show_actors=show_actors))
        return summaries

    async def get_revision(
        self, scope: AccessScope, workspace_id: str, project_id: str, commit_id: str
    ) -> RevisionSummary:
        await self._authorize(scope, workspace_id, project_id)
        envelope = await self._canon.get_envelope(commit_id)
        if envelope is None:
            raise NotFoundError("Revision not found.")
        branch = await self._working_branch(workspace_id, project_id)
        commits = await self._canon.list_commits(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch.id,
            after_revision=0,
            limit=1000,
        )
        for commit in commits:
            if commit.commit_id == commit_id:
                return await self._summary(
                    commit,
                    show_actors=await self._permits(
                        scope, workspace_id, project_id, Permission.PROJECT_WRITE
                    ),
                )
        raise NotFoundError("Revision not found.")

    async def _summary(self, commit: CanonRevision, *, show_actors: bool) -> RevisionSummary:
        envelope = await self._canon.get_envelope(commit.commit_id)
        operation_count = 0
        if envelope is not None and envelope.canonical:
            operation_count = len(json.loads(envelope.canonical))
        context = await self._commit_context(
            commit.workspace_id, commit.project_id, commit.branch_id, commit.commit_id
        )
        evidence = await self._canon.list_evidence(
            workspace_id=commit.workspace_id,
            project_id=commit.project_id,
            commit_id=commit.commit_id,
        )
        return RevisionSummary(
            revision=commit.revision,
            commit_id=commit.commit_id,
            expected_head=commit.expected_head,
            result_head=commit.result_head,
            schema_version_id=commit.schema_version_id,
            envelope_checksum=commit.envelope_checksum,
            operation_count=operation_count,
            created_at=commit.created_at,
            origin=context["origin"],
            intent=context["intent"],
            mutation_id=context["mutation_id"],
            actor_user_id=context["actor_user_id"] if show_actors else None,
            decision_count=len(context["decisions"]),
            evidence_count=len(evidence),
        )

    async def _commit_context(
        self, workspace_id: str, project_id: str, branch_id: str, commit_id: str
    ) -> dict:
        """Origin, actor, and review decisions behind one commit."""
        envelope = await self._canon.get_envelope(commit_id)
        provenance = await self._canon.get_provenance(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch_id,
            provenance_id=f"prov_{commit_id}",
        )
        mutation = (
            await self._journal.get_mutation(
                workspace_id=workspace_id,
                project_id=project_id,
                mutation_id=envelope.mutation_id,
            )
            if envelope is not None
            else None
        )
        decisions = await self._journal.list_decisions(mutation_id=mutation.id) if mutation else []
        return {
            "origin": (
                mutation.origin
                if mutation is not None
                else provenance.origin.value
                if provenance is not None
                else None
            ),
            "intent": mutation.intent if mutation is not None else None,
            "mutation_id": mutation.id if mutation is not None else None,
            "actor_user_id": envelope.actor_user_id if envelope is not None else None,
            "schema_version_id": envelope.schema_version_id if envelope is not None else None,
            "decisions": decisions,
        }

    async def record_provenance(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        record_type: str,
        record_id: str,
    ) -> list[ProvenanceEntry]:
        """Explain every change to a record: commit, origin, review decisions, evidence.

        Any project reader may see why canon is the way it is. Who decided, and
        their review notes, are shown only to members who can write to the project.
        """
        await self._authorize(scope, workspace_id, project_id)
        if record_type not in _READ_RECORD_TYPES:
            raise ValidationError(f"Unknown record type {record_type!r}.")
        versions = await self._canon.list_record_versions(
            workspace_id=workspace_id,
            project_id=project_id,
            record_type=record_type,
            record_id=record_id,
            limit=_MAX_HISTORY,
        )
        if not versions:
            raise NotFoundError("Record not found.")
        show_actors = await self._permits(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        evidence = await self._canon.list_evidence(
            workspace_id=workspace_id,
            project_id=project_id,
            record_type=record_type,
            record_id=record_id,
        )
        contexts: dict[str, dict] = {}
        entries: list[ProvenanceEntry] = []
        for version in sorted(versions, key=lambda v: (v.revision, v.ordinal)):
            if version.commit_id not in contexts:
                contexts[version.commit_id] = await self._commit_context(
                    workspace_id, project_id, version.branch_id, version.commit_id
                )
            context = contexts[version.commit_id]
            entries.append(
                ProvenanceEntry(
                    record_type=version.record_type,
                    record_id=version.record_id,
                    revision=version.revision,
                    commit_id=version.commit_id,
                    operation_type=version.operation_type,
                    origin=context["origin"],
                    intent=context["intent"],
                    schema_version_id=context["schema_version_id"],
                    actor_user_id=context["actor_user_id"] if show_actors else None,
                    decisions=[
                        ProvenanceDecision(
                            decision=decision.decision,
                            actor_user_id=decision.actor_user_id if show_actors else None,
                            note=decision.note if show_actors else None,
                            created_at=decision.created_at,
                        )
                        for decision in context["decisions"]
                    ],
                    evidence=[
                        ProvenanceEvidence(
                            source_artefact_id=item.source_artefact_id, location=item.location
                        )
                        for item in evidence
                        if item.commit_id == version.commit_id and item.ordinal == version.ordinal
                    ],
                    redacted=not show_actors,
                    created_at=version.created_at,
                )
            )
        return entries

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
        # Newest change first, in a stable order within a commit.
        versions = sorted(versions, key=lambda v: (v.revision, v.ordinal), reverse=True)
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
            max_revision=revision,
            limit=_MAX_HISTORY,
        )
        ordered = sorted(versions, key=lambda v: (v.revision, v.ordinal))
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
        versions = sorted(versions, key=lambda v: (v.revision, v.ordinal))
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
            limit=_MAX_HISTORY,
        )
        ordered = sorted(versions, key=lambda v: (v.revision, v.ordinal))
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
            limit=_MAX_HISTORY,
        )
        ordered = sorted(versions, key=lambda v: (v.revision, v.ordinal))
        belief: Belief | None = None
        for version in ordered:
            belief = _replay_belief(version, belief)
        if belief is None:
            raise NotFoundError("Belief did not exist at that revision.")
        return belief

    async def plan_item_at_revision(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        revision: int,
    ) -> PlanItem:
        await self._authorize(scope, workspace_id, project_id)
        versions = await self._canon.list_record_versions(
            workspace_id=workspace_id,
            project_id=project_id,
            record_type="plan_item",
            record_id=plan_id,
            max_revision=revision,
            limit=_MAX_HISTORY,
        )
        item: PlanItem | None = None
        for version in sorted(versions, key=lambda v: (v.revision, v.ordinal)):
            item = _replay_plan_item(version, item)
        if item is None:
            raise NotFoundError("That part of the plan did not exist at that version.")
        return item

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
            limit=_MAX_HISTORY,
        )
        ordered = sorted(versions, key=lambda v: (v.revision, v.ordinal))
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
        branch = await self._working_branch(workspace_id, project_id)
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
            limit=_MAX_HISTORY,
        )
        later = await self._canon.list_record_versions(
            workspace_id=workspace_id,
            project_id=project_id,
            min_revision=to_revision,
            limit=_MAX_HISTORY,
        )
        later_keys = {(version.record_type, version.record_id) for version in later}

        operations: list[dict] = []
        changes: list[RevertChange] = []
        conflicts: list[str] = []
        # Newest change first, so later edits are undone before the ones they built on.
        ordered = sorted(entries, key=lambda v: (v.revision, v.ordinal), reverse=True)
        created_in_range = {
            (v.record_type, v.record_id) for v in ordered if v.operation_type in _CREATION_INVERSE
        }
        # Records this revert removes; references among them are not conflicts.
        removed: set[tuple[str, str]] = set()
        handled_creations: set[tuple[str, str]] = set()
        reasserted = 0

        def manual(version: RecordVersion, reason: str) -> None:
            changes.append(
                RevertChange(
                    revision=version.revision,
                    record_type=version.record_type,
                    record_id=version.record_id,
                    operation_type=version.operation_type,
                    reason=reason,
                )
            )

        def compensate(version: RecordVersion, operation: dict) -> None:
            operations.append(operation)
            changes.append(
                RevertChange(
                    revision=version.revision,
                    record_type=version.record_type,
                    record_id=version.record_id,
                    operation_type=version.operation_type,
                    inverse_operation=operation["op"],
                    reason="compensating",
                )
            )

        for version in ordered:
            key = (version.record_type, version.record_id)
            if key in later_keys:
                conflict = f"{version.record_type} {version.record_id} changed after r{to_revision}"
                if conflict not in conflicts:
                    conflicts.append(conflict)
                continue
            if key in created_in_range:
                # The record did not exist before the range: its net inverse is removal.
                if version.operation_type not in _CREATION_INVERSE:
                    manual(version, "superseded by removing the record this revert created")
                    continue
                handled_creations.add(key)
                if not await self._is_live(workspace_id, project_id, *key):
                    manual(version, "already retired or closed; nothing to undo")
                    continue
                removed.add(key)
                compensate(
                    version,
                    {"op": _CREATION_INVERSE[version.operation_type], "id": version.record_id},
                )
                continue
            if version.operation_type in _NO_INVERSE_REASON:
                manual(version, _NO_INVERSE_REASON[version.operation_type])
                continue
            if version.operation_type == "update_entity":
                before = await self.entity_at_revision(
                    scope, workspace_id, project_id, version.record_id, version.revision - 1
                )
                inverse, blocker = _inverse_update(version, before)
                if blocker is not None:
                    manual(version, blocker)
                else:
                    compensate(version, inverse)
            elif version.operation_type == "amend_event":
                previous = await self.event_at_revision(
                    scope, workspace_id, project_id, version.record_id, version.revision - 1
                )
                inverse, blocker = _inverse_amend(version, previous)
                if blocker is not None:
                    manual(version, blocker)
                else:
                    compensate(version, inverse)
            elif version.operation_type == "amend_fact":
                earlier_fact = await self.fact_at_revision(
                    scope, workspace_id, project_id, version.record_id, version.revision - 1
                )
                inverse, blocker = _inverse_amend_fact(version, earlier_fact)
                if blocker is not None:
                    manual(version, blocker)
                else:
                    compensate(version, inverse)
            elif version.operation_type == "update_plan_item":
                earlier = await self.plan_item_at_revision(
                    scope, workspace_id, project_id, version.record_id, version.revision - 1
                )
                inverse, blocker = _inverse_plan_update(version, earlier)
                if blocker is not None:
                    manual(version, blocker)
                else:
                    compensate(version, inverse)
            elif version.operation_type == "close_fact":
                fact = await self.fact_at_revision(
                    scope, workspace_id, project_id, version.record_id, version.revision - 1
                )
                reasserted += 1
                compensate(
                    version,
                    {
                        "op": "assert_fact",
                        "id": f"tmp:reassert-{reasserted}",
                        "proposition": fact.proposition.model_dump(mode="json"),
                        "confidence": fact.confidence,
                        "valid_from": fact.valid_from.isoformat() if fact.valid_from else None,
                        "valid_until": fact.valid_until.isoformat() if fact.valid_until else None,
                    },
                )
            elif version.operation_type == "close_belief":
                belief = await self.belief_at_revision(
                    scope, workspace_id, project_id, version.record_id, version.revision - 1
                )
                reasserted += 1
                compensate(
                    version,
                    {
                        "op": "assert_belief",
                        "id": f"tmp:reassert-{reasserted}",
                        "holder_entity_id": belief.holder_entity_id,
                        "proposition": belief.proposition.model_dump(mode="json"),
                        "confidence": belief.confidence,
                        "source_ref": belief.source_ref,
                    },
                )
            elif version.operation_type == "retire_asset_link":
                link = await self._canon.get_link(
                    workspace_id=workspace_id, project_id=project_id, link_id=version.record_id
                )
                if link is None:
                    manual(version, "the retired link can no longer be read")
                else:
                    reasserted += 1
                    compensate(
                        version,
                        {
                            "op": "link_asset",
                            "id": f"tmp:reassert-{reasserted}",
                            "asset_id": link.asset_id,
                            "asset_version_id": link.asset_version_id,
                            "target_record_type": link.target_record_type,
                            "target_id": link.target_record_id,
                            "role": link.role.value,
                        },
                    )
            else:
                manual(version, "no automatic inverse")

        # Dependency conflicts: removing an entity that live canon outside this
        # revert still references would leave those records pointing at a retired
        # entity, so the author must resolve them first.
        for record_type, record_id in sorted(removed):
            if record_type != "entity":
                continue
            for dependent in await self._canon.list_active_references(
                workspace_id=workspace_id, project_id=project_id, entity_id=record_id
            ):
                if dependent not in removed:
                    conflicts.append(
                        f"entity {record_id} is still referenced by {dependent[0]} {dependent[1]}"
                    )

        manual_count = sum(1 for change in changes if change.inverse_operation is None)
        return RevertProposal(
            from_revision=from_revision,
            to_revision=to_revision,
            head_revision=head,
            operations=operations,
            changes=changes,
            conflicts=conflicts,
            summary=(
                f"revert r{from_revision + 1}..r{to_revision}: {len(operations)} compensating "
                f"operation(s), {manual_count} manual, {len(conflicts)} conflict(s)"
            ),
        )

    async def _is_live(
        self, workspace_id: str, project_id: str, record_type: str, record_id: str
    ) -> bool:
        scope = {"workspace_id": workspace_id, "project_id": project_id}
        if record_type == "entity":
            entity = await self._canon.get_entity(**scope, entity_id=record_id)
            return entity is not None and entity.is_active
        if record_type == "fact":
            fact = await self._canon.get_fact(**scope, fact_id=record_id)
            return fact is not None and fact.closed_revision is None
        if record_type == "belief":
            belief = await self._canon.get_belief(**scope, belief_id=record_id)
            return belief is not None and belief.closed_revision is None
        if record_type == "event":
            event = await self._canon.get_event(**scope, event_id=record_id)
            return event is not None and event.retired_revision is None
        link = await self._canon.get_link(**scope, link_id=record_id)
        return link is not None and link.retired_revision is None

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
        branch = await self._working_branch(workspace_id, project_id)
        return await self._canon.list_tags(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
        )

    async def tag_history(
        self, scope: AccessScope, workspace_id: str, project_id: str, name: str
    ) -> list[CanonTagHistoryEntry]:
        await self._authorize(scope, workspace_id, project_id)
        branch = await self._working_branch(workspace_id, project_id)
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
        branch = await self._working_branch(workspace_id, project_id)
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
        branch = await self._working_branch(workspace_id, project_id)
        return await self._canon.delete_tag(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id, name=name
        )


def _inverse_update(version: RecordVersion, before: CanonEntity) -> tuple[dict, str | None]:
    """An ``update_entity`` that restores exactly the fields ``version`` changed."""
    change = version.change
    inverse: dict = {"op": "update_entity", "id": version.record_id}
    if change.get("name") is not None:
        inverse["name"] = before.name
    if change.get("slug") is not None:
        if before.slug is None:
            return inverse, "the earlier state had no slug and a slug cannot be cleared"
        inverse["slug"] = before.slug
    if change.get("attributes") is not None:
        inverse["attributes"] = before.attributes
    if change.get("aliases") is not None:
        inverse["aliases"] = before.aliases
    if change.get("external_ids") is not None:
        inverse["external_ids"] = before.external_ids
    if len(inverse) == 2:
        return inverse, "the edit changed nothing"
    return inverse, None


def _stored_time(value: object) -> Any:
    """A time as replayed from a change. An unknown time with no words is no time."""
    when = parse_world_time(value)
    if isinstance(when, Unknown) and not when.label:
        return None
    return when


def _time_or_clear(value: Any) -> dict:
    """The time to put back: an earlier time, or an unknown one that clears it."""
    return (value or Unknown()).model_dump(mode="json")


def _inverse_amend(version: RecordVersion, before: Event) -> tuple[dict, str | None]:
    change = version.change
    inverse: dict = {"op": "amend_event", "id": version.record_id}
    if change.get("name") is not None:
        inverse["name"] = before.name
    if change.get("world_position") is not None:
        if before.world_position is None:
            return inverse, "the earlier state had no world position and it cannot be cleared"
        inverse["world_position"] = before.world_position
    if change.get("when") is not None:
        inverse["when"] = _time_or_clear(before.when)
    if len(inverse) == 2:
        return inverse, "the amendment changed nothing"
    return inverse, None


def _inverse_amend_fact(version: RecordVersion, before: Fact) -> tuple[dict, str | None]:
    change = version.change
    inverse: dict = {"op": "amend_fact", "id": version.record_id}
    if change.get("starts") is not None:
        inverse["starts"] = _time_or_clear(before.starts)
    if change.get("ends") is not None:
        inverse["ends"] = _time_or_clear(before.ends)
    if change.get("visibility") is not None:
        inverse["visibility"] = before.visibility.value
    if len(inverse) == 2:
        return inverse, "the change said nothing new"
    return inverse, None


def _inverse_plan_update(version: RecordVersion, before: PlanItem) -> tuple[dict, str | None]:
    """An ``update_plan_item`` that restores exactly what ``version`` changed."""
    change = version.change
    inverse: dict = {"op": "update_plan_item", "id": version.record_id}
    for key in ("title", "summary", "position", "sets_up", "pays_off", "details"):
        if change.get(key) is not None:
            inverse[key] = getattr(before, key)
    if change.get("refs") is not None:
        inverse["refs"] = [ref.model_dump(mode="json") for ref in before.refs]
    if change.get("detach") or change.get("parent_id"):
        if before.parent_id is None:
            inverse["detach"] = True
        else:
            inverse["parent_id"] = before.parent_id
    if len(inverse) == 2:
        return inverse, "the edit changed nothing"
    return inverse, None


def _replay_plan_item(version: RecordVersion, current: PlanItem | None) -> PlanItem | None:
    change = version.change
    op = change.get("op")
    if op == "create_plan_item":
        return plan_item_from_create(
            change,
            workspace_id=version.workspace_id,
            project_id=version.project_id,
            branch_id=version.branch_id,
            provenance_id=f"prov_{version.commit_id}",
            revision=version.revision,
        )
    if current is None:
        return None
    if op == "update_plan_item":
        return apply_plan_update(current, change)
    if op == "retire_plan_item":
        return current.model_copy(update={"retired_revision": version.revision})
    return current


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
            aliases=change.get("aliases") or [],
            external_ids=change.get("external_ids") or {},
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
                "aliases": current.aliases if change.get("aliases") is None else change["aliases"],
                "external_ids": current.external_ids
                if change.get("external_ids") is None
                else change["external_ids"],
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
            starts=_stored_time(change.get("starts")),
            ends=_stored_time(change.get("ends")),
            visibility=FactVisibility(change.get("visibility") or "ordinary"),
        )
    if current is None:
        return None
    if op == "amend_fact":
        fact_update: dict[str, object] = {}
        if change.get("starts") is not None:
            fact_update["starts"] = _stored_time(change["starts"])
        if change.get("ends") is not None:
            fact_update["ends"] = _stored_time(change["ends"])
        if change.get("visibility") is not None:
            fact_update["visibility"] = FactVisibility(change["visibility"])
        return current.model_copy(update=fact_update)
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
            when=_stored_time(change.get("when")),
            claim=EventClaim.model_validate(change["claim"]) if change.get("claim") else None,
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
        if change.get("when") is not None:
            update["when"] = _stored_time(change["when"])
        return current.model_copy(update=update)
    if op == "retire_event":
        return current.model_copy(update={"retired_revision": version.revision})
    return current


# Re-exported for callers that only need the error type.
__all__ = ["CanonHistoryService", "HistoryError", "TenancyStorePort"]
