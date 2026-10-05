from __future__ import annotations

import importlib.util
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SentenceTransformerEmbedding:
    """Self-contained embedding adapter with lazy model load and honest capability state.

    ``available()`` only reports whether the dependency is importable and never
    forces a model download; ``loaded()`` reports whether the model is in memory.
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        dimension: int = 384,
        version: str = "1",
    ) -> None:
        self._model_name = model_name
        self._dimension = dimension
        self._version = version
        self._model: Any = None
        self._load_attempted = False

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def version(self) -> str:
        return self._version

    @property
    def dimension(self) -> int:
        return self._dimension

    def available(self) -> bool:
        return importlib.util.find_spec("sentence_transformers") is not None

    def loaded(self) -> bool:
        return self._model is not None

    def _get_model(self) -> Any:
        if not self._load_attempted:
            self._load_attempted = True
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(self._model_name)
            except Exception as error:  # pragma: no cover - depends on local model cache
                logger.warning("sentence-transformers failed to load: %s", error)
                self._model = None
        return self._model

    def embed(self, text: str) -> list[float] | None:
        model = self._get_model()
        if model is None:
            return None
        try:
            return model.encode(text).tolist()
        except Exception as error:  # pragma: no cover - provider dependent
            logger.error("Failed to generate embedding: %s", error)
            return None

    def embed_node(self, node_type: str, properties: dict[str, Any]) -> list[float] | None:
        parts = []
        for key, value in properties.items():
            if isinstance(value, (str, int, float, bool)):
                parts.append(f"{key}: {value}")
            elif isinstance(value, dict):
                parts.append(f"{key}: {json.dumps(value)}")
        return self.embed(f"Type: {node_type}. " + "; ".join(parts))
