"""A deterministic stand-in for a multimodal embedding model (CTX-011).

It measures the plumbing, never a model: retrieval quality reported with it says the
harness, gates and ranking work, not that any real model would find the right picture.

- Text: each word maps to a fixed pseudo-random direction (from a hash of the word);
  a text's vector is the normalized sum of its words' directions. Texts that share
  words are near; texts that share none are close to orthogonal.
- Pictures: identified by their checksum (or the hash of their bytes). A test may
  ``describe`` a picture with words, and its vector is then mostly those words'
  direction plus a little of its own, so a test can build near and far pairs on
  purpose. An undescribed picture points in a direction of its own.
- Output is L2-normalized at the native size and, when a shorter size is configured,
  truncated and normalized again, exactly as a Matryoshka model is used.

Same input, same configuration: same vector, on every machine. Calls are recorded
(task, modality, content hash; never the words) so tests can check task separation.
"""

from __future__ import annotations

import hashlib
import random
import re
from collections.abc import Iterable, Sequence

from backend.domain.embedding import (
    EmbeddingHealth,
    EmbeddingInput,
    EmbeddingProvenance,
    EmbeddingSpace,
    EmbeddingTask,
    EmbeddingVector,
    ImageInput,
    InterleavedInput,
    Modality,
    TextInput,
    UnsupportedInputError,
    input_modalities,
    normalize,
    truncate,
)

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an and are as at be by for from has in is it its of on or that the this to was "
    "were with".split()
)
FAKE_TASKS = {task.value: "{text}" for task in EmbeddingTask}


def _direction(seed: str, size: int) -> list[float]:
    digest = hashlib.blake2b(seed.encode("utf-8"), digest_size=16).digest()
    rng = random.Random(int.from_bytes(digest, "big"))
    return [rng.gauss(0.0, 1.0) for _ in range(size)]


class DeterministicEmbedding:
    def __init__(
        self,
        *,
        dimension: int = 768,
        native_dimension: int = 768,
        modalities: Iterable[Modality] = (Modality.TEXT, Modality.IMAGE),
        model: str = "fake-multimodal",
        revision: str = "1",
        available: bool = True,
    ) -> None:
        self._space = EmbeddingSpace(
            model=model,
            revision=revision,
            dimension=dimension,
            native_dimension=native_dimension,
            modalities=frozenset(modalities),
            tasks=dict(FAKE_TASKS),
            task_set="fake-v1",
        )
        self._available = available
        self._words: dict[str, list[float]] = {}
        self._described: dict[str, str] = {}
        self.calls: list[tuple[str, str, str]] = []

    @property
    def space(self) -> EmbeddingSpace:
        return self._space

    def available(self) -> bool:
        return self._available

    def loaded(self) -> bool:
        return self._available

    def health(self) -> EmbeddingHealth:
        return EmbeddingHealth(
            available=self._available,
            loaded=self._available,
            modalities=self._space.modalities,
            reason=None if self._available else "the stand-in embedder is switched off",
        )

    def describe(self, picture: bytes | str, words: str) -> str:
        """Say what a picture shows (by bytes or checksum). Returns its checksum."""
        checksum = picture if isinstance(picture, str) else hashlib.sha256(picture).hexdigest()
        self._described[checksum] = words
        return checksum

    def _word(self, word: str) -> list[float]:
        vector = self._words.get(word)
        if vector is None:
            vector = _direction(f"word:{word}", self._space.native_dimension)
            self._words[word] = vector
        return vector

    def _text(self, text: str) -> list[float]:
        size = self._space.native_dimension
        total = [0.0] * size
        words = [word for word in _WORD.findall(text.lower()) if word not in _STOP]
        for word in words:
            for index, value in enumerate(self._word(word)):
                total[index] += value
        if not words:
            # An empty or stop-word-only text still gets a direction of its own.
            total = _direction(f"text:{text}", size)
        return total

    def _image(self, item: ImageInput) -> list[float]:
        checksum = item.content_hash
        own = normalize(_direction(f"image:{checksum}", self._space.native_dimension))
        words = self._described.get(checksum)
        if words is None:
            return list(own)
        concept = normalize(self._text(words))
        return [0.9 * c + 0.1 * o for c, o in zip(concept, own, strict=True)]

    def embed(
        self, inputs: Sequence[EmbeddingInput], *, task: EmbeddingTask
    ) -> list[EmbeddingVector]:
        if not self._available:
            raise UnsupportedInputError("the stand-in embedder is switched off")
        out: list[EmbeddingVector] = []
        for item in inputs:
            if isinstance(item, InterleavedInput) or not input_modalities(item) <= (
                self._space.modalities
            ):
                kinds = ", ".join(sorted(m.value for m in input_modalities(item)))
                raise UnsupportedInputError(f"the stand-in cannot embed {kinds} input")
            if isinstance(item, TextInput):
                raw = self._text(item.text)
            elif isinstance(item, ImageInput):
                raw = self._image(item)
            else:  # pragma: no cover - refused above unless audio/video are enabled
                raw = _direction(f"{item.modality}:{item.content_hash}", len(self._word("x")))
            values = normalize(raw)
            if self._space.dimension < self._space.native_dimension:
                values = truncate(values, self._space.dimension)
            self.calls.append((task.value, item.modality.value, item.content_hash))
            out.append(
                EmbeddingVector(
                    values=values,
                    provenance=EmbeddingProvenance(
                        model=self._space.model,
                        revision=self._space.revision,
                        dimension=self._space.dimension,
                        task=task,
                        modality=item.modality,
                        normalized=True,
                        space_key=self._space.key,
                        content_hash=item.content_hash,
                    ),
                )
            )
        return out


__all__ = ["FAKE_TASKS", "DeterministicEmbedding"]
