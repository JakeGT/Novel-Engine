"""Embedding adapters.

``SentenceTransformerEmbedding`` is the original text-only ``EmbeddingPort`` (still used
by the legacy graph store). The typed, versioned ``MultimodalEmbeddingPort`` adapters
(CTX-011) are:

- ``MiniLMTextEmbedding``: the same MiniLM model as a text-only generation. MiniLM has
  no task prompts, so a query and a document are embedded alike; it offers only the
  document and query tasks and refuses the rest rather than pretend.
- ``EmbeddingGemmaEmbedding``: ``google/embeddinggemma-2`` (text and pictures in one
  space), behind the optional ``embeddings`` extra and never imported unless
  configured. It needs a pinned model revision, runs in bfloat16 or float32 (never
  float16, which the model card warns gives NaN or silently degraded vectors), uses the
  card's task prompts, and shortens Matryoshka vectors with re-normalization after.

Audio, video and interleaved inputs are defined by the port and refused by both.
No adapter logs the words or bytes it embeds.
"""

from __future__ import annotations

import importlib.util
import io
import json
import logging
import time
import warnings
from collections.abc import Callable, Sequence
from typing import Any

from backend.core.logging import log_event
from backend.domain.embedding import (
    GEMMA_DIMENSIONS,
    GEMMA_TASK_SET,
    GEMMA_TASKS,
    PLAIN_TASKS,
    EmbeddingError,
    EmbeddingHealth,
    EmbeddingInput,
    EmbeddingProvenance,
    EmbeddingSpace,
    EmbeddingTask,
    EmbeddingUnavailableError,
    EmbeddingVector,
    ImageInput,
    Modality,
    TextInput,
    UnsupportedInputError,
    format_text,
    input_modalities,
    normalize,
    truncate,
)

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
                log_event(
                    logger,
                    "embedding.load_failed",
                    level=logging.WARNING,
                    model=self._model_name,
                    error_type=type(error).__name__,
                )
                self._model = None
        return self._model

    def embed(self, text: str) -> list[float] | None:
        model = self._get_model()
        if model is None:
            return None
        try:
            return model.encode(text).tolist()  # type: ignore[no-any-return]
        except Exception as error:  # pragma: no cover - provider dependent
            log_event(
                logger,
                "embedding.generate_failed",
                level=logging.ERROR,
                model=self._model_name,
                error_type=type(error).__name__,
            )
            return None

    def embed_node(self, node_type: str, properties: dict[str, Any]) -> list[float] | None:
        parts = []
        for key, value in properties.items():
            if isinstance(value, (str, int, float, bool)):
                parts.append(f"{key}: {value}")
            elif isinstance(value, dict):
                parts.append(f"{key}: {json.dumps(value)}")
        return self.embed(f"Type: {node_type}. " + "; ".join(parts))


def _refuse_unsupported(space: EmbeddingSpace, inputs: Sequence[EmbeddingInput]) -> None:
    for item in inputs:
        if not space.supports(item):
            kinds = ", ".join(sorted(m.value for m in input_modalities(item)))
            raise UnsupportedInputError(f"{space.model} cannot embed {kinds} input here")


def _vector(
    space: EmbeddingSpace, values: Sequence[float], task: EmbeddingTask, item: EmbeddingInput
) -> EmbeddingVector:
    return EmbeddingVector(
        values=tuple(values),
        provenance=EmbeddingProvenance(
            model=space.model,
            revision=space.revision,
            dimension=space.dimension,
            task=task,
            modality=item.modality,
            normalized=True,
            space_key=space.key,
            content_hash=item.content_hash,
        ),
    )


