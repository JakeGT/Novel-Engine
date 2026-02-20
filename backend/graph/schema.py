from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime

class NodeType(str, Enum):
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

class EdgeType(str, Enum):
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
    CONTAINS = "CONTAINS"
    TRAUMATIZED_BY = "TRAUMATIZED_BY"

class Node(BaseModel):
    id: str
    type: str # Allow dynamic strings, use NodeType enum for standard constants
    properties: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Edge(BaseModel):
    source: str
    target: str
    type: str # Allow dynamic strings, use EdgeType enum for standard constants
    properties: Dict[str, Any] = Field(default_factory=dict)
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
