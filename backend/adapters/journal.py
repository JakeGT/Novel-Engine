"""PostgreSQL-backed canon journal (ADR-011).

Owns branches, optimistic head reservations, immutable commit envelopes, commit
attempts, review decisions, canon revisions, and the transactional outbox.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from backend.adapters.system import uuid7
from backend.core.database import session_scope
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.revisions import CanonRevision
from backend.domain.journal import (
    AttemptStatus,
    Branch,
    BranchReservation,
    CommitAttempt,
    CommitEnvelope,
    HeadConflict,
    JournalError,
    MutationOperation,
    MutationSet,
    MutationStatus,
    OutboxMessage,
    ReservationStatus,
    ReviewDecision,
    RevisionConflict,
)


def _branch(row: Any) -> Branch:
    return Branch(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        name=row.name,
        is_root=row.is_root,
        head_revision=row.head_revision,
        created_at=row.created_at,
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
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


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
                            expected_head
                        )
                        VALUES (
                            :id, :w, :p, :branch, :schema, 'draft', :origin, :actor,
                            :corr, :key, :head
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
                    },
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
        self, *, mutation_id: str, operations: list[MutationOperation]
    ) -> None:
        async with session_scope(self._session_factory) as session:
            for operation in operations:
                await session.execute(
                    text(
                        """
                        INSERT INTO mutation_operations (
                            mutation_id, ordinal, workspace_id, project_id, operation_type, payload
                        )
                        SELECT :m, :ord, ms.workspace_id, ms.project_id, :type, CAST(:payload AS JSONB)
                        FROM mutation_sets ms WHERE ms.id = :m
                        ON CONFLICT (mutation_id, ordinal) DO NOTHING
                        """
                    ),
                    {
                        "m": mutation_id,
                        "ord": operation.ordinal,
                        "type": operation.operation_type,
                        "payload": json.dumps(operation.payload),
                    },
                )

    async def list_operations(self, *, mutation_id: str) -> list[MutationOperation]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT ordinal, operation_type, payload FROM mutation_operations "
                        "WHERE mutation_id = :m ORDER BY ordinal"
                    ),
                    {"m": mutation_id},
                )
            ).all()
        return [
            MutationOperation(
                ordinal=row.ordinal, operation_type=row.operation_type, payload=dict(row.payload)
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
    ) -> ReviewDecision:
        async with session_scope(self._session_factory) as session:
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
        self, *, workspace_id: str, project_id: str, limit: int = 100
    ) -> list[OutboxMessage]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM outbox_messages WHERE workspace_id = :w AND project_id = :p "
                        "ORDER BY created_at LIMIT :limit"
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