class MiniLMTextEmbedding:
    """A text-only ``EmbeddingPort`` (MiniLM by default) as a typed generation.

    It shares the loaded model with the legacy ``SentenceTransformerEmbedding`` so the
    model is in memory once. Its space matches the legacy generation moved across by
    migration 0047 (model, version label, its dimension, no prompts), so search keeps
    working after the upgrade without embedding anything again. Any other text-only
    ``EmbeddingPort`` can be wrapped the same way (tests wrap a fake).
    """

    def __init__(self, base: Any) -> None:
        self._base = base
        self._space = EmbeddingSpace(
            model=str(base.model),
            revision=str(base.version),
            dimension=int(base.dimension),
            native_dimension=int(base.dimension),
            modalities=frozenset({Modality.TEXT}),
            tasks=dict(PLAIN_TASKS),
            task_set="plain",
        )

    @property
    def space(self) -> EmbeddingSpace:
        return self._space

    def available(self) -> bool:
        return bool(self._base.available())

    def loaded(self) -> bool:
        return bool(self._base.loaded())

    def health(self) -> EmbeddingHealth:
        available = self.available()
        return EmbeddingHealth(
            available=available,
            loaded=self.loaded(),
            modalities=self._space.modalities,
            reason=None if available else "sentence-transformers is not installed",
        )

    def _raw(self, texts: list[str]) -> list[Sequence[float]]:
        get_model = getattr(self._base, "_get_model", None)
        if get_model is not None:
            model = get_model()
            if model is None:
                raise EmbeddingUnavailableError("the text model could not be loaded")
            return list(model.encode(texts, normalize_embeddings=True))
        out: list[Sequence[float]] = []
        for text in texts:
            values = self._base.embed(text)
            if values is None:
                raise EmbeddingUnavailableError("the text model returned nothing")
            out.append(values)
        return out

    def embed(
        self, inputs: Sequence[EmbeddingInput], *, task: EmbeddingTask
    ) -> list[EmbeddingVector]:
        _refuse_unsupported(self._space, inputs)
        texts = [
            format_text(self._space.tasks, task, item)
            for item in inputs
            if isinstance(item, TextInput)
        ]
        if not texts:
            return []
        out: list[EmbeddingVector] = []
        for item, values in zip(inputs, self._raw(texts), strict=True):
            vector = normalize([float(value) for value in values])
            if len(vector) != self._space.dimension:
                raise EmbeddingError(
                    f"the model returned {len(vector)} dimensions, expected {self._space.dimension}"
                )
            out.append(_vector(self._space, vector, task, item))
        return out


# Bounds on a picture handed to the model. The picture is already the processed,
# metadata-free web copy (at most 1024px); these are a second fence, per the Pillow
# security handbook: cap bytes before decoding, keep a pixel limit, and treat a
# decompression-bomb warning as an error.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 4096 * 4096
# Only the formats media processing writes for its web copy; Pillow otherwise tries
# every format it knows, including ones with a long history of decoder bugs.
IMAGE_FORMATS = ("WEBP", "PNG", "JPEG")


def decode_image(data: bytes, *, max_bytes: int = MAX_IMAGE_BYTES) -> Any:
    if len(data) > max_bytes:
        raise UnsupportedInputError("the picture is too large to embed")
    from PIL import Image

    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            image = Image.open(io.BytesIO(data), formats=IMAGE_FORMATS)
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise UnsupportedInputError("the picture has too many pixels to embed")
            image.load()
            return image.convert("RGB")
        except UnsupportedInputError:
            raise
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise UnsupportedInputError("the picture has too many pixels to embed") from error
        except Exception as error:  # noqa: BLE001 - any decode failure is a refusal
            raise UnsupportedInputError("the picture could not be read") from error


_ALLOWED_DTYPES = ("auto", "bfloat16", "float32")


