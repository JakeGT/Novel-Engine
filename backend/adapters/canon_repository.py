"""Canonical PostgreSQL canon repository (ADR-012).

One transaction applies a validated commit envelope to normalized/versioned
canonical tables together with the branch head, canonical revision, and outbox.
Neo4j is not involved: application success is decided entirely here.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from backend.adapters.system import uuid7
from backend.core.database import session_scope
from backend.domain.canon.assertions import Belief, Fact, FactStatus
from backend.domain.canon.entities import CanonEntity, EntityLifecycle
from backend.domain.canon.events import Event, EventParticipant
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.propositions import Proposition
from backend.domain.canon.revisions import CanonRevision, OriginKind, Provenance
from backend.domain.history import CanonTag, CanonTagHistoryEntry
from backend.domain.journal import CommitEnvelope, HeadConflict, RevisionConflict
from backend.domain.media import AssetLifecycle, AssetLink, LinkRole, MediaAsset, MediaAssetVersion
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
from backend.domain.projection import (
    CanonStorageError,
    ProjectionStatus,
    ProjectionWatermark,
    RecordVersion,
)
from backend.domain.validation import (
    AuthoritativeValidator,
    CanonValidationError,
    ValidationContext,
)

_DEFAULT_VALIDATOR = AuthoritativeValidator()


def _asset(row: Any) -> MediaAsset:
    return MediaAsset(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        name=row.name,
        lifecycle=AssetLifecycle(row.lifecycle),
        created_revision=row.created_revision,
        retired_revision=row.retired_revision,
        created_at=row.created_at,
    )


def _asset_version(row: Any) -> MediaAssetVersion:
    return MediaAssetVersion(
        id=row.id,
        asset_id=row.asset_id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        version=row.version,
        object_key=row.object_key,
        content_type=row.content_type,
        byte_length=row.byte_length,
        checksum=row.checksum,
        source=row.source,
        licence=row.licence,
        attribution=row.attribution,
        authority=row.authority,
        visibility=row.visibility,
        created_revision=row.created_revision,
        retired_revision=row.retired_revision,
        created_at=row.created_at,
    )


def _link(row: Any) -> AssetLink:
    return AssetLink(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        branch_id=row.branch_id,
        asset_id=row.asset_id,
        asset_version_id=row.asset_version_id,
        role=LinkRole(row.role),
        target_record_type=row.target_record_type,
        target_record_id=row.target_record_id,
        provenance_id=row.provenance_id,
        created_revision=row.created_revision,
        retired_revision=row.retired_revision,
        created_at=row.created_at,
    )


_RECORD_TYPE = {
    "create_entity": "entity",
    "update_entity": "entity",
    "retire_entity": "entity",
    "assert_fact": "fact",
    "close_fact": "fact",
    "assert_belief": "belief",
    "close_belief": "belief",
    "create_event": "event",
    "amend_event": "event",
    "retire_event": "event",
    "link_asset": "asset_link",
    "retire_asset_link": "asset_link",
}


def _load(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _to_tag(row: Any) -> CanonTag:
    return CanonTag(
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        branch_id=row.branch_id,
        name=row.name,
        revision=row.revision,
        commit_id=row.commit_id,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _entity(row: Any) -> CanonEntity:
    return CanonEntity(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        branch_id=row.branch_id,
        type=row.type,
        name=row.name,
        slug=row.slug,
        lifecycle=EntityLifecycle(row.lifecycle),
        schema_version_id=row.schema_version_id or "",
        created_revision=row.created_revision,
        retired_revision=row.retired_revision,
        attributes=dict(_load(row.attributes) or {}),
    )


def _fact(row: Any) -> Fact:
    return Fact(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        branch_id=row.branch_id,
        proposition=Proposition.model_validate(_load(row.proposition)),
        status=FactStatus(row.status),
        confidence=row.confidence,
        provenance_id=row.provenance_id,
        created_revision=row.created_revision,
        closed_revision=row.closed_revision,
        valid_from=row.valid_from,
        valid_until=row.valid_until,
    )


def _belief(row: Any) -> Belief:
    return Belief(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        branch_id=row.branch_id,
        holder_entity_id=row.holder_entity_id,
        proposition=Proposition.model_validate(_load(row.proposition)),
        confidence=row.confidence,
        acquisition_provenance_id=row.acquisition_provenance_id,
        source_ref=row.source_ref,
        created_revision=row.created_revision,
        closed_revision=row.closed_revision,
    )


def _event(row: Any) -> Event:
    return Event(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        branch_id=row.branch_id,
        kind=row.kind,
        name=row.name,
        participants=[EventParticipant.model_validate(p) for p in (_load(row.participants) or [])],
        location_ids=list(_load(row.location_ids) or []),
        cause_ids=list(_load(row.cause_ids) or []),
        outcome_ids=list(_load(row.outcome_ids) or []),
        world_position=row.world_position,
        provenance_id=row.provenance_id,
        created_revision=row.created_revision,
    )


class _TxCanonReader:
    """Canonical reads bound to the commit transaction (for in-tx validation)."""

    def __init__(self, session: Any, workspace_id: str, project_id: str, branch_id: str) -> None:
        self._session = session
        self._workspace_id = workspace_id
        self._project_id = project_id
        self._branch_id = branch_id

    async def get_entity(self, entity_id: str) -> CanonEntity | None:
        row = (
            await self._session.execute(
                text(
                    "SELECT * FROM canon_entities "
                    "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b AND id = :id"
                ),
                {
                    "w": self._workspace_id,
                    "p": self._project_id,
                    "b": self._branch_id,
                    "id": entity_id,
                },
            )
        ).first()
        return _entity(row) if row else None

    async def count_open_facts(self, subject_id: str, predicate: str) -> int:
        value = (
            await self._session.execute(
                text(
                    "SELECT count(*) FROM canon_facts "
                    "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b "
                    "AND status = 'asserted' "
                    "AND proposition->>'subject_id' = :s AND proposition->>'predicate' = :pr"
                ),
                {
                    "w": self._workspace_id,
                    "p": self._project_id,
                    "b": self._branch_id,
                    "s": subject_id,
                    "pr": predicate,
                },
            )
        ).scalar()
        return int(value or 0)

    async def get_asset(self, asset_id: str) -> MediaAsset | None:
        row = (
            await self._session.execute(
                text(
                    "SELECT * FROM media_assets "
                    "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                ),
                {"w": self._workspace_id, "p": self._project_id, "id": asset_id},
            )
        ).first()
        return _asset(row) if row else None

    async def get_asset_version(self, version_id: str) -> MediaAssetVersion | None:
        row = (
            await self._session.execute(
                text(
                    "SELECT * FROM media_asset_versions "
                    "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                ),
                {"w": self._workspace_id, "p": self._project_id, "id": version_id},
            )
        ).first()
        return _asset_version(row) if row else None


class PostgresCanonRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------------ writes
    async def apply_commit(
        self,
        *,
        envelope: Any,
        operations: list[CanonOperation],
        validator: Any = None,
        allowed_entity_types: frozenset[str] | None = None,
        origin: str = OriginKind.AUTHOR.value,
    ) -> int:
        validator = validator or _DEFAULT_VALIDATOR
        async with session_scope(self._session_factory) as session:
            existing = (
                await session.execute(
                    text("SELECT revision, checksum FROM commit_envelopes WHERE commit_id = :c"),
                    {"c": envelope.commit_id},
                )
            ).first()
            if existing is not None:
                if existing.checksum != envelope.checksum:
                    raise RevisionConflict("commit id reused with a different checksum")
                return int(existing.revision)

            head = (
                await session.execute(
                    text(
                        "SELECT head_revision FROM branches "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :b FOR UPDATE"
                    ),
                    {"w": envelope.workspace_id, "p": envelope.project_id, "b": envelope.branch_id},
                )
            ).first()
            if head is None:
                raise HeadConflict("branch not found in this project")
            if int(head.head_revision) != envelope.expected_head:
                raise HeadConflict(
                    f"expected head {envelope.expected_head} but branch is at {head.head_revision}"
                )
            active = (
                await session.execute(
                    text(
                        "SELECT 1 FROM branch_reservations "
                        "WHERE branch_id = :b AND status = 'active'"
                    ),
                    {"b": envelope.branch_id},
                )
            ).first()
            if active is not None:
                raise HeadConflict("another commit reservation is active for this branch")

            # Authoritative validation runs inside this transaction, against the
            # locked head, before any reservation, envelope, or canon row is written.
            reader = _TxCanonReader(
                session, envelope.workspace_id, envelope.project_id, envelope.branch_id
            )
            context = ValidationContext(
                workspace_id=envelope.workspace_id,
                project_id=envelope.project_id,
                branch_id=envelope.branch_id,
                schema_version_id=envelope.schema_version_id,
                head_revision=envelope.expected_head,
                reader=reader,
            )
            if allowed_entity_types is not None:
                context = ValidationContext(
                    workspace_id=context.workspace_id,
                    project_id=context.project_id,
                    branch_id=context.branch_id,
                    schema_version_id=context.schema_version_id,
                    head_revision=context.head_revision,
                    reader=reader,
                    allowed_entity_types=allowed_entity_types,
                )
            report = await validator.validate(operations, context=context)
            if not report.ok:
                raise CanonValidationError(report)

            reserved = envelope.expected_head + 1
            await session.execute(
                text(
                    """
                    INSERT INTO branch_reservations (
                        id, workspace_id, project_id, branch_id, mutation_id,
                        expected_revision, reserved_revision, status
                    )
                    VALUES (:id, :w, :p, :b, :m, :expected, :reserved, 'active')
                    """
                ),
                {
                    "id": format_canon_id("revision", str(uuid7())),
                    "w": envelope.workspace_id,
                    "p": envelope.project_id,
                    "b": envelope.branch_id,
                    "m": envelope.mutation_id,
                    "expected": envelope.expected_head,
                    "reserved": reserved,
                },
            )
            await session.execute(
                text(
                    """
                    INSERT INTO commit_envelopes (
                        commit_id, mutation_id, workspace_id, project_id, branch_id,
                        revision, expected_head, schema_version_id, canonical, checksum,
                        actor_user_id
                    )
                    VALUES (:id, :m, :w, :p, :b, :rev, :expected, :schema,
                            :canonical, :checksum, :actor)
                    """
                ),
                {
                    "id": envelope.commit_id,
                    "m": envelope.mutation_id,
                    "w": envelope.workspace_id,
                    "p": envelope.project_id,
                    "b": envelope.branch_id,
                    "rev": reserved,
                    "expected": envelope.expected_head,
                    "schema": envelope.schema_version_id,
                    "canonical": envelope.canonical,
                    "checksum": envelope.checksum,
                    "actor": envelope.actor_user_id,
                },
            )

            provenance_id = f"prov_{envelope.commit_id}"
            await session.execute(
                text(
                    """
                    INSERT INTO canon_provenance (
                        id, workspace_id, project_id, branch_id, origin, actor_user_id
                    )
                    VALUES (:id, :w, :p, :b, :origin, :actor)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "id": provenance_id,
                    "w": envelope.workspace_id,
                    "p": envelope.project_id,
                    "b": envelope.branch_id,
                    "origin": origin,
                    "actor": envelope.actor_user_id,
                },
            )

            for operation in operations:
                await self._apply(session, envelope, operation, provenance_id, reserved)

            result = await session.execute(
                text(
                    "UPDATE branches SET head_revision = :r "
                    "WHERE workspace_id = :w AND project_id = :p AND id = :b "
                    "AND head_revision = :expected"
                ),
                {
                    "r": reserved,
                    "w": envelope.workspace_id,
                    "p": envelope.project_id,
                    "b": envelope.branch_id,
                    "expected": envelope.expected_head,
                },
            )
            if result.rowcount == 0:  # type: ignore[attr-defined]
                raise HeadConflict("branch head changed during commit")
            await session.execute(
                text(
                    "UPDATE branch_reservations SET status = 'finalized' "
                    "WHERE branch_id = :b AND status = 'active'"
                ),
                {"b": envelope.branch_id},
            )
            await session.execute(
                text(
                    """
                    INSERT INTO canon_revisions (
                        id, revision, workspace_id, project_id, branch_id, commit_id,
                        expected_head, result_head, schema_version_id, envelope_checksum
                    )
                    VALUES (:id, :rev, :w, :p, :b, :c, :expected, :result, :schema, :checksum)
                    """
                ),
                {
                    "id": format_canon_id("revision", str(uuid7())),
                    "rev": reserved,
                    "w": envelope.workspace_id,
                    "p": envelope.project_id,
                    "b": envelope.branch_id,
                    "c": envelope.commit_id,
                    "expected": envelope.expected_head,
                    "result": reserved,
                    "schema": envelope.schema_version_id,
                    "checksum": envelope.checksum,
                },
            )
            await session.execute(
                text(
                    """
                    INSERT INTO outbox_messages (
                        id, topic, workspace_id, project_id, branch_id, revision,
                        payload, idempotency_key
                    )
                    VALUES (:id, 'canon.commit', :w, :p, :b, :rev,
                            CAST(:payload AS JSONB), :key)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    """
                ),
                {
                    "id": format_canon_id("operation", str(uuid7())),
                    "w": envelope.workspace_id,
                    "p": envelope.project_id,
                    "b": envelope.branch_id,
                    "rev": reserved,
                    "payload": json.dumps({"commit_id": envelope.commit_id, "revision": reserved}),
                    "key": f"commit:{envelope.commit_id}",
                },
            )
        return reserved

    async def _record_version(
        self,
        session: Any,
        envelope: Any,
        operation: CanonOperation,
        revision: int,
    ) -> None:
        await session.execute(
            text(
                """
                INSERT INTO canon_record_versions (
                    workspace_id, project_id, branch_id, record_type, record_id,
                    revision, operation_type, commit_id, change
                )
                VALUES (:w, :p, :b, :rtype, :rid, :rev, :op, :c, CAST(:change AS JSONB))
                """
            ),
            {
                "w": envelope.workspace_id,
                "p": envelope.project_id,
                "b": envelope.branch_id,
                "rtype": _RECORD_TYPE.get(operation.op, operation.op),
                "rid": operation.id,
                "rev": revision,
                "op": operation.op,
                "c": envelope.commit_id,
                "change": json.dumps(operation.model_dump(mode="json")),
            },
        )

    async def _apply(
        self,
        session: Any,
        envelope: Any,
        operation: CanonOperation,
        provenance_id: str,
        revision: int,
    ) -> None:
        scope = {
            "w": envelope.workspace_id,
            "p": envelope.project_id,
            "b": envelope.branch_id,
        }
        if isinstance(operation, CreateEntityOp):
            await session.execute(
                text(
                    """
                    INSERT INTO canon_entities (
                        workspace_id, project_id, branch_id, id, type, name, slug,
                        lifecycle, schema_version_id, created_revision, attributes
                    )
                    VALUES (:w, :p, :b, :id, :type, :name, :slug, 'active', :schema, :rev,
                            CAST(:attributes AS JSONB))
                    """
                ),
                {
                    **scope,
                    "id": operation.id,
                    "type": operation.type,
                    "name": operation.name,
                    "slug": operation.slug,
                    "schema": envelope.schema_version_id,
                    "rev": revision,
                    "attributes": json.dumps(operation.attributes),
                },
            )
        elif isinstance(operation, UpdateEntityOp):
            await self._update(
                session,
                "UPDATE canon_entities SET "
                "name = COALESCE(:name, name), slug = COALESCE(:slug, slug), "
                "attributes = COALESCE(CAST(:attributes AS JSONB), attributes) "
                "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b AND id = :id",
                {
                    **scope,
                    "id": operation.id,
                    "name": operation.name,
                    "slug": operation.slug,
                    "attributes": json.dumps(operation.attributes)
                    if operation.attributes is not None
                    else None,
                },
                operation.id,
            )
        elif isinstance(operation, RetireEntityOp):
            await self._update(
                session,
                "UPDATE canon_entities SET lifecycle = 'retired', retired_revision = :rev "
                "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b AND id = :id",
                {**scope, "id": operation.id, "rev": revision},
                operation.id,
            )
        elif isinstance(operation, AssertFactOp):
            await session.execute(
                text(
                    """
                    INSERT INTO canon_facts (
                        workspace_id, project_id, branch_id, id, proposition, status,
                        confidence, provenance_id, created_revision, valid_from, valid_until
                    )
                    VALUES (:w, :p, :b, :id, CAST(:proposition AS JSONB), 'asserted',
                            :confidence, :prov, :rev, :valid_from, :valid_until)
                    """
                ),
                {
                    **scope,
                    "id": operation.id,
                    "proposition": json.dumps(operation.proposition.model_dump(mode="json")),
                    "confidence": operation.confidence,
                    "prov": provenance_id,
                    "rev": revision,
                    "valid_from": operation.valid_from,
                    "valid_until": operation.valid_until,
                },
            )
        elif isinstance(operation, CloseFactOp):
            await self._update(
                session,
                "UPDATE canon_facts SET status = 'closed', closed_revision = :rev "
                "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b AND id = :id",
                {**scope, "id": operation.id, "rev": revision},
                operation.id,
            )
        elif isinstance(operation, AssertBeliefOp):
            await session.execute(
                text(
                    """
                    INSERT INTO canon_beliefs (
                        workspace_id, project_id, branch_id, id, holder_entity_id,
                        proposition, confidence, acquisition_provenance_id, source_ref,
                        created_revision
                    )
                    VALUES (:w, :p, :b, :id, :holder, CAST(:proposition AS JSONB),
                            :confidence, :prov, :source_ref, :rev)
                    """
                ),
                {
                    **scope,
                    "id": operation.id,
                    "holder": operation.holder_entity_id,
                    "proposition": json.dumps(operation.proposition.model_dump(mode="json")),
                    "confidence": operation.confidence,
                    "prov": provenance_id,
                    "source_ref": operation.source_ref,
                    "rev": revision,
                },
            )
        elif isinstance(operation, CloseBeliefOp):
            await self._update(
                session,
                "UPDATE canon_beliefs SET closed_revision = :rev "
                "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b AND id = :id",
                {**scope, "id": operation.id, "rev": revision},
                operation.id,
            )
        elif isinstance(operation, CreateEventOp):
            await session.execute(
                text(
                    """
                    INSERT INTO canon_events (
                        workspace_id, project_id, branch_id, id, kind, name, participants,
                        location_ids, cause_ids, outcome_ids, world_position, provenance_id,
                        created_revision
                    )
                    VALUES (:w, :p, :b, :id, :kind, :name, CAST(:participants AS JSONB),
                            CAST(:locations AS JSONB), CAST(:causes AS JSONB),
                            CAST(:outcomes AS JSONB), :world_position, :prov, :rev)
                    """
                ),
                {
                    **scope,
                    "id": operation.id,
                    "kind": operation.kind,
                    "name": operation.name,
                    "participants": json.dumps(
                        [p.model_dump(mode="json") for p in operation.participants]
                    ),
                    "locations": json.dumps(operation.location_ids),
                    "causes": json.dumps(operation.cause_ids),
                    "outcomes": json.dumps(operation.outcome_ids),
                    "world_position": operation.world_position,
                    "prov": provenance_id,
                    "rev": revision,
                },
            )
        elif isinstance(operation, AmendEventOp):
            await self._update(
                session,
                "UPDATE canon_events SET name = COALESCE(:name, name), "
                "world_position = COALESCE(:world_position, world_position) "
                "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b AND id = :id",
                {
                    **scope,
                    "id": operation.id,
                    "name": operation.name,
                    "world_position": operation.world_position,
                },
                operation.id,
            )
        elif isinstance(operation, RetireEventOp):
            await self._update(
                session,
                "UPDATE canon_events SET retired_revision = :rev "
                "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b AND id = :id",
                {**scope, "id": operation.id, "rev": revision},
                operation.id,
            )
        elif isinstance(operation, LinkAssetOp):
            await session.execute(
                text(
                    "INSERT INTO asset_links (workspace_id, project_id, branch_id, id, asset_id, "
                    "asset_version_id, role, target_record_type, target_record_id, provenance_id, "
                    "created_revision) VALUES (:w, :p, :b, :id, :asset_id, :asset_version_id, :role, "
                    ":target_record_type, :target_id, :prov, :rev)"
                ),
                {
                    **scope,
                    "id": operation.id,
                    "asset_id": operation.asset_id,
                    "asset_version_id": operation.asset_version_id,
                    "role": operation.role,
                    "target_record_type": operation.target_record_type,
                    "target_id": operation.target_id,
                    "prov": provenance_id,
                    "rev": revision,
                },
            )
        elif isinstance(operation, RetireAssetLinkOp):
            await self._update(
                session,
                "UPDATE asset_links SET retired_revision = :rev "
                "WHERE workspace_id = :w AND project_id = :p AND id = :id",
                {**scope, "id": operation.id, "rev": revision},
                operation.id,
            )
        await self._record_version(session, envelope, operation, revision)

    async def _update(
        self, session: Any, statement: str, params: dict[str, Any], record_id: str
    ) -> None:
        result = await session.execute(text(statement), params)
        if result.rowcount == 0:  # type: ignore[attr-defined]
            raise HeadConflict(f"canonical record {record_id!r} not found for update")

    # ------------------------------------------------------------------- reads
    async def get_head(self, *, workspace_id: str, project_id: str, branch_id: str) -> int:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT head_revision FROM branches "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :b"
                    ),
                    {"w": workspace_id, "p": project_id, "b": branch_id},
                )
            ).first()
        return int(row.head_revision) if row else 0

    async def get_entity(
        self, *, workspace_id: str, project_id: str, entity_id: str
    ) -> CanonEntity | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM canon_entities "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                    ),
                    {"w": workspace_id, "p": project_id, "id": entity_id},
                )
            ).first()
        return _entity(row) if row else None

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
        clauses = ["workspace_id = :w", "project_id = :p"]
        params: dict[str, Any] = {
            "w": workspace_id,
            "p": project_id,
            "limit": limit,
            "offset": offset,
        }
        if entity_type:
            clauses.append("type = :type")
            params["type"] = entity_type
        if not include_retired:
            clauses.append("lifecycle = 'active'")
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT * FROM canon_entities WHERE {' AND '.join(clauses)} "
                        "ORDER BY name LIMIT :limit OFFSET :offset"
                    ),
                    params,
                )
            ).all()
        return [_entity(row) for row in rows]

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
        clauses = ["workspace_id = :w", "project_id = :p", "lower(name) LIKE :needle"]
        params: dict[str, Any] = {
            "w": workspace_id,
            "p": project_id,
            "needle": f"%{query.lower()}%",
            "limit": limit,
        }
        if entity_type:
            clauses.append("type = :type")
            params["type"] = entity_type
        if not include_retired:
            clauses.append("lifecycle = 'active'")
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT * FROM canon_entities WHERE {' AND '.join(clauses)} "
                        "ORDER BY name LIMIT :limit"
                    ),
                    params,
                )
            ).all()
        return [_entity(row) for row in rows]

    async def get_fact(self, *, workspace_id: str, project_id: str, fact_id: str) -> Fact | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM canon_facts "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                    ),
                    {"w": workspace_id, "p": project_id, "id": fact_id},
                )
            ).first()
        return _fact(row) if row else None

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
        clauses = ["workspace_id = :w", "project_id = :p"]
        params: dict[str, Any] = {
            "w": workspace_id,
            "p": project_id,
            "limit": limit,
            "offset": offset,
        }
        if not include_closed:
            clauses.append("status = 'asserted'")
        if subject_id:
            clauses.append("proposition->>'subject_id' = :subject")
            params["subject"] = subject_id
        if predicate:
            clauses.append("proposition->>'predicate' = :predicate")
            params["predicate"] = predicate
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT * FROM canon_facts WHERE {' AND '.join(clauses)} "
                        "ORDER BY created_revision LIMIT :limit OFFSET :offset"
                    ),
                    params,
                )
            ).all()
        return [_fact(row) for row in rows]

    async def get_belief(
        self, *, workspace_id: str, project_id: str, belief_id: str
    ) -> Belief | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM canon_beliefs "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                    ),
                    {"w": workspace_id, "p": project_id, "id": belief_id},
                )
            ).first()
        return _belief(row) if row else None

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
        clauses = ["workspace_id = :w", "project_id = :p"]
        params: dict[str, Any] = {
            "w": workspace_id,
            "p": project_id,
            "limit": limit,
            "offset": offset,
        }
        if not include_closed:
            clauses.append("closed_revision IS NULL")
        if holder_entity_id:
            clauses.append("holder_entity_id = :holder")
            params["holder"] = holder_entity_id
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT * FROM canon_beliefs WHERE {' AND '.join(clauses)} "
                        "ORDER BY created_revision LIMIT :limit OFFSET :offset"
                    ),
                    params,
                )
            ).all()
        return [_belief(row) for row in rows]

    async def get_event(self, *, workspace_id: str, project_id: str, event_id: str) -> Event | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM canon_events "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                    ),
                    {"w": workspace_id, "p": project_id, "id": event_id},
                )
            ).first()
        return _event(row) if row else None

    async def list_events(
        self,
        *,
        workspace_id: str,
        project_id: str,
        include_retired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Event]:
        clauses = ["workspace_id = :w", "project_id = :p"]
        if not include_retired:
            clauses.append("retired_revision IS NULL")
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT * FROM canon_events WHERE {' AND '.join(clauses)} "
                        "ORDER BY created_revision LIMIT :limit OFFSET :offset"
                    ),
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "limit": limit,
                        "offset": offset,
                    },
                )
            ).all()
        return [_event(row) for row in rows]

    async def get_provenance(
        self, *, workspace_id: str, project_id: str, branch_id: str, provenance_id: str
    ) -> Provenance | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM canon_provenance WHERE workspace_id = :w "
                        "AND project_id = :p AND branch_id = :b AND id = :id"
                    ),
                    {"w": workspace_id, "p": project_id, "b": branch_id, "id": provenance_id},
                )
            ).first()
        if row is None:
            return None
        return Provenance(
            id=row.id,
            workspace_id=row.workspace_id,
            project_id=row.project_id,
            branch_id=row.branch_id,
            origin=OriginKind(row.origin),
            actor_user_id=row.actor_user_id,
            job_id=row.job_id,
            source_ref=row.source_ref,
            note=row.note,
            created_at=row.created_at,
        )

    async def list_record_versions(
        self,
        *,
        workspace_id: str,
        project_id: str,
        record_type: str | None = None,
        record_id: str | None = None,
        min_revision: int | None = None,
        max_revision: int | None = None,
        limit: int = 100,
    ) -> list[RecordVersion]:
        clauses = ["workspace_id = :w", "project_id = :p"]
        params: dict[str, Any] = {"w": workspace_id, "p": project_id, "limit": limit}
        if record_type:
            clauses.append("record_type = :rtype")
            params["rtype"] = record_type
        if record_id:
            clauses.append("record_id = :rid")
            params["rid"] = record_id
        if min_revision is not None:
            clauses.append("revision > :minrev")
            params["minrev"] = min_revision
        if max_revision is not None:
            clauses.append("revision <= :maxrev")
            params["maxrev"] = max_revision
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT * FROM canon_record_versions WHERE {' AND '.join(clauses)} "
                        "ORDER BY revision DESC, created_at DESC LIMIT :limit"
                    ),
                    params,
                )
            ).all()
        return [
            RecordVersion(
                workspace_id=row.workspace_id,
                project_id=row.project_id,
                branch_id=row.branch_id,
                record_type=row.record_type,
                record_id=row.record_id,
                revision=row.revision,
                operation_type=row.operation_type,
                commit_id=row.commit_id,
                change=dict(_load(row.change) or {}),
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def get_envelope(self, commit_id: str) -> CommitEnvelope | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT commit_id, mutation_id, workspace_id, project_id, branch_id, "
                        "revision, expected_head, schema_version_id, canonical, checksum, "
                        "actor_user_id, created_at FROM commit_envelopes WHERE commit_id = :c"
                    ),
                    {"c": commit_id},
                )
            ).first()
        if row is None:
            return None
        return CommitEnvelope(
            commit_id=row.commit_id,
            mutation_id=row.mutation_id,
            workspace_id=row.workspace_id,
            project_id=row.project_id,
            branch_id=row.branch_id,
            revision=int(row.revision),
            expected_head=int(row.expected_head),
            schema_version_id=row.schema_version_id,
            canonical=row.canonical,
            checksum=row.checksum,
            actor_user_id=row.actor_user_id,
            created_at=row.created_at,
        )

    async def list_commits(
        self,
        *,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        after_revision: int = 0,
        limit: int = 100,
    ) -> list[CanonRevision]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT revision, workspace_id, project_id, branch_id, commit_id, "
                        "expected_head, result_head, schema_version_id, envelope_checksum, created_at "
                        "FROM canon_revisions WHERE workspace_id = :w AND project_id = :p "
                        "AND branch_id = :b AND revision > :after "
                        "ORDER BY revision ASC LIMIT :limit"
                    ),
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "b": branch_id,
                        "after": after_revision,
                        "limit": limit,
                    },
                )
            ).fetchall()
        return [
            CanonRevision(
                revision=int(row.revision),
                workspace_id=row.workspace_id,
                project_id=row.project_id,
                branch_id=row.branch_id,
                commit_id=row.commit_id,
                expected_head=int(row.expected_head),
                result_head=int(row.result_head),
                schema_version_id=row.schema_version_id,
                envelope_checksum=row.envelope_checksum,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def get_watermark(
        self, *, projection: str, workspace_id: str, project_id: str, branch_id: str
    ) -> ProjectionWatermark | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM projection_watermarks WHERE projection = :proj "
                        "AND workspace_id = :w AND project_id = :p AND branch_id = :b"
                    ),
                    {"proj": projection, "w": workspace_id, "p": project_id, "b": branch_id},
                )
            ).first()
        if row is None:
            return None
        return ProjectionWatermark(
            projection=row.projection,
            workspace_id=row.workspace_id,
            project_id=row.project_id,
            branch_id=row.branch_id,
            revision=row.revision,
            commit_id=row.commit_id,
            envelope_checksum=row.envelope_checksum,
            projection_schema_version=row.projection_schema_version,
            status=ProjectionStatus(row.status),
            last_error=row.last_error,
            updated_at=row.updated_at,
        )

    async def set_watermark(self, watermark: ProjectionWatermark) -> None:
        async with session_scope(self._session_factory) as session:
            await session.execute(
                text(
                    """
                    INSERT INTO projection_watermarks (
                        projection, workspace_id, project_id, branch_id, revision,
                        commit_id, envelope_checksum, projection_schema_version,
                        status, last_error, updated_at
                    )
                    VALUES (:proj, :w, :p, :b, :rev, :c, :checksum, :schema, :status,
                            :error, now())
                    ON CONFLICT (projection, workspace_id, project_id, branch_id)
                    DO UPDATE SET
                        revision = EXCLUDED.revision,
                        commit_id = EXCLUDED.commit_id,
                        envelope_checksum = EXCLUDED.envelope_checksum,
                        projection_schema_version = EXCLUDED.projection_schema_version,
                        status = EXCLUDED.status,
                        last_error = EXCLUDED.last_error,
                        updated_at = now()
                    """
                ),
                {
                    "proj": watermark.projection,
                    "w": watermark.workspace_id,
                    "p": watermark.project_id,
                    "b": watermark.branch_id,
                    "rev": watermark.revision,
                    "c": watermark.commit_id,
                    "checksum": watermark.envelope_checksum,
                    "schema": watermark.projection_schema_version,
                    "status": watermark.status.value,
                    "error": watermark.last_error,
                },
            )

    async def create_tag(self, tag: CanonTag) -> CanonTag:
        async with session_scope(self._session_factory) as session:
            existing = (
                await session.execute(
                    text(
                        "SELECT revision, commit_id FROM canon_tags "
                        "WHERE workspace_id=:w AND project_id=:p AND branch_id=:b AND name=:n"
                    ),
                    {"w": tag.workspace_id, "p": tag.project_id, "b": tag.branch_id, "n": tag.name},
                )
            ).first()
            if existing is not None:
                await session.execute(
                    text(
                        "INSERT INTO canon_tag_history (workspace_id, project_id, branch_id, "
                        "name, revision, commit_id) VALUES (:w, :p, :b, :n, :rev, :c)"
                    ),
                    {
                        "w": tag.workspace_id,
                        "p": tag.project_id,
                        "b": tag.branch_id,
                        "n": tag.name,
                        "rev": existing.revision,
                        "c": existing.commit_id,
                    },
                )
            await session.execute(
                text(
                    """
                    INSERT INTO canon_tags (
                        workspace_id, project_id, branch_id, name, revision, commit_id,
                        created_by_user_id, created_at, updated_at
                    )
                    VALUES (:w, :p, :b, :n, :rev, :c, :actor, now(), now())
                    ON CONFLICT (workspace_id, project_id, branch_id, name)
                    DO UPDATE SET
                        revision = EXCLUDED.revision,
                        commit_id = EXCLUDED.commit_id,
                        created_by_user_id = EXCLUDED.created_by_user_id,
                        updated_at = now()
                    """
                ),
                {
                    "w": tag.workspace_id,
                    "p": tag.project_id,
                    "b": tag.branch_id,
                    "n": tag.name,
                    "rev": tag.revision,
                    "c": tag.commit_id,
                    "actor": tag.created_by_user_id,
                },
            )
        stored = await self.get_tag(
            workspace_id=tag.workspace_id,
            project_id=tag.project_id,
            branch_id=tag.branch_id,
            name=tag.name,
        )
        if stored is None:  # pragma: no cover - defensive
            raise CanonStorageError("tag was not persisted")
        return stored

    async def get_tag(
        self, *, workspace_id: str, project_id: str, branch_id: str, name: str
    ) -> CanonTag | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM canon_tags WHERE workspace_id=:w AND project_id=:p "
                        "AND branch_id=:b AND name=:n"
                    ),
                    {"w": workspace_id, "p": project_id, "b": branch_id, "n": name},
                )
            ).first()
        return _to_tag(row) if row else None

    async def list_tags(
        self, *, workspace_id: str, project_id: str, branch_id: str
    ) -> list[CanonTag]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM canon_tags WHERE workspace_id=:w AND project_id=:p "
                        "AND branch_id=:b ORDER BY name"
                    ),
                    {"w": workspace_id, "p": project_id, "b": branch_id},
                )
            ).fetchall()
        return [_to_tag(row) for row in rows]

    async def list_tag_history(
        self, *, workspace_id: str, project_id: str, branch_id: str, name: str
    ) -> list[CanonTagHistoryEntry]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT workspace_id, project_id, branch_id, name, revision, commit_id, "
                        "replaced_at FROM canon_tag_history WHERE workspace_id=:w AND project_id=:p "
                        "AND branch_id=:b AND name=:n ORDER BY replaced_at DESC"
                    ),
                    {"w": workspace_id, "p": project_id, "b": branch_id, "n": name},
                )
            ).fetchall()
        return [
            CanonTagHistoryEntry(
                workspace_id=row.workspace_id,
                project_id=row.project_id,
                branch_id=row.branch_id,
                name=row.name,
                revision=row.revision,
                commit_id=row.commit_id,
                replaced_at=row.replaced_at,
            )
            for row in rows
        ]

    async def delete_tag(
        self, *, workspace_id: str, project_id: str, branch_id: str, name: str
    ) -> bool:
        async with session_scope(self._session_factory) as session:
            result = await session.execute(
                text(
                    "DELETE FROM canon_tags WHERE workspace_id=:w AND project_id=:p "
                    "AND branch_id=:b AND name=:n"
                ),
                {"w": workspace_id, "p": project_id, "b": branch_id, "n": name},
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def get_link(
        self, *, workspace_id: str, project_id: str, link_id: str
    ) -> AssetLink | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM asset_links "
                        "WHERE workspace_id=:w AND project_id=:p AND id=:id"
                    ),
                    {"w": workspace_id, "p": project_id, "id": link_id},
                )
            ).first()
        return _link(row) if row else None

    async def list_links(
        self,
        *,
        workspace_id: str,
        project_id: str,
        asset_id: str | None = None,
        branch_id: str | None = None,
        include_retired: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AssetLink]:
        clauses = ["workspace_id=:w", "project_id=:p"]
        params: dict[str, Any] = {"w": workspace_id, "p": project_id}
        if asset_id is not None:
            clauses.append("asset_id=:a")
            params["a"] = asset_id
        if branch_id is not None:
            clauses.append("branch_id=:b")
            params["b"] = branch_id
        if not include_retired:
            clauses.append("retired_revision IS NULL")
        params["limit"] = limit
        params["offset"] = offset
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM asset_links WHERE "
                        + " AND ".join(clauses)
                        + " ORDER BY created_revision DESC, id LIMIT :limit OFFSET :offset"
                    ),
                    params,
                )
            ).all()
        return [_link(row) for row in rows]

    async def health(self) -> bool:
        try:
            async with session_scope(self._session_factory) as session:
                await session.execute(text("SELECT 1 FROM canon_entities LIMIT 1"))
            return True
        except Exception:
            return False
