"""The deterministic bundle prototype against full-context and vector-only bundling (CON-013).

No model is needed: what is compared is what each strategy would *show* a critic.
For every labelled defect, a strategy covers it when one group shows all of its
passages and all of the records needed to see it at once (``seen_together``). Also
reported: tokens in all, groups, the largest group, passages read more than once,
secrets shown (and in how many groups), and every defect omitted with the records
missing.

What this does not measure: whether a model would actually notice a defect it is
shown, or raise false ones. A covered defect is necessary, not sufficient, for a
critic to find it. The vector-only baseline ranks records by word overlap with the
passage, a deterministic stand-in for an embedding index: a real embedding model may
rank differently, better on paraphrase and worse on exact names.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.domain.review_bundles import (
    INSTRUCTION_TOKENS,
    BundlePlan,
    Evidence,
    ReviewUnit,
    missing_for,
    nearest_evidence,
    plan_bundles,
    preview,
    review_units,
    seen_together,
    single_context,
)
from tests.eval.consistency.manuscript import (
    DEFECTS,
    RULES,
    TYPES,
    evidence,
    mentions,
    scene_texts,
)

SET_VERSION = "saltglass-bundles/1"
# The smallest budget a group may have (instructions and one passage), one that makes
# full context split, and one it fits in whole.
BUDGETS = {"floor": 1_000, "tight": 1_400, "roomy": 20_000}
NEAREST_K = 4

_WORD = re.compile(r"[a-z]+")
_STOP = frozenset(
    "the a an and of to in at on by for from with as is was were be been her his he she "
    "it its they them their that this there then than so not no nor but or into over "
    "under out up down all any each every own said say says who what when where which "
    "had has have did does do would could should may might must one two".split()
)


def _words(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if word not in _STOP and len(word) > 2}


def lexical(
    texts: dict[str, str], words: dict[str, str]
) -> Callable[[ReviewUnit, Evidence], float]:
    """Word overlap (Jaccard) between a passage and a record: the vector stand-in."""
    unit_words = {unit_id: _words(text) for unit_id, text in texts.items()}
    record_words = {key: _words(text) for key, text in words.items()}

    def similarity(unit: ReviewUnit, item: Evidence) -> float:
        a, b = unit_words[unit.id], record_words[item.key]
        return len(a & b) / len(a | b) if a | b else 0.0

    return similarity


def corpus() -> tuple[list[ReviewUnit], dict[str, str], dict[str, list[str]]]:
    """Every scene's units, each unit's words, and each defect's unit ids."""
    units: list[ReviewUnit] = []
    texts: dict[str, str] = {}
    by_scene: dict[str, tuple[str, list[ReviewUnit]]] = {}
    for frame, text in scene_texts():
        mine = review_units(text, frame, mentions(text), TYPES, rules=RULES)
        units.extend(mine)
        by_scene[frame.scene_id] = (text, mine)
        texts.update({unit.id: text[unit.start : unit.end] for unit in mine})
    located: dict[str, list[str]] = {}
    for defect in DEFECTS:
        ids: list[str] = []
        for scene_id, phrase in defect.passages:
            text, mine = by_scene[scene_id]
            at = text.index(phrase)
            ids.append(next(unit.id for unit in mine if unit.start <= at < unit.end))
        located[defect.id] = ids
    return units, texts, located


def _measure(plan: BundlePlan, located: dict[str, list[str]]) -> dict[str, Any]:
    readings: dict[str, int] = {}
    for bundle in plan.bundles:
        for unit_id in bundle.unit_ids:
            readings[unit_id] = readings.get(unit_id, 0) + 1
    covered: list[str] = []
    omitted: dict[str, list[str]] = {}
    for defect in DEFECTS:
        units = located[defect.id]
        if seen_together(plan, units, defect.records):
            covered.append(defect.id)
        else:
            # No group shows all the passages at once: say so rather than list records.
            together = any(set(units) <= set(bundle.unit_ids) for bundle in plan.bundles)
            omitted[defect.id] = (
                sorted(missing_for(plan, units, defect.records))
                if together
                else ["(its passages are never shown together)"]
            )
    secrets = [bundle for bundle in plan.bundles if bundle.author_only]
    return {
        "strategy": plan.strategy,
        "groups": len(plan.bundles),
        "tokens": plan.tokens,
        # Passages and records only, without each group's fixed instructions.
        "content_tokens": plan.tokens - INSTRUCTION_TOKENS * len(plan.bundles),
        "largest_group": max((bundle.tokens for bundle in plan.bundles), default=0),
        "passages": len(plan.units),
        "passages_read_more_than_once": sum(1 for count in readings.values() if count > 1),
        "readings": sum(readings.values()),
        "every_passage_read": all(unit.id in readings for unit in plan.units),
        "coverage_complete": plan.coverage.complete,
        "groups_showing_a_secret": len(secrets),
        "defects": len(DEFECTS),
        "defects_covered": len(covered),
        "coverage_of_defects": round(len(covered) / len(DEFECTS), 3),
        "covered": covered,
        "omitted": omitted,
        "warnings": len(plan.warnings),
    }


def evaluate() -> dict[str, Any]:
    units, texts, located = corpus()
    records, words = evidence()
    similarity = lexical(texts, words)
    results: dict[str, Any] = {}
    for name, budget in BUDGETS.items():
        deterministic = plan_bundles(units, records, budget=budget)
        results[name] = {
            "budget": budget,
            "deterministic": _measure(deterministic, located),
            "full_context": _measure(single_context(units, records, budget=budget), located),
            "vector_only": _measure(
                nearest_evidence(units, records, similarity, k=NEAREST_K, budget=budget), located
            ),
            "preview": list(preview(deterministic).lines[:3]),
        }
    no_graph = plan_bundles(units, records, budget=BUDGETS["floor"], graph=False)
    return {
        "set": SET_VERSION,
        "passages": len(units),
        "records": len(records),
        "defects": len(DEFECTS),
        "nearest_k": NEAREST_K,
        "budgets": results,
        "fallback_without_graph": _measure(no_graph, located),
        "cost_tokens": 0,
    }


__all__ = ["BASELINE", "BUDGETS", "SET_VERSION", "corpus", "evaluate", "lexical", "pinned"]


BASELINE = Path(__file__).with_name("bundles_baseline.json")


def pinned(measured: dict[str, Any]) -> dict[str, Any]:
    """Everything here is deterministic; the preview's words are left out."""
    return {
        **measured,
        "budgets": {
            name: {key: value for key, value in numbers.items() if key != "preview"}
            for name, numbers in measured["budgets"].items()
        },
    }


if __name__ == "__main__":
    # uv run python -m tests.eval.consistency.bundles
    BASELINE.write_text(json.dumps(pinned(evaluate()), indent=2, sort_keys=True) + "\n")
    print(f"wrote {BASELINE}")
