"""Typed embedding adapters (CTX-011): the stand-in, MiniLM as a generation, and
EmbeddingGemma 2 behind a stub loader (no model is downloaded here)."""

import io
import math
import sys
from types import SimpleNamespace

import pytest

from backend.adapters.embedding import (
    EmbeddingGemmaEmbedding,
    MiniLMTextEmbedding,
    build_multimodal_embedder,
    decode_image,
)
from backend.adapters.embedding_fake import DeterministicEmbedding
from backend.domain.embedding import (
    AudioInput,
    EmbeddingError,
    EmbeddingTask,
    EmbeddingUnavailableError,
    ImageInput,
    InterleavedInput,
    Modality,
    TextInput,
    UnsupportedInputError,
    VideoInput,
    cosine,
    truncate,
)
from tests.doubles import FakeEmbeddingProvider

pytestmark = pytest.mark.unit


def _png(colour: tuple[int, int, int], size: int = 8) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (size, size), colour).save(buffer, format="PNG")
    return buffer.getvalue()


# --- the deterministic stand-in -------------------------------------------------------


def test_fake_is_deterministic_and_normalized() -> None:
    one = DeterministicEmbedding(dimension=512)
    two = DeterministicEmbedding(dimension=512)
    a = one.embed([TextInput(text="windswept basalt coast")], task=EmbeddingTask.DOCUMENT)[0]
    b = two.embed([TextInput(text="windswept basalt coast")], task=EmbeddingTask.DOCUMENT)[0]
    assert a.values == b.values
    assert math.sqrt(sum(v * v for v in a.values)) == pytest.approx(1.0)
    assert a.provenance.dimension == 512 and a.provenance.normalized
    assert a.provenance.space_key == one.space.key


def test_fake_shortened_vectors_are_the_truncated_native_ones() -> None:
    full = DeterministicEmbedding(dimension=768)
    short = DeterministicEmbedding(dimension=512)
    item = TextInput(text="lighthouse keeper's lantern")
    native = full.embed([item], task=EmbeddingTask.DOCUMENT)[0].values
    assert short.embed([item], task=EmbeddingTask.DOCUMENT)[0].values == pytest.approx(
        truncate(native, 512)
    )


def test_fake_builds_near_and_far_pairs_on_purpose() -> None:
    fake = DeterministicEmbedding(dimension=256)
    coast = _png((40, 40, 60))
    forge = _png((200, 80, 10))
    fake.describe(coast, "windswept basalt coast cliffs")
    fake.describe(forge, "blacksmith forge sparks anvil")
    query = fake.embed([TextInput(text="basalt coast")], task=EmbeddingTask.QUERY)[0].values
    near, far = (
        v.values
        for v in fake.embed(
            [
                ImageInput(data=coast, media_type="image/png"),
                ImageInput(data=forge, media_type="image/png"),
            ],
            task=EmbeddingTask.DOCUMENT,
        )
    )
    assert cosine(query, near) > 0.4 > cosine(query, far)


def test_fake_records_tasks_and_hashes_never_words() -> None:
    fake = DeterministicEmbedding(dimension=128)
    fake.embed([TextInput(text="secret words")], task=EmbeddingTask.QUERY)
    fake.embed([TextInput(text="secret words")], task=EmbeddingTask.DOCUMENT)
    assert [call[0] for call in fake.calls] == ["query", "document"]
    assert all("secret" not in part for call in fake.calls for part in call)


@pytest.mark.parametrize(
    "item",
    [
        AudioInput(data=b"RIFF", media_type="audio/wav"),
        VideoInput(data=b"\x00", media_type="video/mp4"),
        InterleavedInput(parts=(TextInput(text="a"),)),
    ],
)
def test_audio_video_and_interleaved_are_refused_by_every_adapter(item) -> None:  # type: ignore[no-untyped-def]
    adapters = [
        DeterministicEmbedding(dimension=128),
        MiniLMTextEmbedding(FakeEmbeddingProvider()),
        EmbeddingGemmaEmbedding(revision="abc123", loader=lambda *a, **k: None),
    ]
    for adapter in adapters:
        with pytest.raises(UnsupportedInputError):
            adapter.embed([item], task=EmbeddingTask.DOCUMENT)


