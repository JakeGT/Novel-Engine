import pytest
from pydantic import ValidationError

from backend.graph.policies import (
    CHARACTER_CREATION_POLICY,
    GOD_HAND_POLICY,
    SCENE_GENERATION_POLICY,
    WORLD_CREATION_POLICY,
    ContextType,
    TraversalPolicy,
    get_policy,
)

pytestmark = pytest.mark.unit


def test_known_context_types_return_predefined_policies() -> None:
    assert get_policy("scene_generation") is SCENE_GENERATION_POLICY
    assert get_policy("world_creation") is WORLD_CREATION_POLICY
    assert get_policy("character_creation") is CHARACTER_CREATION_POLICY
    assert get_policy("god_hand") is GOD_HAND_POLICY


def test_unknown_context_type_falls_back_to_scene_generation() -> None:
    assert get_policy("does_not_exist") is SCENE_GENERATION_POLICY


def test_scene_generation_policy_rules() -> None:
    policy = SCENE_GENERATION_POLICY

    assert policy.max_depth == 2
    assert "HAS_APPEARANCE" in policy.allowed_edge_types
    assert "System" in policy.excluded_node_types
    assert policy.use_semantic_search is True
    assert policy.use_pagerank is True
    assert policy.pagerank_damping == 0.85
    assert policy.semantic_top_k == 5
    assert policy.max_nodes == 30


def test_world_creation_policy_rules() -> None:
    policy = WORLD_CREATION_POLICY

    assert policy.max_depth == 3
    assert policy.use_semantic_search is False
    assert policy.use_pagerank is False
    assert "Appearance" in policy.excluded_node_types
    assert policy.semantic_top_k == 15
    assert policy.semantic_min_score == 0.3
    assert policy.max_nodes == 100


def test_character_creation_policy_rules() -> None:
    policy = CHARACTER_CREATION_POLICY

    assert policy.max_depth == 2
    assert policy.weight_depth == 2.0
    assert "Character" in policy.excluded_node_types
    assert "Belief" in policy.excluded_node_types
    assert policy.max_nodes == 20


def test_god_hand_policy_traverses_all_edge_types() -> None:
    assert GOD_HAND_POLICY.allowed_edge_types is None
    assert GOD_HAND_POLICY.max_depth == 1
    assert GOD_HAND_POLICY.excluded_node_types == set()


def test_default_semantic_top_k_is_five() -> None:
    policy = TraversalPolicy(context_type=ContextType.SCENE_GENERATION)

    assert policy.semantic_top_k == 5


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_depth": -1},
        {"max_depth": 11},
        {"max_nodes": 0},
        {"max_nodes": 1001},
        {"semantic_top_k": -1},
        {"semantic_top_k": 101},
        {"semantic_min_score": 1.5},
        {"pagerank_damping": 0.0},
        {"weight_depth": -0.1},
    ],
)
def test_traversal_policy_rejects_out_of_range_values(overrides) -> None:
    with pytest.raises(ValidationError):
        TraversalPolicy(context_type=ContextType.SCENE_GENERATION, **overrides)
