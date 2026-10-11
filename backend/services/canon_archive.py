"""Canonical project archive (format v3) export/import.

Exports the canonical PostgreSQL model for one project — schema versions, the
ordered commit envelopes, the canon snapshot, tags, media identity/version
metadata, and project objects (bytes) — as a checksummed, self-contained archive.
Import is preflight-validated and idempotent: commits replay under deterministic
target-scoped commit ids, so re-importing the same archive into the same project
is a no-op. Project-scoped entity references are rewritten for the target, so a
cross-project restore can legitimately produce a different commit checksum.

An import is recorded in a status ledger before its first durable write and is
marked ``completed`` only after every commit has replayed, the graph projection
has been rebuilt (when one is configured), and the journal, normalized canon,
and projection have been verified to agree. A failed import stays visibly
unsuccessful; it can be retried or removed. Canon has no derived vector index
in Phase 2, so there is nothing else to rebuild.

Full-history export and import are owner-level operations: the ``*_as`` entry
points authorize the actor and write a durable security-audit record carrying
counts and checksums only.

Credentials, sessions, provider usage, and unrelated tenant data are never
included.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from backend.core.logging import log_event
from backend.core.metrics import metrics
from backend.domain.archive import ArchiveImport, ArchiveImportError, ImportStatus
from backend.domain.canon.propositions import EntityRefObject
from backend.domain.canon.revisions import OriginKind
from backend.domain.errors import NotFoundError
from backend.domain.history import CanonTag
from backend.domain.identity import AccessScope
from backend.domain.journal import CommitEnvelope, MutationOperation, MutationStatus
from backend.domain.media import MediaAsset, MediaAssetVersion
from backend.domain.operations import (
    AssertBeliefOp,
    AssertFactOp,
    CanonOperation,
    canonicalize_operations,
    operations_checksum,
    parse_operation,
)
from backend.domain.review import MutationIntent, classify_review
from backend.domain.tenancy import Permission
from backend.ports import (
    CanonJournalPort,
    CanonRepositoryPort,
    MediaStorePort,
    ObjectStorePort,
    SchemaVersionStorePort,
)

ARCHIVE_FORMAT = "novel-engine/canon-archive"
FORMAT_VERSION = "3"
MAX_ENTRIES = 100_000
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024

logger = logging.getLogger(__name__)


class ArchiveError(RuntimeError):
    """Raised when an archive is malformed, unsafe, or fails verification."""


def _checksum(payload: dict[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "checksum"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _dedup_commit_id(
    archive_checksum: str,
    source_commit_id: str,
    target_workspace_id: str,
    target_project_id: str,
) -> str:
    digest = hashlib.sha256(
        f"{archive_checksum}:{source_commit_id}:{target_workspace_id}:{target_project_id}".encode()
    ).hexdigest()
    return f"cmt_{digest[:32]}"


def _import_id(archive_checksum: str, workspace_id: str, project_id: str) -> str:
    digest = hashlib.sha256(f"{archive_checksum}:{workspace_id}:{project_id}".encode()).hexdigest()
    return f"imp_{digest[:32]}"


def _remap_operation_scope(operation: CanonOperation, *, target_project_id: str) -> CanonOperation:
    if not isinstance(operation, (AssertFactOp, AssertBeliefOp)):
        return operation
    obj = operation.proposition.object
    if not isinstance(obj, EntityRefObject):
        return operation
    proposition = operation.proposition.model_copy(
        update={"object": obj.model_copy(update={"project_id": target_project_id})}
    )
    return operation.model_copy(update={"proposition": proposition})


def _safe_object_key(key: object) -> bool:
    return (
        isinstance(key, str)
        and bool(key)
        and "/" not in key
        and "\\" not in key
        and ".." not in key
    )


class CanonArchiveService:
    def __init__(
        self,
        *,
        canon: CanonRepositoryPort,
        journal: CanonJournalPort,
        schemas: SchemaVersionStorePort,
        media: MediaStorePort,
        objects: ObjectStorePort,
        projector: object | None = None,
        equivalence: object | None = None,
        tenancy: object | None = None,
        audit: object | None = None,
        canon_writer: object | None = None,
        local_mode: bool = False,
    ) -> None:
        self._canon = canon
        self._journal = journal
        self._schemas = schemas
        self._media = media
        self._objects = objects
        self._projector = projector
        self._equivalence = equivalence
        self._tenancy = tenancy
        self._audit = audit
        self._canon_writer = canon_writer
        self._local_mode = local_mode

    # ----------------------------------------------- authorized entry points
    async def _require_owner(self, scope: AccessScope, workspace_id: str, project_id: str) -> None:
        """Full-history archive operations need project administration (owners only)."""
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(  # type: ignore[attr-defined]
            scope, workspace_id, project_id, Permission.PROJECT_ADMIN
        )

    async def _record_audit(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        action: str,
        detail: dict[str, Any],
    ) -> None:
        """Durable security-audit record: who did what, with counts, never content."""
        if self._audit is None:
            return
        await self._audit.record_audit(  # type: ignore[attr-defined]
            workspace_id=workspace_id,
            actor_user_id=scope.actor_user_id,
            action=action,
            target_type="project",
            target_id=project_id,
            detail=detail,
            correlation_id=scope.correlation_id,
        )

    async def export_project_as(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> dict[str, Any]:
        await self._require_owner(scope, workspace_id, project_id)
        archive = await self.export_project(workspace_id, project_id)
        await self._record_audit(
            scope,
            workspace_id,
            project_id,
            "archive.exported",
            {
                "archive_checksum": archive["checksum"],
                "format_version": archive["format_version"],
                "commits": len(archive["commits"]),
                "objects": len(archive["objects"]),
                "head_revision": archive["project"]["head_revision"],
            },
        )
        return archive

    async def import_project_as(
        self, scope: AccessScope, workspace_id: str, project_id: str, archive: dict[str, Any]
    ) -> dict[str, Any]:
        await self._require_owner(scope, workspace_id, project_id)
        checksum = archive.get("checksum") if isinstance(archive, dict) else None
        try:
            result = await self.import_project(
                workspace_id, project_id, archive, actor_user_id=scope.actor_user_id
            )
        except Exception as error:
            await self._record_audit(
                scope,
                workspace_id,
                project_id,
                "archive.import_failed",
                {"archive_checksum": str(checksum or ""), "error": type(error).__name__},
            )
            raise
        await self._record_audit(
            scope,
            workspace_id,
            project_id,
            "archive.imported",
            {
                "archive_checksum": result["checksum"],
                "import_id": result["import_id"],
                "commits": result["commits"],
                "objects": result["objects"],
                "verified": result["verified"],
            },
        )
        return result

    async def list_imports_as(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> list[ArchiveImport]:
        if not self._local_mode and self._tenancy is not None:
            await self._tenancy.require_project(  # type: ignore[attr-defined]
                scope, workspace_id, project_id, Permission.PROJECT_READ
            )
        return await self._canon.list_imports(workspace_id=workspace_id, project_id=project_id)

    async def remove_import_as(
        self, scope: AccessScope, workspace_id: str, project_id: str, import_id: str
    ) -> ArchiveImport:
        await self._require_owner(scope, workspace_id, project_id)
        removed = await self.remove_import(workspace_id, project_id, import_id)
        await self._record_audit(
            scope,
            workspace_id,
            project_id,
            "archive.import_removed",
            {"import_id": import_id, "archive_checksum": removed.archive_checksum},
        )
        return removed

    async def remove_import(
        self, workspace_id: str, project_id: str, import_id: str
    ) -> ArchiveImport:
        """Erase the canon a failed or interrupted import wrote, and nothing else.

        Refused once an import has completed, and refused when the project holds
        any canon the import did not write, so authored work is never removed.
        """
        imports = await self._canon.list_imports(workspace_id=workspace_id, project_id=project_id)
        record = next((item for item in imports if item.id == import_id), None)
        if record is None:
            raise NotFoundError("Import not found.")
        if record.status not in {ImportStatus.FAILED, ImportStatus.IMPORTING}:
            raise ArchiveImportError(
                f"an import in state {record.status.value!r} cannot be removed"
            )
        branch_id = await self._root_branch_id(workspace_id, project_id)
        revisions = await self._canon.list_commits(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id, limit=MAX_ENTRIES
        )
        for revision in revisions:
            envelope = await self._canon.get_envelope(revision.commit_id)
            mutation = (
                await self._journal.get_mutation(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    mutation_id=envelope.mutation_id,
                )
                if envelope is not None
                else None
            )
            if mutation is None or not mutation.idempotency_key.startswith("archive:"):
                raise ArchiveImportError(
                    "the project contains canon this import did not write; it was not removed"
                )
        for key in record.object_keys:
            self._objects.delete(key, workspace_id=workspace_id, project_id=project_id)
        # Only what this import created is removed: a schema version or asset the
        # project already had before the import stays.
        await self._canon.purge_import(
            workspace_id=workspace_id,
            project_id=project_id,
            created_records=record.created_records,
        )
        if self._canon_writer is not None:
            await self._canon_writer.purge(  # type: ignore[attr-defined]
                workspace_id=workspace_id, project_id=project_id
            )
        metrics.increment(
            "archive_operations_total",
            "Archive operations by kind and outcome.",
            operation="remove_import",
            outcome="completed",
        )
        return await self._canon.record_removed_import(record)

    async def _root_branch_id(self, workspace_id: str, project_id: str) -> str:
        branch = await self._journal.ensure_root_branch(
            workspace_id=workspace_id, project_id=project_id, name="main"
        )
        return branch.id

    # ---------------------------------------------------------------- export
    async def export_project(self, workspace_id: str, project_id: str) -> dict[str, Any]:
        branch_id = await self._root_branch_id(workspace_id, project_id)
        schema_versions = await self._schemas.list(workspace_id=workspace_id, project_id=project_id)
        revisions = await self._canon.list_commits(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch_id,
            limit=MAX_ENTRIES,
        )
        commits: list[dict[str, Any]] = []
        for revision in revisions:
            envelope = await self._canon.get_envelope(revision.commit_id)
            if envelope is None:
                raise ArchiveError(f"missing envelope for revision {revision.revision}")
            mutation = await self._journal.get_mutation(
                workspace_id=workspace_id,
                project_id=project_id,
                mutation_id=envelope.mutation_id,
            )
            decisions = (
                await self._journal.list_decisions(mutation_id=mutation.id) if mutation else []
            )
            provenance = await self._canon.get_provenance(
                workspace_id=workspace_id,
                project_id=project_id,
                branch_id=branch_id,
                provenance_id=f"prov_{envelope.commit_id}",
            )
            commits.append(
                {
                    "commit_id": envelope.commit_id,
                    "revision": envelope.revision,
                    "expected_head": envelope.expected_head,
                    "schema_version_id": envelope.schema_version_id,
                    "canonical": envelope.canonical,
                    "checksum": envelope.checksum,
                    "actor_user_id": envelope.actor_user_id,
                    "origin": (
                        mutation.origin
                        if mutation is not None
                        else provenance.origin.value
                        if provenance is not None
                        else OriginKind.MIGRATION.value
                    ),
                    "mutation": (
                        {
                            "correlation_id": mutation.correlation_id,
                            "intent": mutation.intent,
                            "decisions": [
                                {
                                    "decision": decision.decision,
                                    "actor_user_id": decision.actor_user_id,
                                    "note": decision.note,
                                    "created_at": decision.created_at.isoformat(),
                                }
                                for decision in decisions
                            ],
                        }
                        if mutation is not None
                        else None
                    ),
                    "created_at": envelope.created_at.isoformat(),
                }
            )

        entities = await self._canon.list_entities(
            workspace_id=workspace_id,
            project_id=project_id,
            include_retired=True,
            limit=MAX_ENTRIES,
        )
        facts = await self._canon.list_facts(
            workspace_id=workspace_id, project_id=project_id, include_closed=True, limit=MAX_ENTRIES
        )
        beliefs = await self._canon.list_beliefs(
            workspace_id=workspace_id, project_id=project_id, include_closed=True, limit=MAX_ENTRIES
        )
        events = await self._canon.list_events(
            workspace_id=workspace_id,
            project_id=project_id,
            include_retired=True,
            limit=MAX_ENTRIES,
        )
        tags = await self._canon.list_tags(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
        )
        plan_items = await self._canon.list_plan_items(
            workspace_id=workspace_id,
            project_id=project_id,
            include_retired=True,
            limit=MAX_ENTRIES,
        )

        assets = await self._media.list_assets(
            workspace_id=workspace_id, project_id=project_id, limit=MAX_ENTRIES
        )
        versions: list[MediaAssetVersion] = []
        for asset in assets:
            versions.extend(
                await self._media.list_versions(
                    workspace_id=workspace_id, project_id=project_id, asset_id=asset.id
                )
            )

        objects: list[dict[str, Any]] = []
        for stat in self._objects.list_objects(workspace_id=workspace_id, project_id=project_id):
            payload = self._objects.open(stat.key, workspace_id=workspace_id, project_id=project_id)
            objects.append(
                {
                    "key": stat.key,
                    "content_type": stat.content_type,
                    "byte_length": stat.byte_length,
                    "checksum": stat.checksum,
                    "data": base64.b64encode(payload).decode("ascii"),
                }
            )

        body: dict[str, Any] = {
            "format": ARCHIVE_FORMAT,
            "format_version": FORMAT_VERSION,
            "project": {
                "workspace_id": workspace_id,
                "project_id": project_id,
                "root_branch_id": branch_id,
                "head_revision": revisions[-1].revision if revisions else 0,
            },
            "schemas": [
                {
                    "id": version.id,
                    "version": version.version,
                    "status": version.status.value,
                    "definition": version.definition,
                    "content_hash": version.content_hash,
                    "compatibility": version.compatibility,
                }
                for version in schema_versions
            ],
            "commits": commits,
            "canon": {
                "entities": [entity.model_dump(mode="json") for entity in entities],
                "facts": [fact.model_dump(mode="json") for fact in facts],
                "beliefs": [belief.model_dump(mode="json") for belief in beliefs],
                "events": [event.model_dump(mode="json") for event in events],
            },
            # What the author intends, kept out of ``canon`` on purpose: a reader of an
            # archive must never take a planned scene for something that happened.
            "plan": {
                "intent": True,
                "items": [item.model_dump(mode="json") for item in plan_items],
            },
            "tags": [
                {
                    "name": tag.name,
                    "revision": tag.revision,
                    "commit_id": tag.commit_id,
                    "created_by_user_id": tag.created_by_user_id,
                }
                for tag in tags
            ],
            "media": {
                "assets": [asset.model_dump(mode="json") for asset in assets],
                "versions": [version.model_dump(mode="json") for version in versions],
            },
            "objects": objects,
            "derived": {},
            "sections": {},
        }
        body["checksum"] = _checksum(body)
        metrics.increment(
            "archive_operations_total",
            "Archive operations by kind and outcome.",
            operation="export",
            outcome="completed",
        )
        log_event(
            logger,
            "archive.export",
            workspace_id=workspace_id,
            project_id=project_id,
            commits=len(commits),
            objects=len(objects),
        )
        return body

    # ---------------------------------------------------------------- import
    async def import_project(
        self,
        target_workspace_id: str,
        target_project_id: str,
        archive: dict[str, Any],
        *,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        # Validation and the target preflight perform no durable writes. Only
        # then is the import recorded, before anything is restored.
        self._validate(archive)
        archive_checksum = archive["checksum"]
        import_id = _import_id(archive_checksum, target_workspace_id, target_project_id)
        await self._preflight_target(target_workspace_id, target_project_id, archive)
        record = await self._canon.begin_import(
            import_id=import_id,
            workspace_id=target_workspace_id,
            project_id=target_project_id,
            archive_checksum=archive_checksum,
            format_version=str(archive["format_version"]),
            commits_total=len(archive["commits"]),
            actor_user_id=actor_user_id,
        )
        try:
            result = await self._restore(
                target_workspace_id, target_project_id, archive, import_id=import_id
            )
        except Exception as error:
            # The error class is recorded; archive content never is.
            await self._canon.finish_import(
                import_id=import_id, status=ImportStatus.FAILED, error=type(error).__name__
            )
            metrics.increment(
                "archive_operations_total",
                "Archive operations by kind and outcome.",
                operation="import",
                outcome="failed",
            )
            log_event(
                logger,
                "archive.import.failed",
                level=logging.WARNING,
                workspace_id=target_workspace_id,
                project_id=target_project_id,
                import_id=import_id,
                error_type=type(error).__name__,
            )
            raise
        if record.status is not ImportStatus.COMPLETED:
            await self._canon.finish_import(import_id=import_id, status=ImportStatus.COMPLETED)
        metrics.increment(
            "archive_operations_total",
            "Archive operations by kind and outcome.",
            operation="import",
            outcome="completed",
        )
        return {**result, "import_id": import_id}

    def _replay_plan(
        self, target_workspace_id: str, target_project_id: str, archive: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Target-scoped commits in revision order (pure; no I/O)."""
        plan: list[dict[str, Any]] = []
        for raw in sorted(archive["commits"], key=lambda item: item["revision"]):
            operations = [
                _remap_operation_scope(
                    parse_operation(operation), target_project_id=target_project_id
                )
                for operation in json.loads(raw["canonical"])
            ]
            plan.append(
                {
                    "raw": raw,
                    "commit_id": _dedup_commit_id(
                        archive["checksum"],
                        raw["commit_id"],
                        target_workspace_id,
                        target_project_id,
                    ),
                    "operations": operations,
                    "canonical": canonicalize_operations(operations),
                    "checksum": operations_checksum(operations),
                }
            )
        return plan

    async def _preflight_target(
        self, target_workspace_id: str, target_project_id: str, archive: dict[str, Any]
    ) -> None:
        """Refuse a target that already holds canon this archive did not produce."""
        branch_id = await self._root_branch_id(target_workspace_id, target_project_id)
        plan = self._replay_plan(target_workspace_id, target_project_id, archive)
        target_head = await self._canon.get_head(
            workspace_id=target_workspace_id, project_id=target_project_id, branch_id=branch_id
        )
        if target_head > len(plan):
            raise ArchiveError("target project already contains unrelated canon")
        for item in plan[:target_head]:
            existing_envelope = await self._canon.get_envelope(item["commit_id"])
            if (
                existing_envelope is None
                or existing_envelope.workspace_id != target_workspace_id
                or existing_envelope.project_id != target_project_id
                or existing_envelope.checksum != item["checksum"]
            ):
                raise ArchiveError("target project already contains unrelated canon")

    async def _restore(
        self,
        target_workspace_id: str,
        target_project_id: str,
        archive: dict[str, Any],
        *,
        import_id: str,
    ) -> dict[str, Any]:
        archive_checksum = archive["checksum"]
        branch_id = await self._root_branch_id(target_workspace_id, target_project_id)

        imported_commits = self._replay_plan(target_workspace_id, target_project_id, archive)

        prior_record = next(
            (
                item
                for item in await self._canon.list_imports(
                    workspace_id=target_workspace_id, project_id=target_project_id
                )
                if item.id == import_id
            ),
            None,
        )
        # Records an earlier attempt of this import created stay attributed to it.
        created: dict[str, list[str]] = {
            key: list((prior_record.created_records if prior_record else {}).get(key, []))
            for key in ("schema_versions", "assets", "asset_versions")
        }
        object_key_map: dict[str, str] = {}
        stored_keys: list[str] = []
        object_count = 0
        existing = {
            stat.checksum: stat.key
            for stat in self._objects.list_objects(
                workspace_id=target_workspace_id, project_id=target_project_id
            )
        }
        for raw in archive["objects"]:
            payload = base64.b64decode(raw["data"], validate=True)
            if raw["checksum"] in existing:
                object_key_map[raw["key"]] = existing[raw["checksum"]]
                continue
            stat = self._objects.put(
                payload,
                workspace_id=target_workspace_id,
                project_id=target_project_id,
                content_type=raw.get("content_type", "application/octet-stream"),
            )
            existing[raw["checksum"]] = stat.key
            object_key_map[raw["key"]] = stat.key
            object_count += 1
            stored_keys.append(stat.key)
        if stored_keys:
            # Remember exactly which objects this import stored so a failed import
            # can be removed without touching objects that were already there.
            stored_keys = [*(prior_record.object_keys if prior_record else []), *stored_keys]
            await self._canon.update_import(
                import_id=import_id, stage="objects", object_keys=stored_keys
            )

        existing_schemas = await self._schemas.list(
            workspace_id=target_workspace_id, project_id=target_project_id
        )
        by_hash: dict[str, str] = {version.content_hash: version.id for version in existing_schemas}
        schema_map: dict[str, str] = {}
        for raw in sorted(archive["schemas"], key=lambda item: item["version"]):
            mapped = by_hash.get(raw["content_hash"])
            if mapped is None:
                draft = await self._schemas.create_draft(
                    workspace_id=target_workspace_id,
                    project_id=target_project_id,
                    definition=raw["definition"],
                    content_hash=raw["content_hash"],
                    compatibility="additive",
                    created_by_user_id=None,
                )
                target_version = await self._schemas.publish(
                    workspace_id=target_workspace_id,
                    project_id=target_project_id,
                    version_id=draft.id,
                )
                mapped = (target_version or draft).id
                by_hash[raw["content_hash"]] = mapped
                created["schema_versions"].append(mapped)
                await self._canon.update_import(
                    import_id=import_id, stage="schemas", created_records=created
                )
            schema_map[raw["id"]] = mapped

        for raw in archive["media"]["assets"]:
            if (
                await self._media.get_asset(
                    workspace_id=target_workspace_id,
                    project_id=target_project_id,
                    asset_id=raw["id"],
                )
                is None
            ):
                created["assets"].append(raw["id"])
                await self._canon.update_import(
                    import_id=import_id, stage="media", created_records=created
                )
            await self._media.import_asset(
                MediaAsset.model_validate(
                    {**raw, "workspace_id": target_workspace_id, "project_id": target_project_id}
                )
            )
        for raw in archive["media"]["versions"]:
            source_key = raw["object_key"]
            if (
                await self._media.get_version(
                    workspace_id=target_workspace_id,
                    project_id=target_project_id,
                    version_id=raw["id"],
                )
                is None
            ):
                created["asset_versions"].append(raw["id"])
                await self._canon.update_import(
                    import_id=import_id, stage="media", created_records=created
                )
            await self._media.import_version(
                MediaAssetVersion.model_validate(
                    {
                        **raw,
                        "workspace_id": target_workspace_id,
                        "project_id": target_project_id,
                        "object_key": object_key_map.get(source_key, source_key),
                    }
                )
            )

        commit_id_map: dict[str, str] = {}
        for applied, item in enumerate(imported_commits, start=1):
            raw = item["raw"]
            new_commit_id = item["commit_id"]
            commit_id_map[raw["commit_id"]] = new_commit_id
            raw_mutation = raw.get("mutation") or {}
            decisions = raw_mutation.get("decisions") or []
            source_origin = raw.get("origin") or OriginKind.MIGRATION.value
            intent = raw_mutation.get("intent") or MutationIntent.EDIT.value
            latest_decision = decisions[-1].get("decision") if decisions else None
            needs_review = classify_review(
                origin=source_origin,
                intent=intent,
                operation_types=[operation.op for operation in item["operations"]],
            ).requires_review
            if needs_review and latest_decision != "approved":
                # Older archives may contain accepted commits from before the
                # review gate covered them. Restore them as migrations rather
                # than fabricating a human decision.
                source_origin = OriginKind.MIGRATION.value
            mutation = await self._journal.create_mutation(
                workspace_id=target_workspace_id,
                project_id=target_project_id,
                branch_id=branch_id,
                expected_head=raw["revision"] - 1,
                actor_user_id=raw.get("actor_user_id"),
                origin=source_origin,
                schema_version_id=schema_map.get(raw.get("schema_version_id") or ""),
                correlation_id=raw_mutation.get("correlation_id") or "",
                idempotency_key=f"archive:{new_commit_id}",
                intent=intent,
            )
            await self._journal.record_operations(
                mutation_id=mutation.id,
                operations=[
                    MutationOperation(
                        ordinal=index,
                        operation_type=operation.op,
                        payload=operation.model_dump(mode="json"),
                    )
                    for index, operation in enumerate(item["operations"])
                ],
            )
            existing_decisions = await self._journal.list_decisions(mutation_id=mutation.id)
            if not existing_decisions:
                for index, decision in enumerate(decisions):
                    # The accepted source decision restores the approved state the
                    # commit transaction requires; it is recorded, never invented.
                    restores_approval = (
                        index == len(decisions) - 1
                        and decision["decision"] == "approved"
                        and mutation.status is MutationStatus.DRAFT
                    )
                    await self._journal.record_decision(
                        mutation_id=mutation.id,
                        decision=decision["decision"],
                        actor_user_id=decision.get("actor_user_id"),
                        note=decision.get("note") or "restored review decision",
                        transition_to=MutationStatus.APPROVED if restores_approval else None,
                    )
            await self._canon.apply_commit(
                envelope=CommitEnvelope(
                    commit_id=new_commit_id,
                    mutation_id=mutation.id,
                    workspace_id=target_workspace_id,
                    project_id=target_project_id,
                    branch_id=branch_id,
                    revision=raw["revision"],
                    expected_head=raw["revision"] - 1,
                    schema_version_id=schema_map.get(raw.get("schema_version_id") or ""),
                    canonical=item["canonical"],
                    checksum=item["checksum"],
                    actor_user_id=raw.get("actor_user_id"),
                    created_at=datetime.fromisoformat(raw["created_at"]),
                ),
                operations=item["operations"],
                origin=source_origin,
            )
            await self._canon.update_import(
                import_id=import_id, stage="commits", commits_applied=applied
            )

        for raw in archive["tags"]:
            now = datetime.now(UTC)
            await self._canon.create_tag(
                CanonTag(
                    workspace_id=target_workspace_id,
                    project_id=target_project_id,
                    branch_id=branch_id,
                    name=raw["name"],
                    revision=raw["revision"],
                    commit_id=commit_id_map.get(raw["commit_id"], raw["commit_id"]),
                    created_by_user_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )

        projection_rebuilt = False
        if self._projector is not None:
            await self._projector.rebuild(  # type: ignore[attr-defined]
                workspace_id=target_workspace_id,
                project_id=target_project_id,
                branch_id=branch_id,
            )
            projection_rebuilt = True

        # Prove the restore before calling it successful: the journal must replay
        # to the normalized tables, and the rebuilt projection must answer the
        # same queries. Disagreement fails the import instead of hiding it.
        verified = False
        if self._equivalence is not None:
            await self._canon.update_import(import_id=import_id, stage="verifying")
            report = await self._equivalence.verify(  # type: ignore[attr-defined]
                target_workspace_id, target_project_id
            )
            if not report.ok:
                raise ArchiveError(
                    "restored canon failed verification: " + "; ".join(report.problems[:5])
                )
            verified = True

        log_event(
            logger,
            "archive.import",
            workspace_id=target_workspace_id,
            project_id=target_project_id,
            commits=len(archive["commits"]),
            objects=object_count,
            projection_rebuilt=projection_rebuilt,
        )
        return {
            "objects": object_count,
            "assets": len(archive["media"]["assets"]),
            "versions": len(archive["media"]["versions"]),
            "commits": len(archive["commits"]),
            "checksum": archive_checksum,
            "projection_rebuilt": projection_rebuilt,
            "verified": verified,
        }

    async def accepted_checksum(self, workspace_id: str, project_id: str) -> str:
        """Recompute a canonical checksum of the accepted commit chain."""
        branch_id = await self._root_branch_id(workspace_id, project_id)
        revisions = await self._canon.list_commits(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch_id,
            limit=MAX_ENTRIES,
        )
        parts = [f"{rev.revision}:{rev.envelope_checksum}" for rev in revisions]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()

    # ------------------------------------------------------------- validation
    @staticmethod
    def _validate(archive: dict[str, Any]) -> None:
        if archive.get("format") != ARCHIVE_FORMAT:
            raise ArchiveError("unsupported archive format")
        if archive.get("format_version") != FORMAT_VERSION:
            raise ArchiveError("unsupported archive format version")
        if archive.get("checksum") != _checksum(archive):
            raise ArchiveError("archive checksum mismatch")

        project = archive.get("project") or {}
        source_ws = project.get("workspace_id")
        source_pr = project.get("project_id")

        seen_ids: set[str] = set()
        entity_ids: set[str] = set()

        def claim(identifier: str, *, kind: str) -> None:
            if not identifier:
                raise ArchiveError(f"{kind} entry is missing an id")
            if identifier in seen_ids:
                raise ArchiveError(f"duplicate id across archive entries: {identifier}")
            seen_ids.add(identifier)

        canon = archive.get("canon") or {}
        entities = canon.get("entities", [])
        media = archive.get("media") or {}
        total = (
            sum(
                len(archive.get(section, []) or [])
                for section in ("schemas", "commits", "objects", "tags")
            )
            + sum(
                len(canon.get(key, []) or []) for key in ("entities", "facts", "beliefs", "events")
            )
            + sum(len(media.get(key, []) or []) for key in ("assets", "versions"))
            + len((archive.get("plan") or {}).get("items", []) or [])
        )
        if total > MAX_ENTRIES:
            raise ArchiveError("archive exceeds entry limit")

        for raw in entities:
            if raw.get("workspace_id") not in (None, source_ws) or raw.get("project_id") not in (
                None,
                source_pr,
            ):
                raise ArchiveError("entity belongs to a different project")
            claim(raw["id"], kind="entity")
            entity_ids.add(raw["id"])

        def check_refs(ids: list[str], kind: str) -> None:
            for ref in ids:
                if ref not in entity_ids:
                    raise ArchiveError(f"{kind} references an entity outside the archive: {ref}")

        for raw in canon.get("facts", []):
            claim(raw["id"], kind="fact")
            check_refs([raw["proposition"]["subject_id"]], "fact")
            obj = raw["proposition"].get("object") or {}
            if obj.get("kind") == "entity_ref":
                check_refs([obj["entity_id"]], "fact")
        for raw in canon.get("beliefs", []):
            claim(raw["id"], kind="belief")
            check_refs([raw["holder_entity_id"], raw["proposition"]["subject_id"]], "belief")
            obj = raw["proposition"].get("object") or {}
            if obj.get("kind") == "entity_ref":
                check_refs([obj["entity_id"]], "belief")
        for raw in canon.get("events", []):
            claim(raw["id"], kind="event")
            check_refs([p["entity_id"] for p in raw.get("participants", [])], "event")
        for raw in (archive.get("plan") or {}).get("items", []):
            claim(raw["id"], kind="plan item")
            check_refs([ref["entity_id"] for ref in raw.get("refs", [])], "plan item")

        for raw in archive.get("schemas", []):
            claim(raw["id"], kind="schema")
        for raw in (archive.get("media") or {}).get("assets", []):
            claim(raw["id"], kind="asset")
        for raw in (archive.get("media") or {}).get("versions", []):
            claim(raw["id"], kind="asset_version")

        previous = 0
        for raw in sorted(archive.get("commits", []), key=lambda item: item["revision"]):
            if raw["expected_head"] != previous or raw["revision"] != previous + 1:
                raise ArchiveError("commit revisions are not contiguous")
            canonical = raw["canonical"]
            if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != raw["checksum"]:
                raise ArchiveError("commit canonical checksum mismatch")
            for op in json.loads(canonical):
                parse_operation(op)
            try:
                OriginKind(raw.get("origin", OriginKind.MIGRATION.value))
            except (TypeError, ValueError) as error:
                raise ArchiveError(f"unknown commit origin {raw.get('origin')!r}") from error
            mutation = raw.get("mutation")
            if mutation is not None and not isinstance(mutation, dict):
                raise ArchiveError("commit mutation metadata must be an object or null")
            if (mutation or {}).get("intent", MutationIntent.EDIT.value) not in {
                item.value for item in MutationIntent
            }:
                raise ArchiveError("unknown mutation intent in commit metadata")
            decisions = (mutation or {}).get("decisions", [])
            if not isinstance(decisions, list):
                raise ArchiveError("commit review decisions must be a list")
            for decision in decisions:
                if not isinstance(decision, dict):
                    raise ArchiveError("commit review decision must be an object")
                if decision.get("decision") not in {"approved", "rejected"}:
                    raise ArchiveError("unknown review decision in commit metadata")
            previous = raw["revision"]

        decoded_bytes = 0
        for raw in archive.get("objects", []):
            if not _safe_object_key(raw.get("key")):
                raise ArchiveError(f"unsafe object key: {raw.get('key')!r}")
            data = raw.get("data")
            if not isinstance(data, str):
                raise ArchiveError("object entry is missing its payload")
            try:
                decoded = base64.b64decode(data, validate=True)
            except (ValueError, binascii.Error) as error:
                raise ArchiveError("object entry has invalid base64 data") from error
            decoded_bytes += len(decoded)
            if decoded_bytes > MAX_ARCHIVE_BYTES:
                raise ArchiveError("archive exceeds decoded object byte limit")
            if hashlib.sha256(decoded).hexdigest() != raw.get("checksum"):
                raise ArchiveError("object checksum does not match its payload")
            if raw.get("byte_length") is not None and int(raw["byte_length"]) != len(decoded):
                raise ArchiveError("object byte_length does not match its payload")


__all__ = ["ARCHIVE_FORMAT", "FORMAT_VERSION", "ArchiveError", "CanonArchiveService"]