class EmbeddingGemmaEmbedding:
    """``google/embeddinggemma-2``: text and pictures in one space (behind the extra).

    Nothing is imported until the first ``embed``; ``available`` only checks that the
    libraries are installed and a revision is pinned. The model loads at that revision
    and never in float16. Vectors are made at the native 768 dimensions, then shortened
    to the configured Matryoshka size (768, 512, 256 or 128) and normalized again.
    """

    NATIVE_DIMENSION = 768

    def __init__(
        self,
        *,
        model_name: str = "google/embeddinggemma-2",
        revision: str = "",
        dimension: int = 512,
        dtype: str = "auto",
        device: str | None = None,
        loader: Callable[..., Any] | None = None,
    ) -> None:
        if dimension not in GEMMA_DIMENSIONS:
            raise EmbeddingError(
                "EmbeddingGemma 2 vectors can be 768, 512, 256 or 128 numbers long"
            )
        if dtype not in _ALLOWED_DTYPES:
            # float16 is refused outright: the model card says it can produce NaN or
            # silently degraded embeddings without an error.
            raise EmbeddingError("EmbeddingGemma 2 runs in bfloat16 or float32, never float16")
        self._model_name = model_name
        self._revision = revision.strip()
        self._dtype = dtype
        self._device = device
        self._loader = loader
        self._model: Any = None
        self._load_error: str | None = None
        self._space = EmbeddingSpace(
            model=model_name,
            revision=self._revision or "unpinned",
            dimension=dimension,
            native_dimension=self.NATIVE_DIMENSION,
            modalities=frozenset({Modality.TEXT, Modality.IMAGE}),
            tasks=dict(GEMMA_TASKS),
            task_set=GEMMA_TASK_SET,
        )

    @property
    def space(self) -> EmbeddingSpace:
        return self._space

    def _missing(self) -> str | None:
        if not self._revision:
            return "no model revision is pinned (EMBEDDING_GEMMA_REVISION)"
        if self._loader is None:
            for module in ("sentence_transformers", "torch"):
                if importlib.util.find_spec(module) is None:
                    return "the embeddings extra is not installed"
        return self._load_error

    def available(self) -> bool:
        return self._missing() is None

    def loaded(self) -> bool:
        return self._model is not None

    def health(self) -> EmbeddingHealth:
        reason = self._missing()
        return EmbeddingHealth(
            available=reason is None,
            loaded=self.loaded(),
            modalities=self._space.modalities,
            reason=reason,
        )

    def resolved_dtype(self) -> str:
        if self._dtype != "auto":
            return self._dtype
        try:
            import torch

            if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
                return "bfloat16"
        except Exception:  # noqa: BLE001 - no accelerator means float32
            pass
        return "float32"

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        reason = self._missing()
        if reason is not None:
            raise EmbeddingUnavailableError(reason)
        dtype_name = self.resolved_dtype()
        try:
            if self._loader is not None:
                self._model = self._loader(
                    self._model_name, revision=self._revision, dtype=dtype_name
                )
            else:  # pragma: no cover - needs the extra and the model download
                import torch
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(
                    self._model_name,
                    revision=self._revision,
                    device=self._device,
                    model_kwargs={"torch_dtype": getattr(torch, dtype_name)},
                )
        except Exception as error:  # noqa: BLE001 - recorded as a plain reason
            self._load_error = "the model could not be loaded"
            log_event(
                logger,
                "embedding.load_failed",
                level=logging.WARNING,
                model=self._model_name,
                error_type=type(error).__name__,
            )
            raise EmbeddingUnavailableError(self._load_error) from error
        log_event(logger, "embedding.loaded", model=self._model_name, dtype=dtype_name)
        return self._model

    def embed(
        self, inputs: Sequence[EmbeddingInput], *, task: EmbeddingTask
    ) -> list[EmbeddingVector]:
        _refuse_unsupported(self._space, inputs)
        if not inputs:
            return []
        prepared: list[Any] = []
        for item in inputs:
            if isinstance(item, TextInput):
                prepared.append(format_text(self._space.tasks, task, item))
            elif isinstance(item, ImageInput):
                # Media takes no task prompt; the placeholder marks where it goes.
                prepared.append({"text": "<|image|>", "image": decode_image(item.data)})
            else:  # pragma: no cover - refused above
                raise UnsupportedInputError("unsupported input")
        model = self._get_model()
        raw = model.encode(prepared, normalize_embeddings=False)
        out: list[EmbeddingVector] = []
        for item, values in zip(inputs, raw, strict=True):
            full = [float(value) for value in values]
            if len(full) != self.NATIVE_DIMENSION:
                raise EmbeddingError(
                    f"the model returned {len(full)} dimensions, expected {self.NATIVE_DIMENSION}"
                )
            out.append(_vector(self._space, truncate(full, self._space.dimension), task, item))
        return out


