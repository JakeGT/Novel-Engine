from backend.graph.vector_search import generate_query_embedding
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
from backend.core.config import settings
from backend.graph.manager import graph_manager
from backend.graph.vector_search import generate_node_embedding
from backend.graph.resolver import extract_entity_ids
from backend.graph.schema import Node
from backend.graph.policies import WORLD_CREATION_POLICY
import logging

logger = logging.getLogger(__name__)

class WorldQuestion(BaseModel):
    id: str = Field(description="Unique identifier for the question")
    category: str = Field(description="Category of the question")
    question: str = Field(description="The question to be asked")
    context_required: bool = Field(default = False, description = "Whether context from knowledge graph is required.")

class WorldQuestionSheet(BaseModel):
    world_name: str = Field(description="The name of the world")
    world_id: str = Field(description="The id of the worldn anchor node")
    questions: List[WorldQuestion] = Field(description="The list of questions to be asked")

class NodeUpdate(BaseModel):
    id: str = Field(description="The id of the node")
    type: str = Field(description="The type of the node")
    properties: Dict[str, Any] = Field(description="The properties of the node")

class EdgeUpdate(BaseModel):
    source: str = Field(description="The source of the edge")
    target: str = Field(description="The target of the edge")
    type: str = Field(description="The type of the edge")
    properties: Dict[str, Any] = Field(description="The properties of the edge")

class GraphUpdate(BaseModel):
    nodes: List[NodeUpdate] = Field(description="List of node data to create or update")
    edges: List[EdgeUpdate] = Field(description="List of edge data to create")

class WorldService:
    def __init__(self, model: str = "gpt-4o"):
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = model

    def init_world_anchor(self, world_name: str, world_description: str) -> Node:
        world_id = f"world_{world_name.lower().replace(' ', '_')}"
        properties = {"name": world_name, "description": world_description}

        embedding = generate_node_embedding("World", properties)
        new_node = Node(id=world_id, type="World", properties=properties, embedding=embedding)
        graph_manager.add_node(new_node)
        return new_node
        

    async def generate_world_questions(self, world_id: str) -> WorldQuestionSheet:
        node = graph_manager.get_node(world_id)
        if not node:
            raise ValueError(f"World node {world_id} not found")
        world_description = node.get("properties", {}).get("description", "")
        world_name = node.get("properties", {}).get("name", "")

        # TODO: Cache file

        context_str = "No existing relevant Knowledge Graph Context"
        context_nodes = []

        explitict_ids = extract_entity_ids(world_description)
        for n_id in explicit_ids:
            node = graph_manager.get_node(n_id)
            if node:
                context_nodes.append(f"- Explicitly Tagged [{n_id}] ({node.get('type')}): {json.dumps(node.get('properties', {}))}")

        desc_embeddings = generate_query_embedding(world_description)
        if desc_embeddings:
            semantic_ids = graph_manager.search_semantic_nodes(desc_embeddings, top_k=WORLD_CREATION_POLICY.semantic_top_k, min_score=WORLD_CREATION_POLICY.semantic_min_score)
            for n_id in semantic_ids:
                if n_id in explicit_ids: continue
                node = graph_manager.get_node(n_id)
                if node:
                    if node.get('type') not in WORLD_CREATION_POLICY.excluded_node_types:
                        context_nodes.append(f"- Semantically Related [{n_id}] ({node.get('type')}): {json.dumps(node.get('properties', {}))}")

        if context_nodes:
            context_str = "Existing Knowledge Graph Context (Use this to anchor your questions in the established world lore):\n" + "\n".join(context_nodes)

        prompt = f"World Name: {world_name} (ID: {world_id}) \nWorld Description: {world_description}\n\n{context_str}"

        

        


