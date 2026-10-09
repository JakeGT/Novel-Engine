"""Workspace/project cascade purge across non-relational stores.

Deleting a workspace or project must remove its canon (journal, normalized
records, schema versions, media identity), the graph projection, derived vectors,
credentials, usage records, object storage, and debug artifacts — not only the
tenancy rows. This adapter coordinates those stores behind a single port.
"""

from __future__ import annotations

from typing import Any


class StoreWorkspacePurge:
    def __init__(
        self,
        *,
        graph: Any,
        vectors: Any,
        credentials: Any,
        usage: Any,
        objects: Any,
        artifacts: Any,
        canon: Any = None,
        canon_writer: Any = None,
    ) -> None:
        self._canon = canon
        self._canon_writer = canon_writer
        self._graph = graph
        self._vectors = vectors
        self._credentials = credentials
        self._usage = usage
        self._objects = objects
        self._artifacts = artifacts

    def attach_canon(self, *, canon: Any, canon_writer: Any = None) -> None:
        """Late-bind canon stores, which are constructed after the tenancy service."""
        self._canon = canon
        self._canon_writer = canon_writer

    async def purge_workspace(self, workspace_id: str) -> dict[str, int]:
        canon: dict[str, int] = {}
        if self._canon is not None:
            canon["canon_rows"] = await self._canon.purge_workspace(workspace_id=workspace_id)
        if self._canon_writer is not None:
            canon["canon_projection_nodes"] = await self._canon_writer.purge(
                workspace_id=workspace_id
            )
        return {
            **canon,
            "graph_nodes": self._graph.delete_workspace_nodes(workspace_id),
            "vector_documents": self._vectors.delete_workspace(workspace_id=workspace_id),
            "credentials": await self._credentials.delete_workspace(workspace_id),
            "usage_records": await self._usage.delete_workspace(workspace_id),
            "objects": self._objects.delete_workspace(workspace_id=workspace_id),
            "artifacts": self._artifacts.delete_workspace(workspace_id=workspace_id),
        }

    async def purge_project(self, workspace_id: str, project_id: str) -> dict[str, int]:
        canon: dict[str, int] = {}
        if self._canon is not None:
            canon["canon_rows"] = await self._canon.purge_project(
                workspace_id=workspace_id, project_id=project_id
            )
        if self._canon_writer is not None:
            canon["canon_projection_nodes"] = await self._canon_writer.purge(
                workspace_id=workspace_id, project_id=project_id
            )
        return {
            **canon,
            "graph_nodes": self._graph.delete_project_nodes(workspace_id, project_id),
            "vector_documents": self._vectors.delete_project(
                workspace_id=workspace_id, project_id=project_id
            ),
            "objects": self._objects.delete_project(
                workspace_id=workspace_id, project_id=project_id
            ),
            "artifacts": self._artifacts.delete_project(
                workspace_id=workspace_id, project_id=project_id
            ),
        }
