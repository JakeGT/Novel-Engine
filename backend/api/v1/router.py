from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query, status

from backend.api.dependencies import (
    get_character_service,
    get_container,
    get_embeddings,
    get_graph,
    get_schema_registry,
)
from backend.api.v1 import schemas as s
from backend.container import Container
from backend.domain.errors import (
    ConflictError,
    FeatureNotImplementedError,
    NotFoundError,
    ValidationError,
)
from backend.graph.policies import get_policy
from backend.graph.resolver import extract_entity_ids
from backend.graph.schema import Edge, Node
from backend.graph.summarizer import summarize_context
from backend.ports import EmbeddingPort, GraphRepository, SchemaRegistryPort
from backend.services.character_service import CharacterService

router = APIRouter()

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


def _validate_identifier(value: str, kind: str) -> None:
    if not _IDENTIFIER.match(value or ""):
        raise ValidationError(
            f"Invalid {kind}: {value!r}. Use letters, digits, and underscores only."
        )


@router.get("/entities", response_model=s.EntityList, tags=["entities"])
def list_entities(
    type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    graph: GraphRepository = Depends(get_graph),
) -> s.EntityList:
    nodes = graph.get_nodes(node_type=type)
    page = nodes[offset : offset + limit]
    return s.EntityList(
        items=[s.entity_from_node(node) for node in page],
        total=len(nodes),
        limit=limit,
        offset=offset,
    )


@router.get("/entities/search", response_model=s.EntitySearchList, tags=["entities"])
def search_entities(
    q: str = Query(min_length=2),
    type: str | None = Query(default=None),
    graph: GraphRepository = Depends(get_graph),
) -> s.EntitySearchList:
    results = graph.search_nodes_by_name(q, type)
    items = [s.EntitySearchItem(id=r["id"], name=r["name"], type=r["type"]) for r in results]
    return s.EntitySearchList(items=items, total=len(items))


@router.post(
    "/entities",
    response_model=s.Entity,
    status_code=status.HTTP_201_CREATED,
    tags=["entities"],
)
def create_entity(
    body: s.EntityCreate,
    graph: GraphRepository = Depends(get_graph),
    embeddings: EmbeddingPort = Depends(get_embeddings),
    registry: SchemaRegistryPort = Depends(get_schema_registry),
) -> s.Entity:
    _validate_identifier(body.id, "entity id")
    _validate_identifier(body.type, "entity type")
    if not registry.is_valid_node_type(body.type):
        raise ValidationError(
            f"Unknown node type {body.type!r}. Register it via the schema API first."
        )
    embedding = embeddings.embed_node(body.type, body.properties)
    node = Node(id=body.id, type=body.type, properties=body.properties, embedding=embedding)
    graph.add_node(node)
    return s.entity_from_node(node)


@router.get("/entities/{entity_id}", response_model=s.Entity, tags=["entities"])
def get_entity(entity_id: str, graph: GraphRepository = Depends(get_graph)) -> s.Entity:
    node = graph.get_node(entity_id)
    if node is None:
        raise NotFoundError(f"Entity {entity_id!r} not found")
    return s.entity_from_node(node)


@router.put("/entities/{entity_id}", response_model=s.Entity, tags=["entities"])
def update_entity(
    entity_id: str,
    body: s.EntityUpdate,
    graph: GraphRepository = Depends(get_graph),
    embeddings: EmbeddingPort = Depends(get_embeddings),
    registry: SchemaRegistryPort = Depends(get_schema_registry),
) -> s.Entity:
    if body.id is not None and body.id != entity_id:
        raise ValidationError("Body id does not match the path id.")
    _validate_identifier(body.type, "entity type")
    if not registry.is_valid_node_type(body.type):
        raise ValidationError(
            f"Unknown node type {body.type!r}. Register it via the schema API first."
        )
    if graph.get_node(entity_id) is None:
        raise NotFoundError(f"Entity {entity_id!r} not found")
    embedding = embeddings.embed_node(body.type, body.properties)
    node = Node(id=entity_id, type=body.type, properties=body.properties, embedding=embedding)
    updated = graph.update_node(node)
    if updated is None:
        raise NotFoundError(f"Entity {entity_id!r} not found")
    return s.entity_from_node(updated)


