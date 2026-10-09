from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Query, status

from backend.adapters.system import uuid7
from backend.api.auth import (
    get_project_scope,
    require_local_graph_write,
    require_local_schema_write,
    require_project_scope,
    require_session,
)
from backend.api.dependencies import (
    get_character_service,
    get_container,
    get_credential_service,
    get_embeddings,
    get_graph,
    get_schema_registry,
    get_settings,
    get_usage_service,
)
from backend.api.v1 import schemas as s
from backend.container import Container
from backend.core.config import Settings
from backend.domain.errors import (
    CapabilityUnavailableError,
    ConflictError,
    FeatureNotImplementedError,
    NotFoundError,
    ValidationError,
)
from backend.domain.identity import AccessScope
from backend.domain.usage import UsageOutcome, UsageRecord
from backend.graph.policies import get_policy
from backend.graph.resolver import extract_entity_ids
from backend.graph.schema import Edge, Node
from backend.graph.summarizer import summarize_context
from backend.ports import AIUsage, EmbeddingPort, GraphRepository, SchemaRegistryPort
from backend.services.auth_service import AuthenticatedSession
from backend.services.character_service import CharacterService
from backend.services.credential_service import CredentialService
from backend.services.usage_service import ProviderUsageService

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

project_router = APIRouter()

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


def _validate_identifier(value: str, kind: str) -> None:
    if not _IDENTIFIER.match(value or ""):
        raise ValidationError(
            f"Invalid {kind}: {value!r}. Use letters, digits, and underscores only."
        )


async def _provider_context(
    *,
    settings: Settings,
    credentials: CredentialService,
    usage: ProviderUsageService,
    scope: AccessScope,
) -> tuple[str | None, str | None, str | None, Any]:
    """Resolve the BYOK credential, enforce usage limits, and build a usage sink.

    Hosted deployments require a workspace credential (no platform-funded
    inference); local self-hosted mode uses the operator's server key.
    """
    workspace_id = scope.workspace_id or ""
    project_id = scope.project_id or ""
    credential_id: str | None = None
    api_key: str | None = None
    provider_base_url: str | None = None
    provider = settings.AI_PROVIDER
    if settings.is_hosted:
        resolved = await credentials.resolve_active_for_use(scope, workspace_id)
        if resolved is None:
            raise CapabilityUnavailableError(
                "No provider credential is configured for this workspace."
            )
        credential_id, api_key = resolved
        provider_base_url = OPENROUTER_BASE_URL
        provider = "openrouter"

    await usage.ensure_within_limits(workspace_id=workspace_id, actor_user_id=scope.actor_user_id)

    async def sink(ai_usage: AIUsage, model: str) -> None:
        await usage.record(
            UsageRecord(
                id=str(uuid7()),
                workspace_id=workspace_id,
                project_id=project_id or None,
                actor_user_id=scope.actor_user_id,
                provider=provider,
                model=model or "",
                requested_route=model or "",
                prompt_tokens=ai_usage.prompt_tokens,
                completion_tokens=ai_usage.completion_tokens,
                outcome=UsageOutcome.SUCCESS,
            )
        )

    return credential_id, api_key, provider_base_url, sink


@project_router.get("/entities", response_model=s.EntityList, tags=["entities"])
def list_entities(
    type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    graph: GraphRepository = Depends(get_graph),
    scope: AccessScope = Depends(get_project_scope),
) -> s.EntityList:
    nodes = graph.get_nodes(
        workspace_id=scope.workspace_id or "", project_id=scope.project_id or "", node_type=type
    )
    page = nodes[offset : offset + limit]
    return s.EntityList(
        items=[s.entity_from_node(node) for node in page],
        total=len(nodes),
        limit=limit,
        offset=offset,
    )


