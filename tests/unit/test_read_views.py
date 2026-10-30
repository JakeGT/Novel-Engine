"""Pure parts of the story's ready views (QRY-001)."""

import json
import math

from backend.domain.read_views import (
    at_moment,
    canonical,
    checksum,
    dependents,
    differences,
    interval_of,
    pair,
    span_from,
    span_json,
)
from backend.domain.worldtime import Interval, Span, Truth


def test_numbers_and_unbounded_ends_survive_a_trip_through_json() -> None:
    span = Span(start=Interval(3, 3), end=Interval(-math.inf, math.inf), stated=True)
    row = {"span": span_json(span), "number": 1}
    back = json.loads(json.dumps(row))
    assert canonical(back) == canonical(row) and checksum([back]) == checksum([row])
    assert span_from(back["span"]) == span
    assert pair(None) is None and interval_of(None) is None
    assert interval_of([None, 4]) == Interval(-math.inf, 4)


def test_rows_hold_at_a_moment_with_their_certainty() -> None:
    rows = [
        {"entity_id": "m", "span": span_json(Span(Interval(10, 10), None, True))},
        {"entity_id": "m", "at": [5.0, 5.0]},
        {"entity_id": "m", "at": [4.0, 8.0]},
        {"entity_id": "other", "at": [5.0, 5.0]},
    ]
    at_five = at_moment(rows, 5, entity_id="m")
    assert [(row.get("at"), truth) for row, truth in at_five] == [
        ([5.0, 5.0], Truth.TRUE),
        ([4.0, 8.0], Truth.MAYBE),
    ]
    assert len(at_moment(rows, None, entity_id="m")) == 3
    assert [row.get("at") for row, _ in at_moment(rows, 12, entity_id="m")] == [None]


def test_dependents_are_nearest_first_and_cut_at_the_limit() -> None:
    rows = [
        {"from": "fact:f", "to": "entity:e", "why": "subject"},
        {"from": "plan_item:s", "to": "fact:f", "why": "shows"},
        {"from": "plan_item:t", "to": "plan_item:s", "why": "part of"},
        {"from": "plan_item:u", "to": "plan_item:t", "why": "part of"},
        {"from": "fact:f", "to": "plan_item:u", "why": "loop"},
    ]
    found, cut = dependents(rows, "entity:e")
    assert [(d.record, d.depth, d.via) for d in found] == [
        ("fact:f", 1, "entity:e"),
        ("plan_item:s", 2, "fact:f"),
        ("plan_item:t", 3, "plan_item:s"),
    ]
    assert not cut
    found, cut = dependents(rows, "entity:e", limit=1)
    assert len(found) == 1 and cut


def test_differences_count_rows_on_one_side_only() -> None:
    assert differences([{"a": 1}, {"a": 2}], [{"a": 2.0}, {"a": 3}]) == 2
