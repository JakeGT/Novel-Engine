import pytest

from backend.graph.policies import ContextType, TraversalPolicy
from backend.graph.traversal import build_traversal_query

pytestmark = pytest.mark.unit


def _policy(**overrides) -> TraversalPolicy:
    return TraversalPolicy(context_type=ContextType.SCENE_GENERATION, **overrides)


def test_hop_query_is_undirected_with_depth_bound() -> None:
    query = build_traversal_query(_policy(max_depth=2))

    assert "-[r*0..2]-" in query
    assert "$weight_depth" in query


def test_query_includes_excluded_labels_as_conditions() -> None:
    query = build_traversal_query(_policy(excluded_node_types={"System", "Meta"}))

    assert "NOT 'Meta' IN labels(n)" in query
    assert "NOT 'System' IN labels(n)" in query
    assert "AND" in query


def test_query_sorts_and_joins_allowed_edge_types() -> None:
    query = build_traversal_query(_policy(allowed_edge_types={"OWNS", "KNOWS"}))

    assert ":KNOWS|OWNS" in query


def test_pagerank_query_extends_depth_and_uses_damping() -> None:
    query = build_traversal_query(_policy(use_pagerank=True, max_depth=2, pagerank_damping=0.9))

    assert "-[r*0..3]-" in query
    assert "$damping_factor" in query
    assert "$weight_depth" not in query


def test_query_without_exclusions_or_allowed_types_is_valid() -> None:
    query = build_traversal_query(_policy())

    assert "WHERE anchor.id IN $anchor_ids" in query
    assert "-[r*0..2]-" in query