@router.get("/entities/{entity_id}/edges", response_model=s.EdgeList, tags=["entities"])
def list_entity_edges(entity_id: str, graph: GraphRepository = Depends(get_graph)) -> s.EdgeList:
    if graph.get_node(entity_id) is None:
        raise NotFoundError(f"Entity {entity_id!r} not found")
    edges = graph.get_edges(entity_id)
    return s.EdgeList(
        items=[s.edge_from_domain(edge) for edge in edges],
        total=len(edges),
        limit=len(edges),
        offset=0,
    )


@router.post(
    "/edges",
    response_model=s.EdgeModel,
    status_code=status.HTTP_201_CREATED,
    tags=["edges"],
)
def create_edge(
    body: s.EdgeCreate,
    graph: GraphRepository = Depends(get_graph),
    registry: SchemaRegistryPort = Depends(get_schema_registry),
) -> s.EdgeModel:
    _validate_identifier(body.type, "edge type")
    if not registry.is_valid_edge_type(body.type):
        raise ValidationError(
            f"Unknown edge type {body.type!r}. Register it via the schema API first."
        )
    if graph.get_node(body.source) is None:
        raise ValidationError(f"Source entity {body.source!r} not found")
    if graph.get_node(body.target) is None:
        raise ValidationError(f"Target entity {body.target!r} not found")
    edge = Edge(source=body.source, target=body.target, type=body.type, properties=body.properties)
    graph.add_edge(edge)
    return s.edge_from_domain(edge)


@router.get("/edges", response_model=s.EdgeList, tags=["edges"])
def list_edges(
    source_id: str = Query(...),
    target_id: str = Query(...),
    type: str | None = Query(default=None),
    graph: GraphRepository = Depends(get_graph),
) -> s.EdgeList:
    edges = [
        edge
        for edge in graph.get_edges(source_id)
        if edge.target == target_id and (type is None or edge.type == type)
    ]
    return s.EdgeList(
        items=[s.edge_from_domain(edge) for edge in edges],
        total=len(edges),
        limit=len(edges),
        offset=0,
    )


@router.get("/schema/node-types", response_model=s.SchemaTypesResponse, tags=["schema"])
def list_node_types(
    registry: SchemaRegistryPort = Depends(get_schema_registry),
) -> s.SchemaTypesResponse:
    return s.SchemaTypesResponse(types=sorted(set(registry.get_all_node_types())))


@router.post(
    "/schema/node-types",
    response_model=s.SchemaTypesResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["schema"],
)
def add_node_type(
    body: s.SchemaTypeRequest, registry: SchemaRegistryPort = Depends(get_schema_registry)
) -> s.SchemaTypesResponse:
    _validate_identifier(body.type, "node type")
    if registry.is_valid_node_type(body.type):
        raise ConflictError(f"Node type {body.type!r} already exists")
    registry.add_custom_node_type(body.type)
    return s.SchemaTypesResponse(types=sorted(set(registry.get_all_node_types())))


@router.delete(
    "/schema/node-types/{node_type}", response_model=s.SchemaTypesResponse, tags=["schema"]
)
def remove_node_type(
    node_type: str, registry: SchemaRegistryPort = Depends(get_schema_registry)
) -> s.SchemaTypesResponse:
    if node_type not in registry.custom_node_types:
        raise NotFoundError(f"Custom node type {node_type!r} not found")
    registry.remove_custom_node_type(node_type)
    return s.SchemaTypesResponse(types=sorted(set(registry.get_all_node_types())))


@router.get("/schema/edge-types", response_model=s.SchemaTypesResponse, tags=["schema"])
def list_edge_types(
    registry: SchemaRegistryPort = Depends(get_schema_registry),
) -> s.SchemaTypesResponse:
    return s.SchemaTypesResponse(types=sorted(set(registry.get_all_edge_types())))


@router.post(
    "/schema/edge-types",
    response_model=s.SchemaTypesResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["schema"],
)
def add_edge_type(
    body: s.SchemaTypeRequest, registry: SchemaRegistryPort = Depends(get_schema_registry)
) -> s.SchemaTypesResponse:
    _validate_identifier(body.type, "edge type")
    if registry.is_valid_edge_type(body.type):
        raise ConflictError(f"Edge type {body.type!r} already exists")
    registry.add_custom_edge_type(body.type)
    return s.SchemaTypesResponse(types=sorted(set(registry.get_all_edge_types())))