@project_router.get("/entities/search", response_model=s.EntitySearchList, tags=["entities"])
def search_entities(
    q: str = Query(min_length=2),
    type: str | None = Query(default=None),
    graph: GraphRepository = Depends(get_graph),
    scope: AccessScope = Depends(get_project_scope),
) -> s.EntitySearchList:
    results = graph.search_nodes_by_name(
        q, workspace_id=scope.workspace_id or "", project_id=scope.project_id or "", type=type
    )
    items = [s.EntitySearchItem(id=r["id"], name=r["name"], type=r["type"]) for r in results]
    return s.EntitySearchList(items=items, total=len(items))


@project_router.post(
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
    scope: AccessScope = Depends(require_project_scope),
    _guard: AuthenticatedSession = Depends(require_local_graph_write),
) -> s.Entity:
    _validate_identifier(body.id, "entity id")
    _validate_identifier(body.type, "entity type")
    if not registry.is_valid_node_type(body.type):
        raise ValidationError(
            f"Unknown node type {body.type!r}. Register it via the schema API first."
        )
    embedding = embeddings.embed_node(body.type, body.properties)
    node = Node(
        id=body.id,
        type=body.type,
        properties=body.properties,
        embedding=embedding,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
    )
    graph.add_node(node)
    return s.entity_from_node(node)


@project_router.get("/entities/{entity_id}", response_model=s.Entity, tags=["entities"])
def get_entity(
    entity_id: str,
    graph: GraphRepository = Depends(get_graph),
    scope: AccessScope = Depends(get_project_scope),
) -> s.Entity:
    node = graph.get_node(
        entity_id, workspace_id=scope.workspace_id or "", project_id=scope.project_id or ""
    )
    if node is None:
        raise NotFoundError(f"Entity {entity_id!r} not found")
    return s.entity_from_node(node)


@project_router.put("/entities/{entity_id}", response_model=s.Entity, tags=["entities"])
def update_entity(
    entity_id: str,
    body: s.EntityUpdate,
    graph: GraphRepository = Depends(get_graph),
    embeddings: EmbeddingPort = Depends(get_embeddings),
    registry: SchemaRegistryPort = Depends(get_schema_registry),
    scope: AccessScope = Depends(require_project_scope),
    _guard: AuthenticatedSession = Depends(require_local_graph_write),
) -> s.Entity:
    if body.id is not None and body.id != entity_id:
        raise ValidationError("Body id does not match the path id.")
    _validate_identifier(body.type, "entity type")
    if not registry.is_valid_node_type(body.type):
        raise ValidationError(
            f"Unknown node type {body.type!r}. Register it via the schema API first."
        )
    workspace_id = scope.workspace_id or ""
    project_id = scope.project_id or ""
    existing = graph.get_node(entity_id, workspace_id=workspace_id, project_id=project_id)
    if existing is None:
        raise NotFoundError(f"Entity {entity_id!r} not found")
    if existing.type != body.type:
        raise ConflictError(
            f"Entity {entity_id!r} is of type {existing.type!r}; changing type is not supported."
        )
    embedding = embeddings.embed_node(body.type, body.properties)
    node = Node(
        id=entity_id,
        type=body.type,
        properties=body.properties,
        embedding=embedding,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
    )
    updated = graph.update_node(node)
    if updated is None:
        raise NotFoundError(f"Entity {entity_id!r} not found")
    return s.entity_from_node(updated)


@project_router.delete(
    "/entities/{entity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["entities"],
)
def delete_entity(
    entity_id: str,
    graph: GraphRepository = Depends(get_graph),
    scope: AccessScope = Depends(require_project_scope),
    _guard: AuthenticatedSession = Depends(require_local_graph_write),
) -> None:
    if not graph.delete_node(
        entity_id, workspace_id=scope.workspace_id or "", project_id=scope.project_id or ""
    ):
        raise NotFoundError(f"Entity {entity_id!r} not found")


