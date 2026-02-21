import re
from typing import List, Dict, Any, Tuple
from backend.graph.manager import graph_manager
import logging

logger = logging.getLogger(__name__)

class ContextService:
    # Matches [Display Name](entity:NodeType:NodeID)
    TAG_REGEX = re.compile(r'\[([^\]]+)\]\(entity:([^:]+):([^)]+)\)')

    def get_context_for_prompt(self, prompt_text: str) -> Dict[str, Any]:
        """
        Parses a prompt for entity Markdown tags, extracts the IDs, retrieves their 
        Knowledge Graph data, and returns the bundle of context.
        """
        extracted_ids = self._extract_entity_ids(prompt_text)
        
        if not extracted_ids:
            return {"nodes": []}

        # Fetch the core nodes
        context_nodes = []
        for node_id in extracted_ids:
            node_data = graph_manager.get_node(node_id)
            if node_data:
                # If it's a Character, aggressively fetch its sub-modules (Appearance, State, etc.)
                # In a full RAG, this would be a more complex Cypher query.
                # TODO: Add consider a more complete RAG implementation for events, locations, characters, etc.
                if node_data.get('type') == 'Character' or 'Character' in node_data.get('labels', []):
                    sub_nodes = self._fetch_character_submodules(node_id)
                    node_data['sub_modules'] = sub_nodes
                
                context_nodes.append(node_data)

        return {"nodes": context_nodes}

    def _extract_entity_ids(self, text: str) -> List[str]:
        """
        Finds all unique Node IDs within the provided text string using regex.
        """
        matches = self.TAG_REGEX.findall(text)
        # matches is a list of tuples: (Display Name, Type, ID)
        
        # Deduplicate IDs while preserving order
        seen = set()
        ids = []
        for _, _, node_id in matches:
            if node_id not in seen:
                seen.add(node_id)
                ids.append(node_id)
        
        return ids

    def _fetch_character_submodules(self, character_id: str) -> List[Dict[str, Any]]:
        """
        Helper method to grab a character's connected Appearance, Personality, State, and Beliefs.
        """
        query = """
        MATCH (c:Entity {id: $char_id})-[r]->(sub)
        WHERE type(r) IN ['HAS_APPEARANCE', 'HAS_PERSONALITY', 'CURRENT_STATE', 'BELIEVES']
        RETURN sub.id as id, labels(sub) as labels, sub as properties, type(r) as relation
        """
        try:
            with graph_manager.driver.session() as session:
                result = session.run(query, char_id=character_id)
                sub_modules = []
                for record in result:
                    props = dict(record["properties"])
                    # Cleanup neo4j node object
                    if 'id' in props: del props['id']
                    
                    labels = [l for l in record["labels"] if l != "Entity"]
                    node_type = labels[0] if labels else "Unknown"
                    
                    sub_modules.append({
                        "relation": record["relation"],
                        "id": record["id"],
                        "type": node_type,
                        "properties": props
                    })
                return sub_modules
        except Exception as e:
            logger.error(f"Error fetching submodules for {character_id}: {e}")
            return []

context_service = ContextService()
