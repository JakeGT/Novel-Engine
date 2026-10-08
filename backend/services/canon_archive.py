"""Canonical project archive (format v3) export/import.

Exports the canonical PostgreSQL model for one project — schema versions, the
ordered commit envelopes, the canon snapshot, tags, media identity/version
metadata, and project objects (bytes) — as a checksummed, self-contained archive.
Import is preflight-validated, checksum-preserving (each commit's canonical
checksum is unchanged), and idempotent: commits replay under deterministic
remapped commit ids, so re-importing the same archive into the same empty project
is a no-op. Derived projections are rebuilt separately from accepted canon.

Credentials, sessions, provider usage, and unrelated tenant data are never
included.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from backend.domain.history import CanonTag
from backend.domain.journal import CommitEnvelope
from backend.domain.media import MediaAsset, MediaAssetVersion
from backend.domain.operations import parse_operation
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


class ArchiveError(RuntimeError):
    """Raised when an archive is malformed, unsafe, or fails verification."""


def _checksum(payload: dict[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "checksum"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _dedup_commit_id(archive_checksum: str, source_commit_id: str) -> str:
    digest = hashlib.sha256(f"{archive_checksum}:{source_commit_id}".encode()).hexdigest()
    return f"cmt_{digest[:32]}"


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
    ) -> None:
        self._canon = canon
        self._journal = journal
        self._schemas = schemas
        self._media = media
        self._objects = objects

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
            commits.append(
                {
                    "commit_id": envelope.commit_id,
                    "revision": envelope.revision,
                    "expected_head": envelope.expected_head,
                    "schema_version_id": envelope.schema_version_id,
                    "canonical": envelope.canonical,
                    "checksum": envelope.checksum,
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
        return body

    # ---------------------------------------------------------------- import
    async def import_project(
        self, target_workspace_id: str, target_project_id: str, archive: dict[str, Any]
    ) -> dict[str, Any]:
        self._validate(archive)
        archive_checksum = archive["checksum"]
        branch_id = await self._root_branch_id(target_workspace_id, target_project_id)

        object_key_map: dict[str, str] = {}
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

        published = await self._schemas.get_published(
            workspace_id=target_workspace_id, project_id=target_project_id
        )
        by_hash: dict[str, str] = {published.content_hash: published.id} if published else {}
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
            schema_map[raw["id"]] = mapped

        for raw in archive["media"]["assets"]:
            await self._media.import_asset(
                MediaAsset.model_validate(
                    {**raw, "workspace_id": target_workspace_id, "project_id": target_project_id}
                )
            )
        for raw in archive["media"]["versions"]:
            source_key = raw["object_key"]
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
        for raw in sorted(archive["commits"], key=lambda item: item["revision"]):
            new_commit_id = _dedup_commit_id(archive_checksum, raw["commit_id"])
            commit_id_map[raw["commit_id"]] = new_commit_id
            operations = [parse_operation(op) for op in json.loads(raw["canonical"])]
            await self._canon.apply_commit(
                envelope=CommitEnvelope(
                    commit_id=new_commit_id,
                    mutation_id=f"import:{archive_checksum[:16]}",
                    workspace_id=target_workspace_id,
                    project_id=target_project_id,
                    branch_id=branch_id,
                    revision=raw["revision"],
                    expected_head=raw["revision"] - 1,
                    schema_version_id=schema_map.get(raw.get("schema_version_id") or ""),
                    canonical=raw["canonical"],
                    checksum=raw["checksum"],
                    actor_user_id=None,
                    created_at=datetime.fromisoformat(raw["created_at"]),
                ),
                operations=operations,
                origin="import",
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

        return {
            "objects": object_count,
            "assets": len(archive["media"]["assets"]),
            "versions": len(archive["media"]["versions"]),
            "commits": len(archive["commits"]),
            "checksum": archive_checksum,
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
        total = sum(
            len(archive.get(section, []) or [])
            for section in ("schemas", "commits", "objects", "tags")
        ) + sum(len(canon.get(key, []) or []) for key in ("entities", "facts", "beliefs", "events"))
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
        for raw in canon.get("beliefs", []):
            claim(raw["id"], kind="belief")
            check_refs([raw["holder_entity_id"], raw["proposition"]["subject_id"]], "belief")
        for raw in canon.get("events", []):
            claim(raw["id"], kind="event")
            check_refs([p["entity_id"] for p in raw.get("participants", [])], "event")

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
            previous = raw["revision"]

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
            if hashlib.sha256(decoded).hexdigest() != raw.get("checksum"):
                raise ArchiveError("object checksum does not match its payload")
            if raw.get("byte_length") is not None and int(raw["byte_length"]) != len(decoded):
                raise ArchiveError("object byte_length does not match its payload")


__all__ = ["ARCHIVE_FORMAT", "FORMAT_VERSION", "ArchiveError", "CanonArchiveService"]