@project_router.get("/entities/{entity_id}/edges", response_model=s.EdgeList, tags=["entities"])
def list_entity_edges(
    entity_id: str,
    graph: GraphRepository = Depends(get_graph),
    scope: AccessScope = Depends(get_project_scope),
) -> s.EdgeList:
    workspace_id = scope.workspace_id or ""
    project_id = scope.project_id or ""
    if graph.get_node(entity_id, workspace_id=workspace_id, project_id=project_id) is None:
        raise NotFoundError(f"Entity {entity_id!r} not found")
    edges = graph.get_edges(entity_id, workspace_id=workspace_id, project_id=project_id)
    return s.EdgeList(
        items=[s.edge_from_domain(edge) for edge in edges],
        total=len(edges),
        limit=len(edges),
        offset=0,
    )


@project_router.post(
    "/edges",
    response_model=s.EdgeModel,
    status_code=status.HTTP_201_CREATED,
    tags=["edges"],
)
def create_edge(
    body: s.EdgeCreate,
    graph: GraphRepository = Depends(get_graph),
    registry: SchemaRegistryPort = Depends(get_schema_registry),
    scope: AccessScope = Depends(require_project_scope),
    _guard: AuthenticatedSession = Depends(require_local_graph_write),
) -> s.EdgeModel:
    _validate_identifier(body.type, "edge type")
    if not registry.is_valid_edge_type(body.type):
        raise ValidationError(
            f"Unknown edge type {body.type!r}. Register it via the schema API first."
        )
    workspace_id = scope.workspace_id or ""
    project_id = scope.project_id or ""
    if graph.get_node(body.source, workspace_id=workspace_id, project_id=project_id) is None:
        raise ValidationError(f"Source entity {body.source!r} not found")
    if graph.get_node(body.target, workspace_id=workspace_id, project_id=project_id) is None:
        raise ValidationError(f"Target entity {body.target!r} not found")
    edge = Edge(
        source=body.source,
        target=body.target,
        type=body.type,
        properties=body.properties,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
    )
    graph.add_edge(edge)
    return s.edge_from_domain(edge)


@project_router.get("/edges", response_model=s.EdgeList, tags=["edges"])
def list_edges(
    source_id: str = Query(...),
    target_id: str = Query(...),
    type: str | None = Query(default=None),
    graph: GraphRepository = Depends(get_graph),
    scope: AccessScope = Depends(get_project_scope),
) -> s.EdgeList:
    edges = [
        edge
        for edge in graph.get_edges(
            source_id, workspace_id=scope.workspace_id or "", project_id=scope.project_id or ""
        )
        if edge.target == target_id and (type is None or edge.type == type)
    ]
    return s.EdgeList(
        items=[s.edge_from_domain(edge) for edge in edges],
        total=len(edges),
        limit=len(edges),
        offset=0,
    )


@project_router.get("/schema/node-types", response_model=s.SchemaTypesResponse, tags=["schema"])
def list_node_types(
    registry: SchemaRegistryPort = Depends(get_schema_registry),
    _session: AuthenticatedSession = Depends(require_session),
) -> s.SchemaTypesResponse:
    return s.SchemaTypesResponse(types=sorted(set(registry.get_all_node_types())))


@project_router.post(
    "/schema/node-types",
    response_model=s.SchemaTypesResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["schema"],
)
def add_node_type(
    body: s.SchemaTypeRequest,
    registry: SchemaRegistryPort = Depends(get_schema_registry),
    _admin: AuthenticatedSession = Depends(require_local_schema_write),
) -> s.SchemaTypesResponse:
    _validate_identifier(body.type, "node type")
    if registry.is_valid_node_type(body.type):
        raise ConflictError(f"Node type {body.type!r} already exists")
    registry.add_custom_node_type(body.type)
    return s.SchemaTypesResponse(types=sorted(set(registry.get_all_node_types())))


@project_router.delete(
    "/schema/node-types/{node_type}", response_model=s.SchemaTypesResponse, tags=["schema"]
)
def remove_node_type(
    node_type: str,
    registry: SchemaRegistryPort = Depends(get_schema_registry),
    _admin: AuthenticatedSession = Depends(require_local_schema_write),
) -> s.SchemaTypesResponse:
    if node_type not in registry.custom_node_types:
        raise NotFoundError(f"Custom node type {node_type!r} not found")
    registry.remove_custom_node_type(node_type)
    return s.SchemaTypesResponse(types=sorted(set(registry.get_all_node_types())))