def api_embedder(
    *,
    provider_id: str,
    base_url: str,
    api_key: str,
    model: str,
    dimension: int,
    name: str = "",
    send_dimension: bool = True,
    timeout_seconds: float = 30.0,
    transport: Any = None,
    sleep: Any = time.sleep,
) -> Any:
    """An ``ApiTextEmbedding`` for a provider, with that provider's request fields.

    Used both for the server's settings and for a setup saved on the page, so the
    provider's ``dimension_field`` and ``query_field`` are always applied. Returns the
    adapter without calling anything.
    """
    from backend.adapters.embedding_api import ApiTextEmbedding
    from backend.domain.embedding_providers import embedding_provider

    spec = embedding_provider(provider_id)
    return ApiTextEmbedding(
        base_url=base_url,
        api_key=api_key,
        model=model,
        dimension=dimension,
        send_dimension=send_dimension,
        name=name,
        dimension_field=spec.dimension_field if spec is not None else "dimensions",
        query_field=spec.query_field if spec is not None else None,
        timeout_seconds=timeout_seconds,
        transport=transport,
        sleep=sleep,
    )


def build_multimodal_embedder(settings: Any, legacy: SentenceTransformerEmbedding) -> Any:
    """The typed embedder an install is configured for (``EMBEDDING_PROVIDER``).

    ``minilm`` (the default) reuses the legacy model; ``embeddinggemma`` builds the
    Gemma adapter without importing anything; ``api`` sends text to an embedding service
    over HTTP (``embedding_api.py``); ``fake`` is the deterministic stand-in
    for tests and the evaluation harness; ``none`` turns search by meaning off.
    """
    provider = str(getattr(settings, "EMBEDDING_PROVIDER", "minilm")).lower()
    if provider == "none":
        return None
    if provider == "embeddinggemma":
        return EmbeddingGemmaEmbedding(
            model_name=settings.EMBEDDING_GEMMA_MODEL,
            revision=settings.EMBEDDING_GEMMA_REVISION,
            dimension=settings.EMBEDDING_GEMMA_DIMENSION,
            dtype=settings.EMBEDDING_DTYPE,
        )
    if provider == "api":
        from backend.domain.embedding_providers import (
            MAX_DIMENSION,
            default_dimension,
            embedding_provider,
        )

        provider_id = str(getattr(settings, "EMBEDDING_API_PROVIDER", "openai") or "openai")
        spec = embedding_provider(provider_id)
        base_url = str(getattr(settings, "EMBEDDING_API_BASE_URL", "") or "").strip()
        if not base_url and spec is not None:
            base_url = spec.base_url or ""
        model = str(getattr(settings, "EMBEDDING_API_MODEL", "") or "").strip()
        if not model and spec is not None and spec.models:
            model = spec.models[0].id
        dimension = int(getattr(settings, "EMBEDDING_API_DIMENSION", 0) or 0)
        if dimension == 0:
            dimension = default_dimension(model) or 0
        if not 1 <= dimension <= MAX_DIMENSION:
            raise EmbeddingError(
                "EMBEDDING_API_DIMENSION must resolve to a size between 1 and 2000"
            )
        name = str(getattr(settings, "EMBEDDING_API_NAME", "") or "").strip()
        if not name and spec is not None:
            name = spec.label
        return api_embedder(
            provider_id=provider_id,
            base_url=base_url,
            api_key=settings.EMBEDDING_API_KEY,
            model=model,
            dimension=dimension,
            name=name,
            send_dimension=settings.EMBEDDING_API_SEND_DIMENSION,
            timeout_seconds=settings.EMBEDDING_API_TIMEOUT_SECONDS,
        )
    if provider == "fake":
        from backend.adapters.embedding_fake import DeterministicEmbedding

        return DeterministicEmbedding(dimension=settings.EMBEDDING_FAKE_DIMENSION)
    return MiniLMTextEmbedding(legacy)


__all__ = [
    "EmbeddingGemmaEmbedding",
    "MiniLMTextEmbedding",
    "SentenceTransformerEmbedding",
    "api_embedder",
    "build_multimodal_embedder",
    "decode_image",
]
