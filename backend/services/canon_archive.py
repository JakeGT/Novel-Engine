"""Project archive (format v4) export/import.

Exports one project completely — schema versions, the ordered commit envelopes, the
canon snapshot, tags, media identity/version metadata, project objects (bytes), the
author's texts, name answers, rules and story-time unit, which text holds each scene,
what was read from each scene, the author's voice note, every what-if (where it
started, its own saved changes and named versions, its scene texts and readings) and
the conversations with the assistant — as a checksummed, self-contained archive.

Left out on purpose, and said so in the archive's ``left_out`` list: drafts waiting
for the author to keep or discard (working material, not the story), proposals not
yet saved, assistant jobs, provider usage, credentials and sessions.

Import is preflight-validated and idempotent: commits replay under deterministic
target-scoped commit ids, so re-importing the same archive into the same project
is a no-op. A what-if is started again from the main story at the version it first
started from, then its own changes replay on it. Project-scoped entity references are
rewritten for the target, so a cross-project restore can legitimately produce a
different commit checksum.

Version 3 archives (main story only) still import: everything version 4 adds is an
optional section. The version was raised because an older reader would have
restored a version 4 archive's main story and silently dropped its what-ifs and
conversations; a version check makes it refuse instead.

An import is recorded in a status ledger before its first durable write and is
marked ``completed`` only after every commit has replayed, the graph projection
has been rebuilt (when one is configured), and the journal, normalized canon,
and projection have been verified to agree. Each what-if's restored history is
checked against the archive's checksums too. A failed import stays visibly
unsuccessful; it can be retried or removed.

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
import re
from datetime import UTC, datetime
from typing import Any

from backend.core.logging import log_event
from backend.core.metrics import metrics
from backend.domain.archive import ArchiveImport, ArchiveImportError, ImportStatus
from backend.domain.assist import AssistSession
from backend.domain.branching import working_in
from backend.domain.canon.propositions import EntityRefObject
from backend.domain.canon.revisions import OriginKind
from backend.domain.dismissals import MAX_NOTE as MAX_DISMISSAL_NOTE
from backend.domain.dismissals import DismissReason, FindingDismissal, FindingKind
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
from backend.domain.rules import RuleBody
from backend.domain.tenancy import Permission
from backend.domain.validation import AuthoritativeValidator, RuleEnforcement
from backend.ports import (
    CanonJournalPort,
    CanonRepositoryPort,
    MediaStorePort,
    ObjectStorePort,
    SchemaVersionStorePort,
)

ARCHIVE_FORMAT = "novel-engine/canon-archive"
FORMAT_VERSION = "4"
# Every version this code can restore. A version 3 archive has no what-ifs,
# conversations or readings; it restores exactly as it always did.
READABLE_VERSIONS = frozenset({"3", "4"})
# What an archive does not carry, in the author's words.
LEFT_OUT = (
    "Drafts waiting for you to keep or discard: they are working material, not the story.",
    "Proposed changes not yet reviewed and saved.",
    "Assistant jobs, usage records, sign-in details and settings.",
)
MAX_NAME = 120
MAX_ENTRIES = 100_000
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
# History in an archive was already accepted once, so a replay checks the story's
# integrity and never the author's own rules: a rule set to block must not refuse
# the restore of a story it was never checked against, or a retry of one.
_REPLAY_VALIDATOR = AuthoritativeValidator()

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


def _dedup_branch_id(
    archive_checksum: str,
    source_branch_id: str,
    target_workspace_id: str,
    target_project_id: str,
) -> str:
    digest = hashlib.sha256(
        f"{archive_checksum}:branch:{source_branch_id}:{target_workspace_id}:{target_project_id}".encode()
    ).hexdigest()
    return f"brn_{digest[:32]}"


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


def _plan_commits(
    commits: list[dict[str, Any]],
    archive_checksum: str,
    target_workspace_id: str,
    target_project_id: str,
) -> list[dict[str, Any]]:
    """Target-scoped commits of one line of the story, in revision order (pure)."""
    plan: list[dict[str, Any]] = []
    for raw in sorted(commits, key=lambda item: item["revision"]):
        operations = [
            _remap_operation_scope(parse_operation(operation), target_project_id=target_project_id)
            for operation in json.loads(raw["canonical"])
        ]
        plan.append(
            {
                "raw": raw,
                "commit_id": _dedup_commit_id(
                    archive_checksum, raw["commit_id"], target_workspace_id, target_project_id
                ),
                "operations": operations,
                "canonical": canonicalize_operations(operations),
                "checksum": operations_checksum(operations),
            }
        )
    return plan


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
        sources: object | None = None,
        rules: object | None = None,
        clocks: object | None = None,
        studio: object | None = None,
        assists: object | None = None,
    ) -> None:
        self._canon = canon
        self._assists = assists
        self._clocks = clocks
        self._studio = studio
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
        self._sources = sources
        self._rules = rules

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
                "what_ifs": len(archive.get("what_ifs", [])),
                "conversations": len(archive.get("conversations", [])),
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
                "what_ifs": result.get("what_ifs", 0),
                "conversations": result.get("conversations", 0),
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
        for branch in await self._journal.list_branches(
            workspace_id=workspace_id, project_id=project_id
        ):
            if branch.is_root:
                continue
            for revision in await self._canon.list_commits(
                workspace_id=workspace_id,
                project_id=project_id,
                branch_id=branch.id,
                after_revision=branch.fork_revision or 0,
                limit=MAX_ENTRIES,
            ):
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
                        "a what-if holds changes this import did not write; it was not removed"
                    )
        if self._assists is not None:
            for session_id in record.created_records.get("assist_sessions", []):
                await self._assists.delete(  # type: ignore[attr-defined]
                    workspace_id=workspace_id, project_id=project_id, session_id=session_id
                )
        if self._rules is not None:
            for rule_id in record.created_records.get("story_rules", []):
                await self._rules.remove_rule(  # type: ignore[attr-defined]
                    workspace_id=workspace_id, project_id=project_id, rule_id=rule_id
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
        # An archive is the main story, whatever what-if the request was made from:
        # its records and its history must come from the same line (ADR-020).
        with working_in(None):
            return await self._export_main(workspace_id, project_id)

    async def _export_commits(
        self,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        *,
        after_revision: int = 0,
        mutation_commits: dict[str, str],
    ) -> list[dict[str, Any]]:
        """One line of the story's saved changes, oldest first, as archive entries.

        ``mutation_commits`` collects which saved change each proposal became, so the
        conversations and readings that point at a proposal can follow it on restore.
        """
        revisions = await self._canon.list_commits(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch_id,
            after_revision=after_revision,
            limit=MAX_ENTRIES,
        )
        commits: list[dict[str, Any]] = []
        for revision in revisions:
            envelope = await self._canon.get_envelope(revision.commit_id)
            if envelope is None:
                raise ArchiveError(f"missing envelope for revision {revision.revision}")
            mutation_commits[envelope.mutation_id] = envelope.commit_id
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
        return commits

    async def _export_main(self, workspace_id: str, project_id: str) -> dict[str, Any]:
        branch_id = await self._root_branch_id(workspace_id, project_id)
        schema_versions = await self._schemas.list(workspace_id=workspace_id, project_id=project_id)
        mutation_commits: dict[str, str] = {}
        commits = await self._export_commits(
            workspace_id, project_id, branch_id, mutation_commits=mutation_commits
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
                "head_revision": commits[-1]["revision"] if commits else 0,
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
        # The author's own writing and rules: theirs to take away, and never canon.
        # Left out entirely when there are none, so an archive without them is unchanged.
        what_ifs = await self._export_what_ifs(workspace_id, project_id, mutation_commits)
        writing = await self._export_writing(workspace_id, project_id, mutation_commits)
        if writing:
            body["writing"] = writing
        if what_ifs:
            carried = {text["id"] for text in (writing or {}).get("texts", [])}
            for what_if in what_ifs:
                await self._export_what_if_writing(
                    workspace_id, project_id, what_if, carried, mutation_commits
                )
            body["what_ifs"] = what_ifs
        conversations = await self._export_conversations(workspace_id, project_id, mutation_commits)
        if conversations:
            body["conversations"] = conversations
        story_rules = await self._export_rules(workspace_id, project_id)
        if story_rules:
            body["story_rules"] = story_rules
        if self._clocks is not None:
            unit = await self._clocks.get_unit(  # type: ignore[attr-defined]
                workspace_id=workspace_id, project_id=project_id
            )
            if unit:
                body["story_time"] = {"unit": unit}
        # Said in the archive itself, so whoever opens it knows what is not there.
        body["left_out"] = list(LEFT_OUT)
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
            what_ifs=len(what_ifs),
        )
        return body

    async def _export_what_ifs(
        self, workspace_id: str, project_id: str, mutation_commits: dict[str, str]
    ) -> list[dict[str, Any]]:
        """Every what-if: where it started, its own saved changes and its named versions.

        What it shares with the main story is not repeated; restoring the main story up
        to the version the what-if started from and then its own changes rebuilds it.
        """
        found: list[dict[str, Any]] = []
        branches = await self._journal.list_branches(
            workspace_id=workspace_id, project_id=project_id
        )
        for branch in sorted(branches, key=lambda item: (item.created_at, item.id)):
            if branch.is_root:
                continue
            fork = branch.fork_revision or 0
            commits = await self._export_commits(
                workspace_id,
                project_id,
                branch.id,
                after_revision=fork,
                mutation_commits=mutation_commits,
            )
            tags = await self._canon.list_tags(
                workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
            )
            found.append(
                {
                    "id": branch.id,
                    "name": branch.name,
                    "description": branch.description,
                    "status": branch.status,
                    "created_by": branch.created_by,
                    "created_at": branch.created_at.isoformat(),
                    "fork_revision": fork,
                    "commits": commits,
                    "tags": [
                        {
                            "name": tag.name,
                            "revision": tag.revision,
                            "commit_id": tag.commit_id,
                            "created_by_user_id": tag.created_by_user_id,
                        }
                        for tag in tags
                    ],
                }
            )
        return found

    async def _export_readings(
        self,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        carried: set[str],
        mutation_commits: dict[str, str],
    ) -> list[dict[str, Any]]:
        """What was read from each scene in one line of the story, oldest first.

        A proposal a reading made travels as the saved change it became; one that was
        never saved is not in the archive, so it is not pointed at.
        """
        if self._studio is None:
            return []
        readings = await self._studio.readings_in(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
        )
        return [
            {
                "id": reading.id,
                "plan_id": reading.plan_id,
                "text_id": reading.source_id,
                "version_id": reading.version_id,
                "passages": reading.passages,
                "proposal_commits": [
                    mutation_commits[item] for item in reading.proposals if item in mutation_commits
                ],
                "discarded": reading.discarded,
                "created_by": reading.created_by,
                "created_at": reading.created_at.isoformat(),
                "status": reading.status,
                "covered": [list(span) for span in reading.covered],
                "base_reading_id": reading.base_reading_id,
            }
            for reading in readings
            if reading.source_id in carried
        ]

    async def _export_dismissals(
        self, workspace_id: str, project_id: str, branch_id: str
    ) -> list[dict[str, Any]]:
        """The author's dismissed findings in one line of the story, withdrawn ones too.

        Each holds a fingerprint of the words it was about, never the words.
        """
        if self._studio is None:
            return []
        return [
            {
                "id": item.id,
                "plan_id": item.plan_id,
                "fingerprint": item.fingerprint,
                "kind": item.kind.value,
                "code": item.code,
                "reason": item.reason.value,
                "note": item.note,
                "expires_at": item.expires_at.isoformat() if item.expires_at else None,
                "created_by": item.created_by,
                "created_at": item.created_at.isoformat(),
                "withdrawn_at": item.withdrawn_at.isoformat() if item.withdrawn_at else None,
            }
            for item in await self._studio.dismissals_in(  # type: ignore[attr-defined]
                workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
            )
        ]

    async def _export_what_if_writing(
        self,
        workspace_id: str,
        project_id: str,
        what_if: dict[str, Any],
        carried: set[str],
        mutation_commits: dict[str, str],
    ) -> None:
        """Which text holds each scene in a what-if, and what was read from it there."""
        if self._studio is None:
            return
        scenes = await self._studio.manuscripts_for(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id, branch_id=what_if["id"]
        )
        links = [{"plan_id": plan, "text_id": text} for plan, text in scenes if text in carried]
        if links:
            what_if["scenes"] = links
        readings = await self._export_readings(
            workspace_id, project_id, what_if["id"], carried, mutation_commits
        )
        if readings:
            what_if["readings"] = readings
        dismissals = await self._export_dismissals(workspace_id, project_id, what_if["id"])
        if dismissals:
            what_if["dismissals"] = dismissals

    async def _export_conversations(
        self, workspace_id: str, project_id: str, mutation_commits: dict[str, str]
    ) -> list[dict[str, Any]]:
        """The author's conversations with the assistant, kept as the evidence they are.

        The proposal a conversation led to travels as the saved change it became.
        """
        if self._assists is None:
            return []
        sessions = await self._assists.list_for_project(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id, limit=MAX_ENTRIES
        )
        found = []
        for session in sessions:
            entry = session.model_dump(mode="json", exclude={"workspace_id", "project_id"})
            entry["commit_id"] = mutation_commits.get(entry.pop("mutation_id") or "")
            found.append(entry)
        return found

    async def _export_writing(
        self,
        workspace_id: str,
        project_id: str,
        mutation_commits: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if self._sources is None:
            return {}
        texts: list[dict[str, Any]] = []
        for source in await self._sources.list_sources(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id
        ):
            versions = await self._sources.list_versions(  # type: ignore[attr-defined]
                workspace_id=workspace_id, project_id=project_id, source_id=source.id
            )
            texts.append(
                {
                    "id": source.id,
                    "title": source.title,
                    "kind": source.kind.value,
                    "versions": [
                        {
                            "id": version.id,
                            "number": version.number,
                            "content": version.content,
                            "content_hash": version.content_hash,
                            # Only when not the author's, so older archives are unchanged.
                            **(
                                {"origin": version.origin.value}
                                if version.origin.value != "author"
                                else {}
                            ),
                        }
                        for version in versions
                    ],
                }
            )
        names = [
            {
                "id": binding.id,
                "phrase": binding.phrase,
                "verdict": binding.verdict.value,
                "entity_id": binding.entity_id,
                "source_id": binding.source_id,
                # Limits and examples only when set, so older archives read the same (CTX-016).
                **{
                    key: value
                    for key, value in (
                        ("branch_id", binding.branch_id),
                        ("from_scene_id", binding.from_scene_id),
                        ("until_scene_id", binding.until_scene_id),
                        ("viewpoint_id", binding.viewpoint_id),
                    )
                    if value is not None
                },
                **(
                    {"examples": [example.as_json() for example in binding.examples]}
                    if binding.examples
                    else {}
                ),
            }
            for binding in await self._sources.list_bindings(  # type: ignore[attr-defined]
                workspace_id=workspace_id, project_id=project_id
            )
            # A model's unanswered guess is not the author's and is not carried.
            if binding.verdict.value != "suggested"
        ]
        found: dict[str, Any] = {"texts": texts, "names": names} if texts or names else {}
        if self._studio is not None and self._journal is not None:
            main = await self._journal.working_branch(  # type: ignore[attr-defined]
                workspace_id=workspace_id, project_id=project_id
            )
            scenes = await self._studio.manuscripts_for(  # type: ignore[attr-defined]
                workspace_id=workspace_id, project_id=project_id, branch_id=main.id
            )
            # Only links to texts the archive carries: a removed text is not exported.
            carried = {item["id"] for item in texts}
            scenes = [(plan, text) for plan, text in scenes if text in carried]
            if scenes:
                found["scenes"] = [{"plan_id": plan, "text_id": text} for plan, text in scenes]
            readings = await self._export_readings(
                workspace_id, project_id, main.id, carried, mutation_commits or {}
            )
            if readings:
                found["readings"] = readings
            dismissals = await self._export_dismissals(workspace_id, project_id, main.id)
            if dismissals:
                found["dismissals"] = dismissals
            voice = await self._studio.voice(  # type: ignore[attr-defined]
                workspace_id=workspace_id, project_id=project_id
            )
            if voice:
                found["voice"] = voice
        return found

    async def _export_rules(self, workspace_id: str, project_id: str) -> list[dict[str, Any]]:
        if self._rules is None:
            return []
        found: list[dict[str, Any]] = []
        for rule in await self._rules.list_rules(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id
        ):
            versions = await self._rules.list_versions(  # type: ignore[attr-defined]
                workspace_id=workspace_id, project_id=project_id, rule_id=rule.id
            )
            found.append(
                {
                    "id": rule.id,
                    "title": rule.title,
                    "sentence": rule.sentence,
                    "status": rule.status.value,
                    "versions": [
                        {
                            "number": number,
                            "body": body.model_dump(mode="json"),
                            "examples": [item.model_dump(mode="json") for item in examples],
                        }
                        for number, body, examples in versions
                    ],
                    # Said only for a rule set to block, so a warning rule's entry (and any
                    # archive made before rules could block) reads the same as before.
                    **(
                        {"enforcement": rule.enforcement.value}
                        if rule.enforcement is RuleEnforcement.BLOCKING
                        else {}
                    ),
                }
            )
        return found

    async def _restore_writing(
        self,
        workspace_id: str,
        project_id: str,
        archive: dict[str, Any],
        *,
        actor_user_id: str | None,
        import_id: str | None = None,
    ) -> dict[str, int]:
        """Bring back the author's texts, name answers and rules, after canon is restored.

        Safe to run again: whatever is already there is left as it is, so an import
        interrupted here completes on the next attempt.
        """
        from datetime import UTC, datetime

        from backend.domain.canon.naming import normalize_name
        from backend.domain.rules import RuleBody, RuleExample, RuleStatus
        from backend.domain.sources import (
            MAX_EXAMPLES,
            AliasBinding,
            NameExample,
            SourceKind,
            Verdict,
            VersionOrigin,
            content_hash,
        )

        restored = {"texts": 0, "names": 0, "rules": 0}
        writing = archive.get("writing") or {}
        if self._sources is not None:
            for text in writing.get("texts", []):
                existing = await self._sources.get_source(  # type: ignore[attr-defined]
                    workspace_id=workspace_id, project_id=project_id, source_id=text["id"]
                )
                if existing is not None:
                    continue
                await self._sources.add_source(  # type: ignore[attr-defined]
                    workspace_id=workspace_id,
                    project_id=project_id,
                    source_id=text["id"],
                    title=text["title"],
                    kind=SourceKind(text["kind"]),
                    actor_user_id=actor_user_id,
                )
                previous: str | None = None
                for version in sorted(text["versions"], key=lambda item: item["number"]):
                    await self._sources.add_version(  # type: ignore[attr-defined]
                        workspace_id=workspace_id,
                        project_id=project_id,
                        version_id=version["id"],
                        source_id=text["id"],
                        content=version["content"],
                        content_hash=content_hash(version["content"]),
                        actor_user_id=actor_user_id,
                        origin=VersionOrigin(version.get("origin", "author")),
                        parent_version_id=previous,
                    )
                    previous = version["id"]
                restored["texts"] += 1
            known = {
                binding.id
                for binding in await self._sources.list_bindings(  # type: ignore[attr-defined]
                    workspace_id=workspace_id, project_id=project_id
                )
            }
            for name in writing.get("names", []):
                if name["id"] in known:
                    continue
                await self._sources.put_binding(  # type: ignore[attr-defined]
                    AliasBinding(
                        workspace_id=workspace_id,
                        project_id=project_id,
                        id=name["id"],
                        phrase=name["phrase"],
                        normalized=normalize_name(name["phrase"]),
                        verdict=Verdict(name["verdict"]),
                        entity_id=name.get("entity_id"),
                        source_id=name.get("source_id"),
                        decided_by=actor_user_id,
                        decided_at=datetime.now(UTC),
                        # A what-if comes back under a new id; its answers follow it.
                        branch_id=_dedup_branch_id(
                            archive["checksum"], name["branch_id"], workspace_id, project_id
                        )
                        if name.get("branch_id")
                        else None,
                        from_scene_id=name.get("from_scene_id"),
                        until_scene_id=name.get("until_scene_id"),
                        viewpoint_id=name.get("viewpoint_id"),
                        examples=tuple(
                            NameExample.from_json(example)
                            for example in name.get("examples") or []
                            if isinstance(example, dict)
                        )[:MAX_EXAMPLES],
                    )
                )
                restored["names"] += 1
        if self._studio is not None and self._journal is not None:
            main = await self._journal.working_branch(  # type: ignore[attr-defined]
                workspace_id=workspace_id, project_id=project_id
            )
            for link in writing.get("scenes", []):
                # A link to a text the archive does not carry would point at nothing.
                if not isinstance(link, dict) or not await self._sources.get_source(  # type: ignore[union-attr, attr-defined]
                    workspace_id=workspace_id, project_id=project_id, source_id=link.get("text_id")
                ):
                    continue
                current = await self._studio.manuscript(  # type: ignore[attr-defined]
                    workspace_id=workspace_id,
                    project_id=project_id,
                    branch_id=main.id,
                    plan_id=link["plan_id"],
                )
                if current is None:
                    await self._studio.link(  # type: ignore[attr-defined]
                        workspace_id=workspace_id,
                        project_id=project_id,
                        branch_id=main.id,
                        plan_id=link["plan_id"],
                        source_id=link["text_id"],
                    )
            voice = writing.get("voice")
            if isinstance(voice, str) and voice.strip():
                if not await self._studio.voice(  # type: ignore[attr-defined]
                    workspace_id=workspace_id, project_id=project_id
                ):
                    await self._studio.set_voice(  # type: ignore[attr-defined]
                        workspace_id=workspace_id, project_id=project_id, notes=voice[:4000]
                    )
        unit = (archive.get("story_time") or {}).get("unit")
        if self._clocks is not None and isinstance(unit, str) and unit.strip():
            current = await self._clocks.get_unit(  # type: ignore[attr-defined]
                workspace_id=workspace_id, project_id=project_id
            )
            if current is None:
                await self._clocks.set_unit(  # type: ignore[attr-defined]
                    workspace_id=workspace_id, project_id=project_id, unit=unit.strip()[:30]
                )
        if self._rules is not None:
            for rule in archive.get("story_rules", []):
                existing = await self._rules.get_rule(  # type: ignore[attr-defined]
                    workspace_id=workspace_id, project_id=project_id, rule_id=rule["id"]
                )
                if existing is not None:
                    continue
                if import_id is not None:
                    # Attributed before it is written, so removing a failed import
                    # removes it too; removing one never written does nothing.
                    await self._record_created(
                        workspace_id, project_id, import_id, "story_rules", rule["id"]
                    )
                versions = sorted(rule["versions"], key=lambda item: item["number"])
                wordings = [
                    (
                        RuleBody.model_validate(version["body"]),
                        [RuleExample.model_validate(item) for item in version.get("examples", [])],
                    )
                    for version in versions
                ]
                await self._rules.add_rule(  # type: ignore[attr-defined]
                    workspace_id=workspace_id,
                    project_id=project_id,
                    rule_id=rule["id"],
                    title=rule["title"],
                    sentence=rule.get("sentence", ""),
                    body=wordings[0][0],
                    examples=wordings[0][1],
                    actor_user_id=actor_user_id,
                )
                for body, examples in wordings[1:]:
                    await self._rules.change_rule(  # type: ignore[attr-defined]
                        workspace_id=workspace_id,
                        project_id=project_id,
                        rule_id=rule["id"],
                        body=body,
                        examples=examples,
                        actor_user_id=actor_user_id,
                    )
                if rule.get("status") == RuleStatus.PAUSED.value:
                    await self._rules.change_rule(  # type: ignore[attr-defined]
                        workspace_id=workspace_id,
                        project_id=project_id,
                        rule_id=rule["id"],
                        status=RuleStatus.PAUSED,
                    )
                if rule.get("enforcement") == RuleEnforcement.BLOCKING.value:
                    await self._rules.change_rule(  # type: ignore[attr-defined]
                        workspace_id=workspace_id,
                        project_id=project_id,
                        rule_id=rule["id"],
                        enforcement=RuleEnforcement.BLOCKING,
                    )
                restored["rules"] += 1
        return restored

    async def _record_created(
        self, workspace_id: str, project_id: str, import_id: str, key: str, record_id: str
    ) -> None:
        """Attribute one record to an import, so removing the import removes it."""
        record = next(
            (
                item
                for item in await self._canon.list_imports(
                    workspace_id=workspace_id, project_id=project_id
                )
                if item.id == import_id
            ),
            None,
        )
        created = {
            name: list(value) for name, value in (record.created_records if record else {}).items()
        }
        ids = created.setdefault(key, [])
        if record_id not in ids:
            ids.append(record_id)
            await self._canon.update_import(
                import_id=import_id, stage="writing", created_records=created
            )

    async def _mutation_for(
        self, archive_checksum: str, workspace_id: str, project_id: str, commit_id: object
    ) -> str | None:
        """The restored proposal behind a saved change the archive names, if it is there."""
        if not isinstance(commit_id, str) or not commit_id:
            return None
        envelope = await self._canon.get_envelope(
            _dedup_commit_id(archive_checksum, commit_id, workspace_id, project_id)
        )
        return envelope.mutation_id if envelope is not None else None

    async def _restore_readings(
        self,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        readings: list[dict[str, Any]],
        archive_checksum: str,
    ) -> int:
        from backend.domain.studio import SceneReading

        if self._studio is None or self._sources is None:
            return 0
        restored = 0
        for raw in readings:
            # A reading of a text that is not there would point at nothing.
            if not await self._sources.get_source(  # type: ignore[attr-defined]
                workspace_id=workspace_id, project_id=project_id, source_id=raw["text_id"]
            ):
                continue
            proposals = [
                mutation
                for commit in raw.get("proposal_commits", [])
                if (
                    mutation := await self._mutation_for(
                        archive_checksum, workspace_id, project_id, commit
                    )
                )
            ]
            added = await self._studio.restore_reading(  # type: ignore[attr-defined]
                SceneReading(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    branch_id=branch_id,
                    id=raw["id"],
                    plan_id=raw["plan_id"],
                    source_id=raw["text_id"],
                    version_id=raw["version_id"],
                    # The job that read it is not carried; the reading stands on its own.
                    job_id=None,
                    passages=int(raw.get("passages", 0)),
                    proposals=tuple(proposals),
                    discarded=int(raw.get("discarded", 0)),
                    created_by=raw.get("created_by"),
                    created_at=datetime.fromisoformat(raw["created_at"]),
                    status=raw.get("status", "complete"),
                    covered=tuple((int(a), int(b)) for a, b in raw.get("covered", [])),
                    base_reading_id=raw.get("base_reading_id"),
                )
            )
            restored += 1 if added else 0
        return restored

    async def _restore_dismissals(
        self, workspace_id: str, project_id: str, branch_id: str, dismissals: list[dict[str, Any]]
    ) -> int:
        if self._studio is None:
            return 0

        def when(value: object) -> datetime | None:
            return datetime.fromisoformat(str(value)) if value else None

        restored = 0
        for raw in dismissals:
            added = await self._studio.restore_dismissal(  # type: ignore[attr-defined]
                FindingDismissal(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    branch_id=branch_id,
                    id=raw["id"],
                    plan_id=raw["plan_id"],
                    fingerprint=raw["fingerprint"],
                    kind=FindingKind(raw["kind"]),
                    code=raw["code"],
                    reason=DismissReason(raw["reason"]),
                    note=raw.get("note", ""),
                    expires_at=when(raw.get("expires_at")),
                    created_by=raw.get("created_by"),
                    created_at=datetime.fromisoformat(raw["created_at"]),
                    withdrawn_at=when(raw.get("withdrawn_at")),
                )
            )
            restored += 1 if added else 0
        return restored

    async def _restore_additions(
        self,
        workspace_id: str,
        project_id: str,
        archive: dict[str, Any],
        *,
        import_id: str,
    ) -> dict[str, int]:
        """What version 4 adds beyond the story itself: each what-if's scene texts, and in
        every line of the story its readings and dismissed findings; then the
        conversations with the assistant.

        Safe to run again: what is already there is left as it is.
        """
        checksum = archive["checksum"]
        readings = dismissed = 0
        if self._studio is not None:
            main = await self._root_branch_id(workspace_id, project_id)
            dismissed += await self._restore_dismissals(
                workspace_id,
                project_id,
                main,
                (archive.get("writing") or {}).get("dismissals", []),
            )
            for raw in archive.get("what_ifs", []):
                dismissed += await self._restore_dismissals(
                    workspace_id,
                    project_id,
                    _dedup_branch_id(checksum, raw["id"], workspace_id, project_id),
                    raw.get("dismissals", []),
                )
        if self._studio is not None and self._sources is not None:
            readings += await self._restore_readings(
                workspace_id,
                project_id,
                main,
                (archive.get("writing") or {}).get("readings", []),
                checksum,
            )
            for raw in archive.get("what_ifs", []):
                what_if_id = _dedup_branch_id(checksum, raw["id"], workspace_id, project_id)
                for link in raw.get("scenes", []):
                    if not await self._sources.get_source(  # type: ignore[attr-defined]
                        workspace_id=workspace_id, project_id=project_id, source_id=link["text_id"]
                    ):
                        continue
                    current = await self._studio.manuscript(  # type: ignore[attr-defined]
                        workspace_id=workspace_id,
                        project_id=project_id,
                        branch_id=what_if_id,
                        plan_id=link["plan_id"],
                    )
                    if current != link["text_id"]:
                        await self._studio.link(  # type: ignore[attr-defined]
                            workspace_id=workspace_id,
                            project_id=project_id,
                            branch_id=what_if_id,
                            plan_id=link["plan_id"],
                            source_id=link["text_id"],
                        )
                readings += await self._restore_readings(
                    workspace_id, project_id, what_if_id, raw.get("readings", []), checksum
                )
        conversations = 0
        if self._assists is not None and archive.get("conversations"):
            prior = next(
                (
                    item
                    for item in await self._canon.list_imports(
                        workspace_id=workspace_id, project_id=project_id
                    )
                    if item.id == import_id
                ),
                None,
            )
            created = {
                key: list(value) for key, value in (prior.created_records if prior else {}).items()
            }
            mine = created.setdefault("assist_sessions", [])
            copy_suffix = hashlib.sha256(
                f"{checksum}:{workspace_id}:{project_id}".encode()
            ).hexdigest()[:12]
            for raw in archive["conversations"]:
                fields = {key: value for key, value in raw.items() if key != "commit_id"}
                # The id is kept, because the saved changes it led to cite it as their
                # evidence. Only when another project in this database already holds it
                # (a copy restored beside its original) does the copy get its own.
                candidates = [raw["id"], f"{raw['id']}-{copy_suffix}"]
                present = [
                    await self._assists.get(  # type: ignore[attr-defined]
                        workspace_id=workspace_id, project_id=project_id, session_id=candidate
                    )
                    for candidate in candidates
                ]
                if any(present):
                    continue
                mutation_id = await self._mutation_for(
                    checksum, workspace_id, project_id, raw.get("commit_id")
                )
                for candidate in candidates:
                    await self._assists.save(  # type: ignore[attr-defined]
                        AssistSession.model_validate(
                            {
                                **fields,
                                "id": candidate,
                                "workspace_id": workspace_id,
                                "project_id": project_id,
                                "mutation_id": mutation_id,
                            }
                        )
                    )
                    if await self._assists.get(  # type: ignore[attr-defined]
                        workspace_id=workspace_id, project_id=project_id, session_id=candidate
                    ):
                        mine.append(candidate)
                        conversations += 1
                        # Attributed at once: if a later one fails, removing the import
                        # still removes this one, which a retry would skip as present.
                        await self._canon.update_import(
                            import_id=import_id, stage="conversations", created_records=created
                        )
                        break
                else:
                    raise ArchiveError("a conversation could not be restored")
        return {"conversations": conversations, "readings": readings, "dismissals": dismissed}

    # ---------------------------------------------------------------- import
    async def import_project(
        self,
        target_workspace_id: str,
        target_project_id: str,
        archive: dict[str, Any],
        *,
        actor_user_id: str | None = None,
    ) -> dict[str, Any]:
        # The main story is restored as the main story, whatever line the request named.
        with working_in(None):
            return await self._import(
                target_workspace_id, target_project_id, archive, actor_user_id=actor_user_id
            )

    async def _import(
        self,
        target_workspace_id: str,
        target_project_id: str,
        archive: dict[str, Any],
        *,
        actor_user_id: str | None,
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
            commits_total=len(archive["commits"])
            + sum(len(item.get("commits", [])) for item in archive.get("what_ifs", [])),
            actor_user_id=actor_user_id,
        )
        try:
            result = await self._restore(
                target_workspace_id, target_project_id, archive, import_id=import_id
            )
            # Only once the story itself is back: its texts, name answers and rules.
            result = {
                **result,
                "writing": await self._restore_writing(
                    target_workspace_id,
                    target_project_id,
                    archive,
                    actor_user_id=actor_user_id,
                    import_id=import_id,
                ),
                **await self._restore_additions(
                    target_workspace_id, target_project_id, archive, import_id=import_id
                ),
            }
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
        """Target-scoped main-story commits in revision order (pure; no I/O)."""
        return _plan_commits(
            archive["commits"], archive["checksum"], target_workspace_id, target_project_id
        )

    def _what_if_plans(
        self, target_workspace_id: str, target_project_id: str, archive: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Each what-if with its target id and its own target-scoped commits (pure)."""
        return [
            {
                "raw": raw,
                "branch_id": _dedup_branch_id(
                    archive["checksum"], raw["id"], target_workspace_id, target_project_id
                ),
                "commits": _plan_commits(
                    raw.get("commits", []),
                    archive["checksum"],
                    target_workspace_id,
                    target_project_id,
                ),
            }
            for raw in archive.get("what_ifs", [])
        ]

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
        plans = self._what_if_plans(target_workspace_id, target_project_id, archive)
        if not plans:
            return
        ours = {plan["branch_id"]: plan for plan in plans}
        for branch in await self._journal.list_branches(
            workspace_id=target_workspace_id, project_id=target_project_id
        ):
            if branch.is_root:
                continue
            what_if = ours.get(branch.id)
            if what_if is None:
                # Its name could clash, and its changes are not this archive's to keep.
                raise ArchiveError("target project already contains unrelated what-ifs")
            fork = int(what_if["raw"]["fork_revision"])
            head = await self._canon.get_head(
                workspace_id=target_workspace_id, project_id=target_project_id, branch_id=branch.id
            )
            if branch.fork_revision != fork or head - fork > len(what_if["commits"]):
                raise ArchiveError("target project already contains unrelated what-ifs")
            for item in what_if["commits"][: head - fork]:
                existing_envelope = await self._canon.get_envelope(item["commit_id"])
                if existing_envelope is None or existing_envelope.checksum != item["checksum"]:
                    raise ArchiveError("target project already contains unrelated what-ifs")

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
            key: list(value)
            for key, value in (prior_record.created_records if prior_record else {}).items()
        }
        for key in ("schema_versions", "assets", "asset_versions"):
            created.setdefault(key, [])
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
        what_ifs = self._what_if_plans(target_workspace_id, target_project_id, archive)
        for plan in what_ifs:
            for item in plan["commits"]:
                commit_id_map[item["raw"]["commit_id"]] = item["commit_id"]
        forks: dict[int, list[dict[str, Any]]] = {}
        for plan in what_ifs:
            forks.setdefault(int(plan["raw"]["fork_revision"]), []).append(plan)

        async def start_what_ifs_at(revision: int) -> None:
            # A what-if starts again exactly where it first started: before the main
            # story moves past that version.
            for plan in forks.pop(revision, []):
                await self._restart_what_if(
                    target_workspace_id, target_project_id, branch_id, plan, revision
                )

        applied = 0
        for item in imported_commits:
            commit_id_map[item["raw"]["commit_id"]] = item["commit_id"]
            await start_what_ifs_at(item["raw"]["revision"] - 1)
            await self._replay_commit(
                target_workspace_id, target_project_id, branch_id, item, schema_map
            )
            applied += 1
            await self._canon.update_import(
                import_id=import_id, stage="commits", commits_applied=applied
            )
        await start_what_ifs_at(len(imported_commits))

        await self._restore_tags(
            target_workspace_id, target_project_id, branch_id, archive["tags"], commit_id_map
        )

        for plan in what_ifs:
            what_if_id = plan["branch_id"]
            # Validators read the line being written; in a what-if that is the what-if.
            with working_in(what_if_id):
                for item in plan["commits"]:
                    await self._replay_commit(
                        target_workspace_id, target_project_id, what_if_id, item, schema_map
                    )
                    applied += 1
                    await self._canon.update_import(
                        import_id=import_id, stage="commits", commits_applied=applied
                    )
            await self._restore_tags(
                target_workspace_id,
                target_project_id,
                what_if_id,
                plan["raw"].get("tags", []),
                commit_id_map,
            )
            if plan["raw"].get("status") == "set_aside":
                await self._canon.set_branch_status(  # type: ignore[attr-defined]
                    workspace_id=target_workspace_id,
                    project_id=target_project_id,
                    branch_id=what_if_id,
                    status="set_aside",
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
        # Each what-if's history must be exactly the archive's: same versions, same checksums.
        for plan in what_ifs:
            fork = int(plan["raw"]["fork_revision"])
            restored = await self._canon.list_commits(
                workspace_id=target_workspace_id,
                project_id=target_project_id,
                branch_id=plan["branch_id"],
                after_revision=fork,
                limit=MAX_ENTRIES,
            )
            expected = [(item["raw"]["revision"], item["checksum"]) for item in plan["commits"]]
            found = [(rev.revision, rev.envelope_checksum) for rev in restored]
            if found != expected:
                raise ArchiveError("a restored what-if failed verification")

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
            "what_ifs": len(what_ifs),
            "what_if_commits": sum(len(plan["commits"]) for plan in what_ifs),
            "checksum": archive_checksum,
            "projection_rebuilt": projection_rebuilt,
            "verified": verified,
        }

    async def _replay_commit(
        self,
        target_workspace_id: str,
        target_project_id: str,
        branch_id: str,
        item: dict[str, Any],
        schema_map: dict[str, str],
    ) -> None:
        """Replay one saved change onto one line of the story; a no-op when already there."""
        raw = item["raw"]
        new_commit_id = item["commit_id"]
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
            validator=_REPLAY_VALIDATOR,
            origin=source_origin,
        )

    async def _restore_tags(
        self,
        target_workspace_id: str,
        target_project_id: str,
        branch_id: str,
        tags: list[dict[str, Any]],
        commit_id_map: dict[str, str],
    ) -> None:
        for raw in tags:
            commit_id = commit_id_map.get(raw["commit_id"], raw["commit_id"])
            current = await self._canon.get_tag(
                workspace_id=target_workspace_id,
                project_id=target_project_id,
                branch_id=branch_id,
                name=raw["name"],
            )
            if current is not None and (current.revision, current.commit_id) == (
                raw["revision"],
                commit_id,
            ):
                continue
            now = datetime.now(UTC)
            await self._canon.create_tag(
                CanonTag(
                    workspace_id=target_workspace_id,
                    project_id=target_project_id,
                    branch_id=branch_id,
                    name=raw["name"],
                    revision=raw["revision"],
                    commit_id=commit_id,
                    created_by_user_id=None,
                    created_at=now,
                    updated_at=now,
                )
            )

    async def _restart_what_if(
        self,
        target_workspace_id: str,
        target_project_id: str,
        main_id: str,
        plan: dict[str, Any],
        revision: int,
    ) -> None:
        """Start a what-if again from the main story, at the version it first started from."""
        raw = plan["raw"]
        existing = await self._journal.get_branch(
            workspace_id=target_workspace_id,
            project_id=target_project_id,
            branch_id=plan["branch_id"],
        )
        if existing is not None:
            return
        head = await self._canon.get_head(
            workspace_id=target_workspace_id, project_id=target_project_id, branch_id=main_id
        )
        if head != revision:
            raise ArchiveError("a what-if could not be started again where it first started")
        await self._canon.fork_branch(  # type: ignore[attr-defined]
            workspace_id=target_workspace_id,
            project_id=target_project_id,
            parent_branch_id=main_id,
            branch_id=plan["branch_id"],
            name=" ".join(str(raw["name"]).split()),
            description=raw.get("description"),
            actor_user_id=raw.get("created_by"),
        )

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
    def _validate_commits(commits: list[dict[str, Any]], start: int) -> int:
        """Check one line's saved changes follow on from ``start``; return its last version."""
        previous = start
        for raw in sorted(commits, key=lambda item: item["revision"]):
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
        return previous

    @staticmethod
    def _validate_readings(readings: object, text_ids: set[str]) -> None:
        if not isinstance(readings, list):
            raise ArchiveError("a scene's readings are malformed")
        seen: set[str] = set()
        for raw in readings:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
                raise ArchiveError("a scene's reading is malformed")
            if raw["id"] in seen:
                raise ArchiveError(f"duplicate reading id: {raw['id']}")
            seen.add(raw["id"])
            if not isinstance(raw.get("plan_id"), str) or not isinstance(
                raw.get("version_id"), str
            ):
                raise ArchiveError("a scene's reading is malformed")
            if raw.get("text_id") not in text_ids:
                raise ArchiveError("a reading points at a text outside the archive")
            if raw.get("status", "complete") not in {"partial", "complete"}:
                raise ArchiveError("a scene's reading is malformed")
            passages, discarded = raw.get("passages", 0), raw.get("discarded", 0)
            if not all(isinstance(n, int) and n >= 0 for n in (passages, discarded)):
                raise ArchiveError("a scene's reading is malformed")
            covered = raw.get("covered", [])
            if not isinstance(covered, list) or not all(
                isinstance(span, list) and len(span) == 2 and all(isinstance(n, int) for n in span)
                for span in covered
            ):
                raise ArchiveError("a scene's reading is malformed")
            proposals = raw.get("proposal_commits", [])
            if not isinstance(proposals, list) or not all(isinstance(c, str) for c in proposals):
                raise ArchiveError("a scene's reading is malformed")
            try:
                datetime.fromisoformat(str(raw.get("created_at")))
            except ValueError as error:
                raise ArchiveError("a scene's reading is malformed") from error

    @staticmethod
    def _validate_dismissals(dismissals: object) -> None:
        if not isinstance(dismissals, list):
            raise ArchiveError("dismissed findings are malformed")
        seen: set[str] = set()
        for raw in dismissals:
            if (
                not isinstance(raw, dict)
                or not isinstance(raw.get("id"), str)
                or not raw["id"]
                or raw["id"] in seen
                or not isinstance(raw.get("plan_id"), str)
                or not isinstance(raw.get("code"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", str(raw.get("fingerprint")))
                or raw.get("kind") not in {item.value for item in FindingKind}
                or raw.get("reason") not in {item.value for item in DismissReason}
                or not isinstance(raw.get("note", ""), str)
                or len(raw.get("note", "")) > MAX_DISMISSAL_NOTE
            ):
                raise ArchiveError("a dismissed finding is malformed")
            seen.add(raw["id"])
            try:
                for key in ("created_at", "expires_at", "withdrawn_at"):
                    if key == "created_at" or raw.get(key) is not None:
                        datetime.fromisoformat(str(raw.get(key)))
            except ValueError as error:
                raise ArchiveError("a dismissed finding is malformed") from error

    @staticmethod
    def _validate_additions(
        archive: dict[str, Any],
        *,
        main_head: int,
        text_ids: set[str],
        claim: Any,
    ) -> None:
        """What version 4 adds: what-ifs, conversations and scene readings."""
        what_ifs = archive.get("what_ifs", [])
        conversations = archive.get("conversations", [])
        if not isinstance(what_ifs, list) or not isinstance(conversations, list):
            raise ArchiveError("archive what-ifs or conversations are malformed")
        total = len(conversations) + sum(
            len(item.get("commits", []) or [])
            + len(item.get("readings", []) or [])
            + len(item.get("dismissals", []) or [])
            for item in what_ifs
            if isinstance(item, dict)
        )
        if total > MAX_ENTRIES:
            raise ArchiveError("archive exceeds entry limit")
        writing = archive.get("writing") or {}
        CanonArchiveService._validate_readings(writing.get("readings", []), text_ids)
        CanonArchiveService._validate_dismissals(writing.get("dismissals", []))
        commit_ids = {raw["commit_id"] for raw in archive.get("commits", [])}
        if len(commit_ids) != len(archive.get("commits", [])):
            raise ArchiveError("duplicate commit id in archive")
        names: set[str] = set()
        for raw in what_ifs:
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
                raise ArchiveError("a what-if is malformed")
            claim(raw["id"], kind="what-if")
            name = raw.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME:
                raise ArchiveError("a what-if has no usable name")
            if name.lower() == "main" or name.lower() in names:
                raise ArchiveError("two what-ifs share a name")
            names.add(name.lower())
            if raw.get("status", "active") not in {"active", "set_aside"}:
                raise ArchiveError("a what-if is malformed")
            fork = raw.get("fork_revision")
            if not isinstance(fork, int) or not 0 <= fork <= main_head:
                raise ArchiveError("a what-if starts from a version the archive does not hold")
            commits = raw.get("commits", [])
            if not isinstance(commits, list):
                raise ArchiveError("a what-if is malformed")
            head = CanonArchiveService._validate_commits(commits, fork)
            for commit in commits:
                if commit["commit_id"] in commit_ids:
                    raise ArchiveError("duplicate commit id in archive")
                commit_ids.add(commit["commit_id"])
            for tag in raw.get("tags", []):
                if not isinstance(tag, dict) or not 0 <= int(tag.get("revision", -1)) <= head:
                    raise ArchiveError("a what-if's named version is malformed")
            for link in raw.get("scenes", []):
                if not isinstance(link, dict) or link.get("text_id") not in text_ids:
                    raise ArchiveError("a what-if's scene points at a text outside the archive")
            CanonArchiveService._validate_readings(raw.get("readings", []), text_ids)
            CanonArchiveService._validate_dismissals(raw.get("dismissals", []))
        for raw in conversations:
            if not isinstance(raw, dict):
                raise ArchiveError("a conversation is malformed")
            claim(str(raw.get("id") or ""), kind="conversation")
            if raw.get("commit_id") is not None and not isinstance(raw["commit_id"], str):
                raise ArchiveError("a conversation is malformed")
            try:
                AssistSession.model_validate(
                    {
                        **{k: v for k, v in raw.items() if k != "commit_id"},
                        "workspace_id": "-",
                        "project_id": "-",
                    }
                )
            except ValueError as error:
                raise ArchiveError("a conversation is malformed") from error

    @staticmethod
    def _validate(archive: dict[str, Any]) -> None:
        """Refuse a malformed archive in plain words, before anything is written."""
        try:
            CanonArchiveService._validate_parts(archive)
        except ArchiveError:
            raise
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
            # A part missing, or the wrong kind of thing: the archive is damaged.
            raise ArchiveError(
                "the archive is damaged or incomplete: something it needs is missing or not "
                "what it should be"
            ) from error

    @staticmethod
    def _validate_parts(archive: dict[str, Any]) -> None:
        if archive.get("format") != ARCHIVE_FORMAT:
            raise ArchiveError("unsupported archive format")
        if archive.get("format_version") not in READABLE_VERSIONS:
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

        writing = archive.get("writing") or {}
        texts = writing.get("texts", []) if isinstance(writing, dict) else None
        names = writing.get("names", []) if isinstance(writing, dict) else None
        story_rules = archive.get("story_rules", [])
        if not all(isinstance(part, list) for part in (texts, names, story_rules)):
            raise ArchiveError("archive writing section is malformed")
        if len(texts or []) + len(names or []) + len(story_rules) > MAX_ENTRIES:
            raise ArchiveError("archive exceeds entry limit")
        text_ids: set[str] = set()
        for raw in texts or []:
            claim(raw["id"], kind="text")
            text_ids.add(raw["id"])
            numbers = [version["number"] for version in raw.get("versions", [])]
            if not numbers or sorted(numbers) != list(range(1, len(numbers) + 1)):
                raise ArchiveError("a text's versions are not contiguous")
            for version in raw["versions"]:
                claim(version["id"], kind="text version")
                digest = hashlib.sha256(version["content"].encode("utf-8")).hexdigest()
                if digest != version.get("content_hash"):
                    raise ArchiveError("a text's content does not match its checksum")
        for raw in names or []:
            claim(raw["id"], kind="name answer")
            if raw.get("verdict") not in ("is", "is_not", "ignore"):
                raise ArchiveError("unknown name answer")
            if raw.get("entity_id") is not None:
                check_refs([raw["entity_id"]], "name answer")
            if raw.get("source_id") is not None and raw["source_id"] not in text_ids:
                raise ArchiveError("a name answer points at a text outside the archive")
            if raw.get("viewpoint_id") is not None:
                check_refs([raw["viewpoint_id"]], "name answer")
            examples = raw.get("examples", [])
            if not isinstance(examples, list) or not all(
                isinstance(item, dict) and isinstance(item.get("quote"), str) for item in examples
            ):
                raise ArchiveError("a name answer's examples are malformed")
        for raw in story_rules:
            claim(raw["id"], kind="story rule")
            if not raw.get("versions"):
                raise ArchiveError("a story rule has no wording")
            for version in raw["versions"]:
                try:
                    RuleBody.model_validate(version["body"])
                except ValueError as error:
                    raise ArchiveError("a story rule is malformed") from error

        for raw in archive.get("schemas", []):
            claim(raw["id"], kind="schema")
        for raw in (archive.get("media") or {}).get("assets", []):
            claim(raw["id"], kind="asset")
        for raw in (archive.get("media") or {}).get("versions", []):
            claim(raw["id"], kind="asset_version")

        main_head = CanonArchiveService._validate_commits(archive.get("commits", []), 0)
        CanonArchiveService._validate_additions(
            archive, main_head=main_head, text_ids=text_ids, claim=claim
        )

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


__all__ = [
    "ARCHIVE_FORMAT",
    "FORMAT_VERSION",
    "READABLE_VERSIONS",
    "ArchiveError",
    "CanonArchiveService",
]