@router.delete(
    "/schema/edge-types/{edge_type}", response_model=s.SchemaTypesResponse, tags=["schema"]
)
def remove_edge_type(
    edge_type: str, registry: SchemaRegistryPort = Depends(get_schema_registry)
) -> s.SchemaTypesResponse:
    if edge_type not in registry.custom_edge_types:
        raise NotFoundError(f"Custom edge type {edge_type!r} not found")
    registry.remove_custom_edge_type(edge_type)
    return s.SchemaTypesResponse(types=sorted(set(registry.get_all_edge_types())))


@router.post("/context", response_model=s.ContextResponse, tags=["context"])
def build_context(
    body: s.ContextRequest,
    graph: GraphRepository = Depends(get_graph),
    embeddings: EmbeddingPort = Depends(get_embeddings),
) -> s.ContextResponse:
    policy = get_policy(body.context_type)
    explicit_anchors = extract_entity_ids(body.text)

    semantic_anchors: list[str] = []
    if policy.use_semantic_search:
        embedding = embeddings.embed(body.text)
        if embedding:
            semantic_anchors = graph.search_semantic_nodes(
                embedding,
                top_k=policy.semantic_top_k,
                min_score=policy.semantic_min_score,
            )

    anchors = list(dict.fromkeys(explicit_anchors + semantic_anchors))
    if not anchors:
        return s.ContextResponse(nodes=[], summary="", policy_used=policy.context_type.value)

    subgraph = graph.traverse(anchors, policy)
    nodes = [
        s.ContextNode(
            id=item["id"],
            type=item.get("type", "Unknown"),
            properties=item.get("properties", {}),
            relevance_score=float(item.get("relevance_score", 0)),
            depth=item.get("depth"),
        )
        for item in subgraph
    ]
    return s.ContextResponse(
        nodes=nodes,
        summary=summarize_context(subgraph),
        policy_used=policy.context_type.value,
    )


@router.post("/rag/query", tags=["rag"])
def rag_query(body: s.QueryRequest) -> None:
    raise FeatureNotImplementedError(
        "RAG answer generation is not implemented. Use /api/v1/context for retrieval."
    )


@router.post(
    "/characters/init",
    response_model=s.Entity,
    status_code=status.HTTP_201_CREATED,
    tags=["characters"],
)
def character_init(
    body: s.CharacterInitRequest,
    service: CharacterService = Depends(get_character_service),
) -> s.Entity:
    node = service.init_character_anchor(body.name, body.bio)
    return s.entity_from_node(node)


@router.post("/characters/generate-questions", response_model=s.QuestionSheet, tags=["characters"])
async def character_generate_questions(
    body: s.CharacterQuestionsRequest,
    service: CharacterService = Depends(get_character_service),
) -> s.QuestionSheet:
    try:
        sheet = await service.generate_questions(body.id, body.use_cache)
    except ValueError as error:
        raise NotFoundError(str(error)) from error
    return s.QuestionSheet(
        character_name=sheet.character_name,
        character_id=sheet.character_id,
        questions=[
            s.QuestionItem(
                id=question.id,
                question=question.question,
                category=question.category,
                context_required=question.context_required,
            )
            for question in sheet.questions
        ],
    )


@router.post(
    "/characters/process-answers", response_model=s.GraphUpdateResponse, tags=["characters"]
)
async def character_process_answers(
    body: s.CharacterAnswersRequest,
    service: CharacterService = Depends(get_character_service),
    container: Container = Depends(get_container),
) -> s.GraphUpdateResponse:
    try:
        update = await service.process_answers(body.id, body.chapter, body.answers, body.use_cache)
    except ValueError as error:
        raise NotFoundError(str(error)) from error

    for node_data in update.nodes:
        embedding = container.embeddings.embed_node(node_data.type, node_data.properties)
        container.graph.add_node(
            Node(
                id=node_data.id,
                type=node_data.type,
                properties=node_data.properties,
                embedding=embedding,
            )
        )
    for edge_data in update.edges:
        container.graph.add_edge(
            Edge(
                source=edge_data.source,
                target=edge_data.target,
                type=edge_data.type,
                properties=edge_data.properties,
            )
        )

    return s.GraphUpdateResponse(
        nodes=[s.NodeUpdateItem(**node.model_dump()) for node in update.nodes],
        edges=[s.EdgeUpdateItem(**edge.model_dump()) for edge in update.edges],
    )
