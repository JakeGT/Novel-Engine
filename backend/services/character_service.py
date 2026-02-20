from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from backend.graph.schema import NodeType, EdgeType
from openai import AsyncOpenAI
from backend.core.config import settings
from backend.graph.manager import graph_manager
import json
from pathlib import Path

class CharacterQuestion(BaseModel):
    id: str = Field(description="Unique identifier for the question (e.g., personality_1)")
    question: str
    category: str = Field(description="e.g., personality, appearance, motivation, secret")
    context_required: bool = Field(default=False, description="Whether this question needs context from the Knowledge Graph")

class CharacterQuestionSheet(BaseModel):
    character_name: str
    character_id: str
    questions: List[CharacterQuestion]

class NodeUpdate(BaseModel):
    id: str
    type: str # Use string for flexibility with Pydantic validation
    properties: Dict[str, Any]

class EdgeUpdate(BaseModel):
    source: str
    target: str
    type: str
    properties: Dict[str, Any]

class GraphUpdate(BaseModel):
    nodes: List[NodeUpdate] = Field(description="List of node data to create or update")
    edges: List[EdgeUpdate] = Field(description="List of edge data to create")

class CharacterService:
    def __init__(self, model: str = "gpt-4o"):
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = model

    def _save_debug_response(self, data: BaseModel, filename: str):
        debug_dir = Path("backend/debug")
        debug_dir.mkdir(parents=True, exist_ok=True)
        file_path = debug_dir / filename
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(data.model_dump_json(indent=4))

    async def generate_questions(self, character_name: str, character_id: str, bio: str) -> CharacterQuestionSheet:
        """
        Generates a list of questions to flesh out a character based on a basic bio.
        (Workflow Step 3)
        """
        prompt = f"Character Name: {character_name} (ID: {character_id})\nCharacter Bio: {bio}"
        
        response = await self.client.beta.chat.completions.parse(
            model=self.model,
            messages=[
                {
                    "role": "system", 
                    "content": (
                        "You are a character creation assistant. Your goal is to generate deep, specific questions "
                        "that reveal a character's stable identity, appearance, personality, current state, and beliefs.\n\n"
                        "Focus on:\n"
                        "1. Appearance: Physical traits and marks.\n"
                        "2. Personality: Traits, attachment style, alignment.\n"
                        "3. State: Current emotions, relationships/trust, chapter context.\n"
                        "4. Beliefs: Core claims about the world or others.\n"
                        "5. Backstory: Major events they participated in."
                    )
                },
                {"role": "user", "content": prompt}
            ],
            response_format=CharacterQuestionSheet,
        )
        # Inject the ID into the parsed object
        parsed = response.choices[0].message.parsed
        parsed.character_id = character_id
        
        self._save_debug_response(parsed, f"questions_{character_id}.json")
        return parsed

    async def process_answers(self, character_name: str, character_id: str, chapter_number: int, answers: Dict[str, str]) -> GraphUpdate:
        """
        Converts user answers into Knowledge Graph nodes and edges using the modular architecture.
        (Workflow Step 4)

        TODO: Add RAG for existing events, locations, and characters
        """
        answers_text = "\n".join([f"Q: {q}\nA: {a}" for q, a in answers.items()])
        prompt = f"Character: {character_name} (ID: {character_id}) Chapter Number: {chapter_number}\n\nUser Answers:\n{answers_text}"

        system_message = (
            "You are a Knowledge Graph specialist. Your task is to convert character details into a modular Graph format.\n\n"
            "MANDATORY STRUCTURE:\n"
            "1. Appearance: (Character)-[:HAS_APPEARANCE]->(:Appearance {build, height, hair, etc}). For example: (Character)-[:HAS_APPEARANCE]->(:Appearance {build: 'athletic', height: '6ft', hair: 'brown'})\n"
            "2. Personality: (Character)-[:HAS_PERSONALITY]->(:PersonalityProfile {core_trait, attachment_style, alignment}). For example: (Character)-[:HAS_PERSONALITY]->(:PersonalityProfile {core_trait: 'brave', attachment_style: 'avoidant', alignment: 'good'})\n"
            "3. State: (Character)-[:CURRENT_STATE]->(:State {anger, trust_levels, chapter, etc}). For example: (Character)-[:CURRENT_STATE]->(:State {anger: 0.5, trust_levels: 0.7, trust_character1: 0.4, chapter: 1})\n"
            "4. Beliefs: (Character)-[:BELIEVES]->(:Belief {about, claim, confidence, is_true}). For example: (Character)-[:BELIEVES]->(:Belief {about: 'The King', claim: 'He is a tyrant', confidence: 0.9, is_true: true})\n"
            "5. Events: (Character)-[:PARTICIPATED_IN]->(Event) (refer to existing events if mentioned)\n\n"
            "Instructions:\n"
            "- Ensure the Character node is the anchor.\n"
            "- Break down details into these specific sub-nodes.\n"
            "Use the following deterministic ID format:"
            "- Appearance: appearance"
            "- Personality: personality"
            "- State: state"
            "- Beliefs: belief_{short_slug}"
            "- Events: event_{short_slug}"
        )
        logging.info("Sending answered questions to LLM for processing")
        response = await self.client.beta.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt}
            ],
            response_format=GraphUpdate,
        )

        parsed = response.choices[0].message.parsed
        self._save_debug_response(parsed, f"graph_update_{character_id}_ch{chapter_number}_raw.json")
        
        
        # Post-process IDs to ensure uniqueness and character linking
        for node in parsed.nodes:
            if node.type == "Appearance":
                node.id = f"app_{character_id}"
            elif node.type == "PersonalityProfile":
                node.id = f"pers_{character_id}"
            elif node.type == "State":
                node.id = f"state_{character_id}_ch{chapter_number}"
            elif node.type == "Belief":
                node.id = f"belief_{node.id}_{character_id}"
            elif node.type == "Event":
                # Check for existing event by name if properties exist
                event_name = node.properties.get("name")
                if event_name:
                    existing = graph_manager.get_node_by_type_and_name("Event", event_name)
                    if existing:
                        node.id = existing["id"]
                    else:
                        # Search for top 10 and ask user through frontend
                        # TODO: Implement this and consider embeddings search
                        pass
            else:
                if node.type not in NodeType:
                    raise ValueError(f"Unknown node type: {node.type}")
                node.id += f"{node.type}_{character_id}"

        for edge in parsed.edges:
            if edge.type not in EdgeType:
                raise ValueError(f"Unknown edge type: {edge.type}")
            if edge.source != character_id:
                raise ValueError(f"Edge source is not the character: {edge.source}")
            if edge.target not in [n.id for n in parsed.nodes]:
                raise ValueError(f"Edge target is not a node: {edge.target}")

        # Final validation of IDs and types
        existing_node_ids = {n.id for n in parsed.nodes}
        existing_node_ids.add(character_id) # Character is usually the source/base

        self._save_debug_response(parsed, f"graph_update_{character_id}_ch{chapter_number}.json")
        return parsed
