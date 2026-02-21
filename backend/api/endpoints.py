from fastapi import APIRouter, HTTPException
from backend.graph.manager import graph_manager
from backend.graph.schema import Node, Edge, NodeType, schema_registry
from backend.core.vector_store import vector_store
from backend.services.character_service import CharacterService
from backend.services.context_service import context_service
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

character_service = CharacterService()

router = APIRouter()

class CreateNodeRequest(BaseModel):
    id: str
    type: str # Use string instead of Enum to support dynamic types
    properties: Dict[str, Any]

class CreateEdgeRequest(BaseModel):
    source: str
    target: str
    type: str # Simplified for flexibility, ideally EdgeType
    properties: Dict[str, Any]

class QueryRequest(BaseModel):
    query: str

class CustomTypeRequest(BaseModel):
    type: str

@router.get("/schema/nodes/")
def get_custom_node_types():
    return {"types": list(schema_registry.custom_node_types)}

@router.post("/schema/nodes/")
def add_custom_node_type(request: CustomTypeRequest):
    schema_registry.add_custom_node_type(request.type)
    return {"message": f"Custom node type '{request.type}' added.", "types": list(schema_registry.custom_node_types)}

@router.delete("/schema/nodes/{node_type}")
def remove_custom_node_type(node_type: str):
    schema_registry.remove_custom_node_type(node_type)
    return {"message": f"Custom node type '{node_type}' removed.", "types": list(schema_registry.custom_node_types)}

@router.get("/schema/edges/")
def get_custom_edge_types():
    return {"types": list(schema_registry.custom_edge_types)}

@router.post("/schema/edges/")
def add_custom_edge_type(request: CustomTypeRequest):
    schema_registry.add_custom_edge_type(request.type)
    return {"message": f"Custom edge type '{request.type}' added.", "types": list(schema_registry.custom_edge_types)}

@router.delete("/schema/edges/{edge_type}")
def remove_custom_edge_type(edge_type: str):
    schema_registry.remove_custom_edge_type(edge_type)
    return {"message": f"Custom edge type '{edge_type}' removed.", "types": list(schema_registry.custom_edge_types)}

@router.post("/graph/nodes/")
def create_node(node: CreateNodeRequest):
    if not schema_registry.is_valid_node_type(node.type):
        schema_registry.add_custom_node_type(node.type)
        
    try:
        new_node = Node(id=node.id, type=node.type, properties=node.properties)
        graph_manager.add_node(new_node)
        return {"message": "Node created", "node": new_node}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/graph/nodes/{node_id}")
def get_node(node_id: str):
    node = graph_manager.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return node

@router.get("/graph/nodes/search")
def search_nodes(query: str):
    """
    Fast fuzzy search across node names (or IDs if no name) for the given query.
    Used for frontend @-mentions to provide quick autocomplete suggestions.
    """
    if not query or len(query.strip()) < 2:
        return []
        
    return graph_manager.search_nodes_by_name(query.strip())

@router.get("/graph/nodes/entities")
def get_entities(type: str = None):
    if type:
        return graph_manager.get_nodes_by_type(type)
    return []

@router.put("/graph/nodes/{node_id}")
def update_node(node_id: str, node: CreateNodeRequest):
    node = graph_manager.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    node.type = node.type
    node.properties = node.properties
    graph_manager.update_node(node)
    return {"message": "Node updated", "node": node}

@router.post("/graph/edges/")
def create_edge(edge: CreateEdgeRequest):
    # Basic validation
    if not graph_manager.get_node(edge.source) or not graph_manager.get_node(edge.target):
        raise HTTPException(status_code=400, detail="Source or Target node not found")
        
    if not schema_registry.is_valid_edge_type(edge.type):
        schema_registry.add_custom_edge_type(edge.type)
    
    try:
        # In a real app, map string to EdgeType enum
        new_edge = Edge(source=edge.source, target=edge.target, type=edge.type, properties=edge.properties)
        graph_manager.add_edge(new_edge)
        return {"message": "Edge created", "edge": new_edge}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/graph/edges/{edge_id}")
def delete_edge(edge_id: str):
    try:
        graph_manager.delete_edge(edge_id)
        return {"message": "Edge deleted", "edge_id": edge_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
        
@router.post("/rag/query")
def rag_query(request: QueryRequest):
    # 1. Retrieve relevant text chunks
    docs = vector_store.query(request.query)
    
    # 2. (Placeholder) Logic to traverse Graph based on entities in query
    # entities = extract_entities(request.query)
    # graph_context = graph_manager.get_subgraph(entities)
    
    return {
        "vector_results": docs,
        "graph_context": "Placeholder for Graph Traversal Results",
        "generated_answer": "Placeholder for LLM Generation"
    }

class ContextRequest(BaseModel):
    text: str

@router.post("/rag/context")
def extract_context_from_tags(request: ContextRequest):
    """
    Takes a chunk of text (e.g., a user's prompt or scene drafted in the frontend),
    finds all [DisplayName](entity:Type:ID) tags, and returns their full
    Knowledge Graph data (and immediate sub-modules) as context for the LLM.
    """
    context_data = context_service.get_context_for_prompt(request.text)
    return context_data

# Character Creation Workflow Endpoints

class CharacterInitRequest(BaseModel):
    name: str
    bio: Optional[str] = None

class QuestionsRequest(BaseModel):
    name: str
    bio: str

class AnswersRequest(BaseModel):
    name: str
    answers: Dict[str, str]

@router.post("/characters/init")
async def init_character(request: CharacterInitRequest):
    """Workflow Step 1 & 2: Create initial node"""
    node = Node(id=request.name.lower().replace(" ", "_"), type="Character", properties={"name": request.name, "bio": request.bio})
    graph_manager.add_node(node)
    return {"message": "Character initialized", "node": node}

@router.post("/characters/generate-questions")
async def generate_questions(request: QuestionsRequest):
    """Workflow Step 3: Generate questions via GPT-4o"""
    questions = await character_service.generate_questions(request.name, request.bio)
    return questions

@router.post("/characters/process-answers")
async def process_answers(request: AnswersRequest):
    """Workflow Step 4: Convert answers to KG format and update graph"""
    update = await character_service.process_answers(request.name, request.answers)
    
    # Apply updates to graph
    for node_data in update.nodes:
        node = Node(id=node_data.id, type=node_data.type, properties=node_data.properties)
        graph_manager.add_node(node)
    
    for edge_data in update.edges:
        edge = Edge(source=edge_data.source, target=edge_data.target, type=edge_data.type, properties=edge_data.properties)
        graph_manager.add_edge(edge)
        
    return {"message": "Character graph updated", "update": update}
