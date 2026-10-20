"""Embedding arithmetic and the one-space rule (CTX-011, ADR-010)."""

import math

import pytest

from backend.domain.embedding import (
    GEMMA_TASKS,
    PLAIN_TASKS,
    EmbeddingError,
    EmbeddingSpace,
    EmbeddingTask,
    Generation,
    GenerationState,
    MixedSpaceError,
    Modality,
    TextInput,
    UnsupportedInputError,
    VideoInput,
    cosine,
    format_text,
    normalize,
    require_generation_id,
    require_same_space,
    truncate,
)

pytestmark = pytest.mark.unit


def _norm(values) -> float:  # type: ignore[no-untyped-def]
    return math.sqrt(sum(v * v for v in values))


def test_normalize_gives_unit_length_and_refuses_empty_or_invalid() -> None:
    assert _norm(normalize([3.0, 4.0])) == pytest.approx(1.0)
    with pytest.raises(EmbeddingError):
        normalize([0.0, 0.0])
    with pytest.raises(EmbeddingError):
        normalize([float("nan"), 1.0])


def test_truncation_renormalizes_and_keeps_the_leading_values() -> None:
    full = normalize([1.0, 2.0, 3.0, 4.0])
    short = truncate(full, 2)
    assert len(short) == 2
    assert _norm(short) == pytest.approx(1.0)
    # The direction of the leading values is kept: the same as normalizing them alone.
    assert short == pytest.approx(normalize([1.0, 2.0]))
    with pytest.raises(EmbeddingError):
        truncate(full, 5)


def test_vectors_of_different_sizes_are_never_compared() -> None:
    with pytest.raises(MixedSpaceError):
        cosine([1.0, 0.0], [1.0, 0.0, 0.0])


def _space(**overrides) -> EmbeddingSpace:  # type: ignore[no-untyped-def]
    values = {
        "model": "m",
        "revision": "r1",
        "dimension": 512,
        "native_dimension": 768,
        "modalities": frozenset({Modality.TEXT}),
        "tasks": dict(PLAIN_TASKS),
        "task_set": "plain",
    }
    values.update(overrides)
    return EmbeddingSpace(**values)


def test_space_key_names_model_revision_size_and_prompts() -> None:
    base = _space()
    assert base.key != _space(revision="r2").key
    assert base.key != _space(dimension=256).key
    assert base.key != _space(task_set="other").key
    assert base.key != _space(model="n").key
    with pytest.raises(EmbeddingError):
        _space(dimension=1024)  # longer than the model's own size


def _generation(space: EmbeddingSpace) -> Generation:
    return Generation(
        id="gen_a",
        model=space.model,
        revision=space.revision,
        dimension=space.dimension,
        native_dimension=space.native_dimension,
        modalities=space.modalities,
        tasks=space.tasks,
        task_set=space.task_set,
        normalization="l2",
        space_key=space.key,
        state=GenerationState.ACTIVE,
    )


def test_a_generation_refuses_a_vector_from_another_space() -> None:
    generation = _generation(_space())
    require_same_space(generation, _space())
    with pytest.raises(MixedSpaceError):
        require_same_space(generation, _space(dimension=256))


def test_generation_ids_are_narrow_because_they_name_indexes() -> None:
    assert require_generation_id("gen_legacy_1") == "gen_legacy_1"
    for bad in ("gen_x'; drop table", "GEN_A", "gen_", "x" * 50, "gen_a-b"):
        with pytest.raises(EmbeddingError):
            require_generation_id(bad)


def test_task_prompts_follow_the_model_card_and_keep_braces_in_words() -> None:
    item = TextInput(text="Mara {not a field}", title=None)
    assert format_text(GEMMA_TASKS, EmbeddingTask.QUERY, item) == (
        "task: search result | query: Mara {not a field}"
    )
    assert format_text(GEMMA_TASKS, EmbeddingTask.DOCUMENT, item) == (
        "title: none | text: Mara {not a field}"
    )
    titled = TextInput(text="words", title="Chapter One")
    assert format_text(GEMMA_TASKS, EmbeddingTask.DOCUMENT, titled).startswith(
        "title: Chapter One | "
    )
    assert format_text(GEMMA_TASKS, EmbeddingTask.FACT_CHECK, item).startswith(
        "task: fact checking | query: "
    )
    with pytest.raises(UnsupportedInputError):
        format_text(PLAIN_TASKS, EmbeddingTask.FACT_CHECK, item)


def test_a_space_without_video_does_not_claim_to_support_it() -> None:
    space = _space(modalities=frozenset({Modality.TEXT, Modality.IMAGE}))
    assert not space.supports(VideoInput(data=b"x", media_type="video/mp4"))
    assert space.supports(TextInput(text="a"))
