"""Unit guard for exact project-scoped semantic retrieval construction."""

from typing import Any

import pytest

from backend.graph.manager import GraphManager

pytestmark = pytest.mark.unit


class _RecordingSession:
    def __init__(self, sink: dict[str, Any], rows: list[dict[str, Any]]) -> None:
        self._sink = sink
        self._rows = rows

    def __enter__(self) -> "_RecordingSession":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def run(self, query: str, **params: Any) -> list:
        self._sink["query"] = query
        self._sink["params"] = params
        return self._rows


class _RecordingDriver:
    def __init__(self, sink: dict[str, Any], rows: list[dict[str, Any]]) -> None:
        self._sink = sink
        self._rows = rows

    def session(self) -> _RecordingSession:
        return _RecordingSession(self._sink, self._rows)


def _manager(sink: dict[str, Any], rows: list[dict[str, Any]]) -> GraphManager:
    manager = GraphManager.__new__(GraphManager)
    manager.driver = _RecordingDriver(sink, rows)
    return manager


def test_semantic_query_filters_before_exact_scoring() -> None:
    sink: dict[str, Any] = {}
    rows = [{"id": "n1"}, {"id": "n2"}]
    manager = _manager(sink, rows)

    result = manager.search_semantic_nodes(
        [0.1] * 384, workspace_id="ws-a", project_id="prj-1", top_k=5, min_score=0.5
    )

    assert result == ["n1", "n2"]
    query = sink["query"]
    assert "MATCH (node:Entity)" in query
    assert "node.workspace_id = $workspace_id" in query
    assert "node.project_id = $project_id" in query
    assert "vector.similarity.cosine" in query
    assert "db.index.vector.queryNodes" not in query
    assert sink["params"]["workspace_id"] == "ws-a"
    assert sink["params"]["project_id"] == "prj-1"
    assert sink["params"]["top_k"] == 5
