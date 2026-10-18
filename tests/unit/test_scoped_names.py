"""Name answers that hold only in part of the story (CTX-016): resolution and examples."""

from datetime import UTC, datetime, timedelta

import pytest

from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.naming import normalize_name
from backend.domain.drafting import DraftBrief, check_draft
from backend.domain.sources import (
    AliasBinding,
    NameExample,
    NamePlace,
    Verdict,
    build_index,
    example_holds,
    find_mentions,
    replaces,
)

pytestmark = pytest.mark.unit

T0 = datetime(2026, 10, 18, tzinfo=UTC)
ORDER = {"s1": 1, "s2": 2, "s3": 3}


def _entity(entity_id: str, name: str) -> CanonEntity:
    return CanonEntity(
        id=entity_id,
        workspace_id="w",
        project_id="p",
        branch_id="b",
        type="Character",
        name=name,
        schema_version_id="s",
        created_revision=1,
    )


ENTITIES = [_entity("mara", "Mara"), _entity("roe", "Roe"), _entity("father", "Old Ashe")]
_counter = iter(range(10_000))


def _answer(phrase: str, entity_id: str | None, verdict: Verdict = Verdict.IS, **scope):
    number = next(_counter)
    return AliasBinding(
        workspace_id="w",
        project_id="p",
        id=f"alb_{number:04d}",
        phrase=phrase,
        normalized=normalize_name(phrase),
        verdict=verdict,
        entity_id=entity_id,
        source_id=scope.pop("source_id", None),
        decided_by=None,
        decided_at=T0 + timedelta(minutes=number),
        **scope,
    )


def _at(scene: str | None = None, *, pov=(), branch=None, source="txt") -> NamePlace:
    return NamePlace(
        source_id=source, branch_id=branch, scene_id=scene, viewpoint_ids=pov, order=ORDER
    )


def _means(phrase: str, bindings, place: NamePlace) -> list[str]:
    return build_index(ENTITIES, bindings, place=place).resolve(phrase)


def test_from_a_scene_on_one_entry_and_before_it_another():
    answers = [
        _answer("the captain", "mara", until_scene_id="s1"),
        _answer("the captain", "roe", from_scene_id="s2"),
    ]
    assert _means("the captain", answers, _at("s1")) == ["mara"]
    assert _means("The Captain", answers, _at("s2")) == ["roe"]
    assert _means("the captain", answers, _at("s3")) == ["roe"]
    # Outside any scene (a note, a pasted text), neither holds.
    assert _means("the captain", answers, _at(None)) == []


def test_an_answer_whose_scene_is_gone_stops_applying():
    answers = [_answer("the captain", "roe", from_scene_id="s9")]
    assert _means("the captain", answers, _at("s3")) == []


def test_a_viewpoint_answer_holds_only_in_scenes_seen_by_that_character():
    answers = [_answer("the old man", "father", viewpoint_id="tobin")]
    assert _means("the old man", answers, _at("s2", pov=("tobin",))) == ["father"]
    assert _means("the old man", answers, _at("s2", pov=("mara",))) == []


def test_a_what_ifs_answer_stays_in_it():
    answers = [_answer("the stranger", "roe", branch_id="br_1")]
    assert _means("the stranger", answers, _at(branch="br_1")) == ["roe"]
    assert _means("the stranger", answers, _at(branch=None)) == []
    assert _means("the stranger", answers, _at(branch="br_2")) == []


def test_the_most_specific_answer_wins_whatever_order_they_were_given_in():
    narrow = _answer("the old man", "father", viewpoint_id="tobin", from_scene_id="s2")
    wide = _answer("the old man", "roe")
    middle = _answer("the old man", "mara", from_scene_id="s2")
    answers = [narrow, wide, middle]
    assert _means("the old man", answers, _at("s1", pov=("tobin",))) == ["roe"]
    assert _means("the old man", answers, _at("s3")) == ["mara"]
    assert _means("the old man", answers, _at("s3", pov=("tobin",))) == ["father"]


