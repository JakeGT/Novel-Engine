"""Workspace/project cascade purge across non-relational stores.

Deleting a workspace or project must remove its canon (journal, normalized
records, schema versions, media identity), the graph projection, derived vectors,
credentials, usage records, assistant conversations, source texts, object storage, and debug artifacts — not only the
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
        assist: Any = None,
        ai_settings: Any = None,
        allowances: Any = None,
        sources: Any = None,
        rules: Any = None,
    ) -> None:
        self._canon = canon
        self._canon_writer = canon_writer
        self._graph = graph
        self._vectors = vectors
        self._credentials = credentials
        self._usage = usage
        self._objects = objects
        self._artifacts = artifacts
        self._assist = assist
        self._ai_settings = ai_settings
        self._allowances = allowances
        self._sources = sources
        self._rules = rules
        self._access_keys: Any = None
        self._connector: Any = None

    def attach_canon(self, *, canon: Any, canon_writer: Any = None) -> None:
        """Late-bind canon stores, which are constructed after the tenancy service."""
        self._canon = canon
        self._canon_writer = canon_writer

    def attach_access_keys(self, access_keys: Any) -> None:
        """Late-bind the access key store (QRY-003): keys go with their workspace or story."""
        self._access_keys = access_keys

    def attach_connector(self, connector: Any) -> None:
        """Late-bind the connector store (SCN-002A): computers go with their workspace."""
        self._connector = connector

    async def purge_workspace(self, workspace_id: str) -> dict[str, int]:
        canon: dict[str, int] = {}
        if self._canon is not None:
            canon["canon_rows"] = await self._canon.purge_workspace(workspace_id=workspace_id)
        if self._canon_writer is not None:
            canon["canon_projection_nodes"] = await self._canon_writer.purge(
                workspace_id=workspace_id
            )
        if self._assist is not None:
            canon["assist_sessions"] = await self._assist.delete_workspace(workspace_id)
        if self._ai_settings is not None:
            canon["ai_settings"] = await self._ai_settings.delete_workspace(workspace_id)
        if self._allowances is not None:
            canon["usage_allowance"] = await self._allowances.delete_workspace(workspace_id)
        if self._sources is not None:
            canon["source_texts"] = await self._sources.delete_workspace(workspace_id)
        if self._rules is not None:
            canon["story_rules"] = await self._rules.delete_workspace(workspace_id)
        if self._access_keys is not None:
            canon["access_keys"] = await self._access_keys.delete_workspace(workspace_id)
        if self._connector is not None:
            canon["connected_computers"] = await self._connector.delete_workspace(workspace_id)
        return {
            **canon,
            "graph_nodes": self._graph.delete_workspace_nodes(workspace_id),
            "embedding_vectors": self._vectors.delete_workspace(workspace_id=workspace_id),
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
        if self._assist is not None:
            canon["assist_sessions"] = await self._assist.delete_project(workspace_id, project_id)
        if self._sources is not None:
            canon["source_texts"] = await self._sources.delete_project(workspace_id, project_id)
        if self._rules is not None:
            canon["story_rules"] = await self._rules.delete_project(workspace_id, project_id)
        if self._access_keys is not None:
            canon["access_keys"] = await self._access_keys.delete_project(workspace_id, project_id)
        if self._connector is not None:
            canon["connector_requests"] = await self._connector.delete_project(
                workspace_id, project_id
            )
        return {
            **canon,
            "graph_nodes": self._graph.delete_project_nodes(workspace_id, project_id),
            "embedding_vectors": self._vectors.delete_project(
                workspace_id=workspace_id, project_id=project_id
            ),
            "objects": self._objects.delete_project(
                workspace_id=workspace_id, project_id=project_id
            ),
            "artifacts": self._artifacts.delete_project(
                workspace_id=workspace_id, project_id=project_id
            ),
        }
