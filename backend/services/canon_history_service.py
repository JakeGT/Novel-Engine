"""Canon revision history, per-record history, tags, and revision inspection (2H).

All reads go to canonical PostgreSQL (ADR-012). These are inspection views over
committed history; they never mutate canon and never expose secret payloads.
"""

from __future__ import annotations

import json

from backend.domain.canon.entities import CanonEntity, EntityLifecycle
from backend.domain.canon.revisions import CanonRevision
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.history import (
    CanonTag,
    CanonTagHistoryEntry,
    HistoryError,
    RecordHistoryEntry,
    RevisionSummary,
)
from backend.domain.identity import AccessScope
from backend.domain.journal import Branch
from backend.domain.projection import RecordVersion
from backend.domain.tenancy import Permission
from backend.ports import CanonJournalPort, CanonRepositoryPort, TenancyStorePort

_READ_RECORD_TYPES = {"entity", "fact", "belief", "event"}


class CanonHistoryService:
    def __init__(
        self,
        *,
        canon: CanonRepositoryPort,
        journal: CanonJournalPort,
        tenancy: object | None = None,
        local_mode: bool = False,
    ) -> None:
        self._canon = canon
        self._journal = journal
        self._tenancy = tenancy
        self._local_mode = local_mode

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
                "name": change.get("name") or current.name,
                "slug": change.get("slug", current.slug),
                "attributes": change.get("attributes") or current.attributes,
            }
        )
    if op == "retire_entity":
        return current.model_copy(
            update={"lifecycle": EntityLifecycle.RETIRED, "retired_revision": version.revision}
        )
    return current


# Re-exported for callers that only need the error type.
__all__ = ["CanonHistoryService", "HistoryError", "TenancyStorePort"]
