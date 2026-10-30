"""Story time: values, resolution, ordering and fact spans (ADR-020)."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from backend.domain.worldtime import (
    WORLD_TIME,
    At,
    Between,
    Direction,
    Interval,
    Order,
    Relative,
    Span,
    TimeProblem,
    Truth,
    Unknown,
    compare,
    describe,
    parse_world_time,
    resolve_all,
    span_of,
    spans_overlap,
    true_at,
)


def test_each_kind_of_time_round_trips_through_json() -> None:
    for value in (
        Unknown(),
        At(at=12, label="the spring of year 12"),
        Between(earliest=3, latest=5),
        Between(latest=0, label="before the founding"),
        Relative(anchor_event_id="evt_war", offset=3, direction=Direction.BEFORE),
    ):
        assert WORLD_TIME.validate_json(WORLD_TIME.dump_json(value)) == value


def test_a_span_needs_an_end_and_its_ends_in_order() -> None:
    with pytest.raises(ValidationError):
        Between()
    with pytest.raises(ValidationError):
        Between(earliest=5, latest=3)


def test_a_stored_time_that_no_longer_parses_is_none_not_an_error() -> None:
    assert parse_world_time({"kind": "at"}) is None
    assert parse_world_time(None) is None
    assert parse_world_time({"kind": "at", "at": 4}) == At(at=4)


def test_relative_times_follow_their_anchor_and_carry_its_uncertainty() -> None:
    times = {
        "war": Between(earliest=100, latest=102),
        "exile": Relative(anchor_event_id="war", offset=3, direction=Direction.BEFORE),
        "return": Relative(anchor_event_id="exile", offset=10, give_or_take=1),
        "lost": Unknown(),
    }
    resolved = resolve_all(times)
    assert resolved["exile"].interval == Interval(97, 99)
    assert resolved["return"].interval == Interval(106, 110)
    assert resolved["lost"].interval is None


def test_a_chain_that_comes_back_on_itself_is_reported_not_looped() -> None:
    resolved = resolve_all(
        {
            "a": Relative(anchor_event_id="b", offset=1),
            "b": Relative(anchor_event_id="a", offset=1),
            "self": Relative(anchor_event_id="self", offset=0),
        }
    )
    assert resolved["a"].problem is TimeProblem.CYCLE
    assert resolved["self"].problem is TimeProblem.CYCLE
    assert resolved["a"].interval is None


def test_an_anchor_that_is_gone_is_reported() -> None:
    resolved = resolve_all({"a": Relative(anchor_event_id="gone", offset=1)})
    assert resolved["a"].problem is TimeProblem.MISSING_ANCHOR


def test_ordering_is_certain_only_when_the_spans_do_not_meet() -> None:
    assert compare(Interval(1, 2), Interval(3, 4)) is Order.BEFORE
    assert compare(Interval(3, 4), Interval(1, 2)) is Order.AFTER
    assert compare(Interval(1, 3), Interval(3, 4)) is Order.OVERLAPS
    assert compare(None, Interval(3, 4)) is Order.UNKNOWN


intervals = st.tuples(
    st.floats(min_value=-1e6, max_value=1e6), st.floats(min_value=0, max_value=1e3)
).map(lambda pair: Interval(pair[0], pair[0] + pair[1]))


@given(intervals, intervals)
def test_ordering_is_antisymmetric(a: Interval, b: Interval) -> None:
    flipped = {Order.BEFORE: Order.AFTER, Order.AFTER: Order.BEFORE}
    assert compare(b, a) is flipped.get(compare(a, b), compare(a, b))


def test_a_fact_with_no_span_is_true_throughout() -> None:
    assert true_at(span_of(None, None), Interval(-50, -50)) is Truth.TRUE


def test_a_span_is_true_from_its_start_and_no_longer_at_its_end() -> None:
    span = Span(start=Interval(10, 10), end=Interval(20, 20), stated=True)
    assert true_at(span, Interval(9, 9)) is Truth.FALSE
    assert true_at(span, Interval(10, 10)) is Truth.TRUE
    assert true_at(span, Interval(19, 19)) is Truth.TRUE
    assert true_at(span, Interval(20, 20)) is Truth.FALSE


def test_a_fuzzy_edge_makes_the_answer_maybe() -> None:
    span = Span(start=Interval(10, 14), end=None, stated=True)
    assert true_at(span, Interval(12, 12)) is Truth.MAYBE
    assert true_at(span, Interval(15, 15)) is Truth.TRUE


@given(intervals, intervals, intervals)
def test_a_moment_inside_a_certain_span_is_never_false(
    start: Interval, length: Interval, moment: Interval
) -> None:
    end = Interval(start.hi + length.lo + 1, start.hi + length.hi + 1)
    span = Span(start=start, end=end, stated=True)
    if start.hi <= moment.lo and moment.hi < end.lo:
        assert true_at(span, moment) is Truth.TRUE


def test_spans_that_meet_end_to_start_do_not_overlap() -> None:
    first = Span(start=None, end=Interval(10, 10), stated=True)
    second = Span(start=Interval(10, 10), end=None, stated=True)
    assert not spans_overlap(first, second)
    assert spans_overlap(first, Span(start=Interval(9, 9), end=None, stated=True))
    assert spans_overlap(first, span_of(None, None))


def test_times_are_described_in_the_authors_words_first() -> None:
    names = {"evt_war": "the war"}.get
    assert describe(At(at=12, label="spring of 12")) == "spring of 12"
    assert describe(At(at=12)) == "year 12"
    assert describe(Between(earliest=3, latest=5), unit="day") == "between day 3 and 5"
    assert describe(Between(latest=0)) == "before year 0"
    assert (
        describe(
            Relative(anchor_event_id="evt_war", offset=3, direction=Direction.BEFORE),
            event_name=names,
        )
        == "3 years before the war"
    )
    assert (
        describe(Relative(anchor_event_id="evt_x", offset=1, give_or_take=1)) == "about 1 year "
        "after an event no longer in the story"
    )
    assert describe(None) == "not placed in time"


def test_open_ended_spans_resolve_to_unbounded_intervals() -> None:
    resolved = resolve_all({"a": Between(latest=0)})
    interval = resolved["a"].interval
    assert interval is not None and interval.lo == -math.inf and interval.hi == 0
