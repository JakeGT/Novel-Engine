"""A short sample story, so a newcomer can see what a filled-in story bible looks like.

It is an original, deliberately small world: enough to show entries of several
types, tags and importance, facts read as sentences, an entry still waiting to be
filled in, and a belief that disagrees with what is true. It is added by the person
who asks for it, as one ordinary version of an empty project.
"""

from __future__ import annotations

from backend.adapters.system import uuid7
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.marks import marks
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.operations import (
    AssertBeliefOp,
    AssertFactOp,
    CanonOperation,
    CreateEntityOp,
)

SAMPLE_SOURCE = "sample-story"

# key → (type, name, aliases, details, tags, importance, fill in later)
_ENTRIES: dict[str, tuple[str, str, list[str], dict[str, str], list[str], str, bool]] = {
    "ines": (
        "Character",
        "Ines Varga",
        ["the mapmaker"],
        {
            "role": "cartographer of the coast",
            "temperament": "stubborn, exact, slow to trust",
            "wants": "to chart the shoals her mother died on",
        },
        ["protagonist"],
        "main",
        False,
    ),
    "benedek": (
        "Character",
        "Benedek Varga",
        ["Old Benedek"],
        {"role": "keeper of the Kestrel Point light", "temperament": "gentle, evasive"},
        ["Ines’s father", "mentor"],
        "supporting",
        False,
    ),
    "rooke": (
        "Character",
        "Captain Rooke",
        [],
        {"role": "master of the Marigold", "temperament": "charming, patient"},
        ["antagonist", "smuggler"],
        "supporting",
        False,
    ),
    "amsel": ("Character", "Dr. Amsel", [], {}, ["Ines’s teacher"], "minor", True),
    "point": (
        "Location",
        "Kestrel Point",
        ["the Point"],
        {"description": "a headland with a lighthouse, an hour’s walk from town"},
        ["home"],
        "supporting",
        False,
    ),
    "port": (
        "Location",
        "Port Ardent",
        [],
        {"description": "a harbour town that lives on what the tide brings in"},
        [],
        "supporting",
        False,
    ),
    "wardens": (
        "Faction",
        "The Tide Wardens",
        ["the Wardens"],
        {"purpose": "keep the lights and the charts of the coast"},
        [],
        "minor",
        False,
    ),
    "clock": (
        "Object",
        "The brass tide-clock",
        [],
        {"description": "tells the hour of the next low water; it has never been wrong"},
        ["heirloom"],
        "minor",
        False,
    ),
}

# (who or what, kind of fact, what it points to)
_FACTS = [
    ("ines", "LOCATED_AT", "point"),
    ("benedek", "LOCATED_AT", "point"),
    ("rooke", "LOCATED_AT", "port"),
    ("ines", "RELATED_TO", "benedek"),
    ("ines", "KNOWS", "rooke"),
    ("ines", "KNOWS", "amsel"),
    ("benedek", "MEMBER_OF", "wardens"),
    ("benedek", "OWNS", "clock"),
]

# (who believes it, about whom, kind of fact, what, how sure)
_BELIEFS = [
    # Rooke thinks the keeper has gone into town; he has not. The page shows the mismatch.
    ("rooke", "benedek", "LOCATED_AT", "port", 0.75),
]


def sample_operations(project_id: str) -> list[CanonOperation]:
    ids = {key: format_canon_id("entity", str(uuid7())) for key in _ENTRIES}

    def points_to(subject: str, predicate: str, target: str) -> Proposition:
        return Proposition(
            subject_id=ids[subject],
            predicate=predicate,
            object=EntityRefObject(entity_id=ids[target], project_id=project_id),
        )

    operations: list[CanonOperation] = [
        CreateEntityOp(
            id=ids[key],
            type=entity_type,
            name=name,
            aliases=aliases,
            attributes={
                **details,
                **marks(tags=tags, importance=importance, needs_detail=later),
            },
        )
        for key, (entity_type, name, aliases, details, tags, importance, later) in _ENTRIES.items()
    ]
    operations.extend(
        AssertFactOp(
            id=format_canon_id("fact", str(uuid7())),
            proposition=points_to(subject, predicate, target),
        )
        for subject, predicate, target in _FACTS
    )
    operations.extend(
        AssertBeliefOp(
            id=format_canon_id("belief", str(uuid7())),
            holder_entity_id=ids[holder],
            proposition=points_to(subject, predicate, target),
            confidence=confidence,
            source_ref=SAMPLE_SOURCE,
        )
        for holder, subject, predicate, target, confidence in _BELIEFS
    )
    return operations


__all__ = ["SAMPLE_SOURCE", "sample_operations"]
