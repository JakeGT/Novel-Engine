"""Deterministic review bundles: units, checks, groups, budgets and coverage (CON-013)."""

from __future__ import annotations

import pytest

from backend.domain.review_bundles import (
    INSTRUCTION_TOKENS,
    MAX_UNIT_TOKENS,
    Anchor,
    Evidence,
    RuleScope,
    SceneFrame,
    anchors_for,
    fact_anchors,
    missing_for,
    nearest_evidence,
    plan_bundles,
    preview,
    review_units,
    seen_together,
    single_context,
    tokens_of,
)

pytestmark = pytest.mark.unit

TYPES = {
    "mara": "Character",
    "ines": "Character",
    "harbor": "Location",
    "lens": "Object",
    "guild": "Faction",
}
FRAME = SceneFrame("s1", 1, point_of_view="mara")


def _mentions(text: str) -> list[tuple[int, int, str]]:
    found = []
    for word, key in (("Mara", "mara"), ("Ines", "ines"), ("lens", "lens"), ("harbor", "harbor")):
        at = text.find(word)
        while at != -1:
            found.append((at, at + len(word), key))
            at = text.find(word, at + 1)
    return found


def _units(text: str, frame: SceneFrame = FRAME, **kw):
    return review_units(text, frame, _mentions(text), TYPES, **kw)


def _record(key: str, *anchors: Anchor, tokens: int = 20, **kw) -> Evidence:
    return Evidence(key=key, tokens=tokens, anchors=frozenset(anchors), **kw)


def test_units_are_cut_at_paragraphs_and_keep_offsets_and_a_digest_not_the_words() -> None:
    text = "Mara climbed the stair.\n\nInes waited by the lens.\n\n   \n"
    units = _units(text)
    assert [unit.id for unit in units] == ["s1#0", "s1#1"]
    assert text[units[1].start : units[1].end] == "Ines waited by the lens."
    assert units[1].entity_ids == {"ines", "lens"}
    assert units[0].cut == "paragraph" and len(units[0].digest) == 16
    assert not hasattr(units[0], "text")


def test_a_long_paragraph_is_cut_at_sentence_ends_then_between_words() -> None:
    sentence = "Mara watched the harbor for a long while. "
    text = sentence * 40
    units = _units(text, max_tokens=50)
    assert len(units) > 1 and {unit.cut for unit in units} == {"sentence"}
    assert all(unit.tokens <= 50 for unit in units)
    # Nothing is lost or repeated between the pieces.
    assert "".join(text[u.start : u.end] for u in units).replace(" ", "") == text.replace(" ", "")
    one_long_sentence = "word " * 400
    pieces = _units(one_long_sentence, max_tokens=50)
    assert {unit.cut for unit in pieces} == {"words"}
    assert all(unit.tokens <= 50 for unit in pieces)


def test_a_passage_needs_the_checks_of_what_it_names_and_what_the_plan_says() -> None:
    frame = SceneFrame(
        "s2",
        2,
        point_of_view="mara",
        reveals=frozenset({"f_secret"}),
        threads=frozenset({"t_lens"}),
        has_conditions=True,
    )
    rule = RuleScope("r1", frozenset({"lens"}))
    found = anchors_for({"ines", "lens", "harbor"}, frame, TYPES, [rule])
    keys = {anchor.key for anchor in found}
    assert keys == {
        "pov:mara",
        "timeline:mara",
        "state:mara",
        "timeline:ines",
        "state:ines",
        "timeline:lens",
        "resource:lens",
        "reveal:f_secret",
        "thread:t_lens",
        "rule:r1",
        "scene:s2",
    }
    # A place is not in two places at once, and a rule about something unnamed is not asked.
    assert "timeline:harbor" not in keys
    assert not any(a.family == "rule" for a in anchors_for({"ines"}, frame, TYPES, [rule]))


def test_a_fact_goes_to_the_checks_it_is_evidence_for() -> None:
    def keys(*args, **kw) -> set[str]:
        return {anchor.key for anchor in fact_anchors(*args, entity_types=TYPES, **kw)}

    assert keys("mara", "LOCATED_AT", "harbor") == {"timeline:mara"}
    assert keys("ines", "CARRIES", "lens") == {"resource:lens", "timeline:lens", "state:ines"}
    assert keys("mara", "HAS_STATUS", None) == {"state:mara"}
    assert keys("lens", "HAS_STATUS", None) == {"resource:lens"}
    assert keys("mara", "KNOWS", "ines") == {"state:mara", "state:ines"}
    rule = RuleScope("r1", frozenset({"mara"}), predicates=frozenset({"MEMBER_OF"}))
    assert keys("mara", "MEMBER_OF", "guild", rules=[rule]) == {"state:mara", "rule:r1"}
    assert keys("ines", "MEMBER_OF", "guild", rules=[rule]) == {"state:ines"}


def test_each_check_gets_its_passages_and_its_records_and_coverage_is_complete() -> None:
    text = "Mara climbed the stair.\n\nInes waited by the lens.\n\nRain fell on the harbor."
    units = _units(text, SceneFrame("s1", 1))
    records = [
        _record("fact:ines_at", Anchor("timeline", "ines")),
        _record("fact:lens", Anchor("resource", "lens")),
        _record("fact:unrelated", Anchor("timeline", "nobody")),
    ]
    plan = plan_bundles(units, records)
    by_id = {bundle.id: bundle for bundle in plan.bundles}
    assert by_id["timeline:ines+lens"].evidence_keys == ("fact:ines_at",)
    assert seen_together(plan, ["s1#1"], ["fact:ines_at", "fact:lens"]) is False
    assert seen_together(plan, ["s1#1"], ["fact:lens"]) is True
    assert plan.coverage.complete
    assert ("fact:unrelated", "no passage under review is about it") in (
        plan.coverage.evidence_unused
    )
    # The paragraph naming nothing is read on its own, and said to be.
    lonely = next(b for b in plan.bundles if b.fallback == "unit")
    assert lonely.unit_ids == ("s1#2",)
    assert any("name nothing a check is about" in w for w in plan.warnings)