@project_router.get("/schema/edge-types", response_model=s.SchemaTypesResponse, tags=["schema"])
def list_edge_types(
    registry: SchemaRegistryPort = Depends(get_schema_registry),
    _session: AuthenticatedSession = Depends(require_session),
) -> s.SchemaTypesResponse:
    return s.SchemaTypesResponse(types=sorted(set(registry.get_all_edge_types())))


@project_router.post(
    "/schema/edge-types",
    response_model=s.SchemaTypesResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["schema"],
)
def add_edge_type(
    body: s.SchemaTypeRequest,
    registry: SchemaRegistryPort = Depends(get_schema_registry),
    _admin: AuthenticatedSession = Depends(require_local_schema_write),
) -> s.SchemaTypesResponse:
    _validate_identifier(body.type, "edge type")
    if registry.is_valid_edge_type(body.type):
        raise ConflictError(f"Edge type {body.type!r} already exists")
    registry.add_custom_edge_type(body.type)
    return s.SchemaTypesResponse(types=sorted(set(registry.get_all_edge_types())))


@project_router.delete(
    "/schema/edge-types/{edge_type}", response_model=s.SchemaTypesResponse, tags=["schema"]
)
def remove_edge_type(
    edge_type: str,
    registry: SchemaRegistryPort = Depends(get_schema_registry),
    _admin: AuthenticatedSession = Depends(require_local_schema_write),
) -> s.SchemaTypesResponse:
    if edge_type not in registry.custom_edge_types:
        raise NotFoundError(f"Custom edge type {edge_type!r} not found")
    registry.remove_custom_edge_type(edge_type)
    return s.SchemaTypesResponse(types=sorted(set(registry.get_all_edge_types())))


@project_router.post("/context", response_model=s.ContextResponse, tags=["context"])
def build_context(
    body: s.ContextRequest,
    graph: GraphRepository = Depends(get_graph),
    embeddings: EmbeddingPort = Depends(get_embeddings),
    scope: AccessScope = Depends(get_project_scope),
) -> s.ContextResponse:
    policy = get_policy(body.context_type)
    workspace_id = scope.workspace_id or ""
    project_id = scope.project_id or ""
    explicit_anchors = extract_entity_ids(body.text)

    semantic_anchors: list[str] = []
    if policy.use_semantic_search:
        embedding = embeddings.embed(body.text)
        if embedding:
            semantic_anchors = graph.search_semantic_nodes(
                embedding,
                workspace_id=workspace_id,
                project_id=project_id,
                top_k=policy.semantic_top_k,
                min_score=policy.semantic_min_score,
            )

    anchors = list(dict.fromkeys(explicit_anchors + semantic_anchors))
    if not anchors:
        return s.ContextResponse(nodes=[], summary="", policy_used=policy.context_type.value)

    subgraph = graph.traverse(anchors, policy, workspace_id=workspace_id, project_id=project_id)
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


@project_router.post("/rag/query", tags=["rag"])
def rag_query(body: s.QueryRequest) -> None:
    raise FeatureNotImplementedError(
        "RAG answer generation is not implemented. Use /api/v1/context for retrieval."
    )


@project_router.post(
    "/characters/init",
    response_model=s.Entity,
    status_code=status.HTTP_201_CREATED,
    tags=["characters"],
)
def character_init(
    body: s.CharacterInitRequest,
    service: CharacterService = Depends(get_character_service),
    scope: AccessScope = Depends(require_project_scope),
    _local: AuthenticatedSession = Depends(require_local_graph_write),
) -> s.Entity:
    """Legacy graph-backed prototype (local only).

    The product workflow is ``/projects/{project_id}/characters/...``, which
    produces reviewable canon proposals instead of writing graph nodes.
    """
    node = service.init_character_anchor(
        body.name,
        body.bio,
        workspace_id=scope.workspace_id or "",
        project_id=scope.project_id or "",
    )
    return s.entity_from_node(node)


