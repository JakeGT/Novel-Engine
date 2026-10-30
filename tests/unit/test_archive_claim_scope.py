"""Restoring an archive into another project moves what a claim says there too.

Found while seeding a claim into the reference world (QLT-001): a claim's proposition
kept the source project's id, so restoring a story with a claim into a new project was
refused as a cross-project reference.
"""

import pytest

from backend.domain.canon.events import EventClaim
from backend.domain.canon.propositions import EntityRefObject, Proposition, StringObject
from backend.domain.operations import CreateEventOp
from backend.services.canon_archive import _remap_operation_scope

pytestmark = pytest.mark.unit


def _claim(obj) -> CreateEventOp:
    return CreateEventOp(
        id="evt_c",
        kind="claim",
        name="She says so",
        claim=EventClaim(proposition=Proposition(subject_id="ent_a", predicate="P", object=obj)),
    )


def test_a_claims_entity_reference_moves_to_the_target_project():
    moved = _remap_operation_scope(
        _claim(EntityRefObject(entity_id="ent_b", project_id="prj_old")),
        target_project_id="prj_new",
    )
    assert isinstance(moved, CreateEventOp) and moved.claim is not None
    assert moved.claim.proposition.object == EntityRefObject(
        entity_id="ent_b", project_id="prj_new"
    )


def test_a_claim_with_a_plain_value_and_an_ordinary_event_are_unchanged():
    plain = _claim(StringObject(value="red"))
    assert _remap_operation_scope(plain, target_project_id="prj_new") == plain
    event = CreateEventOp(id="evt_e", kind="storm", name="The storm")
    assert _remap_operation_scope(event, target_project_id="prj_new") == event
