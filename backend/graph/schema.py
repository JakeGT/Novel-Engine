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

class EdgeType(str, Enum):
    # Structural
    LOCATED_AT = "LOCATED_AT"
    PART_OF = "PART_OF"
    OWNS = "OWNS"
    MEMBER_OF = "MEMBER_OF"
    OCCURRED_AT = "OCCURRED_AT"
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
