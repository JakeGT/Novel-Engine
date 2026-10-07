"""Unit guard for scoped semantic retrieval query construction.

The graph vector index returns global candidates; the query must filter on the
node returned by the index itself (workspace + project) and oversample so other
tenants cannot crowd a project out, without re-matching by id (which could collide
across projects).
"""

from typing import Any

import pytest

from backend.graph.manager import SEMANTIC_OVERSAMPLE, GraphManager

pytestmark = pytest.mark.unit


class _RecordingSession:
    def __init__(self, sink: dict[str, Any]) -> None:
        self._sink = sink

    def __enter__(self) -> "_RecordingSession":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def run(self, query: str, **params: Any) -> list:
        self._sink["query"] = query
        self._sink["params"] = params
        return []


class _RecordingDriver:
    def __init__(self, sink: dict[str, Any]) -> None:
        self._sink = sink

    def session(self) -> _RecordingSession:
        return _RecordingSession(self._sink)


def test_semantic_query_filters_on_returned_node_and_oversamples() -> None:
    sink: dict[str, Any] = {}
    manager = GraphManager.__new__(GraphManager)
    manager.driver = _RecordingDriver(sink)

    result = manager.search_semantic_nodes(
        [0.1] * 384, workspace_id="ws-a", project_id="prj-1", top_k=5, min_score=0.5
    )

    assert result == []
    query = sink["query"]
    assert "node.workspace_id = $workspace_id" in query
    assert "node.project_id = $project_id" in query
    assert "MATCH (e:Entity {id: node.id})" not in query
    assert sink["params"]["candidate_k"] == 5 * SEMANTIC_OVERSAMPLE
    assert sink["params"]["top_k"] == 5
