"""PostgreSQL-backed canon journal (ADR-012; workflow inherited from ADR-011).

Owns branches, optimistic head reservations, immutable commit envelopes, commit
attempts, review decisions, canon revisions, and the transactional outbox.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text

from backend.adapters.system import uuid7
from backend.core.database import session_scope
from backend.domain.branching import working_branch_id
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.revisions import CanonRevision
from backend.domain.errors import NotFoundError
from backend.domain.journal import (
    AttemptStatus,
    Branch,
    BranchReservation,
    CommitAttempt,
    CommitEnvelope,
    HeadConflict,
    JournalError,
    MutationNotFound,
    MutationOperation,
    MutationSet,
    MutationStatus,
    MutationTransition,
    OperationDecision,
    OperationRevision,
    OutboxMessage,
    ReservationStatus,
    ReviewDecision,
    RevisionConflict,
)
from backend.domain.review import ensure_transition, is_editable


def _branch(row: Any) -> Branch:
    return Branch(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        name=row.name,
        is_root=row.is_root,
        head_revision=row.head_revision,
        created_at=row.created_at,
        parent_branch_id=getattr(row, "parent_branch_id", None),
        fork_revision=getattr(row, "fork_revision", None),
        description=getattr(row, "description", None),
        status=getattr(row, "status", None) or "active",
        created_by=getattr(row, "created_by", None),
    )


def _mutation(row: Any) -> MutationSet:
    return MutationSet(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        branch_id=row.branch_id,
        schema_version_id=row.schema_version_id,
        status=MutationStatus(row.status),
        origin=row.origin,
        actor_user_id=row.actor_user_id,
        correlation_id=row.correlation_id,
        idempotency_key=row.idempotency_key,
        expected_head=row.expected_head,
        intent=row.intent,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _transition(row: Any) -> MutationTransition:
    return MutationTransition(
        id=row.id,
        mutation_id=row.mutation_id,
        from_status=MutationStatus(row.from_status),
        to_status=MutationStatus(row.to_status),
        actor_user_id=row.actor_user_id,
        reason=row.reason,
        correlation_id=row.correlation_id,
        idempotency_key=row.idempotency_key,
        created_at=row.created_at,
    )


def _operation_decision(row: Any) -> OperationDecision:
    return OperationDecision(
        id=row.id,
        mutation_id=row.mutation_id,
        ordinal=row.ordinal,
        operation_revision=row.operation_revision,
        decision=row.decision,
        actor_user_id=row.actor_user_id,
        note=row.note,
        created_at=row.created_at,
    )


async def apply_transition(
    session: Any,
    mutation: Any,
    to_status: MutationStatus,
    *,
    actor_user_id: str | None,
    reason: str,
    correlation_id: str = "",
    idempotency_key: str = "",
) -> None:
    """Enforce the state machine and append the transition inside ``session``'s transaction.

    ``mutation`` must be a row already locked ``FOR UPDATE`` by the caller.
    """
    current = MutationStatus(mutation.status)
    ensure_transition(current, to_status)
    await session.execute(
        text("UPDATE mutation_sets SET status = :status, updated_at = now() WHERE id = :id"),
        {"status": to_status.value, "id": mutation.id},
    )
    await session.execute(
        text(
            """
            INSERT INTO mutation_transitions (
                id, mutation_id, workspace_id, project_id, from_status, to_status,
                actor_user_id, reason, correlation_id, idempotency_key
            )
            VALUES (:id, :m, :w, :p, :from_status, :to_status, :actor, :reason, :corr, :key)
            """
        ),
        {
            "id": format_canon_id("operation", str(uuid7())),
            "m": mutation.id,
            "w": mutation.workspace_id,
            "p": mutation.project_id,
            "from_status": current.value,
            "to_status": to_status.value,
            "actor": actor_user_id,
            "reason": reason,
            "corr": correlation_id or mutation.correlation_id,
            "key": idempotency_key or mutation.idempotency_key,
        },
    )


async def _lock_mutation(session: Any, mutation_id: str) -> Any:
    row = (
        await session.execute(
            text("SELECT * FROM mutation_sets WHERE id = :id FOR UPDATE"), {"id": mutation_id}
        )
    ).first()
    if row is None:
        raise MutationNotFound(f"mutation {mutation_id!r} not found")
    return row


def _envelope(row: Any) -> CommitEnvelope:
    return CommitEnvelope(
        commit_id=row.commit_id,
        mutation_id=row.mutation_id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        branch_id=row.branch_id,
        revision=row.revision,
        expected_head=row.expected_head,
        schema_version_id=row.schema_version_id,
        canonical=row.canonical,
        checksum=row.checksum,
        actor_user_id=row.actor_user_id,
        created_at=row.created_at,
    )


# The first key of the two-key advisory locks taken by story_lock, so they never meet the
# single-key locks used elsewhere (credentials, rate limits, the studio).
_STORY_LOCK_SPACE = 12_012


class PostgresCanonJournal:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def ensure_root_branch(
        self, *, workspace_id: str, project_id: str, name: str = "main"
    ) -> Branch:
        async with session_scope(self._session_factory) as session:
            await session.execute(
                text(
                    """
                    INSERT INTO branches (id, workspace_id, project_id, name, is_root, head_revision)
                    VALUES (:id, :w, :p, :name, true, 0)
                    ON CONFLICT (workspace_id, project_id, name) DO NOTHING
                    """
                ),
                {
                    "id": format_canon_id("branch", str(uuid7())),
                    "w": workspace_id,
                    "p": project_id,
                    "name": name,
                },
            )
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM branches "
                        "WHERE workspace_id = :w AND project_id = :p AND name = :name"
                    ),
                    {"w": workspace_id, "p": project_id, "name": name},
                )
            ).one()
        return _branch(row)

    async def working_branch(self, *, workspace_id: str, project_id: str) -> Branch:
        """The what-if this request works in, or the main story (ADR-020)."""
        branch_id = working_branch_id()
        if branch_id is None:
            return await self.ensure_root_branch(workspace_id=workspace_id, project_id=project_id)
        branch = await self.get_branch(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
        )
        if branch is None:
            raise NotFoundError("That what-if was not found.")
        return branch

    async def get_branch(
        self, *, workspace_id: str, project_id: str, branch_id: str
    ) -> Branch | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM branches "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                    ),
                    {"w": workspace_id, "p": project_id, "id": branch_id},
                )
            ).first()
        return _branch(row) if row else None

    async def list_branches(self, *, workspace_id: str, project_id: str) -> list[Branch]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM branches WHERE workspace_id = :w AND project_id = :p "
                        "ORDER BY name"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).all()
        return [_branch(row) for row in rows]

    async def create_mutation(
        self,
        *,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        expected_head: int,
        actor_user_id: str | None = None,
        origin: str = "author",
        schema_version_id: str | None = None,
        correlation_id: str = "",
        idempotency_key: str = "",
        intent: str = "edit",
    ) -> MutationSet:
        async with session_scope(self._session_factory) as session:
            if idempotency_key:
                existing = (
                    await session.execute(
                        text(
                            "SELECT * FROM mutation_sets WHERE workspace_id = :w "
                            "AND project_id = :p AND idempotency_key = :k"
                        ),
                        {"w": workspace_id, "p": project_id, "k": idempotency_key},
                    )
                ).first()
                if existing is not None:
                    return _mutation(existing)
            row = (
                await session.execute(
                    text(
                        """
                        INSERT INTO mutation_sets (
                            id, workspace_id, project_id, branch_id, schema_version_id,
                            status, origin, actor_user_id, correlation_id, idempotency_key,
                            expected_head, intent
                        )
                        VALUES (
                            :id, :w, :p, :branch, :schema, 'draft', :origin, :actor,
                            :corr, :key, :head, :intent
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "id": format_canon_id("mutation", str(uuid7())),
                        "w": workspace_id,
                        "p": project_id,
                        "branch": branch_id,
                        "schema": schema_version_id,
                        "origin": origin,
                        "actor": actor_user_id,
                        "corr": correlation_id,
                        "key": idempotency_key,
                        "head": expected_head,
                        "intent": intent,
                    },
                )
            ).one()
        return _mutation(row)

    async def list_mutations(
        self,
        *,
        workspace_id: str,
        project_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MutationSet]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM mutation_sets WHERE workspace_id = :w AND project_id = :p "
                        "AND branch_id = COALESCE(CAST(:wb AS TEXT), (SELECT id FROM branches "
                        "WHERE workspace_id = :w AND project_id = :p AND is_root)) "
                        "AND (CAST(:status AS TEXT) IS NULL OR status = :status) "
                        "ORDER BY created_at DESC, id DESC LIMIT :limit OFFSET :offset"
                    ),
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "wb": working_branch_id(),
                        "status": status,
                        "limit": limit,
                        "offset": offset,
                    },
                )
            ).all()
        return [_mutation(row) for row in rows]

    async def transition_mutation(
        self,
        *,
        mutation_id: str,
        to_status: MutationStatus,
        actor_user_id: str | None,
        reason: str,
        correlation_id: str = "",
    ) -> MutationSet:
        async with session_scope(self._session_factory) as session:
            mutation = await _lock_mutation(session, mutation_id)
            await apply_transition(
                session,
                mutation,
                to_status,
                actor_user_id=actor_user_id,
                reason=reason,
                correlation_id=correlation_id,
            )
            row = (
                await session.execute(
                    text("SELECT * FROM mutation_sets WHERE id = :id"), {"id": mutation_id}
                )
            ).one()
        return _mutation(row)

    async def list_transitions(self, *, mutation_id: str) -> list[MutationTransition]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text("SELECT * FROM mutation_transitions WHERE mutation_id = :m ORDER BY seq"),
                    {"m": mutation_id},
                )
            ).all()
        return [_transition(row) for row in rows]

    async def retarget_mutation(
        self,
        *,
        mutation_id: str,
        expected_head: int,
        actor_user_id: str | None,
        reason: str,
        keep_approval: bool = False,
    ) -> MutationSet:
        """Point an uncommitted proposal at a newer head.

        Any approval is invalidated unless ``keep_approval``: the caller has shown that
        nothing the proposal touches changed in between.
        """
        async with session_scope(self._session_factory) as session:
            mutation = await _lock_mutation(session, mutation_id)
            status = MutationStatus(mutation.status)
            if not is_editable(status):
                raise RevisionConflict(f"mutation in state {status.value!r} cannot be retargeted")
            await session.execute(
                text(
                    "UPDATE mutation_sets SET expected_head = :head, updated_at = now() "
                    "WHERE id = :id"
                ),
                {"head": expected_head, "id": mutation_id},
            )
            if status is MutationStatus.APPROVED and not keep_approval:
                await apply_transition(
                    session,
                    mutation,
                    MutationStatus.NEEDS_REVIEW,
                    actor_user_id=actor_user_id,
                    reason=reason,
                )
            row = (
                await session.execute(
                    text("SELECT * FROM mutation_sets WHERE id = :id"), {"id": mutation_id}
                )
            ).one()
        return _mutation(row)

    async def get_mutation(
        self, *, workspace_id: str, project_id: str, mutation_id: str
    ) -> MutationSet | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM mutation_sets "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                    ),
                    {"w": workspace_id, "p": project_id, "id": mutation_id},
                )
            ).first()
        return _mutation(row) if row else None

    async def record_operations(
        self, *, mutation_id: str, operations: Sequence[MutationOperation]
    ) -> None:
        async with session_scope(self._session_factory) as session:
            for operation in operations:
                row = (
                    await session.execute(
                        text(
                            """
                        INSERT INTO mutation_operations (
                            mutation_id, ordinal, workspace_id, project_id, operation_type, payload
                        )
                        SELECT :m, :ord, ms.workspace_id, ms.project_id, :type, CAST(:payload AS JSONB)
                        FROM mutation_sets ms WHERE ms.id = :m
                        ON CONFLICT (mutation_id, ordinal) DO UPDATE
                        SET operation_type = mutation_operations.operation_type
                        RETURNING operation_type, payload, revision
                        """
                        ),
                        {
                            "m": mutation_id,
                            "ord": operation.ordinal,
                            "type": operation.operation_type,
                            "payload": json.dumps(operation.payload),
                        },
                    )
                ).one()
                if (
                    row.operation_type != operation.operation_type
                    or dict(row.payload) != operation.payload
                ):
                    raise RevisionConflict(
                        "mutation operation ordinal was reused with different content"
                    )
                await session.execute(
                    text(
                        """
                        INSERT INTO mutation_operation_revisions (
                            mutation_id, ordinal, revision, workspace_id, project_id,
                            operation_type, payload, reason
                        )
                        SELECT :m, :ord, :rev, ms.workspace_id, ms.project_id, :type,
                               CAST(:payload AS JSONB), 'proposed'
                        FROM mutation_sets ms WHERE ms.id = :m
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {
                        "m": mutation_id,
                        "ord": operation.ordinal,
                        "rev": row.revision,
                        "type": operation.operation_type,
                        "payload": json.dumps(operation.payload),
                    },
                )

    async def revise_operation(
        self,
        *,
        mutation_id: str,
        ordinal: int,
        operation_type: str,
        payload: dict[str, Any],
        actor_user_id: str | None,
        reason: str,
    ) -> MutationOperation:
        """Append a new immutable revision of one operation; never rewrites a prior one."""
        async with session_scope(self._session_factory) as session:
            mutation = await _lock_mutation(session, mutation_id)
            status = MutationStatus(mutation.status)
            if not is_editable(status):
                raise RevisionConflict(f"mutation in state {status.value!r} cannot be edited")
            row = (
                await session.execute(
                    text(
                        "UPDATE mutation_operations SET operation_type = :type, "
                        "payload = CAST(:payload AS JSONB), revision = revision + 1 "
                        "WHERE mutation_id = :m AND ordinal = :ord RETURNING revision"
                    ),
                    {
                        "m": mutation_id,
                        "ord": ordinal,
                        "type": operation_type,
                        "payload": json.dumps(payload),
                    },
                )
            ).first()
            if row is None:
                raise MutationNotFound(f"operation {ordinal} not found in {mutation_id!r}")
            await session.execute(
                text(
                    """
                    INSERT INTO mutation_operation_revisions (
                        mutation_id, ordinal, revision, workspace_id, project_id,
                        operation_type, payload, actor_user_id, reason
                    )
                    VALUES (:m, :ord, :rev, :w, :p, :type, CAST(:payload AS JSONB), :actor, :reason)
                    """
                ),
                {
                    "m": mutation_id,
                    "ord": ordinal,
                    "rev": row.revision,
                    "w": mutation.workspace_id,
                    "p": mutation.project_id,
                    "type": operation_type,
                    "payload": json.dumps(payload),
                    "actor": actor_user_id,
                    "reason": reason,
                },
            )
            if status is MutationStatus.APPROVED:
                await apply_transition(
                    session,
                    mutation,
                    MutationStatus.NEEDS_REVIEW,
                    actor_user_id=actor_user_id,
                    reason="operation edited after approval",
                )
        return MutationOperation(
            ordinal=ordinal, operation_type=operation_type, payload=payload, revision=row.revision
        )

    async def list_operation_revisions(self, *, mutation_id: str) -> list[OperationRevision]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM mutation_operation_revisions WHERE mutation_id = :m "
                        "ORDER BY ordinal, revision"
                    ),
                    {"m": mutation_id},
                )
            ).all()
        return [
            OperationRevision(
                mutation_id=row.mutation_id,
                ordinal=row.ordinal,
                revision=row.revision,
                operation_type=row.operation_type,
                payload=dict(row.payload),
                actor_user_id=row.actor_user_id,
                reason=row.reason,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def record_operation_decision(
        self,
        *,
        mutation_id: str,
        ordinal: int,
        decision: str,
        actor_user_id: str | None = None,
        note: str | None = None,
    ) -> OperationDecision:
        recorded = await self.record_operation_decisions(
            mutation_id=mutation_id,
            decisions=[(ordinal, decision, note)],
            actor_user_id=actor_user_id,
        )
        return recorded[0]

    async def record_operation_decisions(
        self,
        *,
        mutation_id: str,
        decisions: Sequence[tuple[int, str, str | None]],
        actor_user_id: str | None = None,
    ) -> list[OperationDecision]:
        """Record several per-operation decisions together: all of them, or none."""
        async with session_scope(self._session_factory) as session:
            mutation = await _lock_mutation(session, mutation_id)
            status = MutationStatus(mutation.status)
            if not is_editable(status):
                raise RevisionConflict(f"mutation in state {status.value!r} cannot be reviewed")
            revisions = {
                row.ordinal: row.revision
                for row in (
                    await session.execute(
                        text(
                            "SELECT ordinal, revision FROM mutation_operations "
                            "WHERE mutation_id = :m"
                        ),
                        {"m": mutation_id},
                    )
                ).all()
            }
            rows = []
            for ordinal, decision, note in decisions:
                if ordinal not in revisions:
                    raise MutationNotFound(f"operation {ordinal} not found in {mutation_id!r}")
                rows.append(
                    (
                        await session.execute(
                            text(
                                """
                                INSERT INTO operation_decisions (
                                    id, mutation_id, ordinal, operation_revision, workspace_id,
                                    project_id, decision, actor_user_id, note
                                )
                                VALUES (:id, :m, :ord, :rev, :w, :p, :decision, :actor, :note)
                                RETURNING *
                                """
                            ),
                            {
                                "id": format_canon_id("operation", str(uuid7())),
                                "m": mutation_id,
                                "ord": ordinal,
                                "rev": revisions[ordinal],
                                "w": mutation.workspace_id,
                                "p": mutation.project_id,
                                "decision": decision,
                                "actor": actor_user_id,
                                "note": note,
                            },
                        )
                    ).one()
                )
            if rows and status is MutationStatus.APPROVED:
                await apply_transition(
                    session,
                    mutation,
                    MutationStatus.NEEDS_REVIEW,
                    actor_user_id=actor_user_id,
                    reason="operation decision changed after approval",
                )
        return [_operation_decision(row) for row in rows]

    async def list_operation_decisions(self, *, mutation_id: str) -> list[OperationDecision]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text("SELECT * FROM operation_decisions WHERE mutation_id = :m ORDER BY seq"),
                    {"m": mutation_id},
                )
            ).all()
        return [_operation_decision(row) for row in rows]

    async def list_operations(self, *, mutation_id: str) -> list[MutationOperation]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT ordinal, operation_type, payload, revision "
                        "FROM mutation_operations WHERE mutation_id = :m ORDER BY ordinal"
                    ),
                    {"m": mutation_id},
                )
            ).all()
        return [
            MutationOperation(
                ordinal=row.ordinal,
                operation_type=row.operation_type,
                payload=dict(row.payload),
                revision=row.revision,
            )
            for row in rows
        ]

    async def record_decision(
        self,
        *,
        mutation_id: str,
        decision: str,
        actor_user_id: str | None = None,
        note: str | None = None,
        transition_to: MutationStatus | None = None,
        correlation_id: str = "",
    ) -> ReviewDecision:
        async with session_scope(self._session_factory) as session:
            if transition_to is not None:
                mutation = await _lock_mutation(session, mutation_id)
                if MutationStatus(mutation.status) is not transition_to:
                    await apply_transition(
                        session,
                        mutation,
                        transition_to,
                        actor_user_id=actor_user_id,
                        reason=note or f"review decision: {decision}",
                        correlation_id=correlation_id,
                    )
            row = (
                await session.execute(
                    text(
                        """
                        INSERT INTO review_decisions (
                            id, mutation_id, workspace_id, project_id, decision, actor_user_id, note
                        )
                        SELECT :id, :m, ms.workspace_id, ms.project_id, :decision, :actor, :note
                        FROM mutation_sets ms WHERE ms.id = :m
                        RETURNING *
                        """
                    ),
                    {
                        "id": format_canon_id("operation", str(uuid7())),
                        "m": mutation_id,
                        "decision": decision,
                        "actor": actor_user_id,
                        "note": note,
                    },
                )
            ).one()
        return ReviewDecision(
            id=row.id,
            mutation_id=row.mutation_id,
            decision=row.decision,
            actor_user_id=row.actor_user_id,
            note=row.note,
            created_at=row.created_at,
        )

    async def list_decisions(self, *, mutation_id: str) -> list[ReviewDecision]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM review_decisions "
                        "WHERE mutation_id = :m ORDER BY created_at, id"
                    ),
                    {"m": mutation_id},
                )
            ).all()
        return [
            ReviewDecision(
                id=row.id,
                mutation_id=row.mutation_id,
                decision=row.decision,
                actor_user_id=row.actor_user_id,
                note=row.note,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def reserve_head(
        self,
        *,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        mutation_id: str,
        expected_revision: int,
    ) -> BranchReservation:
        async with session_scope(self._session_factory) as session:
            head = (
                await session.execute(
                    text(
                        "SELECT head_revision FROM branches "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id FOR UPDATE"
                    ),
                    {"w": workspace_id, "p": project_id, "id": branch_id},
                )
            ).first()
            if head is None:
                raise HeadConflict("branch not found in this project")
            if int(head.head_revision) != expected_revision:
                raise HeadConflict(
                    f"expected head {expected_revision} but branch is at {head.head_revision}"
                )
            active = (
                await session.execute(
                    text(
                        "SELECT 1 FROM branch_reservations "
                        "WHERE branch_id = :id AND status = 'active'"
                    ),
                    {"id": branch_id},
                )
            ).first()
            if active is not None:
                raise HeadConflict("another commit reservation is active for this branch")
            row = (
                await session.execute(
                    text(
                        """
                        INSERT INTO branch_reservations (
                            id, workspace_id, project_id, branch_id, mutation_id,
                            expected_revision, reserved_revision, status
                        )
                        VALUES (:id, :w, :p, :branch, :m, :expected, :reserved, 'active')
                        RETURNING *
                        """
                    ),
                    {
                        "id": format_canon_id("revision", str(uuid7())),
                        "w": workspace_id,
                        "p": project_id,
                        "branch": branch_id,
                        "m": mutation_id,
                        "expected": expected_revision,
                        "reserved": expected_revision + 1,
                    },
                )
            ).one()
        return BranchReservation(
            id=row.id,
            workspace_id=row.workspace_id,
            project_id=row.project_id,
            branch_id=row.branch_id,
            mutation_id=row.mutation_id,
            expected_revision=row.expected_revision,
            reserved_revision=row.reserved_revision,
            status=ReservationStatus(row.status),
            created_at=row.created_at,
        )

    async def append_envelope(self, envelope: CommitEnvelope) -> None:
        async with session_scope(self._session_factory) as session:
            existing = (
                await session.execute(
                    text("SELECT checksum FROM commit_envelopes WHERE commit_id = :id"),
                    {"id": envelope.commit_id},
                )
            ).first()
            if existing is not None:
                if existing.checksum != envelope.checksum:
                    raise RevisionConflict("commit id reused with a different checksum")
                return
            await session.execute(
                text(
                    """
                    INSERT INTO commit_envelopes (
                        commit_id, mutation_id, workspace_id, project_id, branch_id,
                        revision, expected_head, schema_version_id, canonical, checksum,
                        actor_user_id
                    )
                    VALUES (
                        :id, :m, :w, :p, :branch, :rev, :expected, :schema,
                        :canonical, :checksum, :actor
                    )
                    """
                ),
                {
                    "id": envelope.commit_id,
                    "m": envelope.mutation_id,
                    "w": envelope.workspace_id,
                    "p": envelope.project_id,
                    "branch": envelope.branch_id,
                    "rev": envelope.revision,
                    "expected": envelope.expected_head,
                    "schema": envelope.schema_version_id,
                    "canonical": envelope.canonical,
                    "checksum": envelope.checksum,
                    "actor": envelope.actor_user_id,
                },
            )

    async def get_envelope(self, *, commit_id: str) -> CommitEnvelope | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text("SELECT * FROM commit_envelopes WHERE commit_id = :id"),
                    {"id": commit_id},
                )
            ).first()
        return _envelope(row) if row else None

    async def record_attempt(
        self, *, commit_id: str, status: str, detail: str | None = None
    ) -> CommitAttempt:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        """
                        INSERT INTO commit_attempts (
                            id, commit_id, workspace_id, project_id, status, detail
                        )
                        SELECT :id, :c, ce.workspace_id, ce.project_id, :status, :detail
                        FROM commit_envelopes ce WHERE ce.commit_id = :c
                        RETURNING *
                        """
                    ),
                    {
                        "id": format_canon_id("operation", str(uuid7())),
                        "c": commit_id,
                        "status": status,
                        "detail": detail,
                    },
                )
            ).one()
        return CommitAttempt(
            id=row.id,
            commit_id=row.commit_id,
            status=AttemptStatus(row.status),
            detail=row.detail,
            created_at=row.created_at,
        )

    async def finalize(self, *, commit_id: str) -> CanonRevision:
        async with session_scope(self._session_factory) as session:
            envelope_row = (
                await session.execute(
                    text("SELECT * FROM commit_envelopes WHERE commit_id = :id"),
                    {"id": commit_id},
                )
            ).first()
            if envelope_row is None:
                raise JournalError("commit envelope not found")
            envelope = _envelope(envelope_row)

            existing = (
                await session.execute(
                    text("SELECT * FROM canon_revisions WHERE commit_id = :id"),
                    {"id": commit_id},
                )
            ).first()
            if existing is not None:
                return CanonRevision(
                    revision=existing.revision,
                    workspace_id=existing.workspace_id,
                    project_id=existing.project_id,
                    branch_id=existing.branch_id,
                    commit_id=existing.commit_id,
                    expected_head=existing.expected_head,
                    result_head=existing.result_head,
                    schema_version_id=existing.schema_version_id,
                    envelope_checksum=existing.envelope_checksum,
                    created_at=existing.created_at,
                )

            advanced = (
                await session.execute(
                    text(
                        "UPDATE branches SET head_revision = :rev "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :branch "
                        "AND head_revision = :expected RETURNING head_revision"
                    ),
                    {
                        "rev": envelope.revision,
                        "w": envelope.workspace_id,
                        "p": envelope.project_id,
                        "branch": envelope.branch_id,
                        "expected": envelope.expected_head,
                    },
                )
            ).first()
            if advanced is None:
                raise HeadConflict("branch head advanced past the expected revision")

            await session.execute(
                text(
                    "UPDATE branch_reservations SET status = 'finalized' "
                    "WHERE branch_id = :branch AND reserved_revision = :rev AND status = 'active'"
                ),
                {"branch": envelope.branch_id, "rev": envelope.revision},
            )
            revision_row = (
                await session.execute(
                    text(
                        """
                        INSERT INTO canon_revisions (
                            id, revision, workspace_id, project_id, branch_id, commit_id,
                            expected_head, result_head, schema_version_id, envelope_checksum
                        )
                        VALUES (
                            :id, :rev, :w, :p, :branch, :commit, :expected, :result, :schema, :checksum
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "id": format_canon_id("revision", str(uuid7())),
                        "rev": envelope.revision,
                        "w": envelope.workspace_id,
                        "p": envelope.project_id,
                        "branch": envelope.branch_id,
                        "commit": envelope.commit_id,
                        "expected": envelope.expected_head,
                        "result": envelope.revision,
                        "schema": envelope.schema_version_id,
                        "checksum": envelope.checksum,
                    },
                )
            ).one()
            await session.execute(
                text(
                    """
                    INSERT INTO outbox_messages (
                        id, topic, workspace_id, project_id, branch_id, revision,
                        payload, idempotency_key
                    )
                    VALUES (
                        :id, 'canon.commit', :w, :p, :branch, :rev,
                        CAST(:payload AS JSONB), :key
                    )
                    ON CONFLICT (idempotency_key) DO NOTHING
                    """
                ),
                {
                    "id": format_canon_id("operation", str(uuid7())),
                    "w": envelope.workspace_id,
                    "p": envelope.project_id,
                    "branch": envelope.branch_id,
                    "rev": envelope.revision,
                    "payload": json.dumps({"commit_id": envelope.commit_id}),
                    "key": f"commit:{envelope.commit_id}",
                },
            )
        return CanonRevision(
            revision=revision_row.revision,
            workspace_id=revision_row.workspace_id,
            project_id=revision_row.project_id,
            branch_id=revision_row.branch_id,
            commit_id=revision_row.commit_id,
            expected_head=revision_row.expected_head,
            result_head=revision_row.result_head,
            schema_version_id=revision_row.schema_version_id,
            envelope_checksum=revision_row.envelope_checksum,
            created_at=revision_row.created_at,
        )

    async def append_outbox(self, message: OutboxMessage) -> None:
        async with session_scope(self._session_factory) as session:
            await session.execute(
                text(
                    """
                    INSERT INTO outbox_messages (
                        id, topic, workspace_id, project_id, branch_id, revision,
                        payload, idempotency_key
                    )
                    VALUES (
                        :id, :topic, :w, :p, :branch, :rev,
                        CAST(:payload AS JSONB), :key
                    )
                    ON CONFLICT (idempotency_key) DO NOTHING
                    """
                ),
                {
                    "id": message.id,
                    "topic": message.topic,
                    "w": message.workspace_id,
                    "p": message.project_id,
                    "branch": message.branch_id,
                    "rev": message.revision,
                    "payload": json.dumps(message.payload),
                    "key": message.idempotency_key,
                },
            )

    async def list_outbox(
        self, *, workspace_id: str, project_id: str, limit: int = 100, pending_only: bool = False
    ) -> list[OutboxMessage]:
        # Pending messages are asked for by themselves: the oldest messages of a long-lived
        # project are all delivered, so a plain "oldest first" page would never reach the new.
        pending = " AND published_at IS NULL" if pending_only else ""
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM outbox_messages WHERE workspace_id = :w AND project_id = :p"
                        f"{pending} ORDER BY created_at LIMIT :limit"
                    ),
                    {"w": workspace_id, "p": project_id, "limit": limit},
                )
            ).all()
        return [
            OutboxMessage(
                id=row.id,
                topic=row.topic,
                workspace_id=row.workspace_id,
                project_id=row.project_id,
                branch_id=row.branch_id,
                revision=row.revision,
                payload=dict(row.payload),
                idempotency_key=row.idempotency_key,
                created_at=row.created_at,
                published_at=row.published_at,
            )
            for row in rows
        ]

    async def projects_with_pending_outbox(self, *, limit: int = 50) -> list[tuple[str, str]]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT workspace_id, project_id, min(created_at) AS oldest "
                        "FROM outbox_messages WHERE published_at IS NULL "
                        "GROUP BY workspace_id, project_id ORDER BY oldest LIMIT :limit"
                    ),
                    {"limit": limit},
                )
            ).all()
        return [(str(row.workspace_id), str(row.project_id)) for row in rows]

    @asynccontextmanager
    async def story_lock(self, *, workspace_id: str, project_id: str) -> AsyncIterator[bool]:
        """The right to deliver one story's changes to the graph, held while the block runs.

        Yields False at once, without waiting, when another process holds it, so two servers
        never deliver the same story at the same time. The lock belongs to a transaction kept
        open for the block, so it is let go however the block ends, even if the process dies.
        """
        async with session_scope(self._session_factory) as session:
            held = (
                await session.execute(
                    text("SELECT pg_try_advisory_xact_lock(:space, hashtext(:key))"),
                    {"space": _STORY_LOCK_SPACE, "key": f"{workspace_id}/{project_id}"},
                )
            ).scalar()
            yield bool(held)

    async def mark_outbox_published(self, *, message_id: str) -> None:
        async with session_scope(self._session_factory) as session:
            await session.execute(
                text("UPDATE outbox_messages SET published_at = now() WHERE id = :id"),
                {"id": message_id},
            )

    async def count_failed_outbox(self, *, workspace_id: str, project_id: str) -> int:
        async with session_scope(self._session_factory) as session:
            count = (
                await session.execute(
                    text(
                        "SELECT count(*) FROM outbox_messages "
                        "WHERE workspace_id = :w AND project_id = :p "
                        "AND published_at IS NULL AND attempts > 0"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).scalar()
        return int(count or 0)

    async def mark_outbox_failed(self, *, message_id: str, error: str) -> None:
        async with session_scope(self._session_factory) as session:
            await session.execute(
                text(
                    "UPDATE outbox_messages "
                    "SET attempts = attempts + 1, last_error = :error WHERE id = :id"
                ),
                {"id": message_id, "error": error[:500]},
            )
