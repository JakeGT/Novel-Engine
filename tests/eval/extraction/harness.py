"""Reading prose into suggested changes, scored against labelled passages (EXT-008).

Each labelled passage is added as a text and read through the API, exactly as the
author's "Read with the assistant" does. What comes back is the set of suggested
changes waiting for review, and the guesses about who a phrase means; they are
turned back into (subject, kind of fact, object) by name and scored with the existing
scorer (``backend.devtools.intake_eval.score_facts``), overall and by the kind of
reading each expected item needs.

In CI the model is the scripted one, replaying an answer written by hand for each
case (``recorded``), mistakes and all. That exercises the harness and the reading
pipeline's own filtering (an invented quote is dropped), and pins the numbers, but
measures no model. ``make eval-model`` runs the same cases against the configured
provider instead, after the person confirms the spend; that is the only measurement
of a model's reading, and it never runs in CI.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from backend.devtools.intake_eval import score_facts
from backend.domain.canon.naming import normalize_name
from backend.services.intake_workflow import PassageDraft
from tests.contract.test_canon_entities import _project_id, _publish_schema
from tests.eval.report import ratio

CASES = Path(__file__).with_name("cases.json")

Answer = Callable[[dict[str, Any], dict[str, str]], None]


def load() -> dict[str, Any]:
    return json.loads(CASES.read_text(encoding="utf-8"))


def _head(client, base: str) -> int:
    return int(client.get(f"{base}/projection").json()["head_revision"])


def build(client, container, entries: list[dict[str, Any]]) -> dict[str, str]:
    project_id = _project_id(container)
    _publish_schema(client, project_id)
    base = f"/api/v1/projects/{project_id}"
    ids = {"base": base}
    for entry in entries:
        made = client.post(
            f"{base}/entities",
            json={
                "type": entry["type"],
                "name": entry["name"],
                "aliases": entry.get("aliases", []),
                "expected_revision": _head(client, base),
            },
        )
        assert made.status_code == 201, made.text
        ids[entry["key"]] = made.json()["id"]
    return ids


def recorded_draft(case: dict[str, Any], ids: dict[str, str]) -> PassageDraft:
    """The hand-written answer, with each ``@key`` replaced by that entry's id."""

    def swap(value: Any) -> Any:
        if isinstance(value, str) and value.startswith("@"):
            return ids[value[1:]]
        if isinstance(value, list):
            return [swap(item) for item in value]
        if isinstance(value, dict):
            return {key: swap(item) for key, item in value.items()}
        return value

    return PassageDraft.model_validate(swap(case["recorded"]))


def replay(container) -> Answer:
    def answer(case: dict[str, Any], ids: dict[str, str]) -> None:
        container.ai.queue(recorded_draft(case, ids))

    return answer


def _object_name(obj: dict[str, Any], names: dict[str, str]) -> str:
    if obj.get("kind") == "entity_ref":
        return names.get(obj.get("entity_id", ""), "?")
    return str(obj.get("value", ""))


def drafted(client, base: str, mutation_id: str | None, names: dict[str, str]) -> dict[str, Any]:
    """The suggested changes, by name."""
    found: dict[str, Any] = {"entities": [], "facts": [], "beliefs": []}
    if mutation_id is None:
        return found
    detail = client.get(f"{base}/mutations/{mutation_id}").json()
    operations = detail["operations"]
    known = dict(names)
    for operation in operations:
        if operation["operation_type"] == "create_entity":
            payload = operation["payload"]
            known[payload["id"]] = payload["name"]
            found["entities"].append(payload["name"])
    for operation in operations:
        payload = operation["payload"]
        if operation["operation_type"] == "assert_fact":
            proposition = payload["proposition"]
            found["facts"].append(
                (
                    known.get(proposition["subject_id"], "?"),
                    proposition["predicate"],
                    _object_name(proposition["object"], known),
                )
            )
        elif operation["operation_type"] == "assert_belief":
            proposition = payload["proposition"]
            found["beliefs"].append(
                (
                    known.get(payload["holder_entity_id"], "?"),
                    proposition["predicate"],
                    known.get(proposition["subject_id"], "?")
                    + " > "
                    + _object_name(proposition["object"], known),
                )
            )
    return found


def _norm(items) -> set[tuple[str, ...]]:
    return {tuple(normalize_name(part) for part in item) for item in items}


