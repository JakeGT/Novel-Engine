"""Workspace/project cascade purge across non-relational stores.

Deleting a workspace or project must remove its graph nodes, derived vectors,
credentials, usage records, object storage, and debug artifacts — not only the
relational rows. This adapter coordinates those stores behind a single port.
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
    ) -> None:
        self._graph = graph
        self._vectors = vectors
        self._credentials = credentials
        self._usage = usage
        self._objects = objects
        self._artifacts = artifacts

    async def purge_workspace(self, workspace_id: str) -> dict[str, int]:
        return {
            "graph_nodes": self._graph.delete_workspace_nodes(workspace_id),
            "vector_documents": self._vectors.delete_workspace(workspace_id=workspace_id),
            "credentials": await self._credentials.delete_workspace(workspace_id),
            "usage_records": await self._usage.delete_workspace(workspace_id),
            "objects": self._objects.delete_workspace(workspace_id=workspace_id),
            "artifacts": self._artifacts.delete_workspace(workspace_id=workspace_id),
        }

    async def purge_project(self, workspace_id: str, project_id: str) -> dict[str, int]:
        return {
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
