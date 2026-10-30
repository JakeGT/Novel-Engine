"""Assembling what the assistant is given, and splitting writing into passages (ADR-021)."""

from __future__ import annotations

from backend.domain.context import (
    SCENE_DRAFT_POLICY,
    ContextItem,
    ContextSection,
    LeftOut,
    assemble,
    clamp_budget,
)
from backend.domain.passages import LONGEST, TARGET, split_passages


def _item(key: str, section: ContextSection, words: int = 10, score: float = 1.0, hard=False):
    return ContextItem(
        key=key,
        section=section,
        text=" ".join(["word"] * words),
        reason="because",
        authority="fact",
        record_type="fact",
        record_id=key,
        score=score,
        hard=hard,
    )


def test_hard_items_are_kept_and_the_rest_fill_the_budget_by_score() -> None:
    manifest = assemble(
        [
            _item("scene", ContextSection.SCENE, words=280, hard=True),
            _item("high", ContextSection.TRUE, words=15, score=5),
            _item("low", ContextSection.TRUE, words=15, score=1),
        ],
        budget=300,
    )
    assert [item.key for item in manifest.items] == ["high", "scene"]
    assert [(o.item.key, o.reason) for o in manifest.left_out] == [("low", LeftOut.BUDGET)]
    assert manifest.words == 295


def test_pins_beat_the_budget_and_exclusions_never_drop_a_hard_item() -> None:
    manifest = assemble(
        [
            _item("scene", ContextSection.SCENE, hard=True),
            _item("big", ContextSection.WRITING, words=1000, score=0.1),
            _item("small", ContextSection.TRUE, score=9),
        ],
        budget=300,
        pins=["big"],
        excludes=["small", "scene"],
    )
    assert {item.key for item in manifest.items} == {"scene", "big"}
    assert [(o.item.key, o.reason) for o in manifest.left_out] == [("small", LeftOut.EXCLUDED)]


def test_secrets_stay_out_unless_included_or_pinned() -> None:
    items = [_item("scene", ContextSection.SCENE, hard=True), _item("s", ContextSection.SECRETS)]
    assert [o.reason for o in assemble(items).left_out] == [LeftOut.SECRET]
    assert "s" in {i.key for i in assemble(items, include_secrets=True).items}
    assert "s" in {i.key for i in assemble(items, pins=["s"]).items}


def test_rendering_is_ordered_stable_first_and_identifies_what_was_given() -> None:
    items = [
        _item("w", ContextSection.WRITING, words=3),
        _item("r", ContextSection.RULES, words=3),
        _item("scene", ContextSection.SCENE, words=3, hard=True),
    ]
    first = assemble(items)
    again = assemble(list(reversed(items)))
    assert first.rendered == again.rendered and first.checksum == again.checksum
    assert first.rendered.index("Rules of this story") < first.rendered.index(
        "author's own writing"
    )
    # The scene comes last, where a model attends best.
    assert first.rendered.index("author's own writing") < first.rendered.index("## The scene")
    assert assemble(items, excludes=["w"]).checksum != first.checksum


def test_the_budget_is_held_within_the_policys_bounds() -> None:
    assert clamp_budget(None, SCENE_DRAFT_POLICY) == SCENE_DRAFT_POLICY.default_budget
    assert clamp_budget(5, SCENE_DRAFT_POLICY) == SCENE_DRAFT_POLICY.min_budget
    assert clamp_budget(10**9, SCENE_DRAFT_POLICY) == SCENE_DRAFT_POLICY.max_budget


def test_passages_follow_paragraphs_and_headings() -> None:
    paragraph = "Mara walked to the harbor. " * 20  # about 540 characters
    text = f"# One\n\n{paragraph}\n\n{paragraph}\n\n{paragraph}\n\n# Two\n\nShort."
    passages = split_passages(text)
    assert [p.section for p in passages] == ["One", "One", "Two"]
    for passage in passages:
        assert passage.end - passage.start <= TARGET + 600
        assert not text[passage.start].isspace() and not text[passage.end - 1].isspace()
    assert text[passages[-1].start : passages[-1].end] == "Short."


def test_a_very_long_paragraph_is_cut_at_a_sentence_end() -> None:
    sentence = "The lamp guttered and the tide came in. "
    text = sentence * 200
    passages = split_passages(text)
    assert len(passages) > 1
    for passage in passages:
        assert passage.end - passage.start <= LONGEST
        assert text[passage.start : passage.end].rstrip().endswith(".")


def test_fact_phrasing_matches_the_screens() -> None:
    import re
    from pathlib import Path

    from backend.domain.canon.phrasing import PHRASES

    source = Path("frontend/src/lib/words.ts").read_text()
    table = source[
        source.index("const PREDICATES") : source.index("}", source.index("const PREDICATES"))
    ]
    screen = dict(re.findall(r"(\w+): '([^']+)'", table))
    assert screen == PHRASES
