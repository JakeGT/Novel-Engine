"""Embeddings as typed, versioned representations (ADR-010, CTX-011/CTX-012).

An embedding is a derived index, never story truth. This module holds the storage- and
provider-neutral parts: what may be embedded (typed text, image, audio, video and
interleaved inputs), which task an embedding serves (a stored document, a search
query, a claim to check, a similarity comparison), the vector space a model defines,
and the arithmetic every adapter shares: L2 normalization, Matryoshka truncation with
re-normalization afterwards, and the rule that two vectors are only ever compared when
they come from the same space.

Nothing here imports a model library or touches a database.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum


class Modality(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"


class EmbeddingTask(StrEnum):
    """What an embedding is for. Query and corpus sides are distinct on purpose: a
    model's prompts differ between them, and the task is part of the version."""

    DOCUMENT = "document"
    QUERY = "query"
    FACT_CHECK = "fact_check"
    SIMILARITY = "similarity"


class GenerationState(StrEnum):
    BUILDING = "building"
    ACTIVE = "active"
    RETIRED = "retired"


class EmbeddingError(RuntimeError):
    """Base class for embedding failures. Messages never quote the input."""


class UnsupportedInputError(EmbeddingError):
    """The adapter cannot embed this modality or task. It refuses rather than guessing."""


class MixedSpaceError(EmbeddingError):
    """Two vectors (or a vector and a generation) from different spaces were combined."""


class EmbeddingUnavailableError(EmbeddingError):
    """The adapter is not installed, not configured, or failed to load."""


@dataclass(frozen=True)
class TextInput:
    text: str
    # A document's title, for models whose document prompt carries one.
    title: str | None = None

    @property
    def modality(self) -> Modality:
        return Modality.TEXT

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _MediaInput:
    data: bytes = field(repr=False)
    media_type: str
    # The checksum of ``data`` when the caller already knows it (stored versions do).
    checksum: str | None = None

    @property
    def content_hash(self) -> str:
        return self.checksum or hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True)
class ImageInput(_MediaInput):
    @property
    def modality(self) -> Modality:
        return Modality.IMAGE


@dataclass(frozen=True)
class AudioInput(_MediaInput):
    """Defined so the contract is complete; every current adapter refuses it."""

    @property
    def modality(self) -> Modality:
        return Modality.AUDIO


@dataclass(frozen=True)
class VideoInput(_MediaInput):
    """Defined so the contract is complete; every current adapter refuses it."""

    @property
    def modality(self) -> Modality:
        return Modality.VIDEO


@dataclass(frozen=True)
class InterleavedInput:
    """Text and media in one input, in order. Every current adapter refuses it."""

    parts: tuple[TextInput | ImageInput | AudioInput | VideoInput, ...]

    @property
    def modality(self) -> Modality:
        kinds = {part.modality for part in self.parts}
        return next(iter(kinds)) if len(kinds) == 1 else Modality.TEXT

    @property
    def modalities(self) -> frozenset[Modality]:
        return frozenset(part.modality for part in self.parts)

    @property
    def content_hash(self) -> str:
        digest = hashlib.sha256()
        for part in self.parts:
            digest.update(part.modality.value.encode())
            digest.update(part.content_hash.encode())
        return digest.hexdigest()


EmbeddingInput = TextInput | ImageInput | AudioInput | VideoInput | InterleavedInput


def input_modalities(item: EmbeddingInput) -> frozenset[Modality]:
    if isinstance(item, InterleavedInput):
        return item.modalities
    return frozenset({item.modality})


@dataclass(frozen=True)
class EmbeddingSpace:
    """The vector space a configured model produces.

    Two vectors are comparable only when their spaces have the same ``key``: the same
    model, pinned revision, output dimension and task prompts. Normalization is recorded
    but is not part of the key, because cosine distance ignores a vector's length.
    """

    model: str
    revision: str
    dimension: int
    native_dimension: int
    modalities: frozenset[Modality]
    # Task -> the prompt template used for text on that side ("{text}", "{title}").
    tasks: dict[str, str] = field(hash=False, compare=False, default_factory=dict)
    # A short name for the prompt set; part of the key.
    task_set: str = "plain"
    normalization: str = "l2"

    def __post_init__(self) -> None:
        if not 1 <= self.dimension <= 2000:
            # HNSW indexes the pgvector ``vector`` type up to 2,000 dimensions.
            raise EmbeddingError("an embedding size must be between 1 and 2000")
        if self.dimension > self.native_dimension:
            raise EmbeddingError("an embedding cannot be longer than the model's own size")

    @property
    def key(self) -> str:
        return space_key(self.model, self.revision, self.dimension, self.task_set)

    def supports(self, item: EmbeddingInput) -> bool:
        return input_modalities(item) <= self.modalities and not isinstance(item, InterleavedInput)


