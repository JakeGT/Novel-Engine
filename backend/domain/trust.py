"""How characters take what they are told and what they see (KNW-006).

Deterministic policies that turn a telling or a witnessed event into proposed beliefs.
Nothing here writes anything: the knowledge service puts every proposed belief into one
reviewed proposal, each with the reason it was proposed.

**Trust** is kept on the listener, under the reserved ``_trust`` key of a character's
details: a stance for anyone who tells them something, and stances for particular
speakers. A stance is one of:

- ``believes`` (the default): they take it as said;
- ``doubts``: they come to believe it, but less firmly;
- ``disbelieves``: they do not take it on. No belief is proposed for them.

Disbelief yields *no* belief rather than a belief in the opposite. A proposition is a
subject, a kind of fact and one value; its opposite is rarely one value ("not in
Saltmere" names no place), so a belief in it could not be written down without
inventing something. This follows trust discounting in subjective logic, where distrust
in a source moves its testimony into uncertainty, not into disbelief. The proposal still
says, for each such listener, that they were told and did not believe it.

**Perception**: who sees something happen. Everyone present perceives it unless the
author says they did not notice; being present is not assumed from being in a scene,
the author confirms who was there. What a character sees for themselves is believed
fully: trust is about other people's word, not one's own eyes.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from backend.domain.canon.assertions import Belief
from backend.domain.canon.marks import TRUST_KEY
from backend.domain.canon.propositions import Proposition

Stance = Literal["believes", "doubts", "disbelieves"]
STANCES: tuple[Stance, ...] = ("believes", "doubts", "disbelieves")
DEFAULT_STANCE: Stance = "believes"
How = Literal["told", "overheard", "saw"]

# How firmly a belief is held, by stance; someone who overhears is less sure again.
STANCE_CONFIDENCE: dict[Stance, float] = {"believes": 1.0, "doubts": 0.5, "disbelieves": 0.0}
OVERHEARD_FACTOR = 0.6
MAX_SPEAKERS = 50


@dataclass(frozen=True)
class TrustPolicy:
    """How one character takes what others say: in general, and from particular people."""

    default: Stance = DEFAULT_STANCE
    speakers: dict[str, Stance] = field(default_factory=dict)

    def toward(self, speaker_id: str) -> tuple[Stance, bool]:
        """The stance toward a speaker, and whether it was set for that speaker."""
        if speaker_id in self.speakers:
            return self.speakers[speaker_id], True
        return self.default, False

    def to_attribute(self) -> dict[str, Any] | None:
        """The stored form, or None when it is the default and needs no keeping."""
        if self.default == DEFAULT_STANCE and not self.speakers:
            return None
        return {"default": self.default, "speakers": dict(sorted(self.speakers.items()))}


def _stance(value: Any) -> Stance | None:
    return value if isinstance(value, str) and value in STANCES else None  # type: ignore[return-value]


def trust_of(attributes: Mapping[str, Any] | None) -> TrustPolicy:
    """Read a character's trust from their details; anything unreadable is the default."""
    raw = (attributes or {}).get(TRUST_KEY)
    if not isinstance(raw, Mapping):
        return TrustPolicy()
    speakers: dict[str, Stance] = {}
    found = raw.get("speakers")
    if isinstance(found, Mapping):
        for key, value in list(found.items())[:MAX_SPEAKERS]:
            stance = _stance(value)
            if isinstance(key, str) and key and stance is not None:
                speakers[key] = stance
    return TrustPolicy(default=_stance(raw.get("default")) or DEFAULT_STANCE, speakers=speakers)


def with_trust(attributes: Mapping[str, Any], policy: TrustPolicy) -> dict[str, Any]:
    """The details with this trust kept, or removed when it is the default."""
    kept = {key: value for key, value in attributes.items() if key != TRUST_KEY}
    stored = policy.to_attribute()
    if stored is not None:
        kept[TRUST_KEY] = stored
    return kept


@dataclass(frozen=True)
class Hearer:
    entity_id: str
    how: How


@dataclass(frozen=True)
class ProposedBelief:
    """What the policies say about one hearer or witness, with the reason in words."""

    holder_id: str
    how: How
    # None when no belief is proposed (they did not believe it, or already did).
    confidence: float | None
    why: str
    stance: Stance | None = None
    already: bool = False


