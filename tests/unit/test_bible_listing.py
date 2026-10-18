"""Listing policies for the story bible (CTX-002): deterministic, budgeted, explained."""

import pytest

from backend.domain.bible_listing import (
    CHANGE_POLICY,
    POLICIES,
    Line,
    ListingPolicy,
    Names,
    Tier,
    choose,
    clip_words,
    fold,
    importance_rank,
    named_in,
    ranked,
    tiers,
)
from backend.domain.canon.entities import CanonEntity

pytestmark = pytest.mark.unit


def _entity(entity_id: str, name: str, *, aliases=(), importance=None, retired=False):
    attributes = {"_importance": importance} if importance else {}
    return CanonEntity(
        id=entity_id,
        workspace_id="w",
        project_id="p",
        branch_id="b",
        type="Character",
        name=name,
        schema_version_id="s",
        created_revision=1,
        retired_revision=2 if retired else None,
        lifecycle="retired" if retired else "active",
        aliases=list(aliases),
        attributes=attributes,
    )


def test_names_are_found_whole_ignoring_accents_case_and_punctuation():
    people = [
        _entity("ent_a", "Mara Venn", aliases=["the Fox"]),
        _entity("ent_b", "Zoë"),
        _entity("ent_c", "Mar"),
        _entity("ent_d", "Gone", retired=True),
    ]
    assert fold("Mara's BOAT, Zoë!") == "mara s boat zoe"
    assert named_in("The FOX met zoe; and Gone too.", people) == ["ent_a", "ent_b"]
    # Not inside another word, and an id counts as naming.
    assert named_in("Maranta saw ent_c", people) == ["ent_c"]
    # The longest name wins where two start at the same word.
    finder = Names([_entity("ent_x", "Old"), _entity("ent_y", "Old Mill")])
    assert finder.find("by the old mill") == ["ent_y"]
    assert named_in("   ", people) == []


def test_tiers_are_named_then_one_link_away_then_the_rest():
    found = tiers(["a", "b", "c", "d"], named=["a"], links=[("a", "b"), ("b", "c"), ("d", "d")])
    assert found == {"a": Tier.NAMED, "b": Tier.NEAR, "c": Tier.REST, "d": Tier.REST}


def test_importance_orders_main_first_and_unmarked_between():
    ranks = [
        importance_rank(_entity("x", "X", importance=level))
        for level in ("main", "supporting", None, "minor", "background")
    ]
    assert ranks == sorted(ranks) and len(set(ranks)) == 5


def _line(section: str, record_id: str, words: int, touches=(), rank=()):
    return Line(section, record_id, " ".join(["w"] * words), rank=rank, touches=touches)


def test_named_entries_are_kept_over_budget_and_the_rest_fill_it_tier_by_tier():
    policy = ListingPolicy("test/1", "a test", ("entries", "facts"), budget=10)
    lines = [
        _line("entries", "named", 8, touches=("named",)),
        _line("entries", "near", 3, touches=("near",)),
        _line("entries", "far", 1, touches=("far",)),
        _line("facts", "f-named", 2, touches=("named", "far")),
        _line("facts", "f-far", 1, touches=("far",)),
        _line("beliefs", "ignored", 1, touches=("named",)),
    ]
    tier_of = tiers(["named", "near", "far"], named=["named"], links=[("named", "near")])
    listing = choose(policy, ranked(lines, tier_of), named=["named"])
    # The named entry (8) and its fact (2) use the budget; nothing more fits.
    assert [line.record_id for line in listing.lines] == ["named", "f-named"]
    assert listing.listed == {"entries": 1, "facts": 1}
    assert listing.left_out == {"entries": 2, "facts": 1}
    assert listing.left_out_line() == (
        "(The story is larger than this list: 2 entries and 1 fact less connected to this "
        "request are not listed. Ask if something seems missing.)"
    )
    assert (
        listing.provenance()
        == "context test/1: 1 entry and 1 fact listed; 2 entries and 1 fact left out"
    )
    assert "Not shown: 2 entries and 1 fact." in (listing.note() or "")


def test_rendering_order_is_section_then_tier_then_rank_and_is_deterministic():
    lines = [
        _line("facts", "f1", 1, touches=("b",)),
        _line("entries", "b", 1, touches=("b",), rank=(1,)),
        _line("entries", "c", 1, touches=("c",), rank=(0,)),
        _line("entries", "a", 1, touches=("a",), rank=(5,)),
    ]
    tier_of = tiers(["a", "b", "c"], named=["a"], links=[])
    first = choose(CHANGE_POLICY, ranked(lines, tier_of))
    again = choose(CHANGE_POLICY, ranked(list(reversed(lines)), tier_of))
    assert [line.record_id for line in first.lines] == ["a", "c", "b", "f1"]
    assert first.lines == again.lines
    assert first.complete and first.left_out_line() is None and first.note() is None


def test_a_fact_between_two_named_entries_comes_before_one_reaching_outside():
    lines = [
        _line("facts", "f-out", 1, touches=("a", "z"), rank=("a",)),
        _line("facts", "f-both", 1, touches=("a", "b"), rank=("b",)),
    ]
    tier_of = tiers(["a", "b", "z"], named=["a", "b"], links=[])
    listing = choose(CHANGE_POLICY, ranked(lines, tier_of))
    assert [line.record_id for line in listing.lines] == ["f-both", "f-out"]


def test_every_policy_has_a_versioned_name_a_purpose_and_a_budget():
    versions = [policy.version for policy in POLICIES]
    assert len(set(versions)) == len(versions)
    for policy in POLICIES:
        name, _, number = policy.version.partition("/")
        assert name and number.isdigit() and policy.purpose and policy.budget > 0
        assert policy.sections[0] == "entries"


def test_named_entries_never_take_the_listing_past_its_budget():
    policy = ListingPolicy("test/1", "a test", ("entries",), budget=10)
    lines = [_line("entries", f"n{i}", 4, touches=(f"n{i}",), rank=(i,)) for i in range(5)]
    tier_of = tiers([f"n{i}" for i in range(5)], named=[f"n{i}" for i in range(5)], links=[])
    listing = choose(policy, ranked(lines, tier_of))
    assert [line.record_id for line in listing.lines] == ["n0", "n1"]
    assert listing.words <= policy.budget and listing.left_out == {"entries": 3}


def test_entries_found_another_way_are_near_unless_named():
    found = tiers(["a", "b", "c"], named=["a"], links=[], near=["a", "b"])
    assert found == {"a": Tier.NAMED, "b": Tier.NEAR, "c": Tier.REST}


def test_details_are_cut_to_a_number_of_words_when_a_policy_says_so():
    assert clip_words("one two three", None) == "one two three"
    assert clip_words("one two three", 3) == "one two three"
    assert clip_words("one  two three four", 2) == "one two …"
