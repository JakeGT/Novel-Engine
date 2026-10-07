"""Unit guard for scoped semantic retrieval query construction.

The graph vector index returns global candidates; the query must filter on the
node returned by the index itself (workspace + project) and deepen via bounded
oversampling until enough scoped results exist, without re-matching by id (which
could collide across projects).
"""

from typing import Any

import pytest

from backend.graph.manager import SEMANTIC_MAX_CANDIDATES, SEMANTIC_OVERSAMPLE, GraphManager

pytestmark = pytest.mark.unit


class _RecordingSession:
    def __init__(self, sink: dict[str, Any], scripted: list[list[dict[str, Any]]]) -> None:
        self._sink = sink
        self._scripted = scripted

    def __enter__(self) -> "_RecordingSession":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def run(self, query: str, **params: Any) -> list:
        self._sink.setdefault("queries", []).append(query)
        self._sink.setdefault("params", []).append(params)
        index = len(self._sink["params"]) - 1
        if index < len(self._scripted):
            return self._scripted[index]
        return self._scripted[-1] if self._scripted else []


class _RecordingDriver:
    def __init__(self, sink: dict[str, Any], scripted: list[list[dict[str, Any]]]) -> None:
        self._sink = sink
        self._scripted = scripted

    def session(self) -> _RecordingSession:
        return _RecordingSession(self._sink, self._scripted)


def _manager(sink: dict[str, Any], scripted: list[list[dict[str, Any]]]) -> GraphManager:
    manager = GraphManager.__new__(GraphManager)
    manager.driver = _RecordingDriver(sink, scripted)
    return manager


def test_semantic_query_filters_on_returned_node() -> None:
    sink: dict[str, Any] = {}
    manager = _manager(sink, [[]])

    result = manager.search_semantic_nodes(
        [0.1] * 384, workspace_id="ws-a", project_id="prj-1", top_k=5, min_score=0.5
    )

    assert result == []
    query = sink["queries"][0]
    assert "node.workspace_id = $workspace_id" in query
    assert "node.project_id = $project_id" in query
    assert "MATCH (e:Entity {id: node.id})" not in query


def test_semantic_deepens_when_scoped_results_are_short() -> None:
    sink: dict[str, Any] = {}
    manager = _manager(sink, [[]])

    manager.search_semantic_nodes([0.1] * 384, workspace_id="ws-a", project_id="prj-1", top_k=5)

    candidate_ks = [params["candidate_k"] for params in sink["params"]]
    assert candidate_ks[0] == 5 * SEMANTIC_OVERSAMPLE
    assert candidate_ks[-1] == SEMANTIC_MAX_CANDIDATES
    assert candidate_ks == sorted(candidate_ks)
    assert len(candidate_ks) > 1


def test_semantic_stops_early_when_enough_scoped_results() -> None:
    sink: dict[str, Any] = {}
    rows = [{"id": f"n{index}"} for index in range(5)]
    manager = _manager(sink, [rows])

    result = manager.search_semantic_nodes(
        [0.1] * 384, workspace_id="ws-a", project_id="prj-1", top_k=5
    )

    assert result == [f"n{index}" for index in range(5)]
    assert len(sink["params"]) == 1
