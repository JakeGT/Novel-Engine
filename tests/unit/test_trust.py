"""How characters take what they are told and what they see (KNW-006)."""

import pytest

from backend.domain.canon.assertions import Belief
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.trust import (
    Hearer,
    TrustPolicy,
    propose_beliefs,
    suggested,
    trust_of,
    with_trust,
)

pytestmark = pytest.mark.unit

NAMES = {"mara": "Mara", "tobin": "Tobin", "ines": "Ines", "oren": "Oren"}


def _prop(place: str = "saltmere") -> Proposition:
    return Proposition(
        subject_id="mara",
        predicate="LOCATED_AT",
        object=EntityRefObject(entity_id=place, project_id="p"),
    )


def _belief(holder: str, proposition: Proposition) -> Belief:
    return Belief(
        id=f"belief:{holder}",
        workspace_id="w",
        project_id="p",
        branch_id="main",
        holder_entity_id=holder,
        proposition=proposition,
        acquisition_provenance_id="prov",
        created_revision=1,
    )


def test_by_default_everyone_believes_what_they_are_told_and_overhearing_is_less_sure():
    found = propose_beliefs(
        _prop(),
        [Hearer("tobin", "told"), Hearer("ines", "overheard")],
        speaker_id="mara",
        trust={},
        beliefs=[],
        names=NAMES,
    )
    assert [(item.holder_id, item.confidence) for item in found] == [
        ("tobin", 1.0),
        ("ines", 0.6),
    ]
    assert found[0].why == (
        "Tobin was told by Mara; Tobin believes what people tell them, so believes it firmly."
    )
    assert "overheard it from Mara" in found[1].why and "less firmly" in found[1].why


def test_doubt_halves_and_disbelief_proposes_no_belief_at_all():
    trust = {
        "tobin": TrustPolicy(default="doubts"),
        "ines": TrustPolicy(speakers={"mara": "disbelieves"}),
    }
    found = propose_beliefs(
        _prop(),
        [Hearer("tobin", "told"), Hearer("ines", "told"), Hearer("oren", "overheard")],
        speaker_id="mara",
        trust=trust,
        beliefs=[],
        names=NAMES,
    )
    tobin, ines, oren = found
    assert tobin.confidence == 0.5 and tobin.stance == "doubts"
    assert "doubts what people tell them" in tobin.why
    # Disbelief is no belief, not a belief in the opposite.
    assert ines.confidence is None and ines.stance == "disbelieves"
    assert ines.why == "Ines was told but does not believe Mara, so no belief is added."
    # A doubter who overhears is less sure again; the default for Oren is to believe.
    assert oren.confidence == 0.6


def test_a_stance_toward_one_speaker_wins_over_the_general_one():
    policy = TrustPolicy(default="disbelieves", speakers={"mara": "believes"})
    assert policy.toward("mara") == ("believes", True)
    assert policy.toward("oren") == ("disbelieves", False)
    found = propose_beliefs(
        _prop(),
        [Hearer("tobin", "told")],
        speaker_id="mara",
        trust={"tobin": policy},
        beliefs=[],
        names=NAMES,
    )
    assert found[0].confidence == 1.0 and "believes what Mara says" in found[0].why


def test_seeing_is_believing_whatever_their_trust():
    found = propose_beliefs(
        _prop(),
        [Hearer("tobin", "saw")],
        speaker_id=None,
        trust={"tobin": TrustPolicy(default="disbelieves")},
        beliefs=[],
        names=NAMES,
    )
    assert found[0].confidence == 1.0
    assert found[0].why == "Tobin was there and saw it happen."


def test_nothing_is_proposed_twice_and_an_old_contrary_belief_is_named():
    held = [_belief("tobin", _prop()), _belief("ines", _prop("harbor"))]
    found = propose_beliefs(
        _prop(),
        [Hearer("tobin", "told"), Hearer("ines", "told")],
        speaker_id="mara",
        trust={},
        beliefs=held,
        names=NAMES,
    )
    assert found[0].already and found[0].confidence is None
    assert found[1].confidence == 1.0
    assert "believed something else about this before" in found[1].why


def test_the_same_story_always_gives_the_same_proposals():
    args = dict(speaker_id="mara", trust={"ines": TrustPolicy("doubts")}, beliefs=[], names=NAMES)
    hearers = [Hearer("tobin", "told"), Hearer("ines", "overheard")]
    assert propose_beliefs(_prop(), hearers, **args) == propose_beliefs(_prop(), hearers, **args)


def test_a_suggestion_is_believed_less_firmly():
    item = propose_beliefs(
        _prop(), [Hearer("tobin", "told")], speaker_id="mara", trust={}, beliefs=[], names=NAMES
    )[0]
    softer = suggested(item)
    assert softer.confidence == 0.5 and softer.why.endswith("less firmly again.")


def test_trust_is_kept_beside_the_details_and_only_when_it_is_not_the_default():
    details = {"role": "pilot"}
    assert with_trust(details, TrustPolicy()) == {"role": "pilot"}
    kept = with_trust(details, TrustPolicy("doubts", {"mara": "disbelieves"}))
    assert kept == {
        "role": "pilot",
        "_trust": {"default": "doubts", "speakers": {"mara": "disbelieves"}},
    }
    assert trust_of(kept) == TrustPolicy("doubts", {"mara": "disbelieves"})
    # Anything unreadable is the default, never an error.
    assert trust_of({"_trust": {"default": "maybe", "speakers": {"x": "?"}}}) == TrustPolicy()
    assert trust_of({"_trust": "nonsense"}) == TrustPolicy()
    assert trust_of(None) == TrustPolicy()
