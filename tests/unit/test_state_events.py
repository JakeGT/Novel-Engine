"""A change of state a timed scene shows becomes an event at the scene's time (EXT-001)."""

import pytest

from backend.domain.canon.registry import CORE_PREDICATES
from backend.domain.worldtime import At
from backend.services.intake_workflow import ReadStateChange, _state_events

pytestmark = pytest.mark.unit


def _events(change: str) -> list:
    status = ReadStateChange(
        kind="status",
        change=change,
        subject="ferryman",
        predicate="HAS_STATUS",
        value="dead",
        quote="The ferryman",
    )
    return _state_events(
        [("s0", status)],
        refs={"ferryman": ("ent_ferryman", "Character")},
        names={"ent_ferryman": "The ferryman"},
        predicates=dict(CORE_PREDICATES),
        starts=At(at=12),
        scene=None,
        version_id="ver_1",
        places={"s0": "chars:0-12"},
        taken=set(),
    )


def test_becoming_dead_is_a_death_and_coming_back_is_not() -> None:
    (died,) = _events("starts")
    assert died.kind == "death"
    assert [(p.entity_id, p.role) for p in died.participants] == [("ent_ferryman", "died")]
    (back,) = _events("ends")
    assert back.kind == "condition"
    assert [(p.entity_id, p.role) for p in back.participants] == [("ent_ferryman", "participant")]
    assert back.name == "The ferryman: condition (ends)"
