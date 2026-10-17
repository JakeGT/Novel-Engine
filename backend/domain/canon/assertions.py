"""Facts and beliefs.

A ``Fact`` is an accepted objective proposition. A ``Belief`` is a holder-scoped
proposition that may be false or uncertain without any corresponding fact. Truth
agreement is always derived at a checked revision, never stored on the belief.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel

from backend.domain.canon.propositions import Proposition
from backend.domain.worldtime import WorldTime


class FactStatus(StrEnum):
    ASSERTED = "asserted"
    CLOSED = "closed"


class FactVisibility(StrEnum):
    """Who in the story may know a fact (ADR-020). Knowledge is deny-by-default."""

    # Known to those who hold it; nothing more is said.
    ORDINARY = "ordinary"
    # Everyone in the story knows it.
    COMMON = "common"
    # Known only to those who hold it, and never assumed.
    SECRET = "secret"


class Fact(BaseModel):
    """Accepted objective proposition in the root branch."""

    model_config = {"extra": "forbid"}

    id: str
    workspace_id: str
    project_id: str
    branch_id: str
    proposition: Proposition
    status: FactStatus = FactStatus.ASSERTED
    confidence: float = 1.0
    provenance_id: str
    created_revision: int
    closed_revision: int | None = None
    # Reserved in Phase 2 and never used; story time is ``starts``/``ends``.
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    starts: WorldTime | None = None
    ends: WorldTime | None = None
    visibility: FactVisibility = FactVisibility.ORDINARY


class Belief(BaseModel):
    """Holder-scoped proposition. It has no ``is_true`` field by design."""

    model_config = {"extra": "forbid"}

    id: str
    workspace_id: str
    project_id: str
    branch_id: str
    holder_entity_id: str
    proposition: Proposition
    confidence: float = 1.0
    acquisition_provenance_id: str
    source_ref: str | None = None
    created_revision: int
    closed_revision: int | None = None


class TruthAgreement(StrEnum):
    AGREES = "agrees"
    DISAGREES = "disagrees"
    UNKNOWN = "unknown"


def truth_agreement(belief: Belief, facts: Sequence[Fact]) -> TruthAgreement:
    """Compare a belief to accepted facts for the same subject/predicate.

    Agreement is a query result over a supplied fact set (a checked revision), so
    it can never go stale as a stored field. If no accepted fact covers the
    subject/predicate the belief is ``UNKNOWN`` rather than false.
    """
    subject_id = belief.proposition.subject_id
    predicate = belief.proposition.predicate
    relevant = [
        fact
        for fact in facts
        if fact.status is FactStatus.ASSERTED
        and fact.proposition.subject_id == subject_id
        and fact.proposition.predicate == predicate
    ]
    if not relevant:
        return TruthAgreement.UNKNOWN
    if any(fact.proposition.object == belief.proposition.object for fact in relevant):
        return TruthAgreement.AGREES
    return TruthAgreement.DISAGREES