def space_key(model: str, revision: str, dimension: int, task_set: str) -> str:
    return f"{model}@{revision}/{dimension}/{task_set}"


@dataclass(frozen=True)
class EmbeddingProvenance:
    model: str
    revision: str
    dimension: int
    task: EmbeddingTask
    modality: Modality
    normalized: bool
    space_key: str
    content_hash: str


@dataclass(frozen=True)
class EmbeddingVector:
    values: tuple[float, ...]
    provenance: EmbeddingProvenance

    def __len__(self) -> int:
        return len(self.values)


@dataclass(frozen=True)
class EmbeddingHealth:
    """What an adapter can honestly do right now. Never forces a model download."""

    available: bool
    loaded: bool
    modalities: frozenset[Modality]
    reason: str | None = None


@dataclass(frozen=True)
class Generation:
    """A stored vector space: the rows built with one model configuration."""

    id: str
    model: str
    revision: str
    dimension: int
    native_dimension: int
    modalities: frozenset[Modality]
    tasks: dict[str, str]
    task_set: str
    normalization: str
    space_key: str
    state: GenerationState
    previous_generation_id: str | None = None
    cancel_requested: bool = False
    note: str | None = None
    created_at: str | None = None
    activated_at: str | None = None
    retired_at: str | None = None

    def supports(self, modality: Modality) -> bool:
        return modality in self.modalities

    def accepts(self, space: EmbeddingSpace) -> bool:
        return self.space_key == space.key and self.dimension == space.dimension


GENERATION_ID = re.compile(r"^gen_[a-z0-9_]{1,36}$")


def require_generation_id(value: str) -> str:
    """Generation ids are written into index names and partial-index predicates, so
    only this narrow alphabet is accepted."""
    if not GENERATION_ID.fullmatch(value):
        raise EmbeddingError("that is not a generation id")
    return value


def require_same_space(generation: Generation, space: EmbeddingSpace) -> None:
    if not generation.accepts(space):
        raise MixedSpaceError(
            f"generation {generation.id} holds {generation.space_key}; "
            f"the vector comes from {space.key}"
        )


def require_same_length(left: Sequence[float], right: Sequence[float]) -> None:
    if len(left) != len(right):
        raise MixedSpaceError(f"vectors of {len(left)} and {len(right)} dimensions")


def normalize(values: Sequence[float]) -> tuple[float, ...]:
    """Scale to unit L2 length. A zero or non-finite vector is refused, never stored."""
    norm = math.sqrt(math.fsum(value * value for value in values))
    if not math.isfinite(norm) or norm == 0.0:
        raise EmbeddingError("the model returned an empty or invalid vector")
    return tuple(value / norm for value in values)


