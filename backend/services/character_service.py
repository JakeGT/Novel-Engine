from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from backend.graph.policies import CHARACTER_CREATION_POLICY
from backend.graph.resolver import extract_entity_ids
from backend.graph.schema import Node
from backend.ports import (
    AIGateway,
    ArtifactStore,
    EmbeddingPort,
    GraphRepository,
    SchemaRegistryPort,
)

logger = logging.getLogger(__name__)


class CharacterQuestion(BaseModel):
    id: str = Field(description="Unique identifier for the question (e.g., personality_1)")
    question: str
    category: str = Field(description="e.g., personality, appearance, motivation, secret")
    context_required: bool = Field(
        default=False, description="Whether this question needs context from the Knowledge Graph"
    )


class CharacterQuestionSheet(BaseModel):
    character_name: str
    character_id: str
    questions: list[CharacterQuestion]


class NodeUpdate(BaseModel):
    id: str
    type: str
    properties: dict[str, Any]


class EdgeUpdate(BaseModel):
    source: str
    target: str
    type: str
    properties: dict[str, Any]


class GraphUpdate(BaseModel):
    nodes: list[NodeUpdate] = Field(description="List of node data to create or update")
    edges: list[EdgeUpdate] = Field(description="List of edge data to create")


class CharacterService:
    def __init__(
        self,
        graph: GraphRepository,
        ai: AIGateway,
        embeddings: EmbeddingPort,
        schema_registry: SchemaRegistryPort,
        artifacts: ArtifactStore,
        model: str = "gpt-4o",
    ) -> None:
        self.graph = graph
        self.ai = ai
        self.embeddings = embeddings
        self.schema_registry = schema_registry
        self.artifacts = artifacts
        self.model = model

    def _save_debug_response(
        self, data: BaseModel, filename: str, *, workspace_id: str, project_id: str
    ) -> None:
        self.artifacts.save(
            filename,
            data.model_dump_json(indent=4),
            workspace_id=workspace_id,
            project_id=project_id,
        )

    def _load_debug_response(
        self,
        model_class: type[BaseModel],
        filename: str,
        *,
        workspace_id: str,
        project_id: str,
    ) -> Any | None:
        payload = self.artifacts.load(filename, workspace_id=workspace_id, project_id=project_id)
        if payload is None:
            return None
        try:
            return model_class.model_validate_json(payload)
        except Exception as e:
            logger.warning(f"Failed to load cache {filename}: {e}")
            return None

    def init_character_anchor(
        self,
        character_name: str,
        bio: str | None = None,
        *,
        workspace_id: str,
        project_id: str,
    ) -> Node:
        character_id = f"char_{character_name.lower().replace(' ', '_')}"
        properties = {"name": character_name, "description": bio}
        embedding = self.embeddings.embed_node("Character", properties)
        node = Node(
            id=character_id,
            type="Character",
            properties=properties,
            embedding=embedding,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        self.graph.add_node(node)
        return node

    async def generate_questions(
        self,
        character_id: str,
        use_cache: bool = False,
        *,
        workspace_id: str,
        project_id: str,
    ) -> CharacterQuestionSheet:
        node = self.graph.get_node(character_id, workspace_id=workspace_id, project_id=project_id)
        if node is None:
            raise ValueError(f"Character node {character_id} not found")
        bio = node.properties.get("description", "") or ""
        character_name = node.properties.get("name", "") or ""

        cache_filename = f"questions_{character_id}.json"
        if use_cache:
            cached = self._load_debug_response(
                CharacterQuestionSheet,
                cache_filename,
                workspace_id=workspace_id,
                project_id=project_id,
            )
            if cached:
                logger.info(f"Loaded questions from cache: {cache_filename}")
                return cached

        context_str = "No existing relevant Knowledge Graph context found."
        context_nodes = []

        explicit_ids = extract_entity_ids(bio)
        for n_id in explicit_ids:
            context_node = self.graph.get_node(
                n_id, workspace_id=workspace_id, project_id=project_id
            )
            if context_node:
                context_nodes.append(
                    f"- Explicitly Tagged [{n_id}] ({context_node.type}): "
                    f"{json.dumps(context_node.properties)}"
                )

        bio_embedding = self.embeddings.embed(bio)
        if bio_embedding:
            semantic_ids = self.graph.search_semantic_nodes(
                bio_embedding,
                workspace_id=workspace_id,
                project_id=project_id,
                top_k=CHARACTER_CREATION_POLICY.semantic_top_k,
                min_score=CHARACTER_CREATION_POLICY.semantic_min_score,
            )
            for n_id in semantic_ids:
                if n_id in explicit_ids:
                    continue
                context_node = self.graph.get_node(
                    n_id, workspace_id=workspace_id, project_id=project_id
                )
                if (
                    context_node
                    and context_node.type not in CHARACTER_CREATION_POLICY.excluded_node_types
                ):
                    context_nodes.append(
                        f"- Semantically Related {context_node.properties.get('name', n_id)} "
                        f"({context_node.type}): {json.dumps(context_node.properties)}"
                    )

        if context_nodes:
            context_str = (
                "Existing Knowledge Graph Context (Use this to anchor your questions in the "
                "established world lore):\n" + "\n".join(context_nodes)
            )

        prompt = (
            f"Character Name: {character_name} (ID: {character_id})\n"
            f"Character Bio: {bio}\n\n{context_str}"
        )

        response = await self.ai.generate_structured(
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
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format=CharacterQuestionSheet,
        )
        parsed = response.parsed
        parsed.character_id = character_id

        self._save_debug_response(
            parsed,
            f"questions_{character_id}.json",
            workspace_id=workspace_id,
            project_id=project_id,
        )
        return parsed

    async def _fetch_raw_graph_update(
        self,
        character_name: str,
        character_id: str,
        chapter_number: int,
        answers_text: str,
        use_cache: bool = False,
        *,
        workspace_id: str,
        project_id: str,
    ) -> GraphUpdate:
        cache_filename = f"graph_update_{character_id}_ch{chapter_number}_raw.json"
        if use_cache:
            cached = self._load_debug_response(
                GraphUpdate, cache_filename, workspace_id=workspace_id, project_id=project_id
            )
            if cached:
                logger.info(f"Loaded raw graph update from cache: {cache_filename}")
                return cached

        context_str = "No existing relevant Knowledge Graph context found."
        context_nodes = []

        explicit_ids = extract_entity_ids(answers_text)
        for n_id in explicit_ids:
            node = self.graph.get_node(n_id, workspace_id=workspace_id, project_id=project_id)
            if node:
                context_nodes.append(
                    f"- ID: {n_id} (EXPLICIT MATCH), Name: {node.properties.get('name', 'Unknown')} "
                    f"({node.type}): {json.dumps(node.properties)}"
                )

        answers_embedding = self.embeddings.embed(answers_text)
        if answers_embedding:
            semantic_ids = self.graph.search_semantic_nodes(
                answers_embedding,
                workspace_id=workspace_id,
                project_id=project_id,
                top_k=10,
                min_score=0.4,
            )
            for n_id in semantic_ids:
                if n_id in explicit_ids:
                    continue
                node = self.graph.get_node(n_id, workspace_id=workspace_id, project_id=project_id)
                if node and node.type not in [
                    "Appearance",
                    "PersonalityProfile",
                    "State",
                    "Belief",
                ]:
                    context_nodes.append(
                        f"- ID: {n_id}, Name: {node.properties.get('name', 'Unknown')} "
                        f"({node.type}): {json.dumps(node.properties)}"
                    )

        if context_nodes:
            context_str = (
                "Existing Knowledge Graph Entities (If the user mentions one of these, reuse its "
                "exact ID instead of creating a new node):\n" + "\n".join(context_nodes)
            )

        prompt = (
            f"Character: {character_name} (ID: {character_id}) Chapter Number: {chapter_number}\n\n"
            f"User Answers:\n{answers_text}\n\n{context_str}"
        )

        system_message = (
            "You are a Knowledge Graph specialist. Your task is to convert character details into a modular Graph format.\n\n"
            "MANDATORY STRUCTURE:\n"
            "1. Appearance: (Character)-[:HAS_APPEARANCE]->(:Appearance {build, height, hair, etc}). For example: (Character)-[:HAS_APPEARANCE]->(:Appearance {build: 'athletic', height: '6ft', hair: 'brown'})\n"
            "2. Personality: (Character)-[:HAS_PERSONALITY]->(:PersonalityProfile {core_trait, attachment_style, alignment}). For example: (Character)-[:HAS_PERSONALITY]->(:PersonalityProfile {core_trait: 'brave', attachment_style: 'avoidant', alignment: 'good'})\n"
            "3. State: (Character)-[:CURRENT_STATE]->(:State {anger, trust_levels, chapter, etc}). For example: (Character)-[:CURRENT_STATE]->(:State {anger: 0.5, trust_levels: 0.7, trust_character1: 0.4, chapter: 1})\n"
            "4. Beliefs: (Character)-[:BELIEVES]->(:Belief {about, claim, confidence, is_true}). For example: (Character)-[:BELIEVES]->(:Belief {about: 'The King', claim: 'He is a tyrant', confidence: 0.9, is_true: true})\n"
            "5. Events/Locations/Factions: (Character)-[:PARTICIPATED_IN|LOCATED_AT|MEMBER_OF]->(Event|Location|Faction). **CRITICAL**: Check the 'Existing Knowledge Graph Entities' in the prompt. If the user mentions an entity that matches an existing one, you MUST reuse exactly that ID. Do NOT create a new node if one already exists. If it is entirely new, generate a new ID.\n\n"
            "Instructions:\n"
            "- Ensure the Character node is the anchor.\n"
            "- Break down details into these specific sub-nodes.\n"
            "Use the following deterministic ID format for NEW personal nodes:"
            "- Appearance: appearance"
            "- Personality: personality"
            "- State: state"
            "- Beliefs: belief_{short_slug}\n"
            "- New Events: event_{short_slug}\n\n"
            "AVAILABLE TYPES FOR CUSTOM NODES/EDGES:\n"
            f"Node Types: {', '.join(self.schema_registry.get_all_node_types())}\n"
            f"Edge Types: {', '.join(self.schema_registry.get_all_edge_types())}\n"
            "You may ONLY use the types listed above."
        )
        logger.info("Sending answered questions to LLM for processing")
        response = await self.ai.generate_structured(
            model=self.model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            response_format=GraphUpdate,
        )

        parsed = response.parsed
        self._save_debug_response(
            parsed, cache_filename, workspace_id=workspace_id, project_id=project_id
        )
        return parsed

    async def process_answers(
        self,
        character_id: str,
        chapter_number: int,
        answers: dict[str, str],
        use_cache: bool = False,
        *,
        workspace_id: str,
        project_id: str,
    ) -> GraphUpdate:
        node = self.graph.get_node(character_id, workspace_id=workspace_id, project_id=project_id)
        if node is None:
            raise ValueError(f"Character node {character_id} not found")
        character_name = node.properties.get("name", "") or ""

        answers_text = "\n".join([f"Q: {q}\nA: {a}" for q, a in answers.items()])

        parsed = await self._fetch_raw_graph_update(
            character_name,
            character_id,
            chapter_number,
            answers_text,
            use_cache,
            workspace_id=workspace_id,
            project_id=project_id,
        )

        id_map: dict[str, str] = {}
        new_ids: set[str] = set()
        for node_update in parsed.nodes:
            original_id = node_update.id
            if node_update.type == "Appearance":
                new_id = f"app_{character_id}"
            elif node_update.type == "PersonalityProfile":
                new_id = f"pers_{character_id}"
            elif node_update.type == "State":
                new_id = f"state_{character_id}_ch{chapter_number}"
            elif node_update.type == "Belief":
                new_id = f"belief_{original_id}_{character_id}"
            elif node_update.type in ["Event", "Location", "Faction", "System", "Concept"]:
                new_id = original_id.lower().replace(" ", "_")
            else:
                if not self.schema_registry.is_valid_node_type(node_update.type):
                    raise ValueError(
                        f"Unknown node type '{node_update.type}'. Allowed types: "
                        f"{', '.join(self.schema_registry.get_all_node_types())}"
                    )
                new_id = f"{original_id}_{node_update.type}_{character_id}"

            if new_id in new_ids:
                raise ValueError(f"Duplicate node id after normalization: {new_id}")
            new_ids.add(new_id)
            id_map[original_id] = new_id

            existing = self.graph.get_node(new_id, workspace_id=workspace_id, project_id=project_id)
            if existing is not None and existing.type != node_update.type:
                raise ValueError(f"Entity {new_id!r} already exists with type {existing.type!r}")
            node_update.id = new_id

        for edge in parsed.edges:
            if not self.schema_registry.is_valid_edge_type(edge.type):
                raise ValueError(
                    f"Unknown edge type '{edge.type}'. Allowed types: "
                    f"{', '.join(self.schema_registry.get_all_edge_types())}"
                )
            resolved_source = id_map.get(edge.source, edge.source)
            resolved_target = id_map.get(edge.target, edge.target)
            if resolved_source != character_id:
                raise ValueError(f"Edge source must be the character: {edge.source}")
            if resolved_target not in new_ids:
                raise ValueError(f"Edge target is not a node in this update: {edge.target}")
            edge.source = resolved_source
            edge.target = resolved_target

        self._save_debug_response(
            parsed,
            f"graph_update_{character_id}_ch{chapter_number}.json",
            workspace_id=workspace_id,
            project_id=project_id,
        )
        return parsed
