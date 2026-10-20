"""What a scene's context gives, against hand labels (CTX-010).

For each labelled scene the context service is asked, through the API, what it would
give the assistant. Each item is mapped back to the story record it comes from (a fact
given as true, as something the viewpoint knows, or as something the reader was shown
is the same record). Labels say which records a scene needs (relevant), which must
never be given (forbidden: a secret the viewpoint does not hold, knowledge learned
later, a fact no longer true then, a fact from another line of the story), and which
are about nothing in the scene (distractors). Anything else given is neither.

Measured: Recall@5, Recall@10 and recall over everything given (ranked by the
service's own score), mean reciprocal rank, precision over labelled items, leaks (by
record and by the words of the rendered context), omissions and whether each was said
(left out with a reason) or silent, distractors given, and what-if correctness.

What this does not measure: whether a model writes better with this context, or how
the ranking does on a large real story. Search by meaning is off (no embedding model
in tests), so passages are matched by name only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tests.eval.report import ratio, recall_at, reciprocal_rank
from tests.eval.retrieval.story import build, context

SET_VERSION = "saltglass-retrieval/1"
# Items that are always given and say which scene this is: not ranked.
_FRAME = ("scene", "plan", "intent")
# The same record, given in different sections.
_SAME_RECORD = {"fact": "fact", "knows": "fact", "reader": "fact"}


@dataclass(frozen=True)
class Labelled:
    id: str
    scene: str
    relevant: frozenset[str]
    forbidden: frozenset[str] = frozenset()
    distractors: frozenset[str] = frozenset()
    # Words that must not be anywhere in what is rendered for the assistant.
    forbidden_words: tuple[str, ...] = ()
    branch: bool = False
    budget: int = 12_000
    # A passage of writing that is relevant, by words it contains.
    passages: tuple[str, ...] = ()
    about: str = ""
    tags: frozenset[str] = field(default_factory=frozenset)


_CORIN = frozenset(
    {
        "fact:corin_at_mill",
        "fact:corin_lamps",
        "fact:corin_bell",
        "entity:corin",
        "entity:mill",
        "entity:bell",
        "entity:lamps",
    }
)
_OSRIC_SECRET = "Osric Pell is a member of the Tidewardens"
_KIN = "Ada Quill is related to Osric Pell"

LABELS: list[Labelled] = [
    Labelled(
        "crossing",
        "scene_crossing",
        about="Wren's crossing (year 20): Bram is with her; Osric's oath is not hers yet.",
        relevant=frozenset(
            {
                "entity:wren",
                "entity:quay",
                "entity:bram",
                "fact:wren_at_quay",
                "fact:wren_knows_bram",
                "fact:bram_warden",
            }
        ),
        passages=("Wren Halloway untied the ferry",),
        forbidden=frozenset(
            {"fact:osric_warden", "fact:ada_kin", "fact:bram_at_fen_once", "fact:osric_stays"}
        ),
        forbidden_words=(_OSRIC_SECRET, _KIN),
        distractors=_CORIN,
        tags=frozenset({"leakage", "time"}),
    ),
    Labelled(
        "light",
        "scene_light",
        about="Osric's watch (year 25): his own oath is his to know; his sister is not.",
        relevant=frozenset(
            {
                "entity:osric",
                "entity:light",
                "entity:wren",
                "fact:osric_at_light",
                "fact:osric_warden",
                "fact:wren_at_quay",
            }
        ),
        forbidden=frozenset({"fact:ada_kin", "fact:osric_stays"}),
        forbidden_words=(_KIN,),
        distractors=_CORIN,
        tags=frozenset({"leakage"}),
    ),
    Labelled(
        "house",
        "scene_house",
        about="Ada's evening (year 30): she knows her brother; Osric's oath is not hers.",
        relevant=frozenset(
            {"entity:ada", "entity:house", "fact:ada_at_house", "fact:ada_ledger", "fact:ada_kin"}
        ),
        forbidden=frozenset({"fact:osric_warden", "fact:osric_stays"}),
        forbidden_words=(_OSRIC_SECRET,),
        distractors=_CORIN,
        tags=frozenset({"leakage"}),
    ),
    Labelled(
        "low_water",
        "scene_low_water",
        about="Low water (year 40): Wren has been told the oath, and is wrong about Osric.",
        relevant=frozenset(
            {
                "entity:wren",
                "entity:fen",
                "entity:osric",
                "fact:osric_at_light",
                "fact:osric_warden",
                "belief:wren_wrong",
            }
        ),
        forbidden=frozenset({"fact:ada_kin", "fact:osric_stays", "fact:bram_at_fen_once"}),
        forbidden_words=(_KIN,),
        distractors=_CORIN,
        tags=frozenset({"leakage", "acquisition"}),
    ),
    Labelled(
        "light-what-if",
        "scene_light",
        branch=True,
        about="In the what-if Osric never takes the light: the context is the what-if's story.",
        relevant=frozenset({"entity:osric", "entity:light", "entity:wren", "fact:osric_stays"}),
        forbidden=frozenset({"fact:osric_at_light", "fact:ada_kin"}),
        forbidden_words=(_KIN, "Osric Pell is located at Marrow Light"),
        distractors=_CORIN,
        tags=frozenset({"branch"}),
    ),
    Labelled(
        "crossing-small-budget",
        "scene_crossing",
        budget=300,
        about="The crossing with room for 300 words (the least): what is dropped is said.",
        relevant=frozenset(
            {
                "entity:wren",
                "entity:quay",
                "entity:bram",
                "fact:wren_at_quay",
                "fact:wren_knows_bram",
                "fact:bram_warden",
            }
        ),
        passages=("Wren Halloway untied the ferry",),
        forbidden=frozenset({"fact:osric_warden", "fact:ada_kin"}),
        distractors=_CORIN,
        tags=frozenset({"budget"}),
    ),
]


def _record(item: dict[str, Any], keys: dict[str, str]) -> str:
    kind, _, record_id = item["key"].partition(":")
    kind = _SAME_RECORD.get(kind, kind)
    return f"{kind}:{keys.get(record_id, record_id)}"


def _score(label: Labelled, found: dict[str, Any], keys: dict[str, str]) -> dict[str, Any]:
    items = [item for item in found["items"] if item["key"].split(":", 1)[0] not in _FRAME]
    # The service's own ranking: highest score first, ties in the order it gives them.
    ranked_items = sorted(enumerate(items), key=lambda pair: (-pair[1]["score"], pair[0]))
    ranked: list[str] = []
    passage_hits: set[str] = set()
    for _position, item in ranked_items:
        handle = _record(item, keys)
        for words in label.passages:
            if handle.startswith("passage:") and words in item["text"]:
                handle = f"passage:{words}"
                passage_hits.add(words)
        if handle not in ranked:
            ranked.append(handle)
    relevant = set(label.relevant) | {f"passage:{words}" for words in label.passages}
    given = set(ranked)
    left_out = {_record(o["item"], keys): o["reason"] for o in found["left_out"]}
    leaked = sorted(given & label.forbidden)
    leaked_words = [words for words in label.forbidden_words if words in found["rendered"]]
    omitted = sorted(relevant - given)
    labelled_given = given & (relevant | label.distractors)
    return {
        "id": label.id,
        "about": label.about,
        "branch": label.branch,
        "budget": label.budget,
        "given": len(ranked),
        "words": found["words"],
        "relevant": len(relevant),
        "recall_at_5": recall_at(ranked, relevant, 5),
        "recall_at_10": recall_at(ranked, relevant, 10),
        "recall": recall_at(ranked, relevant, len(ranked)),
        "mrr": reciprocal_rank(ranked, relevant),
        "precision": ratio(len(given & relevant), len(labelled_given)),
        "leaks": leaked,
        "leaked_words": leaked_words,
        "omitted": omitted,
        # An omission the context says nothing about is silent; one left out with a
        # reason (the budget, a secret, the author's choice) is said.
        "omitted_said": {handle: left_out[handle] for handle in omitted if handle in left_out},
        "omitted_silent": [handle for handle in omitted if handle not in left_out],
        "distractors_given": sorted(given & label.distractors),
        "left_out_for_room": sum(1 for o in found["left_out"] if o["reason"] == "budget"),
        "tags": sorted(label.tags),
    }


def evaluate(client, container) -> dict[str, Any]:
    ids = build(client, container)
    keys = {value: key for key, value in ids.items() if isinstance(value, str)}
    scenes = [
        _score(
            label,
            context(client, ids, label.scene, branch=label.branch, budget=label.budget),
            keys,
        )
        for label in LABELS
    ]
    full = [item for item in scenes if item["budget"] >= 12_000]

    def mean(name: str, of: list[dict[str, Any]]) -> float:
        return round(sum(item[name] for item in of) / len(of), 3) if of else 0.0

    branch = [item for item in scenes if item["branch"]]
    return {
        "set": SET_VERSION,
        "scenes": len(scenes),
        "semantic_search": False,
        "recall_at_5": mean("recall_at_5", full),
        "recall_at_10": mean("recall_at_10", full),
        "recall": mean("recall", full),
        "mrr": mean("mrr", full),
        "precision": mean("precision", full),
        "leaks": sum(len(item["leaks"]) + len(item["leaked_words"]) for item in scenes),
        "silent_omissions": sum(len(item["omitted_silent"]) for item in scenes),
        "distractors_given": sum(len(item["distractors_given"]) for item in scenes),
        "branch_correct": all(not item["leaks"] and not item["omitted"] for item in branch),
        "per_scene": scenes,
    }


__all__ = ["LABELS", "SET_VERSION", "Labelled", "evaluate"]