def truncate(values: Sequence[float], dimension: int) -> tuple[float, ...]:
    """Matryoshka truncation: keep the first ``dimension`` values, then re-normalize.

    The EmbeddingGemma 2 model card requires the shortened vector be L2-normalized
    before cosine similarity, and query and document must share the dimension."""
    if dimension < 1 or dimension > len(values):
        raise EmbeddingError("cannot shorten a vector to that size")
    return normalize(values[:dimension])


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    require_same_length(left, right)
    dot = math.fsum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(math.fsum(a * a for a in left))
    right_norm = math.sqrt(math.fsum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


# EmbeddingGemma 2 task prompts, exactly as the official model card gives them
# (https://ai.google.dev/gemma/docs/embeddinggemma/model_card_2). Media inputs take no
# prompt. A change here changes the space: raise GEMMA_TASK_SET with it.
GEMMA_TASK_SET = "gemma2-v1"
GEMMA_TASKS: dict[str, str] = {
    EmbeddingTask.QUERY.value: "task: search result | query: {text}",
    EmbeddingTask.DOCUMENT.value: "title: {title} | text: {text}",
    EmbeddingTask.FACT_CHECK.value: "task: fact checking | query: {text}",
    EmbeddingTask.SIMILARITY.value: "task: sentence similarity | query: {text}",
}
PLAIN_TASKS: dict[str, str] = {
    EmbeddingTask.DOCUMENT.value: "{text}",
    EmbeddingTask.QUERY.value: "{text}",
}
GEMMA_DIMENSIONS = (768, 512, 256, 128)


def format_text(templates: dict[str, str], task: EmbeddingTask, item: TextInput) -> str:
    template = templates.get(task.value)
    if template is None:
        raise UnsupportedInputError(f"this model has no {task.value} task")
    # Only the template is parsed, so braces in the author's words stay as written.
    return template.format(title=(item.title or "").strip() or "none", text=item.text)


__all__ = [
    "GEMMA_DIMENSIONS",
    "CaptionSource",
    "PictureSource",
    "SearchGate",
    "StoredHit",
    "VectorRow",
    "GEMMA_TASKS",
    "GEMMA_TASK_SET",
    "PLAIN_TASKS",
    "AudioInput",
    "EmbeddingError",
    "EmbeddingHealth",
    "EmbeddingInput",
    "EmbeddingProvenance",
    "EmbeddingSpace",
    "EmbeddingTask",
    "EmbeddingUnavailableError",
    "EmbeddingVector",
    "Generation",
    "GenerationState",
    "ImageInput",
    "InterleavedInput",
    "MixedSpaceError",
    "Modality",
    "TextInput",
    "UnsupportedInputError",
    "VideoInput",
    "cosine",
    "format_text",
    "input_modalities",
    "normalize",
    "require_generation_id",
    "require_same_length",
    "require_same_space",
    "space_key",
    "truncate",
]


# --- stored rows and gated search (CTX-012) -------------------------------------------


@dataclass(frozen=True)
class VectorRow:
    """One derived vector as stored: what it points at, never canon."""

    id: str
    kind: str
    modality: Modality
    task: EmbeddingTask
    content_hash: str
    values: tuple[float, ...] = field(repr=False)
    document: str | None = field(default=None, repr=False)
    metadata: dict = field(default_factory=dict, hash=False, compare=False)
    asset_id: str | None = None
    asset_version_id: str | None = None
    segment_id: str | None = None


@dataclass(frozen=True)
class SearchGate:
    """Hard gates applied before anything is ranked (ADR-010).

    Project scope and the generation are always applied. For rows that point at a
    picture: the picture and its version must be current, not rejected, and their
    whole-picture segment not retired. With ``point_of_view`` set, a picture whose
    version is marked secret is left out, and so is one with a secret link (in the
    working line of the story, ``branch_id``; ``None`` means the main story) whose
    ``known_by`` does not name the point-of-view character (``point_of_view_id``;
    ``None`` means no one, so every secretly linked picture is left out).
    """

    kinds: frozenset[str] | None = None
    # None: every row; False: only rows that point at no picture (documents); True:
    # only rows that point at a picture (the picture itself or its caption).
    assets: bool | None = None
    point_of_view: bool = False
    point_of_view_id: str | None = None
    branch_id: str | None = None


@dataclass(frozen=True)
class StoredHit:
    id: str
    kind: str
    modality: Modality
    score: float
    generation_id: str
    document: str | None = field(default=None, repr=False)
    metadata: dict = field(default_factory=dict, hash=False, compare=False)
    asset_id: str | None = None
    asset_version_id: str | None = None
    segment_id: str | None = None


@dataclass(frozen=True)
class PictureSource:
    """A picture ready to embed: its web-safe copy exists (made by media processing)."""

    asset_id: str
    asset_version_id: str
    segment_id: str | None
    object_key: str
    content_type: str
    checksum: str


@dataclass(frozen=True)
class CaptionSource:
    """An accepted (or corrected) caption of one picture version, as the author left it."""

    observation_id: str
    asset_id: str
    asset_version_id: str
    text: str = field(repr=False)