# --- MiniLM as a generation -----------------------------------------------------------


def test_minilm_generation_matches_the_legacy_rows_and_refuses_other_tasks() -> None:
    adapter = MiniLMTextEmbedding(FakeEmbeddingProvider(model="all-MiniLM-L6-v2", version="1"))
    # The space the migration gave the moved rows: same model, label, size and prompts.
    assert adapter.space.key == "all-MiniLM-L6-v2@1/384/plain"
    assert adapter.space.modalities == frozenset({Modality.TEXT})
    vector = adapter.embed([TextInput(text="a")], task=EmbeddingTask.QUERY)[0]
    assert math.sqrt(sum(v * v for v in vector.values)) == pytest.approx(1.0)
    with pytest.raises(UnsupportedInputError):
        adapter.embed([TextInput(text="a")], task=EmbeddingTask.FACT_CHECK)
    with pytest.raises(UnsupportedInputError):
        adapter.embed(
            [ImageInput(data=_png((1, 2, 3)), media_type="image/png")], task=EmbeddingTask.DOCUMENT
        )


# --- EmbeddingGemma 2 behind a stub loader ---------------------------------------------


class _StubModel:
    def __init__(self, size: int = 768, value: float | None = None) -> None:
        self.size = size
        self.value = value
        self.seen: list[object] = []

    def encode(self, prepared, normalize_embeddings):  # type: ignore[no-untyped-def]
        assert normalize_embeddings is False  # shortened first, then normalized here
        self.seen.extend(prepared)
        out = []
        for index, item in enumerate(prepared):
            if self.value is not None:
                out.append([self.value] * self.size)
                continue
            base = float(len(str(item)) + index)
            out.append([base + (i % 7) for i in range(self.size)])
        return out


def _gemma(model: _StubModel, **kwargs) -> EmbeddingGemmaEmbedding:  # type: ignore[no-untyped-def]
    loads: list[dict] = []

    def loader(name, **options):  # type: ignore[no-untyped-def]
        loads.append({"name": name, **options})
        return model

    adapter = EmbeddingGemmaEmbedding(revision="0123abcd", loader=loader, **kwargs)
    adapter.loads = loads  # type: ignore[attr-defined]
    return adapter


def test_gemma_refuses_float16_and_sizes_the_card_does_not_offer() -> None:
    with pytest.raises(EmbeddingError, match="never float16"):
        EmbeddingGemmaEmbedding(revision="x", dtype="float16")
    with pytest.raises(EmbeddingError):
        EmbeddingGemmaEmbedding(revision="x", dimension=300)


def test_gemma_is_unavailable_until_a_revision_is_pinned() -> None:
    adapter = EmbeddingGemmaEmbedding(revision="", loader=lambda *a, **k: _StubModel())
    assert adapter.available() is False
    assert "revision" in (adapter.health().reason or "")
    with pytest.raises(EmbeddingUnavailableError):
        adapter.embed([TextInput(text="a")], task=EmbeddingTask.QUERY)


def test_gemma_loads_at_the_pinned_revision_in_a_safe_dtype() -> None:
    model = _StubModel()
    adapter = _gemma(model, dtype="auto")
    assert adapter.loaded() is False
    adapter.embed([TextInput(text="a")], task=EmbeddingTask.QUERY)
    load = adapter.loads[0]  # type: ignore[attr-defined]
    assert load["revision"] == "0123abcd"
    assert load["dtype"] in ("bfloat16", "float32")
    assert adapter.loaded() is True


