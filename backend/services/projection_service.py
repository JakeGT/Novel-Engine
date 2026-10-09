"""Replayable graph projection (ADR-012).

PostgreSQL canon is authoritative; Neo4j is a rebuildable projection. This service
consumes committed envelopes in branch/revision order, applies them idempotently
to the graph projection, and advances a durable watermark so readers can prove
freshness or observe lag. Canonical commits never depend on this running.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from backend.domain.identity import utcnow
from backend.domain.operations import parse_operation
from backend.domain.projection import ProjectionStatus, ProjectionWatermark
from backend.ports import CanonRepositoryPort, CanonWriterPort

GRAPH_PROJECTION = "graph"
# 2: the projection moved to its own Canon* labels (Neo4j migration 0004).
PROJECTION_SCHEMA_VERSION = "2"
DEFAULT_BATCH = 100


@dataclass(frozen=True)
class ProjectionLag:
    head_revision: int
    applied_revision: int
    status: ProjectionStatus
    last_error: str | None = None

    @property
    def lag(self) -> int:
        return max(0, self.head_revision - self.applied_revision)

    @property
    def current(self) -> bool:
        return self.lag == 0 and self.status is ProjectionStatus.CURRENT


@dataclass(frozen=True)
class ProjectionReport:
    applied: int
    revision: int
    status: ProjectionStatus
    error: str | None = None


class GraphProjectionService:
    def __init__(
        self,
        *,
        canon: CanonRepositoryPort,
        writer: CanonWriterPort,
        projection: str = GRAPH_PROJECTION,
        schema_version: str = PROJECTION_SCHEMA_VERSION,
    ) -> None:
        self._canon = canon
        self._writer = writer
        self._projection = projection
        self._schema_version = schema_version

    async def _current_watermark(self, ws: str, pr: str, br: str) -> ProjectionWatermark | None:
        """The watermark for this projection layout, or ``None`` if there is none.

        A watermark written by an older projection schema says nothing about the
        current layout, so it is treated as "nothing applied" (visible lag) and
        the next run rebuilds from genesis instead of trusting stale freshness.
        """
        watermark = await self._canon.get_watermark(
            projection=self._projection, workspace_id=ws, project_id=pr, branch_id=br
        )
        if watermark is not None and watermark.projection_schema_version != self._schema_version:
            return None
        return watermark

    async def _has_stale_layout(self, ws: str, pr: str, br: str) -> bool:
        watermark = await self._canon.get_watermark(
            projection=self._projection, workspace_id=ws, project_id=pr, branch_id=br
        )
        return watermark is not None and watermark.projection_schema_version != self._schema_version

    async def lag(self, *, workspace_id: str, project_id: str, branch_id: str) -> ProjectionLag:
        head = await self._canon.get_head(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
        )
        watermark = await self._current_watermark(workspace_id, project_id, branch_id)
        applied = watermark.revision if watermark is not None else 0
        if watermark is not None and watermark.status is ProjectionStatus.ERROR:
            status = ProjectionStatus.ERROR
        elif head <= applied:
            status = ProjectionStatus.CURRENT
        else:
            status = ProjectionStatus.LAGGING
        return ProjectionLag(
            head_revision=head,
            applied_revision=applied,
            status=status,
            last_error=watermark.last_error if watermark is not None else None,
        )

    async def project_pending(
        self,
        *,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        limit: int = DEFAULT_BATCH,
    ) -> ProjectionReport:
        if await self._has_stale_layout(workspace_id, project_id, branch_id):
            # Drop anything written under the previous layout before replaying.
            await self._writer.discard(
                workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
            )
        watermark = await self._current_watermark(workspace_id, project_id, branch_id)
        applied_revision = watermark.revision if watermark is not None else 0
        commits = await self._canon.list_commits(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch_id,
            after_revision=applied_revision,
            limit=limit,
        )
        applied_count = 0
        for commit in commits:
            envelope = await self._canon.get_envelope(commit.commit_id)
            if envelope is None:
                continue
            operations = [parse_operation(op) for op in json.loads(envelope.canonical)]
            try:
                await self._writer.apply_commit(
                    commit_id=envelope.commit_id,
                    revision=envelope.revision,
                    checksum=envelope.checksum,
                    workspace_id=envelope.workspace_id,
                    project_id=envelope.project_id,
                    branch_id=envelope.branch_id,
                    operations=operations,
                    schema_version_id=envelope.schema_version_id,
                )
            except Exception as error:  # surfaced as visible lag, never canon corruption
                await self._canon.set_watermark(
                    ProjectionWatermark(
                        projection=self._projection,
                        workspace_id=workspace_id,
                        project_id=project_id,
                        branch_id=branch_id,
                        revision=applied_revision,
                        projection_schema_version=self._schema_version,
                        status=ProjectionStatus.ERROR,
                        last_error=type(error).__name__,
                        updated_at=utcnow(),
                    )
                )
                return ProjectionReport(
                    applied=applied_count,
                    revision=applied_revision,
                    status=ProjectionStatus.ERROR,
                    error=type(error).__name__,
                )
            applied_revision = envelope.revision
            applied_count += 1
            await self._canon.set_watermark(
                ProjectionWatermark(
                    projection=self._projection,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    branch_id=branch_id,
                    revision=envelope.revision,
                    commit_id=envelope.commit_id,
                    envelope_checksum=envelope.checksum,
                    projection_schema_version=self._schema_version,
                    status=ProjectionStatus.CURRENT,
                    updated_at=utcnow(),
                )
            )
        return ProjectionReport(
            applied=applied_count, revision=applied_revision, status=ProjectionStatus.CURRENT
        )

    async def rebuild(
        self, *, workspace_id: str, project_id: str, branch_id: str, batch: int = DEFAULT_BATCH
    ) -> ProjectionReport:
        await self._writer.discard(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
        )
        await self._canon.set_watermark(
            ProjectionWatermark(
                projection=self._projection,
                workspace_id=workspace_id,
                project_id=project_id,
                branch_id=branch_id,
                revision=0,
                projection_schema_version=self._schema_version,
                status=ProjectionStatus.REBUILDING,
                updated_at=utcnow(),
            )
        )
        total = 0
        revision = 0
        while True:
            report = await self.project_pending(
                workspace_id=workspace_id,
                project_id=project_id,
                branch_id=branch_id,
                limit=batch,
            )
            total += report.applied
            revision = report.revision
            if report.status is ProjectionStatus.ERROR or report.applied < batch:
                return ProjectionReport(
                    applied=total, revision=revision, status=report.status, error=report.error
                )