def _held(holder_id: str, proposition: Proposition, beliefs: Iterable[Belief]) -> bool:
    return any(
        belief.holder_entity_id == holder_id
        and belief.closed_revision is None
        and belief.proposition == proposition
        for belief in beliefs
    )


def _thinks_otherwise(holder_id: str, proposition: Proposition, beliefs: Iterable[Belief]) -> bool:
    return any(
        belief.holder_entity_id == holder_id
        and belief.closed_revision is None
        and belief.proposition.subject_id == proposition.subject_id
        and belief.proposition.predicate == proposition.predicate
        and belief.proposition.object != proposition.object
        for belief in beliefs
    )


def propose_beliefs(
    proposition: Proposition,
    hearers: Iterable[Hearer],
    *,
    speaker_id: str | None,
    trust: Mapping[str, TrustPolicy],
    beliefs: Iterable[Belief],
    names: Mapping[str, str],
) -> list[ProposedBelief]:
    """Apply trust and perception to each hearer or witness, in the order given.

    ``speaker_id`` is None for something seen rather than said. Deterministic: the same
    story and the same hearers always give the same proposals and reasons.
    """
    held = list(beliefs)
    speaker = names.get(speaker_id or "", "the speaker")
    proposals: list[ProposedBelief] = []
    for hearer in hearers:
        who = names.get(hearer.entity_id, "Someone")
        otherwise = (
            " They believed something else about this before; that belief is not ended."
            if _thinks_otherwise(hearer.entity_id, proposition, held)
            else ""
        )
        if _held(hearer.entity_id, proposition, held):
            proposals.append(
                ProposedBelief(
                    hearer.entity_id,
                    hearer.how,
                    None,
                    f"{who} already believes this, so nothing is added.",
                    already=True,
                )
            )
            continue
        if hearer.how == "saw" or speaker_id is None:
            proposals.append(
                ProposedBelief(
                    hearer.entity_id,
                    hearer.how,
                    1.0,
                    f"{who} was there and saw it happen.{otherwise}",
                )
            )
            continue
        policy = trust.get(hearer.entity_id, TrustPolicy())
        stance, particular = policy.toward(speaker_id)
        source = (
            f"{who} {_verb(stance)} what {speaker} says"
            if particular
            else f"{who} {_verb(stance)} what people tell them"
        )
        heard = "overheard it" if hearer.how == "overheard" else "was told"
        if stance == "disbelieves":
            proposals.append(
                ProposedBelief(
                    hearer.entity_id,
                    hearer.how,
                    None,
                    f"{who} {heard} but {_verb(stance)} {speaker}, so no belief is added.",
                    stance=stance,
                )
            )
            continue
        confidence = STANCE_CONFIDENCE[stance]
        if hearer.how == "overheard":
            confidence = round(confidence * OVERHEARD_FACTOR, 2)
        firmness = "firmly" if confidence >= 1.0 else "less firmly"
        proposals.append(
            ProposedBelief(
                hearer.entity_id,
                hearer.how,
                confidence,
                f"{who} {heard} by {speaker}; {source}, so believes it {firmness}.{otherwise}"
                if hearer.how == "told"
                else f"{who} {heard} from {speaker}; {source}, so believes it {firmness}."
                f"{otherwise}",
                stance=stance,
            )
        )
    return proposals


# A claim's hearers can believe only what is stated or suggested; a suggestion is held less
# firmly. Wondering aloud gives nothing to believe, and a denial would need a belief in the
# opposite, which is not proposed (see above).
BELIEVED_MODALITIES = ("states", "suggests")
SUGGESTED_FACTOR = 0.5


def suggested(item: ProposedBelief) -> ProposedBelief:
    """The same proposal for something only suggested: half as firm, and saying so."""
    if item.confidence is None:
        return item
    return replace(
        item,
        confidence=round(item.confidence * SUGGESTED_FACTOR, 2),
        why=f"{item.why} It was only suggested, so less firmly again.",
    )


def _verb(stance: Stance) -> str:
    return {"believes": "believes", "doubts": "doubts", "disbelieves": "does not believe"}[stance]


__all__ = [
    "BELIEVED_MODALITIES",
    "DEFAULT_STANCE",
    "OVERHEARD_FACTOR",
    "STANCES",
    "STANCE_CONFIDENCE",
    "SUGGESTED_FACTOR",
    "Hearer",
    "ProposedBelief",
    "Stance",
    "TrustPolicy",
    "propose_beliefs",
    "suggested",
    "trust_of",
    "with_trust",
]