def test_a_narrow_not_a_name_or_ruling_out_overrides_a_wide_answer():
    wide = _answer("the boy", "roe")
    not_here = _answer("the boy", None, Verdict.IGNORE, viewpoint_id="tobin")
    index = build_index(ENTITIES, [wide, not_here], place=_at("s1", pov=("tobin",)))
    assert "the boy" in index.ignored
    assert find_mentions("The boy ran.", index) == []
    ruled = _answer("the boy", "roe", Verdict.IS_NOT, from_scene_id="s3")
    assert _means("the boy", [wide, ruled], _at("s3")) == []
    assert _means("the boy", [wide, ruled], _at("s2")) == ["roe"]


def test_between_equally_specific_answers_the_later_one_decides():
    first = _answer("the captain", "mara", from_scene_id="s1")
    later = _answer("the captain", "roe", viewpoint_id="tobin")
    assert _means("the captain", [later, first], _at("s2", pov=("tobin",))) == ["roe"]


def test_one_decision_per_phrase_and_scope():
    everywhere = _answer("the captain", "mara")
    scoped = _answer("the captain", "roe", from_scene_id="s2")
    again = _answer("the captain", "roe", from_scene_id="s2")
    assert not replaces(everywhere, scoped)
    assert replaces(scoped, again)
    assert scoped.specificity == 1 and everywhere.everywhere


def test_a_guess_never_replaces_the_authors_answer_in_the_same_scope():
    guess = _answer("the captain", "roe", Verdict.SUGGESTED, source_id="txt")
    for verdict, entity_id in (
        (Verdict.IS, "roe"),
        (Verdict.IS_NOT, "roe"),
        (Verdict.IGNORE, None),
    ):
        confirmed = _answer("the captain", entity_id, verdict, source_id="txt")
        assert not replaces(confirmed, guess)
    # A newer guess still takes an older guess's place, and the author's answer takes both.
    assert replaces(_answer("the captain", "roe", Verdict.SUGGESTED, source_id="txt"), guess)
    assert replaces(guess, _answer("the captain", "roe", source_id="txt"))


def test_an_example_is_checked_against_todays_answers():
    answer = _answer("the captain", "roe", from_scene_id="s2")
    example = NameExample(id="nex_1", quote="The captain waved.", means=True, scene_id="s3")
    index = build_index(ENTITIES, [answer], place=_at("s3"))
    assert example_holds(answer, example, index) == (True, ["roe"])
    # A narrower answer given later reads the same words differently.
    narrower = _answer("the captain", "mara", from_scene_id="s3", until_scene_id="s3")
    index = build_index(ENTITIES, [answer, narrower], place=_at("s3"))
    assert example_holds(answer, example, index) == (False, ["mara"])
    # A negative example holds while the phrase does not mean the entry there.
    negative = NameExample(id="nex_2", quote="The captain slept.", means=False)
    assert example_holds(answer, negative, index)[0] is True


def test_an_example_survives_being_stored_and_bad_fields_are_dropped():
    example = NameExample(
        id="nex_1",
        quote="The captain waved.",
        means=True,
        source_id="src",
        version_id="ver",
        scene_id="s2",
        at=(0, 11),
        noted_by="user",
        noted_at=T0,
    )
    assert NameExample.from_json(example.as_json()) == example
    odd = NameExample.from_json({"id": "x", "quote": "q", "means": 1, "at": ["a"], "noted_at": "?"})
    assert odd.at is None and odd.noted_at is None and odd.means is True


def test_the_studio_check_counts_a_scoped_phrase_as_the_viewpoint():
    brief = DraftBrief(point_of_view="Tobin", point_of_view_names=("Tobin",))
    text = "The boy watched the water until the tide turned."
    assert any(f.code == "viewpoint_missing" for f in check_draft(text, brief, []))
    brief.also = {"Tobin": ("the boy",)}
    assert not any(f.code == "viewpoint_missing" for f in check_draft(text, brief, []))
