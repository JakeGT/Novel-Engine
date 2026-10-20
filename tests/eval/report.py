"""Where an evaluation puts its numbers, and the arithmetic every suite shares.

A suite calls ``record(suite, measured)`` with a JSON-safe dict. Under ``make eval``
the runner sets ``NOVEL_EVAL_OUT`` to a directory and each record is written there
as ``<suite>.json``; under plain pytest nothing is written. Records hold counts,
ratios, timings, case ids and record ids only: never prose, captions or secrets.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

ENV = "NOVEL_EVAL_OUT"


def ratio(numerator: int, denominator: int) -> float:
    """A rate rounded for reports; an empty denominator is a perfect score (nothing to miss)."""
    return round(numerator / denominator, 3) if denominator else 1.0


def record(suite: str, measured: dict[str, Any]) -> Path | None:
    """Write one suite's numbers for the runner, when it asked for them."""
    out = os.environ.get(ENV)
    if not out:
        return None
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{suite}.json"
    path.write_text(json.dumps(measured, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def timed[T](call: Callable[[], T]) -> tuple[T, float]:
    """The result and how long it took, in milliseconds."""
    started = time.perf_counter()
    result = call()
    return result, (time.perf_counter() - started) * 1000


def latency(samples_ms: Sequence[float]) -> dict[str, float | int]:
    """Median, slowest and count of some timings. Machine-dependent: reported, never pinned."""
    if not samples_ms:
        return {"runs": 0, "median_ms": 0.0, "max_ms": 0.0}
    return {
        "runs": len(samples_ms),
        "median_ms": round(statistics.median(samples_ms), 3),
        "max_ms": round(max(samples_ms), 3),
    }


def confusion(outcomes: Iterable[tuple[bool, bool]]) -> dict[str, float | int]:
    """(labelled a defect, flagged) pairs as counts, precision, recall and F1."""
    tp = fp = fn = tn = 0
    for defect, flagged in outcomes:
        if defect and flagged:
            tp += 1
        elif defect:
            fn += 1
        elif flagged:
            fp += 1
        else:
            tn += 1
    precision = ratio(tp, tp + fp)
    recall = ratio(tp, tp + fn)
    f1 = round(2 * precision * recall / (precision + recall), 3) if precision + recall else 0.0
    return {
        "defects": tp + fn,
        "clean": fp + tn,
        "true_positive": tp,
        "false_negative": fn,
        "false_positive": fp,
        "true_negative": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_positive_rate": ratio(fp, fp + tn) if fp + tn else 0.0,
    }


def recall_at(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 1.0
    return ratio(len(set(ranked[:k]) & relevant), len(relevant))


def reciprocal_rank(ranked: Sequence[str], relevant: set[str]) -> float:
    for position, key in enumerate(ranked, start=1):
        if key in relevant:
            return round(1 / position, 3)
    return 0.0


__all__ = [
    "ENV",
    "confusion",
    "latency",
    "ratio",
    "recall_at",
    "reciprocal_rank",
    "record",
    "timed",
]
