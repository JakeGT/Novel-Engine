import json
import logging
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.core.logging import log_event

logger = logging.getLogger(__name__)


class NodeType(StrEnum):
    CHARACTER = "Character"
    LOCATION = "Location"
    OBJECT = "Object"
    FACTION = "Faction"
    CONCEPT = "Concept"
    EVENT = "Event"
    SCENE = "Scene"
    ACTION = "Action"
    FLASHBACK = "Flashback"
    INTERACTION = "Interaction"
    STORY = "Story"
    ACT = "Act"
    ARC = "PlotArc"
    BEAT = "Beat"
    THEME = "Theme"
    GOAL = "Goal"
    CONFLICT = "Conflict"
    SECRET = "Secret"
    WORLD_STATE = "WorldState"
    WORLD = "World"
    # World Building
    SYSTEM = "System"
    PRESSURE = "Pressure"
    RESOURCE = "Resource"
    CONSTRAINT = "Constraint"
    LAW = "Law"
    # Character Sub-modules
    APPEARANCE = "Appearance"
    PERSONALITY = "PersonalityProfile"
    STATE = "State"
    BELIEF = "Belief"


class EdgeType(StrEnum):
    # Structural
    LOCATED_AT = "LOCATED_AT"
    PART_OF = "PART_OF"
    OWNS = "OWNS"
    MEMBER_OF = "MEMBER_OF"
    OCCURRED_AT = "OCCURRED_AT"
    CONTAINS = "CONTAINS"
    # Character Structural
    HAS_APPEARANCE = "HAS_APPEARANCE"
    HAS_PERSONALITY = "HAS_PERSONALITY"
    CURRENT_STATE = "CURRENT_STATE"
    HAS_GOAL = "HAS_GOAL"
    # Social
    KNOWS = "KNOWS"
    LIKES = "LIKES"
    HATES = "HATES"
    RELATED_TO = "RELATED_TO"
    PARTICIPATED_IN = "PARTICIPATED_IN"
    # Epistemic
    BELIEVES = "BELIEVES"
    KNOWS_SECRET = "KNOWS_SECRET"
    # Causal
    CAUSED = "CAUSED"
    FOLLOWS = "FOLLOWS"
    MOTIVATED_BY = "MOTIVATED_BY"
    # Narrative
    HAS_ACT = "HAS_ACT"
    HAS_ARC = "HAS_ARC"
    HAS_THEME = "HAS_THEME"
    STARTS_AT = "STARTS_AT"
    CLIMAX_AT = "CLIMAX_AT"
    INVOLVES = "INVOLVES"
    OCCURS_IN = "OCCURS_IN"
    AFFECTS = "AFFECTS"
    REQUIRES_STATE = "REQUIRES_STATE"
    TRIGGERS_EVENT = "TRIGGERS_EVENT"
    CHANGES_STATE = "CHANGES_STATE"
    RESOLVES = "RESOLVES"
    # Systemic (World Building)
    HAS_SYSTEM = "HAS_SYSTEM"
    HAS_PRESSURE = "HAS_PRESSURE"
    REQUIRES_RESOURCE = "REQUIRES_RESOURCE"
    FORBIDDEN_BY = "FORBIDDEN_BY"
    CAUSES = "CAUSES"
    DEPENDS_ON = "DEPENDS_ON"
    TRAUMATIZED_BY = "TRAUMATIZED_BY"


class Node(BaseModel):
    id: str
    type: str  # Allow dynamic strings
    properties: dict[str, Any] = Field(default_factory=dict)
    embedding: list[float] | None = Field(
        default=None, description="Vector representation of the node for semantic search."
    )
    workspace_id: str | None = None
    project_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Edge(BaseModel):
    source: str
    target: str
    type: str  # Allow dynamic strings
    properties: dict[str, Any] = Field(default_factory=dict)
    workspace_id: str | None = None
    project_id: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class SchemaRegistry:
    """
    Manages a hybrid schema combining core hardcoded Enums (NodeType, EdgeType)
    with user-defined custom strings loaded from a JSON config file.
    """

    def __init__(self, config_path: str = "backend/config/custom_schema.json"):
        self.config_path = Path(config_path)
        self.custom_node_types: set[str] = set()
        self.custom_edge_types: set[str] = set()
        self._load()

    def _load(self):
        if self.config_path.exists():
            try:
                with open(self.config_path, encoding="utf-8") as f:
                    data = json.load(f)
                    self.custom_node_types = set(data.get("node_types", []))
                    self.custom_edge_types = set(data.get("edge_types", []))
            except Exception as e:
                log_event(
                    logger,
                    "schema.custom_load_failed",
                    level=logging.WARNING,
                    path=str(self.config_path),
                    error_type=type(e).__name__,
                )

    def _save(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "node_types": sorted(self.custom_node_types),
                    "edge_types": sorted(self.custom_edge_types),
                },
                f,
                indent=4,
            )

    def add_custom_node_type(self, node_type: str):
        if node_type not in self.custom_node_types and node_type not in [e.value for e in NodeType]:
            self.custom_node_types.add(node_type)
            self._save()

    def add_custom_edge_type(self, edge_type: str):
        if edge_type not in self.custom_edge_types and edge_type not in [e.value for e in EdgeType]:
            self.custom_edge_types.add(edge_type)
            self._save()

    def remove_custom_node_type(self, node_type: str):
        if node_type in self.custom_node_types:
            self.custom_node_types.remove(node_type)
            self._save()

    def remove_custom_edge_type(self, edge_type: str):
        if edge_type in self.custom_edge_types:
            self.custom_edge_types.remove(edge_type)
            self._save()

    def is_valid_node_type(self, node_type: str) -> bool:
        return node_type in self.custom_node_types or node_type in [e.value for e in NodeType]

    def is_valid_edge_type(self, edge_type: str) -> bool:
        return edge_type in self.custom_edge_types or edge_type in [e.value for e in EdgeType]

    def get_all_node_types(self) -> list[str]:
        return [e.value for e in NodeType] + sorted(self.custom_node_types)

    def get_all_edge_types(self) -> list[str]:
        return [e.value for e in EdgeType] + sorted(self.custom_edge_types)


schema_registry = SchemaRegistry()
