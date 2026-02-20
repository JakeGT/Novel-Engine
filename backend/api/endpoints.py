from fastapi import APIRouter, HTTPException
from backend.graph.manager import graph_manager
from backend.graph.schema import Node, Edge, NodeType
from backend.core.vector_store import vector_store
from pydantic import BaseModel
from typing import List, Dict, Any

router = APIRouter()

class CreateNodeRequest(BaseModel):
    id: str
    type: NodeType
    properties: Dict[str, Any]

class CreateEdgeRequest(BaseModel):
    source: str
    target: str
    type: str # Simplified for flexibility, ideally EdgeType
    properties: Dict[str, Any]

class QueryRequest(BaseModel):
    query: str

@router.post("/nodes/")
def create_node(node: CreateNodeRequest):
    try:
        new_node = Node(id=node.id, type=node.type, properties=node.properties)
        graph_manager.add_node(new_node)
        return {"message": "Node created", "node": new_node}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/nodes/{node_id}")
def get_node(node_id: str):
    node = graph_manager.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    return node

@router.post("/edges/")
def create_edge(edge: CreateEdgeRequest):
    # Basic validation
    if not graph_manager.get_node(edge.source) or not graph_manager.get_node(edge.target):
        raise HTTPException(status_code=400, detail="Source or Target node not found")
    
    try:
        # In a real app, map string to EdgeType enum
        new_edge = Edge(source=edge.source, target=edge.target, type=edge.type, properties=edge.properties)
        graph_manager.add_edge(new_edge)
        return {"message": "Edge created", "edge": new_edge}
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
