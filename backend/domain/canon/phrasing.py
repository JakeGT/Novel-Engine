"""A kind of fact as the verb of a sentence, for text the assistant and exports read.

Kept in step with ``PREDICATES`` in ``frontend/src/lib/words.ts`` so a fact reads the
same on screen and in what the assistant is given. Kinds an author adds read as their
own words: ``TRAINED_BY`` → "trained by".
"""

from __future__ import annotations

PHRASES: dict[str, str] = {
    "LOCATED_AT": "is located at",
    "OWNS": "owns",
    "MEMBER_OF": "is a member of",
    "PARTICIPATED_IN": "took part in",
    "KNOWS": "knows",
    "RELATED_TO": "is related to",
    "CAUSED": "caused",
    "HAS_NAME": "is named",
    "HAS_COLOR": "has the colour",
    "HAS_POPULATION": "has a population of",
    "HAS_AREA": "has an area of",
    "IS_HAUNTED": "is haunted",
    "HAS_ALIGNMENT": "has the alignment",
    "HAS_STATUS": "has the status",
    "HAS_DETAIL": "has the detail",
}


def phrase(predicate: str) -> str:
    return PHRASES.get(predicate, predicate.replace("_", " ").lower())


__all__ = ["PHRASES", "phrase"]