def test_gemma_separates_query_and_document_prompts() -> None:
    model = _StubModel()
    adapter = _gemma(model)
    adapter.embed([TextInput(text="the coast")], task=EmbeddingTask.QUERY)
    adapter.embed([TextInput(text="the coast", title="Shore")], task=EmbeddingTask.DOCUMENT)
    assert model.seen == [
        "task: search result | query: the coast",
        "title: Shore | text: the coast",
    ]


def test_gemma_shortens_then_normalizes_and_passes_pictures_without_a_prompt() -> None:
    model = _StubModel()
    adapter = _gemma(model, dimension=256)
    vectors = adapter.embed(
        [TextInput(text="a"), ImageInput(data=_png((9, 9, 9)), media_type="image/png")],
        task=EmbeddingTask.DOCUMENT,
    )
    assert [len(v) for v in vectors] == [256, 256]
    for vector in vectors:
        assert math.sqrt(sum(x * x for x in vector.values)) == pytest.approx(1.0)
    assert vectors[1].provenance.modality is Modality.IMAGE
    picture = model.seen[1]
    assert isinstance(picture, dict) and picture["text"] == "<|image|>"


def test_gemma_refuses_an_empty_vector_and_a_wrong_size() -> None:
    with pytest.raises(EmbeddingError):
        _gemma(_StubModel(value=0.0)).embed([TextInput(text="a")], task=EmbeddingTask.QUERY)
    with pytest.raises(EmbeddingError, match="768"):
        _gemma(_StubModel(size=384)).embed([TextInput(text="a")], task=EmbeddingTask.QUERY)


def test_picture_decoding_is_bounded() -> None:
    with pytest.raises(UnsupportedInputError):
        decode_image(b"x" * 20, max_bytes=10)
    with pytest.raises(UnsupportedInputError):
        decode_image(b"not a picture at all")
    assert decode_image(_png((1, 2, 3))).mode == "RGB"


def test_picture_decoding_takes_only_the_web_copy_formats() -> None:
    import io

    from PIL import Image

    for fmt in ("WEBP", "JPEG"):
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4), (9, 9, 9)).save(buffer, format=fmt)
        assert decode_image(buffer.getvalue()).size == (4, 4)
    for fmt in ("GIF", "BMP", "TIFF"):
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4), (9, 9, 9)).save(buffer, format=fmt)
        with pytest.raises(UnsupportedInputError, match="could not be read"):
            decode_image(buffer.getvalue())


# --- choosing the adapter --------------------------------------------------------------


def _settings(**values) -> SimpleNamespace:  # type: ignore[no-untyped-def]
    base = {
        "EMBEDDING_PROVIDER": "minilm",
        "EMBEDDING_GEMMA_MODEL": "google/embeddinggemma-2",
        "EMBEDDING_GEMMA_REVISION": "",
        "EMBEDDING_GEMMA_DIMENSION": 512,
        "EMBEDDING_DTYPE": "auto",
        "EMBEDDING_FAKE_DIMENSION": 768,
    }
    base.update(values)
    return SimpleNamespace(**base)


def test_choosing_gemma_imports_no_model_library() -> None:
    before = set(sys.modules)
    adapter = build_multimodal_embedder(
        _settings(EMBEDDING_PROVIDER="embeddinggemma"), FakeEmbeddingProvider()
    )
    adapter.health()
    assert isinstance(adapter, EmbeddingGemmaEmbedding)
    assert not {"torch", "sentence_transformers"} & (set(sys.modules) - before)


def test_choosing_none_fake_and_minilm() -> None:
    legacy = FakeEmbeddingProvider()
    assert build_multimodal_embedder(_settings(EMBEDDING_PROVIDER="none"), legacy) is None
    fake = build_multimodal_embedder(_settings(EMBEDDING_PROVIDER="fake"), legacy)
    assert isinstance(fake, DeterministicEmbedding) and fake.space.dimension == 768
    assert isinstance(build_multimodal_embedder(_settings(), legacy), MiniLMTextEmbedding)
