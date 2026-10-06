import json
import logging
import os
from typing import Any

from backend.core.config import settings

logger = logging.getLogger(__name__)

EMBEDDING_MODEL_NAME = settings.EMBEDDING_MODEL_NAME
EMBEDDING_DIMENSION = settings.EMBEDDING_DIMENSION

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "True")

_model: Any = None
_model_loaded = False


def _get_model() -> Any:
    global _model, _model_loaded
    if not _model_loaded:
        _model_loaded = True
        try:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
        except Exception as e:
            logger.warning(
                "sentence-transformers failed to load: %s. Semantic Graph Traversal will not function.",
                e,
            )
            _model = None
    return _model


def is_embedding_available() -> bool:
    return _get_model() is not None


def generate_node_embedding(node_type: str, properties: dict[str, Any]) -> list[float] | None:
    """
    Creates a semantic representation of a node based on its type and top-level properties.
    """
    model = _get_model()
    if model is None:
        return None

    desc_str = f"Type: {node_type}. "

    parts = []
    for k, v in properties.items():
        if isinstance(v, (str, int, float, bool)):
            parts.append(f"{k}: {v}")
        elif isinstance(v, dict):
            parts.append(f"{k}: {json.dumps(v)}")

    desc_str += "; ".join(parts)

    try:
        return model.encode(desc_str).tolist()
    except Exception as e:
        logger.error("Failed to generate embedding: %s", e)
        return None


def generate_query_embedding(query_text: str) -> list[float] | None:
    """
    Embeds a free-form text query for semantic search against Node embeddings.
    """
    model = _get_model()
    if model is None:
        return None

    try:
        return model.encode(query_text).tolist()
    except Exception as e:
        logger.error("Failed to generate query embedding: %s", e)
        return None
