"""Measure how well names are found in a labelled passage.

Finding names is deterministic, so this needs no model and gives the same
numbers every time. It reads a labelled passage (``{{key:kind|words}}`` marks
where an entry is meant), runs the same name-finding the application uses, and
reports precision and recall, with recall broken down by how the entry was
referred to. Kinds a deterministic reader cannot be expected to find (a first
name alone, an unanswered description, a pronoun) are reported, never hidden:
they are what a model, or the author's answers, still have to supply.

    python -m backend.devtools.intake_eval tests/fixtures/intake_eval/lantern_coast.json

It also scores a set of drafted facts against expected ones, for use when a
real model's reading is being measured. No model is called from here.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.naming import normalize_name
from backend.domain.sources import (
    AliasBinding,
    MentionStatus,
    Verdict,
    build_index,
    find_mentions,
)

RESOLVER_VERSION = "names/1"
# What explicit marks, names, aliases and answered phrases cover. The rest needs more.
DETERMINISTIC_KINDS = ("mark", "name", "alias", "answered")
_GOLD = re.compile(r"\{\{(?P<key>[a-z0-9_]+):(?P<kind>[a-z]+)\|(?P<words>[^{}]+)\}\}")
_FIXED = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class GoldSpan:
    start: int
    end: int
    key: str
    kind: str


def parse_labelled(parts: list[str]) -> tuple[str, list[GoldSpan]]:
    """The plain text, and where each labelled reference sits in it."""
    labelled = "".join(parts)
    text: list[str] = []
    gold: list[GoldSpan] = []
    at = 0
    length = 0
    for match in _GOLD.finditer(labelled):
        before = labelled[at : match.start()]
        text.append(before)
        length += len(before)
        words = match.group("words")
        gold.append(GoldSpan(length, length + len(words), match.group("key"), match.group("kind")))
        text.append(words)
        length += len(words)
        at = match.end()
    text.append(labelled[at:])
    return "".join(text), gold


def _entity(entry: dict[str, Any]) -> CanonEntity:
    return CanonEntity(
        id=f"ent_{entry['key']}",
        workspace_id="eval",
        project_id="eval",
        branch_id="main",
        type=entry["type"],
        name=entry["name"],
        schema_version_id="eval",
        created_revision=1,
        aliases=list(entry.get("aliases", [])),
    )


def _binding(number: int, answer: dict[str, Any]) -> AliasBinding:
    return AliasBinding(
        workspace_id="eval",
        project_id="eval",
        id=f"alb_{number}",
        phrase=answer["phrase"],
        normalized=normalize_name(answer["phrase"]),
        verdict=Verdict(answer["verdict"]),
        entity_id=f"ent_{answer['key']}" if answer.get("key") else None,
        source_id=None,
        decided_by=None,
        decided_at=_FIXED,
    )


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 3) if denominator else 1.0


def evaluate_names(case: dict[str, Any]) -> dict[str, Any]:
    """Precision and recall of name-finding on one labelled passage."""
    text, gold = parse_labelled(case["text"])
    index = build_index(
        [_entity(entry) for entry in case["entries"]],
        [_binding(number, answer) for number, answer in enumerate(case.get("answers", []))],
        source_id=None,
    )
    found = {
        (mention.start, mention.end): mention.entity_ids[0]
        for mention in find_mentions(text, index)
        if mention.status is MentionStatus.KNOWN
    }
    wanted = {(span.start, span.end): span for span in gold}
    right = [
        place
        for place, entity_id in found.items()
        if place in wanted and entity_id == f"ent_{wanted[place].key}"
    ]
    kinds: dict[str, dict[str, Any]] = {}
    for kind in sorted({span.kind for span in gold}):
        of_kind = [span for span in gold if span.kind == kind]
        hit = sum(1 for span in of_kind if (span.start, span.end) in right)
        kinds[kind] = {"labelled": len(of_kind), "found": hit, "recall": _ratio(hit, len(of_kind))}
    expected = [span for span in gold if span.kind in DETERMINISTIC_KINDS]
    expected_hit = sum(1 for span in expected if (span.start, span.end) in right)
    wrong = sorted(text[start:end] for (start, end) in found if (start, end) not in right)
    return {
        "case": case["version"],
        "resolver": RESOLVER_VERSION,
        "labelled": len(gold),
        "found": len(found),
        "correct": len(right),
        "precision": _ratio(len(right), len(found)),
        "recall": _ratio(len(right), len(gold)),
        # Recall over what explicit marks, names, aliases and answers can cover at all.
        "recall_without_a_model": _ratio(expected_hit, len(expected)),
        "by_kind": kinds,
        "wrongly_found": wrong,
    }


Triple = tuple[str, str, str]


def score_facts(drafted: list[Triple], expected: list[Triple]) -> dict[str, Any]:
    """Precision and recall of drafted (subject, kind of fact, object) against expected ones."""

    def key(item: Triple) -> Triple:
        return (normalize_name(item[0]), item[1], normalize_name(item[2]))

    drafted_keys = {key(item) for item in drafted}
    expected_keys = {key(item) for item in expected}
    right = drafted_keys & expected_keys
    return {
        "drafted": len(drafted_keys),
        "expected": len(expected_keys),
        "correct": len(right),
        "precision": _ratio(len(right), len(drafted_keys)),
        "recall": _ratio(len(right), len(expected_keys)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("case", type=Path, help="A labelled passage (JSON)")
    arguments = parser.parse_args(argv)
    result = evaluate_names(json.loads(arguments.case.read_text(encoding="utf-8")))
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
