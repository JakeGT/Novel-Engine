import inspect

import pytest

from backend.ports import ArtifactStore, GraphRepository, VectorStorePort

pytestmark = pytest.mark.unit

_WORKSPACE_SCOPED_GRAPH = [
    "get_node",
    "get_nodes",
    "get_edges",
    "search_nodes_by_name",
    "search_semantic_nodes",
    "traverse",
    "delete_node",
]
_WORKSPACE_SCOPED_VECTOR = ["add_documents", "query", "delete"]
_WORKSPACE_SCOPED_ARTIFACT = ["save", "load"]


def _requires_workspace(protocol: type, method: str) -> bool:
    signature = inspect.signature(getattr(protocol, method))
    return "workspace_id" in signature.parameters


def test_graph_port_requires_workspace_scope() -> None:
    for method in _WORKSPACE_SCOPED_GRAPH:
        assert _requires_workspace(GraphRepository, method), method


def test_vector_port_requires_workspace_scope() -> None:
    for method in _WORKSPACE_SCOPED_VECTOR:
        assert _requires_workspace(VectorStorePort, method), method


def test_artifact_port_requires_workspace_scope() -> None:
    for method in _WORKSPACE_SCOPED_ARTIFACT:
        assert _requires_workspace(ArtifactStore, method), method
