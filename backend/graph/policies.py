from enum import Enum
from pydantic import BaseModel, Field
from typing import List, Dict, Optional, Set

# TODO: Dyanmic traversal policies based on user settings

class ContextType(str, Enum):
    SCENE_GENERATION = "scene_generation"
    WORLD_CREATION = "world_creation"
    CHARACTER_CREATION = "character_creation"
    GOD_HAND = "god_hand"

class TraversalPolicy(BaseModel):
    """
    Defines the rules for traversing the Knowledge Graph to build context.
    """
    context_type: ContextType
    max_depth: int = Field(default=2, description="Maximum number of hops from the anchor node.")
    allowed_edge_types: Optional[Set[str]] = Field(default=None, description="If True, only traverse these edge types. If None, traverse all.")
    excluded_node_types: Set[str] = Field(default_factory=set, description="Do not return or traverse through these node types.")
    # Relevance Scoring Weights
    weight_depth: float = Field(default=1.0, description="Multiplier for depth penalty (if not using PageRank).")
    weight_recentness: float = Field(default=0.0, description="Multiplier for temporal relevance.")
    
    # Neo4j GraphRAG (Semantic + PageRank) Settings
    use_semantic_search: bool = Field(default=False, description="Whether to embed the prompt and find semantic anchors.")
    semantic_top_k: int = Field(default=3, description="Number of semantic anchors to retrieve if semantic search is used.")
    use_pagerank: bool = Field(default=False, description="If True, uses Graph Data Science Personalized PageRank instead of hop limits.")
    pagerank_damping: float = Field(default=0.85, description="PageRank damping factor (higher spreads further).")
    
    # Limits
    max_nodes: int = Field(default=50, description="Hard cap on the total number of nodes returned.")

    # Semantic Policies
    semantic_min_score: float = Field(default=0.5, description="Minimum semantic similarity score for a node to be included.")
    semantic_top_k: int = Field(default=5, description="Number of semantic anchors to retrieve if semantic search is used.")

# Predefined Policies

SCENE_GENERATION_POLICY = TraversalPolicy(
    context_type=ContextType.SCENE_GENERATION,
    max_depth=2,
    # When writing a scene about Character X, we care about what they know, what state they're in, and their immediate surroundings
    allowed_edge_types={"HAS_APPEARANCE", "HAS_PERSONALITY", "CURRENT_STATE", "BELIEVES", "LOCATED_AT", "OWNS", "PARTICIPATED_IN"},
    excluded_node_types={"System", "Meta"}, # Don't pull meta-game systems into immediate scene prose
    weight_depth=1.5,
    use_semantic_search=True,
    semantic_top_k=5,
    use_pagerank=True,
    pagerank_damping=0.85,
    max_nodes=30
)

WORLD_CREATION_POLICY = TraversalPolicy(
    context_type=ContextType.WORLD_CREATION,
    max_depth=3,
    # When building the world, we care about systemic pressures, rules, and broad locations
    allowed_edge_types={"HAS_SYSTEM", "HAS_PRESSURE", "REQUIRES_RESOURCE", "FORBIDDEN_BY", "AFFECTS", "CAUSES", "LOCATED_AT", "PART_OF"},
    excluded_node_types={"Appearance", "PersonalityProfile", "State"}, # Ignore specific character states
    weight_depth=1.0,
    max_nodes=100,
    semantic_top_k = 15,
    semantic_min_score = 0.3
)

CHARACTER_CREATION_POLICY = TraversalPolicy(
    context_type=ContextType.CHARACTER_CREATION,
    max_depth=2,
    # We want to pull what the character believes, their state, and their immediate relationships
    allowed_edge_types={"HAS_APPEARANCE", "HAS_PERSONALITY", "CURRENT_STATE", "BELIEVES", "KNOWS", "MEMBER_OF"},
    excluded_node_types={'Character', 'Appearance', 'PersonalityProfile', 'State', 'Belief'},
    weight_depth=2.0, # Highly prioritize immediate depth 1 traits
    max_nodes=20,
    semantic_top_k = 5,
    semantic_min_score = 0.4
)

GOD_HAND_POLICY = TraversalPolicy(
    context_type=ContextType.GOD_HAND,
    max_depth=1,
    allowed_edge_types=None, # Traverse everything 1 step away to give the editor full surrounding context
    excluded_node_types=set(),
    weight_depth=1.0,
    max_nodes=100,
)

def get_policy(context_type: str) -> TraversalPolicy:
    policies = {
        ContextType.SCENE_GENERATION.value: SCENE_GENERATION_POLICY,
        ContextType.WORLD_CREATION.value: WORLD_CREATION_POLICY,
        ContextType.CHARACTER_CREATION.value: CHARACTER_CREATION_POLICY,
        ContextType.GOD_HAND.value: GOD_HAND_POLICY
    }
    return policies.get(context_type, SCENE_GENERATION_POLICY)