def score_case(case: dict[str, Any], got: dict[str, Any]) -> dict[str, Any]:
    expected = case["expected"]
    facts = [tuple(item[:3]) for item in expected["facts"]]
    beliefs = [(h, p, f"{s} > {o}") for h, s, p, o, _kind in expected["beliefs"]]
    references = [tuple(item[:2]) for item in expected["references"]]
    not_facts = _norm(tuple(item[:3]) for item in expected["not_facts"])
    drafted_facts = _norm(got["facts"])
    drafted_beliefs = _norm(got["beliefs"])
    drafted_refs = _norm(got["references"])
    drafted_entities = {normalize_name(name) for name in got["entities"]}

    kinds: list[tuple[str, bool]] = []
    for subject, predicate, obj, kind in expected["facts"]:
        kinds.append(
            (
                kind,
                (normalize_name(subject), predicate.casefold(), normalize_name(obj))
                in {(s, p.casefold(), o) for s, p, o in drafted_facts},
            )
        )
    for holder, subject, predicate, obj, kind in expected["beliefs"]:
        key = (normalize_name(holder), predicate.casefold(), normalize_name(f"{subject} > {obj}"))
        kinds.append((kind, key in {(h, p.casefold(), o) for h, p, o in drafted_beliefs}))
    for phrase, name, kind in expected["references"]:
        kinds.append((kind, (normalize_name(phrase), normalize_name(name)) in drafted_refs))
    for name, kind in expected["entities"]:
        kinds.append((kind, normalize_name(name) in drafted_entities))
    said_as_fact = sorted(" ".join(item) for item in drafted_facts & not_facts)
    return {
        "id": case["id"],
        "facts": score_facts(list(got["facts"]), facts),
        "beliefs": score_facts(list(got["beliefs"]), beliefs),
        "references": score_facts(
            [(phrase, "means", name) for phrase, name in got["references"]],
            [(phrase, "means", name) for phrase, name in references],
        ),
        "kinds": kinds,
        "not_facts_made_facts": said_as_fact,
        "discarded": got["discarded"],
    }


def read_case(
    client, ids: dict[str, str], case: dict[str, Any], answer: Answer, names: dict[str, str]
) -> dict[str, Any]:
    base = ids["base"]
    made = client.post(f"{base}/texts", json={"title": case["id"], "content": case["text"]})
    assert made.status_code == 201, made.text
    source_id = made.json()["id"]
    answer(case, ids)
    read = client.post(
        f"{base}/assist/texts/{source_id}/read",
        json={"expected_revision": _head(client, base)},
    )
    assert read.status_code == 200, read.text
    body = read.json()
    got = drafted(client, base, body["mutation_id"], names)
    text = client.get(f"{base}/texts/{source_id}").json()
    got["references"] = [
        (question["phrase"], names.get(candidate, "?"))
        for question in text["questions"]
        if question.get("guessed")
        for candidate in question["candidates"]
    ]
    got["discarded"] = len(body["discarded"])
    return score_case(case, got)


# What one reading sends besides the passage (instructions, the story bible's listing)
# and the most it answers with, in tokens: a deliberate over-estimate for asking first.
_PROMPT_TOKENS = 2_500
_ANSWER_TOKENS = 2_000


def estimate_tokens() -> int:
    """About how many tokens reading every case with a real model uses, at most."""
    return sum(len(case["text"]) // 4 + _PROMPT_TOKENS + _ANSWER_TOKENS for case in load()["cases"])


def _total(results: list[dict[str, Any]], part: str) -> dict[str, Any]:
    drafted_ = sum(item[part]["drafted"] for item in results)
    expected = sum(item[part]["expected"] for item in results)
    correct = sum(item[part]["correct"] for item in results)
    return {
        "drafted": drafted_,
        "expected": expected,
        "correct": correct,
        "precision": ratio(correct, drafted_),
        "recall": ratio(correct, expected),
    }


def evaluate(client, container, answer: Answer | None = None, *, model: str) -> dict[str, Any]:
    data = load()
    ids = build(client, container, data["entries"])
    names = {ids[entry["key"]]: entry["name"] for entry in data["entries"]}
    chosen = answer or replay(container)
    results = [read_case(client, ids, case, chosen, names) for case in data["cases"]]
    by_kind: dict[str, dict[str, Any]] = {}
    for result in results:
        for kind, hit in result["kinds"]:
            slot = by_kind.setdefault(kind, {"labelled": 0, "found": 0})
            slot["labelled"] += 1
            slot["found"] += int(hit)
    for slot in by_kind.values():
        slot["recall"] = ratio(slot["found"], slot["labelled"])
    return {
        "set": data["version"],
        "model": model,
        "cases": len(results),
        "facts": _total(results, "facts"),
        "beliefs": _total(results, "beliefs"),
        "references": _total(results, "references"),
        "by_kind": dict(sorted(by_kind.items())),
        "said_or_near_made_facts": sum(len(item["not_facts_made_facts"]) for item in results),
        "discarded_by_reading": sum(item["discarded"] for item in results),
        "per_case": [
            {key: value for key, value in item.items() if key != "kinds"} for item in results
        ],
    }


__all__ = [
    "CASES",
    "build",
    "estimate_tokens",
    "evaluate",
    "load",
    "recorded_draft",
    "replay",
    "score_case",
]
