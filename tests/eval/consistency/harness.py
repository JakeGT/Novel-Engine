"""Run the labelled consistency cases and score the deterministic checks (CON-012).

Measured per family and overall: detection (recall over labelled defects), false
positives (flags on clean near misses), precision, leakage recall (defects about a
secret or later knowledge reaching a page or a context), latency per check call and
on a larger generated story, and cost (no model is called: zero tokens).

What this does not measure: how a real model's notes compare, how the checks do on an
author's real manuscript, or how often the defects here occur in practice. The cases
are hand-written near misses chosen to probe each check's edges; the rates describe
this set, not a population.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.domain.continuity import HowLong, StoryParts, continuity_findings
from backend.domain.drafting import DraftBrief, SecretToGuard, check_draft
from backend.domain.rules import (
    Check,
    Pattern,
    RuleBody,
    Selector,
    World,
    WorldEntry,
    WorldFact,
    violations,
)
from backend.domain.worldtime import At
from tests.eval.consistency.cases import Case, all_cases
from tests.eval.consistency.story import entities, event, fact, scene
from tests.eval.report import confusion, latency, timed

SET_VERSION = "saltglass-consistency/1"


def run_case(case: Case) -> dict[str, Any]:
    found, ms = timed(case.run)
    flagged = bool(found & case.codes)
    return {
        "id": case.id,
        "family": case.family,
        "defect": case.defect,
        "flagged": flagged,
        "agrees": flagged == case.defect,
        "known_limit": case.known_limit is not None,
        "suspected_bug": case.suspected_bug is not None,
        "leakage": "leakage" in case.tags,
        "codes": sorted(found),
        "ms": ms,
    }


def _scale() -> dict[str, Any]:
    """The checks on a story far larger than any case: timings only, never pinned."""
    people = [f"p{n}" for n in range(60)]
    places = [f"place{n}" for n in range(12)]
    extra = [
        *(entity_like(key, "Character") for key in people),
        *(entity_like(key, "Location") for key in places),
    ]
    scenes = [
        scene(
            f"sc{n}",
            At(at=n),
            place=places[n % len(places)],
            who=[people[n % len(people)], people[(n * 7) % len(people)]],
            position=n,
        )
        for n in range(300)
    ]
    facts = [
        fact(
            f"f{n}", people[n % len(people)], "LOCATED_AT", places[n % len(places)], starts=At(at=n)
        )
        for n in range(400)
    ]
    events = [event(f"e{n}", At(at=n * 3), who=[people[n % len(people)]]) for n in range(100)]
    parts = StoryParts(
        entities=entities(extra), facts=facts, events=events, scenes=scenes, how_long=HowLong()
    )
    _continuity, continuity_ms = timed(lambda: continuity_findings(parts))

    world = World(
        entries={key: WorldEntry(key, "Character", key) for key in people * 1},
        facts={
            f"w{n}": WorldFact(f"w{n}", people[n % len(people)], "MEMBER_OF", f"guild{n % 9}")
            for n in range(2000)
        },
    )
    body = RuleBody(
        applies_to=Selector(type="Character"),
        checks=[Check(kind="at_most", pattern=Pattern(predicate="MEMBER_OF"), count=3)],
    )
    _rules, rules_ms = timed(lambda: violations("r", body, world, limit=None))

    paragraph = (
        "Wren poled the ferry across the Low Fen while the bell at Marrow Light rang the "
        "turn of the tide, and Osric watched from the gallery with the lamp trimmed low."
    )
    text = "\n\n".join([paragraph] * 160)  # about 5,000 words
    brief = DraftBrief(
        point_of_view="Wren Halloway",
        point_of_view_names=("Wren",),
        secrets=[SecretToGuard(f"secret {n}", (f"Name{n}", f"Other{n}")) for n in range(40)],
    )
    _draft, draft_ms = timed(lambda: check_draft(text, brief, []))
    return {
        "continuity": {"scenes": 300, "facts": 400, "events": 100, "ms": round(continuity_ms, 3)},
        "rules": {"entries": len(people), "facts": 2000, "ms": round(rules_ms, 3)},
        "drafting": {"words": len(text.split()), "secrets": 40, "ms": round(draft_ms, 3)},
    }


def entity_like(key: str, kind: str):
    from backend.domain.canon.entities import CanonEntity
    from tests.eval.consistency.story import SCOPE

    return CanonEntity(
        id=key, type=kind, name=key.title(), schema_version_id="eval", created_revision=1, **SCOPE
    )


def evaluate() -> dict[str, Any]:
    cases = all_cases()
    results = [run_case(case) for case in cases]
    families: dict[str, Any] = {}
    for family in sorted({item["family"] for item in results}):
        mine = [item for item in results if item["family"] == family]
        families[family] = {
            **confusion((item["defect"], item["flagged"]) for item in mine),
            "latency": latency([item["ms"] for item in mine]),
        }
    leakage = [item for item in results if item["leakage"]]
    disagreements = sorted(item["id"] for item in results if not item["agrees"])
    return {
        "set": SET_VERSION,
        "cases": len(results),
        "overall": confusion((item["defect"], item["flagged"]) for item in results),
        "families": families,
        "leakage": {
            "cases": len(leakage),
            "caught": sum(1 for item in leakage if item["flagged"]),
            "recall": confusion((True, item["flagged"]) for item in leakage)["recall"],
        },
        # Every case the checks get wrong, and whether that is a stated limit.
        "disagreements": disagreements,
        "unexplained": sorted(
            item["id"]
            for item in results
            if not item["agrees"] and not item["known_limit"] and not item["suspected_bug"]
        ),
        "known_limits": {
            case.id: case.known_limit for case in cases if case.known_limit is not None
        },
        "suspected_bugs": {
            case.id: case.suspected_bug for case in cases if case.suspected_bug is not None
        },
        "missed": sorted(item["id"] for item in results if item["defect"] and not item["flagged"]),
        "false_positives": sorted(
            item["id"] for item in results if not item["defect"] and item["flagged"]
        ),
        "cost_tokens": 0,
        "latency": latency([item["ms"] for item in results]),
        "scale": _scale(),
        "per_case": [
            {key: value for key, value in item.items() if key != "ms"} for item in results
        ],
    }


__all__ = ["BASELINE", "SET_VERSION", "evaluate", "pinned", "run_case"]


BASELINE = Path(__file__).with_name("baseline.json")


def pinned(measured: dict[str, Any]) -> dict[str, Any]:
    """The numbers a change must move on purpose: counts and rates, never timings."""
    return {
        "set": measured["set"],
        "cases": measured["cases"],
        "overall": measured["overall"],
        "families": {
            family: {key: value for key, value in numbers.items() if key != "latency"}
            for family, numbers in measured["families"].items()
        },
        "leakage": measured["leakage"],
        "missed": measured["missed"],
        "false_positives": measured["false_positives"],
    }


if __name__ == "__main__":
    # Regenerate the pinned numbers after a deliberate change to a check or a case:
    # uv run python -m tests.eval.consistency.harness
    BASELINE.write_text(json.dumps(pinned(evaluate()), indent=2, sort_keys=True) + "\n")
    print(f"wrote {BASELINE}")
