"""Canon domain errors."""

from __future__ import annotations


class CanonError(RuntimeError):
    """Base class for canon domain failures."""


class UnknownTypeError(CanonError):
    """An entity type is not declared by the core or project schema."""


class UnknownPredicateError(CanonError):
    """A predicate is not declared by the core or project schema."""


class InvalidPropositionError(CanonError):
    """A proposition's subject, predicate, object shape, or value is invalid."""


class RetiredReferenceError(CanonError):
    """An operation would create a new active reference to retired canon."""


class CrossProjectReferenceError(CanonError):
    """A reference crosses a project boundary."""


class CardinalityError(CanonError):
    """A predicate cardinality (one/many) constraint is violated."""
