"""Canonical PostgreSQL canon repository (ADR-012).

One transaction applies a validated commit envelope to normalized/versioned
canonical tables together with the branch head, canonical revision, and outbox.
Neo4j is not involved: application success is decided entirely here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text

from backend.adapters.canon_tables import CANON_PROJECT_TABLES
from backend.adapters.journal import apply_transition
from backend.adapters.system import uuid7
from backend.core.database import session_scope
from backend.domain.archive import ArchiveImport, ImportStatus
from backend.domain.branching import working_branch_id
from backend.domain.canon.assertions import Belief, Fact, FactStatus, FactVisibility
from backend.domain.canon.entities import CanonEntity, EntityLifecycle
from backend.domain.canon.events import Event, EventParticipant
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.naming import normalize_name
from backend.domain.canon.plans import (
    PlanItem,
    PlanKind,
    PlanRef,
    apply_plan_update,
    plan_item_from_create,
)
from backend.domain.canon.propositions import Proposition
from backend.domain.canon.registry import CORE_ENTITY_TYPES
from backend.domain.canon.revisions import CanonRevision, OriginKind, Provenance
from backend.domain.history import CanonTag, CanonTagHistoryEntry
from backend.domain.journal import (
    CommitEnvelope,
    HeadConflict,
    MutationOperation,
    MutationStatus,
    OperationDecision,
    RevisionConflict,
)
from backend.domain.media import AssetLifecycle, AssetLink, LinkRole, MediaAsset, MediaAssetVersion
from backend.domain.operations import (
    AmendEventOp,
    AmendFactOp,
    AssertBeliefOp,
    AssertFactOp,
    CanonOperation,
    CloseBeliefOp,
    CloseFactOp,
    CreateEntityOp,
    CreateEventOp,
    CreatePlanItemOp,
    LinkAssetOp,
    RetireAssetLinkOp,
    RetireEntityOp,
    RetireEventOp,
    RetirePlanItemOp,
    UpdateEntityOp,
    UpdatePlanItemOp,
    canonicalize_operations,
    operations_checksum,
    parse_operation,
    resolve_temporary_ids,
)
from backend.domain.projection import (
    CanonStorageError,
    EvidenceRecord,
    ProjectionStatus,
    ProjectionWatermark,
    RecordVersion,
)
from backend.domain.review import MutationIntent, effective_operations, review_gate
from backend.domain.validation import (
    AuthoritativeValidator,
    CanonValidationError,
    DomainValidationReport,
    ValidationContext,
    ValidationFinding,
    ValidationSeverity,
)
from backend.domain.worldtime import Unknown, parse_world_time
from backend.services.schema_compiler import compile_definition

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
    "amend_fact": "fact",
    "assert_belief": "belief",
    "close_belief": "belief",
    "create_event": "event",
    "amend_event": "event",
    "retire_event": "event",
    "link_asset": "asset_link",
    "retire_asset_link": "asset_link",
    "create_plan_item": "plan_item",
    "update_plan_item": "plan_item",
    "retire_plan_item": "plan_item",
}


# Tables an import may have found already populated; removal deletes only the
# specific rows the import created in them.
_IMPORT_PRESERVED_TABLES = frozenset(
    {
        "schema_versions",
        "media_assets",
        "media_asset_versions",
        "media_lifecycle_events",
        "archive_imports",
    }
)


def _time_json(value: Any, *, clear_unknown: bool = False) -> str | None:
    """A story time as stored JSON. An unknown time with no words of its own is no time."""
    if value is None:
        return None
    if clear_unknown and isinstance(value, Unknown) and not value.label:
        return None
    return json.dumps(value.model_dump(mode="json"))


def _load(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _archive_import(row: Any) -> ArchiveImport:
    return ArchiveImport(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        archive_checksum=row.archive_checksum,
        format_version=row.format_version,
        status=ImportStatus(row.status),
        stage=row.stage,
        commits_total=row.commits_total,
        commits_applied=row.commits_applied,
        objects_imported=row.objects_imported,
        object_keys=list(_load(row.object_keys) or []),
        created_records=dict(_load(row.created_records) or {}),
        error=row.error,
        actor_user_id=row.actor_user_id,
        started_at=row.started_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


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
        aliases=list(_load(row.aliases) or []),
        external_ids=dict(_load(row.external_ids) or {}),
    )


# The story records a what-if starts with: everything one line of the story holds.
# Named versions come too, since they point at versions the what-if shares.
_FORKED_TABLES = (
    "canon_entities",
    "canon_entity_external_ids",
    "canon_facts",
    "canon_beliefs",
    "canon_events",
    "plan_items",
    "asset_links",
    "canon_provenance",
    "canon_evidence",
    "canon_record_versions",
    "canon_tags",
    # Which text holds each scene's prose; the text itself is copied on first change.
    "scene_manuscripts",
    # What was last read from each scene, so a what-if does not propose it again.
    "scene_readings",
    # Findings the author dismissed in a scene, so a what-if starts with the same decisions.
    "finding_dismissals",
)

# A story read: the what-if this request works in, or the main story when none is named.
_ON_BRANCH = (
    "branch_id = COALESCE(CAST(:wb AS TEXT), (SELECT id FROM branches "
    "WHERE workspace_id = :w AND project_id = :p AND is_root))"
)

_ACTIVE_REFERENCES = """
SELECT 'fact' AS record_type, id FROM canon_facts
  WHERE workspace_id = :w AND project_id = :p AND status = 'asserted' AND {branch}
    AND (proposition->>'subject_id' = :id
         OR (proposition->'object'->>'kind' = 'entity_ref'
             AND proposition->'object'->>'entity_id' = :id))
UNION ALL
SELECT 'belief', id FROM canon_beliefs
  WHERE workspace_id = :w AND project_id = :p AND closed_revision IS NULL AND {branch}
    AND (holder_entity_id = :id OR proposition->>'subject_id' = :id
         OR (proposition->'object'->>'kind' = 'entity_ref'
             AND proposition->'object'->>'entity_id' = :id))
UNION ALL
SELECT 'event', id FROM canon_events
  WHERE workspace_id = :w AND project_id = :p AND retired_revision IS NULL AND {branch}
    AND (participants @> CAST(:participant AS JSONB)
         OR location_ids @> CAST(:member AS JSONB)
         OR cause_ids @> CAST(:member AS JSONB)
         OR outcome_ids @> CAST(:member AS JSONB))