def test_groups_of_one_kind_over_the_same_passages_are_merged() -> None:
    text = "Mara and Ines met by the lens."
    units = _units(text, SceneFrame("s1", 1))
    plan = plan_bundles(units, [])
    timeline = [b for b in plan.bundles if b.family == "timeline"]
    assert len(timeline) == 1 and set(timeline[0].anchors) == {"mara", "ines", "lens"}


def test_a_group_over_the_budget_is_split_never_skipped_and_the_split_is_said() -> None:
    paragraphs = [f"Mara watched the harbor on day {n}. " * 8 for n in range(30)]
    units = _units("\n\n".join(paragraphs))
    budget = INSTRUCTION_TOKENS + MAX_UNIT_TOKENS
    plan = plan_bundles(units, [_record("belief:b", Anchor("pov", "mara"))], budget=budget)
    pov = [bundle for bundle in plan.bundles if bundle.family == "pov"]
    assert len(pov) > 1 and all(bundle.tokens <= budget for bundle in pov)
    assert {unit for bundle in pov for unit in bundle.unit_ids} == {unit.id for unit in units}
    assert all(bundle.evidence_keys == ("belief:b",) for bundle in pov)
    assert any("do not fit one group" in warning for warning in plan.warnings)
    assert plan.coverage.complete


def test_records_that_fill_half_the_budget_are_split_too_and_meet_every_part() -> None:
    units = _units("Mara watched.")
    many = [_record(f"fact:{n}", Anchor("timeline", "mara"), tokens=150, order=n) for n in range(6)]
    plan = plan_bundles(units, many, budget=1_000)
    timeline = [bundle for bundle in plan.bundles if bundle.family == "timeline"]
    assert len(timeline) > 1
    assert {key for bundle in timeline for key in bundle.evidence_keys} == {r.key for r in many}
    assert any("may miss two records" in warning for warning in plan.warnings)


def test_a_secret_is_always_labelled_for_the_author_only() -> None:
    units = _units("Mara watched.")
    plan = plan_bundles(units, [_record("fact:secret", Anchor("pov", "mara"), author_only=True)])
    pov = next(bundle for bundle in plan.bundles if bundle.family == "pov")
    assert pov.author_only == ("fact:secret",)
    assert preview(plan).author_only == 1


def test_a_cap_on_groups_is_never_silent() -> None:
    text = "\n\n".join(f"Mara and Ines, part {n}." for n in range(5))
    plan = plan_bundles(_units(text), [], max_groups=1)
    assert len(plan.bundles) == 1 and plan.coverage.complete is False
    assert any(unit.missing for unit in plan.coverage.units)
    assert any("are not covered" in warning for warning in plan.warnings)


def test_with_no_story_graph_passages_are_read_in_order_and_coverage_says_so() -> None:
    units = _units("Mara watched.\n\nInes waited.")
    plan = plan_bundles(units, [_record("fact:a", Anchor("pov", "mara"))], graph=False)
    assert [bundle.family for bundle in plan.bundles] == ["window"]
    assert plan.bundles[0].evidence_keys == ()
    assert plan.coverage.from_graph is False and plan.coverage.complete is False


def test_the_same_inputs_always_give_the_same_plan() -> None:
    text = "Mara and Ines met by the lens.\n\nRain fell on the harbor."
    records = [
        _record("fact:a", Anchor("timeline", "mara")),
        _record("fact:b", Anchor("pov", "mara")),
    ]
    first = plan_bundles(_units(text), records)
    again = plan_bundles(_units(text), list(reversed(records)))
    assert first == again


def test_the_baselines_share_the_shapes_and_read_every_passage() -> None:
    text = "Mara watched the lens.\n\nInes waited by the harbor."
    units = _units(text)
    records = [
        _record("fact:a", Anchor("timeline", "mara")),
        _record("fact:b", Anchor("pov", "ines")),
    ]
    whole = single_context(units, records, budget=10_000)
    assert len(whole.bundles) == 1 and whole.coverage.complete
    assert seen_together(whole, ["s1#0", "s1#1"], ["fact:a", "fact:b"])
    nearest = nearest_evidence(
        units, records, lambda unit, item: 1.0 if item.key == "fact:a" else 0.0, k=1
    )
    assert [bundle.evidence_keys for bundle in nearest.bundles] == [("fact:a",), ("fact:a",)]
    assert missing_for(nearest, ["s1#1"], ["fact:b"]) == {"fact:b"}
    assert missing_for(nearest, ["s1#0", "s1#1"], ["fact:a"]) == {"fact:a"}


def test_the_preview_says_what_would_be_read_in_plain_words() -> None:
    units = _units("Mara watched the lens.\n\nInes waited.")
    shown = preview(plan_bundles(units, []))
    assert shown.passages == 2 and shown.scenes == 1 and shown.complete
    assert shown.lines[0].startswith("2 passages from 1 scene, read in ")
    assert shown.tokens == sum(line.tokens for line in shown.families)
    assert tokens_of("abcd" * 10) == 10 and tokens_of("") == 0