@project_router.post(
    "/characters/generate-questions", response_model=s.QuestionSheet, tags=["characters"]
)
async def character_generate_questions(
    body: s.CharacterQuestionsRequest,
    service: CharacterService = Depends(get_character_service),
    credentials: CredentialService = Depends(get_credential_service),
    usage: ProviderUsageService = Depends(get_usage_service),
    settings: Settings = Depends(get_settings),
    scope: AccessScope = Depends(require_project_scope),
) -> s.QuestionSheet:
    credential_id, api_key, provider_base_url, usage_sink = await _provider_context(
        settings=settings, credentials=credentials, usage=usage, scope=scope
    )
    try:
        sheet = await service.generate_questions(
            body.id,
            body.use_cache,
            workspace_id=scope.workspace_id or "",
            project_id=scope.project_id or "",
            credential=api_key,
            provider_base_url=provider_base_url,
            usage_sink=usage_sink,
        )
    except ValueError as error:
        raise NotFoundError(str(error)) from error
    if credential_id:
        await credentials.mark_used(scope.workspace_id or "", credential_id)
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


@project_router.post(
    "/characters/process-answers", response_model=s.GraphUpdateResponse, tags=["characters"]
)
async def character_process_answers(
    body: s.CharacterAnswersRequest,
    service: CharacterService = Depends(get_character_service),
    container: Container = Depends(get_container),
    credentials: CredentialService = Depends(get_credential_service),
    usage: ProviderUsageService = Depends(get_usage_service),
    settings: Settings = Depends(get_settings),
    scope: AccessScope = Depends(require_project_scope),
    _local: AuthenticatedSession = Depends(require_local_graph_write),
) -> s.GraphUpdateResponse:
    """Legacy graph-backed prototype (local only); it writes model output to the
    generic graph, so hosted deployments must use the canonical proposal workflow."""
    workspace_id = scope.workspace_id or ""
    project_id = scope.project_id or ""
    credential_id, api_key, provider_base_url, usage_sink = await _provider_context(
        settings=settings, credentials=credentials, usage=usage, scope=scope
    )
    try:
        update = await service.process_answers(
            body.id,
            body.chapter,
            body.answers,
            body.use_cache,
            workspace_id=workspace_id,
            project_id=project_id,
            credential=api_key,
            provider_base_url=provider_base_url,
            usage_sink=usage_sink,
        )
    except ValueError as error:
        raise NotFoundError(str(error)) from error
    if credential_id:
        await credentials.mark_used(workspace_id, credential_id)

    for node_data in update.nodes:
        embedding = container.embeddings.embed_node(node_data.type, node_data.properties)
        container.graph.add_node(
            Node(
                id=node_data.id,
                type=node_data.type,
                properties=node_data.properties,
                embedding=embedding,
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
            )
        )
    for edge_data in update.edges:
        container.graph.add_edge(
            Edge(
                source=edge_data.source,
                target=edge_data.target,
                type=edge_data.type,
                properties=edge_data.properties,
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
            )
        )

    return s.GraphUpdateResponse(
        nodes=[s.NodeUpdateItem(**node.model_dump()) for node in update.nodes],
        edges=[s.EdgeUpdateItem(**edge.model_dump()) for edge in update.edges],
    )


# Generic graph surface: project-scoped under /graph/projects/{project_id}/... so it
# does not shadow the canonical product API (/projects/{project_id}/entities) or the
# schema-version API. Writes here are local/admin-only; hosted writes must use the
# canonical mutation endpoints.
# Compatibility aliases keep the pre-project paths and still resolve a project
# (X-Project-Id header or the local project).
router = APIRouter()
router.include_router(project_router, prefix="/graph/projects/{project_id}", tags=["graph"])
router.include_router(project_router, tags=["legacy"])