UNION ALL
SELECT 'asset_link', id FROM asset_links
  WHERE workspace_id = :w AND project_id = :p AND retired_revision IS NULL AND {branch}
    AND target_record_id = :id
ORDER BY 1, 2
""".replace("{branch}", _ON_BRANCH)


def _reference_params(
    workspace_id: str, project_id: str, entity_id: str, branch_id: str | None = None
) -> dict[str, Any]:
    return {
        "w": workspace_id,
        "p": project_id,
        "wb": branch_id or working_branch_id(),
        "id": entity_id,
        "participant": json.dumps([{"entity_id": entity_id}]),
        "member": json.dumps([entity_id]),
    }


async def _entity_by_slug(
    session: Any, workspace_id: str, project_id: str, slug: str, branch_id: str | None = None
) -> CanonEntity | None:
    row = (
        await session.execute(
            text(
                "SELECT * FROM canon_entities WHERE workspace_id = :w AND project_id = :p "
                "AND slug = :slug AND lifecycle = 'active' AND " + _ON_BRANCH
            ),
            {
                "w": workspace_id,
                "p": project_id,
                "slug": slug,
                "wb": branch_id or working_branch_id(),
            },
        )
    ).first()
    return _entity(row) if row else None


async def _entity_by_external_id(
    session: Any,
    workspace_id: str,
    project_id: str,
    namespace: str,
    value: str,
    branch_id: str | None = None,
) -> CanonEntity | None:
    row = (
        await session.execute(
            text(
                "SELECT e.* FROM canon_entity_external_ids x JOIN canon_entities e "
                "ON e.workspace_id = x.workspace_id AND e.project_id = x.project_id "
                "AND e.branch_id = x.branch_id AND e.id = x.entity_id "
                "WHERE x.workspace_id = :w AND x.project_id = :p "
                "AND x.namespace = :ns AND x.value = :value AND "
                + _ON_BRANCH.replace("branch_id", "x.branch_id", 1)
            ),
            {
                "w": workspace_id,
                "p": project_id,
                "ns": namespace,
                "value": value,
                "wb": branch_id or working_branch_id(),
            },
        )
    ).first()
    return _entity(row) if row else None


async def _entities_by_name(
    session: Any,
    workspace_id: str,
    project_id: str,
    entity_type: str,
    name: str,
    branch_id: str | None = None,
) -> list[CanonEntity]:
    rows = (
        await session.execute(
            text(
                "SELECT * FROM canon_entities WHERE workspace_id = :w AND project_id = :p "
                "AND type = :type AND lifecycle = 'active' AND " + _ON_BRANCH + " "
                "AND lower(regexp_replace(btrim(name), '\\s+', ' ', 'g')) = lower(:name)"
            ),
            {
                "w": workspace_id,
                "p": project_id,
                "wb": branch_id or working_branch_id(),
                "type": entity_type,
                "name": " ".join(name.split()),
            },
        )
    ).all()
    # SQL narrows by whitespace and simple case; the policy's normalization decides.
    key = normalize_name(name)
    return [entity for entity in map(_entity, rows) if normalize_name(entity.name) == key]


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
        starts=parse_world_time(_load(getattr(row, "starts", None))),
        ends=parse_world_time(_load(getattr(row, "ends", None))),
        visibility=FactVisibility(getattr(row, "visibility", None) or "ordinary"),
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
        when=parse_world_time(_load(getattr(row, "world_time", None))),
        provenance_id=row.provenance_id,
        created_revision=row.created_revision,
        retired_revision=getattr(row, "retired_revision", None),
    )


def _plan_item(row: Any) -> PlanItem:
    return PlanItem(
        id=row.id,
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        branch_id=row.branch_id,
        kind=PlanKind(row.kind),
        title=row.title,
        summary=row.summary or "",
        parent_id=row.parent_id,
        position=row.position,
        refs=[PlanRef.model_validate(ref) for ref in (_load(row.refs) or [])],
        sets_up=list(_load(row.sets_up) or []),
        pays_off=list(_load(row.pays_off) or []),
        details=dict(_load(row.details) or {}),
        provenance_id=row.provenance_id,
        created_revision=row.created_revision,
        retired_revision=row.retired_revision,
    )


def _plan_params(item: PlanItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "kind": item.kind.value,
        "title": item.title,
        "summary": item.summary,
        "parent": item.parent_id,
        "position": item.position,
        "refs": json.dumps([ref.model_dump(mode="json") for ref in item.refs]),
        "sets_up": json.dumps(item.sets_up),
        "pays_off": json.dumps(item.pays_off),
        "details": json.dumps(item.details),
    }


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

    async def open_facts(self, subject_id: str, predicate: str) -> list[Fact]:
        rows = (
            await self._session.execute(
                text(
                    "SELECT * FROM canon_facts "
                    "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b "
                    "AND status = 'asserted' "
                    "AND proposition->>'subject_id' = :s AND proposition->>'predicate' = :pr "
                    "ORDER BY id"
                ),
                {
                    "w": self._workspace_id,
                    "p": self._project_id,
                    "b": self._branch_id,
                    "s": subject_id,
                    "pr": predicate,
                },
            )
        ).all()
        return [_fact(row) for row in rows]

    async def get_fact(self, fact_id: str) -> Fact | None:
        row = (
            await self._session.execute(
                text(
                    "SELECT * FROM canon_facts "
                    "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b AND id = :id"
                ),
                {
                    "w": self._workspace_id,
                    "p": self._project_id,
                    "b": self._branch_id,
                    "id": fact_id,
                },
            )
        ).first()
        return _fact(row) if row else None

    async def get_belief(self, belief_id: str) -> Belief | None:
        row = (
            await self._session.execute(
                text(
                    "SELECT * FROM canon_beliefs "
                    "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b AND id = :id"
                ),
                {
                    "w": self._workspace_id,
                    "p": self._project_id,
                    "b": self._branch_id,
                    "id": belief_id,
                },
            )
        ).first()
        return _belief(row) if row else None

    async def get_event(self, event_id: str) -> Event | None:
        row = (
            await self._session.execute(
                text(
                    "SELECT * FROM canon_events "
                    "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b AND id = :id"
                ),
                {
                    "w": self._workspace_id,
                    "p": self._project_id,
                    "b": self._branch_id,
                    "id": event_id,
                },
            )
        ).first()
        return _event(row) if row else None

    async def get_plan_item(self, plan_id: str) -> PlanItem | None:
        row = (
            await self._session.execute(
                text(
                    "SELECT * FROM plan_items "
                    "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b AND id = :id"
                ),
                {
                    "w": self._workspace_id,
                    "p": self._project_id,
                    "b": self._branch_id,
                    "id": plan_id,
                },
            )
        ).first()
        return _plan_item(row) if row else None

    async def plan_child_ids(self, plan_id: str) -> list[str]:
        rows = (
            await self._session.execute(
                text(
                    "SELECT id FROM plan_items WHERE workspace_id = :w AND project_id = :p "
                    "AND branch_id = :b AND parent_id = :id AND retired_revision IS NULL"
                ),
                {
                    "w": self._workspace_id,
                    "p": self._project_id,
                    "b": self._branch_id,
                    "id": plan_id,
                },
            )
        ).all()
        return [row.id for row in rows]

    async def get_link(self, link_id: str) -> AssetLink | None:
        row = (
            await self._session.execute(
                text(
                    "SELECT * FROM asset_links "
                    "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b AND id = :id"
                ),
                {
                    "w": self._workspace_id,
                    "p": self._project_id,
                    "b": self._branch_id,
                    "id": link_id,
                },
            )
        ).first()
        return _link(row) if row else None

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

    async def find_entity_by_slug(self, slug: str) -> CanonEntity | None:
        return await _entity_by_slug(
            self._session, self._workspace_id, self._project_id, slug, self._branch_id
        )

    async def find_entity_by_external_id(self, namespace: str, value: str) -> CanonEntity | None:
        return await _entity_by_external_id(
            self._session, self._workspace_id, self._project_id, namespace, value, self._branch_id
        )

    async def find_entities_by_name(self, entity_type: str, name: str) -> list[CanonEntity]:
        return await _entities_by_name(
            self._session, self._workspace_id, self._project_id, entity_type, name, self._branch_id
        )

    async def count_active_references(self, entity_id: str) -> int:
        rows = (
            await self._session.execute(
                text(_ACTIVE_REFERENCES),
                _reference_params(self._workspace_id, self._project_id, entity_id, self._branch_id),
            )
        ).all()
        return len(rows)

    async def active_entities(self, limit: int) -> list[CanonEntity]:
        rows = (
            await self._session.execute(
                text(
                    "SELECT * FROM canon_entities WHERE workspace_id = :w AND project_id = :p "
                    "AND branch_id = :b AND lifecycle = 'active' ORDER BY id LIMIT :limit"
                ),
                {
                    "w": self._workspace_id,
                    "p": self._project_id,
                    "b": self._branch_id,
                    "limit": limit,
                },
            )
        ).all()
        return [_entity(row) for row in rows]

    async def asserted_facts(self, limit: int) -> list[Fact]:
        rows = (
            await self._session.execute(
                text(
                    "SELECT * FROM canon_facts WHERE workspace_id = :w AND project_id = :p "
                    "AND branch_id = :b AND status = 'asserted' ORDER BY id LIMIT :limit"
                ),
                {
                    "w": self._workspace_id,
                    "p": self._project_id,
                    "b": self._branch_id,
                    "limit": limit,
                },
            )
        ).all()
        return [_fact(row) for row in rows]


class PostgresCanonRepository:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------------ writes
    async def apply_commit(
        self,
        *,
        envelope: Any,
        operations: Sequence[CanonOperation],
        validator: Any = None,
        origin: str = OriginKind.AUTHOR.value,
    ) -> int:
        validator = validator or _DEFAULT_VALIDATOR
        async with session_scope(self._session_factory) as session:

            async def already_committed() -> int | None:
                """The revision this commit id already produced, if it has been applied."""
                existing = (
                    await session.execute(
                        text(
                            "SELECT revision, checksum FROM commit_envelopes WHERE commit_id = :c"
                        ),
                        {"c": envelope.commit_id},
                    )
                ).first()
                if existing is None:
                    return None
                if existing.checksum != envelope.checksum:
                    raise RevisionConflict("commit id reused with a different checksum")
                return int(existing.revision)

            revision = await already_committed()
            if revision is not None:
                return revision

            if (
                canonicalize_operations(operations) != envelope.canonical
                or operations_checksum(operations) != envelope.checksum
            ):
                raise RevisionConflict("commit envelope does not match supplied operations")

            mutation = (
                await session.execute(
                    text("SELECT * FROM mutation_sets WHERE id = :id FOR UPDATE"),
                    {"id": envelope.mutation_id},
                )
            ).first()
            # A concurrent retry of this same commit may have finished while this
            # transaction waited for the lock; it must see that result, not re-run.
            revision = await already_committed()
            if revision is not None:
                return revision
            effective_origin = origin
            intent = MutationIntent.EDIT.value
            status = MutationStatus.DRAFT
            if mutation is not None:
                if (
                    mutation.workspace_id != envelope.workspace_id
                    or mutation.project_id != envelope.project_id
                    or mutation.branch_id != envelope.branch_id
                    or mutation.expected_head != envelope.expected_head
                    or mutation.schema_version_id != envelope.schema_version_id
                ):
                    raise RevisionConflict("commit envelope does not match its mutation set")
                stored_rows = (
                    await session.execute(
                        text(
                            "SELECT ordinal, operation_type, payload, revision "
                            "FROM mutation_operations WHERE mutation_id = :m ORDER BY ordinal"
                        ),
                        {"m": envelope.mutation_id},
                    )
                ).all()
                decision_rows = (
                    await session.execute(
                        text(
                            "SELECT * FROM operation_decisions WHERE mutation_id = :m ORDER BY seq"
                        ),
                        {"m": envelope.mutation_id},
                    )
                ).all()
                # Only the reviewed, non-rejected current operation revisions may commit.
                accepted = effective_operations(
                    [
                        MutationOperation(
                            ordinal=row.ordinal,
                            operation_type=row.operation_type,
                            payload=dict(_load(row.payload)),
                            revision=row.revision,
                        )
                        for row in stored_rows
                    ],
                    [
                        OperationDecision(
                            id=row.id,
                            mutation_id=row.mutation_id,
                            ordinal=row.ordinal,
                            operation_revision=row.operation_revision,
                            decision=row.decision,
                            actor_user_id=row.actor_user_id,
                            note=row.note,
                            created_at=row.created_at,
                        )
                        for row in decision_rows
                    ],
                )
                resolved_stored, _ = resolve_temporary_ids(
                    [parse_operation(item.payload) for item in accepted],
                    new_id=lambda kind, temporary_id: format_canon_id(
                        kind,
                        hashlib.sha256(
                            f"{envelope.mutation_id}:{temporary_id}".encode()
                        ).hexdigest()[:16],
                    ),
                )
                if canonicalize_operations(resolved_stored) != envelope.canonical:
                    raise RevisionConflict("commit operations do not match the reviewed mutation")
                effective_origin = mutation.origin
                intent = mutation.intent
                status = MutationStatus(mutation.status)

            # The review gate is evaluated here, inside the commit transaction, so no
            # service, import, or direct repository caller can skip a required approval.
            gate = review_gate(
                origin=effective_origin,
                intent=intent,
                status=status,
                operation_types=[operation.op for operation in operations],
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
            # Same check once the head is locked, for writers with no mutation row.
            revision = await already_committed()
            if revision is not None:
                return revision
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
            compiled = None
            if envelope.schema_version_id is not None:
                schema_row = (
                    await session.execute(
                        text(
                            "SELECT definition FROM schema_versions "
                            "WHERE workspace_id = :w AND project_id = :p AND id = :id "
                            "AND status = 'published'"
                        ),
                        {
                            "w": envelope.workspace_id,
                            "p": envelope.project_id,
                            "id": envelope.schema_version_id,
                        },
                    )
                ).first()
                if schema_row is None:
                    raise CanonValidationError(
                        DomainValidationReport(
                            findings=(
                                ValidationFinding(
                                    ValidationSeverity.INTEGRITY,
                                    "invalid_schema_version",
                                    "commit schema version is missing, outside this project, or unpublished",
                                ),
                            )
                        )
                    )
                compiled = compile_definition(
                    _load(schema_row.definition),
                    workspace_id=envelope.workspace_id,
                    project_id=envelope.project_id,
                )
            context = ValidationContext(
                workspace_id=envelope.workspace_id,
                project_id=envelope.project_id,
                branch_id=envelope.branch_id,
                schema_version_id=envelope.schema_version_id,
                head_revision=envelope.expected_head,
                reader=reader,
                allowed_entity_types=frozenset(
                    set(CORE_ENTITY_TYPES) | (set(compiled.entity_types) if compiled else set())
                ),
                entity_attribute_models=compiled.entity_models if compiled else {},
                predicate_specs=compiled.predicate_specs if compiled else {},
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
                    "origin": effective_origin,
                    "actor": envelope.actor_user_id,
                },
            )

            for ordinal, operation in enumerate(operations):
                await self._apply(session, envelope, operation, provenance_id, reserved, ordinal)
                for position, evidence in enumerate(operation.evidence):
                    await session.execute(
                        text(
                            """
                            INSERT INTO canon_evidence (
                                workspace_id, project_id, branch_id, commit_id, ordinal,
                                position, record_type, record_id, revision,
                                source_artefact_id, location
                            )
                            VALUES (:w, :p, :b, :c, :ordinal, :position, :rtype, :rid, :rev,
                                    :artefact, :location)
                            """
                        ),
                        {
                            "w": envelope.workspace_id,
                            "p": envelope.project_id,
                            "b": envelope.branch_id,
                            "c": envelope.commit_id,
                            "ordinal": ordinal,
                            "position": position,
                            "rtype": _RECORD_TYPE.get(operation.op, operation.op),
                            "rid": operation.id,
                            "rev": reserved,
                            "artefact": evidence.source_artefact_id,
                            "location": evidence.location,
                        },
                    )

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
            if mutation is not None:
                if status is MutationStatus.DRAFT:
                    # A low-impact direct commit is its own approval; record it as
                    # review evidence in the same transaction as the canon it accepts.
                    await session.execute(
                        text(
                            """
                            INSERT INTO review_decisions (
                                id, mutation_id, workspace_id, project_id, decision,
                                actor_user_id, note
                            )
                            VALUES (:id, :m, :w, :p, 'approved', :actor, :note)
                            """
                        ),
                        {
                            "id": format_canon_id("operation", str(uuid7())),
                            "m": envelope.mutation_id,
                            "w": envelope.workspace_id,
                            "p": envelope.project_id,
                            "actor": envelope.actor_user_id,
                            "note": f"{effective_origin}-direct",
                        },
                    )
                    await apply_transition(
                        session,
                        mutation,
                        MutationStatus.APPROVED,
                        actor_user_id=envelope.actor_user_id,
                        reason=f"{effective_origin}-direct",
                    )
                    mutation = (
                        await session.execute(
                            text("SELECT * FROM mutation_sets WHERE id = :id"),
                            {"id": envelope.mutation_id},
                        )
                    ).one()
                await apply_transition(
                    session,
                    mutation,
                    MutationStatus.COMMITTED,
                    actor_user_id=envelope.actor_user_id,
                    reason=f"committed as revision {reserved}",
                )
        return reserved

    async def _record_version(
        self,
        session: Any,
        envelope: Any,
        operation: CanonOperation,
        revision: int,
        ordinal: int,
    ) -> None:
        await session.execute(
            text(
                """
                INSERT INTO canon_record_versions (
                    workspace_id, project_id, branch_id, record_type, record_id,
                    revision, operation_type, commit_id, change, ordinal
                )
                VALUES (:w, :p, :b, :rtype, :rid, :rev, :op, :c, CAST(:change AS JSONB),
                        :ordinal)
                """
            ),
            {
                "ordinal": ordinal,
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
        ordinal: int,
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
                        lifecycle, schema_version_id, created_revision, attributes,
                        aliases, external_ids
                    )
                    VALUES (:w, :p, :b, :id, :type, :name, :slug, 'active', :schema, :rev,
                            CAST(:attributes AS JSONB), CAST(:aliases AS JSONB),
                            CAST(:external_ids AS JSONB))
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
                    "aliases": json.dumps(operation.aliases),
                    "external_ids": json.dumps(operation.external_ids),
                },
            )
            await self._bind_external_ids(session, scope, operation.id, operation.external_ids)
        elif isinstance(operation, UpdateEntityOp):
            await self._update(
                session,
                "UPDATE canon_entities SET "
                "name = COALESCE(:name, name), slug = COALESCE(:slug, slug), "
                "attributes = COALESCE(CAST(:attributes AS JSONB), attributes), "
                "aliases = COALESCE(CAST(:aliases AS JSONB), aliases), "
                "external_ids = COALESCE(CAST(:external_ids AS JSONB), external_ids) "
                "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b AND id = :id",
                {
                    **scope,
                    "id": operation.id,
                    "name": operation.name,
                    "slug": operation.slug,
                    "attributes": json.dumps(operation.attributes)
                    if operation.attributes is not None
                    else None,
                    "aliases": json.dumps(operation.aliases)
                    if operation.aliases is not None
                    else None,
                    "external_ids": json.dumps(operation.external_ids)
                    if operation.external_ids is not None
                    else None,
                },
                operation.id,
            )
            if operation.external_ids is not None:
                await session.execute(
                    text(
                        "DELETE FROM canon_entity_external_ids WHERE workspace_id = :w "
                        "AND project_id = :p AND branch_id = :b AND entity_id = :id"
                    ),
                    {**scope, "id": operation.id},
                )
                await self._bind_external_ids(session, scope, operation.id, operation.external_ids)
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
                        confidence, provenance_id, created_revision, valid_from, valid_until,
                        starts, ends, visibility
                    )
                    VALUES (:w, :p, :b, :id, CAST(:proposition AS JSONB), 'asserted',
                            :confidence, :prov, :rev, :valid_from, :valid_until,
                            CAST(:starts AS JSONB), CAST(:ends AS JSONB), :visibility)
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
                    "starts": _time_json(operation.starts, clear_unknown=True),
                    "ends": _time_json(operation.ends, clear_unknown=True),
                    "visibility": (operation.visibility or FactVisibility.ORDINARY).value,
                },
            )
        elif isinstance(operation, AmendFactOp):
            await self._update(
                session,
                "UPDATE canon_facts SET "
                "starts = CASE WHEN :set_starts THEN CAST(:starts AS JSONB) ELSE starts END, "
                "ends = CASE WHEN :set_ends THEN CAST(:ends AS JSONB) ELSE ends END, "
                "visibility = COALESCE(:visibility, visibility) "
                "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b AND id = :id",
                {
                    **scope,
                    "id": operation.id,
                    "set_starts": operation.starts is not None,
                    "starts": _time_json(operation.starts, clear_unknown=True),
                    "set_ends": operation.ends is not None,
                    "ends": _time_json(operation.ends, clear_unknown=True),
                    "visibility": operation.visibility.value if operation.visibility else None,
                },
                operation.id,
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
                        created_revision, world_time
                    )
                    VALUES (:w, :p, :b, :id, :kind, :name, CAST(:participants AS JSONB),
                            CAST(:locations AS JSONB), CAST(:causes AS JSONB),
                            CAST(:outcomes AS JSONB), :world_position, :prov, :rev,
                            CAST(:world_time AS JSONB))
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
                    "world_time": _time_json(operation.when, clear_unknown=True),
                },
            )
        elif isinstance(operation, AmendEventOp):
            await self._update(
                session,
                "UPDATE canon_events SET name = COALESCE(:name, name), "
                "world_position = COALESCE(:world_position, world_position), "
                "world_time = CASE WHEN :set_time THEN CAST(:world_time AS JSONB) "
                "ELSE world_time END "
                "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b AND id = :id",
                {
                    **scope,
                    "id": operation.id,
                    "name": operation.name,
                    "world_position": operation.world_position,
                    "set_time": operation.when is not None,
                    "world_time": _time_json(operation.when, clear_unknown=True),
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
        elif isinstance(operation, CreatePlanItemOp):
            created = plan_item_from_create(
                operation,
                workspace_id=envelope.workspace_id,
                project_id=envelope.project_id,
                branch_id=envelope.branch_id,
                provenance_id=provenance_id,
                revision=revision,
            )
            await session.execute(
                text(
                    """
                    INSERT INTO plan_items (
                        workspace_id, project_id, branch_id, id, kind, title, summary,
                        parent_id, position, refs, sets_up, pays_off, details,
                        provenance_id, created_revision
                    )
                    VALUES (:w, :p, :b, :id, :kind, :title, :summary, :parent, :position,
                            CAST(:refs AS JSONB), CAST(:sets_up AS JSONB),
                            CAST(:pays_off AS JSONB), CAST(:details AS JSONB), :prov, :rev)
                    """
                ),
                {**scope, **_plan_params(created), "prov": provenance_id, "rev": revision},
            )
        elif isinstance(operation, UpdatePlanItemOp):
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM plan_items WHERE workspace_id = :w AND project_id = :p "
                        "AND branch_id = :b AND id = :id FOR UPDATE"
                    ),
                    {**scope, "id": operation.id},
                )
            ).first()
            if row is None:
                raise HeadConflict(f"canonical record {operation.id!r} not found for update")
            # One definition of an update, shared with history replay and validation.
            params = _plan_params(apply_plan_update(_plan_item(row), operation))
            params.pop("kind")
            await self._update(
                session,
                "UPDATE plan_items SET title = :title, summary = :summary, parent_id = :parent, "
                "position = :position, refs = CAST(:refs AS JSONB), "
                "sets_up = CAST(:sets_up AS JSONB), pays_off = CAST(:pays_off AS JSONB), "
                "details = CAST(:details AS JSONB) "
                "WHERE workspace_id = :w AND project_id = :p AND branch_id = :b AND id = :id",
                {**scope, **params},
                operation.id,
            )
        elif isinstance(operation, RetirePlanItemOp):
            await self._update(
                session,
                "UPDATE plan_items SET retired_revision = :rev "
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
        await self._record_version(session, envelope, operation, revision, ordinal)

    async def _bind_external_ids(
        self, session: Any, scope: dict[str, Any], entity_id: str, external_ids: dict[str, str]
    ) -> None:
        for namespace, value in external_ids.items():
            await session.execute(
                text(
                    "INSERT INTO canon_entity_external_ids "
                    "(workspace_id, project_id, branch_id, namespace, value, entity_id) "
                    "VALUES (:w, :p, :b, :ns, :value, :id)"
                ),
                {**scope, "ns": namespace, "value": value, "id": entity_id},
            )

    async def _update(
        self, session: Any, statement: str, params: dict[str, Any], record_id: str
    ) -> None:
        result = await session.execute(text(statement), params)
        if result.rowcount == 0:  # type: ignore[attr-defined]
            raise HeadConflict(f"canonical record {record_id!r} not found for update")

    # ------------------------------------------------------- import ledger
    async def begin_import(
        self,
        *,
        import_id: str,
        workspace_id: str,
        project_id: str,
        archive_checksum: str,
        format_version: str,
        commits_total: int,
        actor_user_id: str | None = None,
    ) -> ArchiveImport:
        """Record (or resume) an import. A completed import is returned unchanged."""
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        """
                        INSERT INTO archive_imports (
                            id, workspace_id, project_id, archive_checksum, format_version,
                            status, stage, commits_total, actor_user_id
                        )
                        VALUES (:id, :w, :p, :sum, :fmt, 'importing', 'validated', :total, :actor)
                        ON CONFLICT (workspace_id, project_id, archive_checksum) DO UPDATE SET
                            status = CASE WHEN archive_imports.status = 'completed'
                                          THEN 'completed' ELSE 'importing' END,
                            error = CASE WHEN archive_imports.status = 'completed'
                                         THEN archive_imports.error ELSE NULL END,
                            updated_at = now()
                        RETURNING *
                        """
                    ),
                    {
                        "id": import_id,
                        "w": workspace_id,
                        "p": project_id,
                        "sum": archive_checksum,
                        "fmt": format_version,
                        "total": commits_total,
                        "actor": actor_user_id,
                    },
                )
            ).one()
        return _archive_import(row)

    async def update_import(
        self,
        *,
        import_id: str,
        stage: str,
        commits_applied: int | None = None,
        object_keys: Sequence[str] | None = None,
        created_records: dict[str, list[str]] | None = None,
    ) -> None:
        async with session_scope(self._session_factory) as session:
            await session.execute(
                text(
                    """
                    UPDATE archive_imports SET
                        stage = :stage,
                        commits_applied = COALESCE(:applied, commits_applied),
                        object_keys = COALESCE(CAST(:keys AS JSONB), object_keys),
                        objects_imported = COALESCE(:count, objects_imported),
                        created_records = COALESCE(CAST(:created AS JSONB), created_records),
                        updated_at = now()
                    WHERE id = :id AND status = 'importing'
                    """
                ),
                {
                    "id": import_id,
                    "stage": stage,
                    "applied": commits_applied,
                    "keys": json.dumps(list(object_keys)) if object_keys is not None else None,
                    "count": len(object_keys) if object_keys is not None else None,
                    "created": json.dumps(created_records) if created_records is not None else None,
                },
            )

    async def finish_import(
        self, *, import_id: str, status: ImportStatus, error: str | None = None
    ) -> ArchiveImport:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        """
                        UPDATE archive_imports SET
                            status = :status,
                            error = :error,
                            stage = CASE WHEN :status = 'completed' THEN 'verified' ELSE stage END,
                            updated_at = now(),
                            completed_at = CASE WHEN :status = 'completed' THEN now()
                                                ELSE completed_at END
                        WHERE id = :id RETURNING *
                        """
                    ),
                    {"id": import_id, "status": status.value, "error": error},
                )
            ).one()
        return _archive_import(row)

    async def record_removed_import(self, record: ArchiveImport) -> ArchiveImport:
        """Mark an import removed once its canon has been purged (the ledger row is kept)."""
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        """
                        INSERT INTO archive_imports (
                            id, workspace_id, project_id, archive_checksum, format_version,
                            status, stage, commits_total, commits_applied, error, actor_user_id,
                            started_at
                        )
                        VALUES (:id, :w, :p, :sum, :fmt, 'removed', 'removed', :total, 0, :error,
                                :actor, :started)
                        ON CONFLICT (id) DO UPDATE SET
                            status = 'removed',
                            stage = 'removed',
                            commits_applied = 0,
                            objects_imported = 0,
                            object_keys = '[]'::jsonb,
                            created_records = '{}'::jsonb,
                            updated_at = now()
                        RETURNING *
                        """
                    ),
                    {
                        "id": record.id,
                        "w": record.workspace_id,
                        "p": record.project_id,
                        "sum": record.archive_checksum,
                        "fmt": record.format_version,
                        "total": record.commits_total,
                        "error": record.error,
                        "actor": record.actor_user_id,
                        "started": record.started_at,
                    },
                )
            ).one()
        return _archive_import(row)

    async def list_imports(self, *, workspace_id: str, project_id: str) -> list[ArchiveImport]:
        """Imports into a project, most recent first."""
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT * FROM archive_imports WHERE workspace_id = :w AND project_id = :p "
                        "ORDER BY updated_at DESC, id"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).all()
        return [_archive_import(row) for row in rows]

    async def _purge(self, clause: str, params: dict[str, Any]) -> int:
        removed = 0
        async with session_scope(self._session_factory) as session:
            for table in CANON_PROJECT_TABLES:
                result = await session.execute(text(f"DELETE FROM {table} WHERE {clause}"), params)
                removed += int(result.rowcount or 0)  # type: ignore[attr-defined]
        return removed

    async def purge_project(self, *, workspace_id: str, project_id: str) -> int:
        """Erase every canon, journal, schema, and media row a project owns.

        Deleting a project is the one operation that removes accepted history; it
        runs in a single transaction so a project is never half-erased.
        """
        return await self._purge(
            "workspace_id = :w AND project_id = :p", {"w": workspace_id, "p": project_id}
        )

    async def purge_workspace(self, *, workspace_id: str) -> int:
        return await self._purge("workspace_id = :w", {"w": workspace_id})

    async def purge_import(
        self,
        *,
        workspace_id: str,
        project_id: str,
        created_records: dict[str, list[str]],
    ) -> int:
        """Erase the canon an import wrote plus exactly the supporting records it created.

        Schema versions and media records that existed before the import are kept.
        """
        scope = {"w": workspace_id, "p": project_id}
        removed = 0
        async with session_scope(self._session_factory) as session:
            for table in CANON_PROJECT_TABLES:
                if table in _IMPORT_PRESERVED_TABLES:
                    continue
                result = await session.execute(
                    text(f"DELETE FROM {table} WHERE workspace_id = :w AND project_id = :p"), scope
                )
                removed += int(result.rowcount or 0)  # type: ignore[attr-defined]
            for table, key, column in (
                ("media_lifecycle_events", "asset_versions", "asset_version_id"),
                ("media_lifecycle_events", "assets", "asset_id"),
                ("media_asset_versions", "asset_versions", "id"),
                ("media_assets", "assets", "id"),
                ("schema_versions", "schema_versions", "id"),
            ):
                ids = list(created_records.get(key, []))
                if not ids:
                    continue
                result = await session.execute(
                    text(
                        f"DELETE FROM {table} WHERE workspace_id = :w AND project_id = :p "
                        f"AND {column} = ANY(:ids)"
                    ),
                    {**scope, "ids": ids},
                )
                removed += int(result.rowcount or 0)  # type: ignore[attr-defined]
        return removed

    # ------------------------------------------------------------------ what-ifs
    async def fork_branch(
        self,
        *,
        workspace_id: str,
        project_id: str,
        parent_branch_id: str,
        branch_id: str,
        name: str,
        description: str | None,
        actor_user_id: str | None,
    ) -> int:
        """Start a what-if from a branch's current head, copying its story (ADR-020).

        One transaction: the parent's head is locked so no commit can land half-way
        through the copy, and a commit already reserved on it makes the fork wait for
        another try rather than copy a story that is about to change.
        """
        async with session_scope(self._session_factory) as session:
            parent = (
                await session.execute(
                    text(
                        "SELECT head_revision FROM branches WHERE workspace_id = :w "
                        "AND project_id = :p AND id = :b FOR UPDATE"
                    ),
                    {"w": workspace_id, "p": project_id, "b": parent_branch_id},
                )
            ).first()
            if parent is None:
                raise HeadConflict("branch not found in this project")
            busy = (
                await session.execute(
                    text(
                        "SELECT 1 FROM branch_reservations "
                        "WHERE branch_id = :b AND status = 'active'"
                    ),
                    {"b": parent_branch_id},
                )
            ).first()
            if busy is not None:
                raise HeadConflict("a change is being saved to the story; try again")
            head = int(parent.head_revision)
            await session.execute(
                text(
                    "INSERT INTO branches (id, workspace_id, project_id, name, is_root, "
                    "head_revision, parent_branch_id, fork_revision, description, created_by) "
                    "VALUES (:id, :w, :p, :name, false, :head, :parent, :head, :description, "
                    ":actor)"
                ),
                {
                    "id": branch_id,
                    "w": workspace_id,
                    "p": project_id,
                    "name": name,
                    "head": head,
                    "parent": parent_branch_id,
                    "description": description,
                    "actor": actor_user_id,
                },
            )
            for table in _FORKED_TABLES:
                columns = [
                    row[0]
                    for row in (
                        await session.execute(
                            text(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_schema = current_schema() AND table_name = :t "
                                "ORDER BY ordinal_position"
                            ),
                            {"t": table},
                        )
                    ).all()
                ]
                selected = ", ".join(
                    ":new_branch" if column == "branch_id" else column for column in columns
                )
                await session.execute(
                    text(
                        f"INSERT INTO {table} ({', '.join(columns)}) "  # noqa: S608 - fixed tables
                        f"SELECT {selected} FROM {table} "
                        "WHERE workspace_id = :w AND project_id = :p AND branch_id = :parent"
                    ),
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "parent": parent_branch_id,
                        "new_branch": branch_id,
                    },
                )
        return head

    async def set_branch_status(
        self, *, workspace_id: str, project_id: str, branch_id: str, status: str
    ) -> None:
        async with session_scope(self._session_factory) as session:
            await session.execute(
                text(
                    "UPDATE branches SET status = :status WHERE workspace_id = :w "
                    "AND project_id = :p AND id = :b AND NOT is_root"
                ),
                {"w": workspace_id, "p": project_id, "b": branch_id, "status": status},
            )

    # ------------------------------------------------------------------- reads
    async def get_head(self, *, workspace_id: str, project_id: str, branch_id: str) -> int:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT head_revision FROM branches "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :b"
                    ),
                    {"w": workspace_id, "p": project_id, "wb": working_branch_id(), "b": branch_id},
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
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id AND " + _ON_BRANCH
                    ),
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "wb": working_branch_id(),
                        "id": entity_id,
                    },
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
        clauses = ["workspace_id = :w", "project_id = :p", _ON_BRANCH]
        params: dict[str, Any] = {
            "w": workspace_id,
            "p": project_id,
            "wb": working_branch_id(),
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
        clauses = [
            "workspace_id = :w",
            "project_id = :p",
            _ON_BRANCH,
            "(lower(name) LIKE :needle OR lower(slug) LIKE :needle OR EXISTS ("
            "SELECT 1 FROM jsonb_array_elements_text(aliases) alias "
            "WHERE lower(alias) LIKE :needle))",
        ]
        params: dict[str, Any] = {
            "w": workspace_id,
            "p": project_id,
            "wb": working_branch_id(),
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

    async def get_entity_by_slug(
        self, *, workspace_id: str, project_id: str, slug: str
    ) -> CanonEntity | None:
        async with session_scope(self._session_factory) as session:
            return await _entity_by_slug(session, workspace_id, project_id, slug)

    async def get_entity_by_external_id(
        self, *, workspace_id: str, project_id: str, namespace: str, value: str
    ) -> CanonEntity | None:
        async with session_scope(self._session_factory) as session:
            return await _entity_by_external_id(session, workspace_id, project_id, namespace, value)

    async def find_entities_by_name(
        self, *, workspace_id: str, project_id: str, entity_type: str, name: str
    ) -> list[CanonEntity]:
        async with session_scope(self._session_factory) as session:
            return await _entities_by_name(session, workspace_id, project_id, entity_type, name)

    async def list_active_references(
        self, *, workspace_id: str, project_id: str, entity_id: str
    ) -> list[tuple[str, str]]:
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(_ACTIVE_REFERENCES),
                    _reference_params(workspace_id, project_id, entity_id),
                )
            ).all()
        return [(row.record_type, row.id) for row in rows]

    async def count_active_references(
        self, *, workspace_id: str, project_id: str, entity_id: str
    ) -> int:
        return len(
            await self.list_active_references(
                workspace_id=workspace_id, project_id=project_id, entity_id=entity_id
            )
        )

    async def list_evidence(
        self,
        *,
        workspace_id: str,
        project_id: str,
        record_type: str | None = None,
        record_id: str | None = None,
        commit_id: str | None = None,
    ) -> list[EvidenceRecord]:
        clauses = ["workspace_id = :w", "project_id = :p", _ON_BRANCH]
        params: dict[str, Any] = {"w": workspace_id, "p": project_id, "wb": working_branch_id()}
        if record_type is not None:
            clauses.append("record_type = :rtype")
            params["rtype"] = record_type
        if record_id is not None:
            clauses.append("record_id = :rid")
            params["rid"] = record_id
        if commit_id is not None:
            clauses.append("commit_id = :c")
            params["c"] = commit_id
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT * FROM canon_evidence WHERE {' AND '.join(clauses)} "
                        "ORDER BY revision, ordinal, position"
                    ),
                    params,
                )
            ).all()
        return [
            EvidenceRecord(
                record_type=row.record_type,
                record_id=row.record_id,
                revision=row.revision,
                commit_id=row.commit_id,
                ordinal=row.ordinal,
                source_artefact_id=row.source_artefact_id,
                location=row.location,
            )
            for row in rows
        ]

    async def get_fact(self, *, workspace_id: str, project_id: str, fact_id: str) -> Fact | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM canon_facts "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id AND " + _ON_BRANCH
                    ),
                    {"w": workspace_id, "p": project_id, "wb": working_branch_id(), "id": fact_id},
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
        object_entity_id: str | None = None,
    ) -> list[Fact]:
        clauses = ["workspace_id = :w", "project_id = :p", _ON_BRANCH]
        params: dict[str, Any] = {
            "w": workspace_id,
            "p": project_id,
            "wb": working_branch_id(),
            "limit": limit,
            "offset": offset,
        }
        if object_entity_id:
            clauses.append(
                "proposition->'object'->>'kind' = 'entity_ref' "
                "AND proposition->'object'->>'entity_id' = :object_entity"
            )
            params["object_entity"] = object_entity_id
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
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id AND " + _ON_BRANCH
                    ),
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "wb": working_branch_id(),
                        "id": belief_id,
                    },
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
        clauses = ["workspace_id = :w", "project_id = :p", _ON_BRANCH]
        params: dict[str, Any] = {
            "w": workspace_id,
            "p": project_id,
            "wb": working_branch_id(),
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
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id AND " + _ON_BRANCH
                    ),
                    {"w": workspace_id, "p": project_id, "wb": working_branch_id(), "id": event_id},
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
        entity_id: str | None = None,
    ) -> list[Event]:
        clauses = ["workspace_id = :w", "project_id = :p", _ON_BRANCH]
        params: dict[str, Any] = {
            "w": workspace_id,
            "p": project_id,
            "wb": working_branch_id(),
            "limit": limit,
            "offset": offset,
        }
        if not include_retired:
            clauses.append("retired_revision IS NULL")
        if entity_id:
            # Events the entity takes part in, happens at, causes, or results from.
            clauses.append(
                "(participants @> CAST(:participant AS JSONB) "
                "OR location_ids @> CAST(:member AS JSONB) "
                "OR cause_ids @> CAST(:member AS JSONB) "
                "OR outcome_ids @> CAST(:member AS JSONB))"
            )
            params["participant"] = json.dumps([{"entity_id": entity_id}])
            params["member"] = json.dumps([entity_id])
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT * FROM canon_events WHERE {' AND '.join(clauses)} "
                        "ORDER BY created_revision LIMIT :limit OFFSET :offset"
                    ),
                    params,
                )
            ).all()
        return [_event(row) for row in rows]

    async def get_plan_item(
        self, *, workspace_id: str, project_id: str, plan_id: str
    ) -> PlanItem | None:
        async with session_scope(self._session_factory) as session:
            row = (
                await session.execute(
                    text(
                        "SELECT * FROM plan_items "
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id AND " + _ON_BRANCH
                    ),
                    {"w": workspace_id, "p": project_id, "wb": working_branch_id(), "id": plan_id},
                )
            ).first()
        return _plan_item(row) if row else None

    async def list_plan_items(
        self,
        *,
        workspace_id: str,
        project_id: str,
        include_retired: bool = False,
        limit: int = 2000,
    ) -> list[PlanItem]:
        """The whole plan, in outline order within each parent. Intent only: no
        entry, fact, belief or event query ever returns these."""
        clauses = ["workspace_id = :w", "project_id = :p", _ON_BRANCH]
        if not include_retired:
            clauses.append("retired_revision IS NULL")
        async with session_scope(self._session_factory) as session:
            rows = (
                await session.execute(
                    text(
                        f"SELECT * FROM plan_items WHERE {' AND '.join(clauses)} "
                        "ORDER BY position, created_revision, id LIMIT :limit"
                    ),
                    {"w": workspace_id, "p": project_id, "wb": working_branch_id(), "limit": limit},
                )
            ).all()
        return [_plan_item(row) for row in rows]

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
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "wb": working_branch_id(),
                        "b": branch_id,
                        "id": provenance_id,
                    },
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
        clauses = ["workspace_id = :w", "project_id = :p", _ON_BRANCH]
        params: dict[str, Any] = {
            "w": workspace_id,
            "p": project_id,
            "wb": working_branch_id(),
            "limit": limit,
        }
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
                        "ORDER BY revision DESC, ordinal DESC LIMIT :limit"
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
                ordinal=row.ordinal,
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
                        "WHERE workspace_id=:w AND project_id=:p AND id=:id AND " + _ON_BRANCH
                    ),
                    {"w": workspace_id, "p": project_id, "wb": working_branch_id(), "id": link_id},
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
        target_record_id: str | None = None,
    ) -> list[AssetLink]:
        clauses = ["workspace_id=:w", "project_id=:p"]
        if target_record_id is not None:
            clauses.append("target_record_id=:t")
        params: dict[str, Any] = {"w": workspace_id, "p": project_id}
        if target_record_id is not None:
            params["t"] = target_record_id
        if asset_id is not None:
            clauses.append("asset_id=:a")
            params["a"] = asset_id
        if branch_id is not None:
            clauses.append("branch_id=:b")
            params["b"] = branch_id
        else:
            clauses.append(_ON_BRANCH)
            params["wb"] = working_branch_id()
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
