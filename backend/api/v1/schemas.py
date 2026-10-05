"""Stable API v1 DTOs.

These are the external contract. They are deliberately separate from domain
models (`backend.graph.schema`) and persistence records: embeddings and other
storage-only fields are never exposed, and timestamps are normalized to
timezone-aware UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.graph.schema import Edge, Node


def normalize_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class Entity(BaseModel):
    id: str
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class EdgeModel(BaseModel):
    source: str
    target: str
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)


class EntityCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "char_arin",
                "type": "Character",
                "properties": {"name": "Arin", "role": "scout"},
            }
        }
    )

    id: str = Field(description="Stable, unique entity identifier.")
    type: str = Field(description="Registered node type.")
    properties: dict[str, Any] = Field(default_factory=dict)


class EntityUpdate(BaseModel):
    id: str | None = Field(default=None, description="Optional; must match the path id.")
    type: str = Field(description="Registered node type (full replacement).")
    properties: dict[str, Any] = Field(default_factory=dict)


class EdgeCreate(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "source": "char_arin",
                "target": "loc_gate",
                "type": "LOCATED_AT",
                "properties": {"since": 1},
            }
        }
    )

    source: str
    target: str
    type: str = Field(description="Registered relationship type.")
    properties: dict[str, Any] = Field(default_factory=dict)


class EntityList(BaseModel):
    items: list[Entity]
    total: int
    limit: int
    offset: int


class EdgeList(BaseModel):
    items: list[EdgeModel]
    total: int
    limit: int
    offset: int


class EntitySearchItem(BaseModel):
    id: str
    name: str
    type: str


class EntitySearchList(BaseModel):
    items: list[EntitySearchItem]
    total: int


class SchemaTypeRequest(BaseModel):
    type: str


class SchemaTypesResponse(BaseModel):
    types: list[str]


class ContextRequest(BaseModel):
    text: str
    context_type: str = "scene_generation"


class ContextNode(Entity):
    relevance_score: float = 0.0
    depth: int | None = None


class ContextResponse(BaseModel):
    nodes: list[ContextNode]
    summary: str
    policy_used: str


class QueryRequest(BaseModel):
    query: str


class CharacterInitRequest(BaseModel):
    name: str
    bio: str | None = None


class CharacterQuestionsRequest(BaseModel):
    id: str
    use_cache: bool = False


class CharacterAnswersRequest(BaseModel):
    id: str
    answers: dict[str, str]
    chapter: int
    use_cache: bool = False


class QuestionItem(BaseModel):
    id: str
    question: str
    category: str
    context_required: bool = False


class QuestionSheet(BaseModel):
    character_name: str
    character_id: str
    questions: list[QuestionItem]


class NodeUpdateItem(BaseModel):
    id: str
    type: str
    properties: dict[str, Any]


class EdgeUpdateItem(BaseModel):
    source: str
    target: str
    type: str
    properties: dict[str, Any]


class GraphUpdateResponse(BaseModel):
    nodes: list[NodeUpdateItem]
    edges: list[EdgeUpdateItem]


def entity_from_node(node: Node) -> Entity:
    return Entity(
        id=node.id,
        type=node.type,
        properties=node.properties,
        created_at=normalize_utc(node.created_at),
    )


def edge_from_domain(edge: Edge) -> EdgeModel:
    return EdgeModel(
        source=edge.source,
        target=edge.target,
        type=edge.type,
        properties=edge.properties,
    )
