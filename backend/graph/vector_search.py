import logging
from typing import List, Dict, Any, Optional
import json

import os

logger = logging.getLogger(__name__)

# Fix for PyTorch OMP error on Windows
os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'

try:
    from sentence_transformers import SentenceTransformer
    # all-MiniLM-L6-v2 is fast and produces 384-dimensional embeddings
    model = SentenceTransformer('all-MiniLM-L6-v2')
    HAS_EMBEDDER = True
except Exception as e:
    logger.warning(f"sentence-transformers failed to load: {e}. Semantic Graph Traversal will not function.")
    HAS_EMBEDDER = False

def generate_node_embedding(node_type: str, properties: Dict[str, Any]) -> Optional[List[float]]:
    """
    Creates a semantic representation of a node based on its type and top-level properties.
    """
    if not HAS_EMBEDDER:
        return None
        
    # Build a descriptive string to embed.
    # Exclude system fields or massive lists if necessary, but for now we embed everything.
    desc_str = f"Type: {node_type}. "
    
    parts = []
    for k, v in properties.items():
        if isinstance(v, (str, int, float, bool)):
            parts.append(f"{k}: {v}")
        elif isinstance(v, dict):
            # rudimentary flattening for simple dictionaries
            parts.append(f"{k}: {json.dumps(v)}")
            
    desc_str += "; ".join(parts)
    
    try:
        # Encode returns a numpy array, convert to standard python list for Neo4j driver
        return model.encode(desc_str).tolist()
    except Exception as e:
        logger.error(f"Failed to generate embedding: {e}")
        return None

def generate_query_embedding(query_text: str) -> Optional[List[float]]:
    """
    Embeds a free-form text query for semantic search against Node embeddings.
    """
    if not HAS_EMBEDDER:
        return None
        
    try:
        return model.encode(query_text).tolist()
    except Exception as e:
        logger.error(f"Failed to generate query embedding: {e}")
        return None
