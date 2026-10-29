"""Deterministic review bundles for prose (CON-013, ADR-006, plan/16).

A model critic should see the passages it reviews together with exactly the parts of the
story that bear on them, and nothing it does not need. This module works out, without a
model and without a vector index, which passages go to which check, with which records,
in groups that fit a token budget, and proves that every passage got every check it
needs.

1. **Review units.** A scene's text is cut into units at paragraph breaks. A paragraph
   longer than the unit limit is cut at sentence ends; a single sentence longer than
   that at word boundaries (and the plan says so). A unit keeps its offsets, a digest of
   its words and the entries named in it, never the words themselves.
2. **Anchors.** Each unit is given the checks (families) it needs and what each is
   about: ``timeline:<entry>`` for every person or thing named in it and the viewpoint
   character, ``state:<character>``, ``resource:<object>``, ``pov:<viewpoint>``,
   ``reveal:<fact>`` and ``thread:<thread>`` for what the plan says the scene shows or
   moves along, ``rule:<rule>`` for each story rule about an entry it names, and
   ``scene:<scene>`` when the scene has conditions. Records (``Evidence``) carry the
   anchors their caller derived from the story graph (a "located at" fact anchors the
   timeline of its subject; a belief anchors its holder's viewpoint; a rule its own id).
3. **Groups.** One group per anchor: the units with that anchor and the records with it.
   Groups of one family over the same units are merged; groups over mostly the same
   units are merged while they still fit. A unit may sit in several groups, one per
   check it needs; that costs more, and keeps timeline, knowledge and thread checks out
   of one noisy prompt (plan/16).
4. **Budget.** A group over the budget is split, never skipped: its units in reading
   order (a scene's units stay together where they fit), and when its records alone take
   more than half the budget, the records too, every part of the records meeting every
   part of the units. A split is a warning, since a critic then sees less at once.
5. **Coverage.** A manifest lists, for every unit, each anchor it needs and the groups
   that carry it. A unit no check claims is reviewed alone with the records about the
   entries it names (a single-unit fallback); with no story graph at all, units are
   reviewed in reading-order windows and the manifest says the checks were not chosen.
   A cap on the number of groups is never silent: what it leaves uncovered is listed and
   the plan is marked incomplete.

Two baselines share the same shapes so they can be compared on the same labelled
defects (``tests/eval``): ``single_context`` (everything in one prompt, split only when
it does not fit) and ``nearest_evidence`` (each unit with the records most similar to it,
by a similarity the caller supplies).

Nothing here reads storage, calls a model, or decides a finding; it is a pure function of
its inputs, so the same story always gives the same plan.
"""

from __future__ import annotations

import bisect
import hashlib
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

POLICY_VERSION = "review-bundles/1"
CHARS_PER_TOKEN = 4
# Per group: the instructions, the answer format and the deterministic findings already
# known. A fixed part, like the other estimates (``check_passes``).
INSTRUCTION_TOKENS = 600
DEFAULT_BUDGET = 6_000
MAX_UNIT_TOKENS = 400
MAX_GROUPS = 400
# Groups of one family whose units overlap at least this much are merged while they fit.
MERGE_OVERLAP = 0.8

FAMILIES: tuple[str, ...] = (
    "timeline",
    "state",
    "resource",
    "pov",
    "reveal",
    "thread",
    "rule",
    "scene",
)
# Families a fallback uses: never chosen as a check, only when nothing else applies.
FALLBACK_UNIT = "unit"
FALLBACK_WINDOW = "window"

FAMILY_WORDS: dict[str, str] = {
    "timeline": "Where and when people and things are",
    "state": "Condition, injury and life",
    "resource": "Who has what",
    "pov": "What the viewpoint character knows",
    "reveal": "What the reader is shown",
    "thread": "Threads and promises",
    "rule": "Story rules",
    "scene": "What the scene needs before and after",
    FALLBACK_UNIT: "A passage no check claimed, read on its own",
    FALLBACK_WINDOW: "Passages read in order, without the story's connections",
}

# Who can be in one place at a time, and who has a condition.
BODY_TYPES = frozenset({"Character", "Object"})
CHARACTER_TYPES = frozenset({"Character"})
RESOURCE_TYPES = frozenset({"Object"})
# Kinds of fact, as the continuity checks read them (``continuity``).
PLACE_KINDS = frozenset({"LOCATED_AT"})
POSSESSION_KINDS = frozenset({"OWNS", "HOLDS", "CARRIES", "POSSESSES", "WIELDS"})
CONDITION_KINDS = frozenset({"HAS_STATUS", "STATUS", "CONDITION", "AGE", "HAS_AGE", "AGED"})

_PARAGRAPH = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"”’)\]]*\s+")


def tokens_of(text: str) -> int:
    """About how many tokens some text is: a token is about four characters of English."""
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ inputs


@dataclass(frozen=True, order=True)
class Anchor:
    family: str
    id: str

    @property
    def key(self) -> str:
        return f"{self.family}:{self.id}"


@dataclass(frozen=True)
class SceneFrame:
    """What the plan says about one scene, for anchoring its units."""

    scene_id: str
    # Its place in reading order.
    order: int
    point_of_view: str | None = None
    place_id: str | None = None
    # Facts the scene shows or hints to the reader; threads it opens, moves or settles.
    reveals: frozenset[str] = frozenset()
    threads: frozenset[str] = frozenset()
    has_conditions: bool = False


@dataclass(frozen=True)
class RuleScope:
    """A story rule and the entries it is about, as the rule engine selects them."""

    rule_id: str
    entity_ids: frozenset[str]
    # The kinds of fact its checks and selector read: a fact of one of these kinds about
    # an entry in scope goes to the rule's check with the rule (``fact_anchors``).
    predicates: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ReviewUnit:
    id: str
    scene_id: str
    order: int
    index: int
    start: int
    end: int
    digest: str
    tokens: int
    entity_ids: frozenset[str]
    anchors: frozenset[Anchor]
    # How it was cut: at a paragraph break, a sentence end, or (a fallback) between words.
    cut: str = "paragraph"


@dataclass(frozen=True)
class Evidence:
    """A record a check may need: an entry, fact, belief, event, rule, reveal or condition."""

    key: str
    tokens: int
    anchors: frozenset[Anchor]
    entity_ids: frozenset[str] = frozenset()
    # A secret: always labelled for the author only in any group it goes to.
    author_only: bool = False
    # Where it sits in story time or reading order, for splitting records in order.
    order: float = 0.0


# ------------------------------------------------------------------ units


def _cuts(text: str, start: int, end: int, limit: int) -> list[tuple[int, int, str]]:
    """A paragraph as pieces no longer than ``limit`` tokens: sentences, then words."""
    if tokens_of(text[start:end]) <= limit:
        return [(start, end, "paragraph")]
    pieces: list[tuple[int, int, str]] = []
    sentences: list[tuple[int, int]] = []
    cursor = start
    for match in _SENTENCE_END.finditer(text, start, end):
        sentences.append((cursor, match.start()))
        cursor = match.end()
    if cursor < end:
        sentences.append((cursor, end))
    group_start: int | None = None
    group_end = start
    for s_start, s_end in sentences:
        if tokens_of(text[s_start:s_end]) > limit:
            if group_start is not None:
                pieces.append((group_start, group_end, "sentence"))
                group_start = None
            pieces.extend(_words(text, s_start, s_end, limit))
            continue
        if group_start is not None and tokens_of(text[group_start:s_end]) > limit:
            pieces.append((group_start, group_end, "sentence"))
            group_start = None
        if group_start is None:
            group_start = s_start
        group_end = s_end
    if group_start is not None:
        pieces.append((group_start, group_end, "sentence"))
    return pieces


def _words(text: str, start: int, end: int, limit: int) -> list[tuple[int, int, str]]:
    pieces: list[tuple[int, int, str]] = []
    room = limit * CHARS_PER_TOKEN
    cursor = start
    while cursor < end:
        stop = min(end, cursor + room)
        if stop < end:
            space = text.rfind(" ", cursor + 1, stop)
            stop = space if space > cursor else stop
        pieces.append((cursor, stop, "words"))
        cursor = stop
        while cursor < end and text[cursor].isspace():
            cursor += 1
    return pieces


def anchors_for(
    entity_ids: Iterable[str],
    frame: SceneFrame,
    entity_types: Mapping[str, str],
    rules: Sequence[RuleScope] = (),
) -> frozenset[Anchor]:
    """The checks a passage with these entries in it needs, in this scene."""
    named = set(entity_ids)
    found: set[Anchor] = set()
    present = set(named)
    if frame.point_of_view:
        present.add(frame.point_of_view)
        found.add(Anchor("pov", frame.point_of_view))
    for entity_id in present:
        kind = entity_types.get(entity_id)
        if kind in BODY_TYPES:
            found.add(Anchor("timeline", entity_id))
        if kind in CHARACTER_TYPES:
            found.add(Anchor("state", entity_id))
        if kind in RESOURCE_TYPES and entity_id in named:
            found.add(Anchor("resource", entity_id))
    found.update(Anchor("reveal", fact_id) for fact_id in frame.reveals)
    found.update(Anchor("thread", thread_id) for thread_id in frame.threads)
    found.update(Anchor("rule", rule.rule_id) for rule in rules if rule.entity_ids & named)
    if frame.has_conditions:
        found.add(Anchor("scene", frame.scene_id))
    return frozenset(found)


def fact_anchors(
    subject_id: str,
    predicate: str,
    object_id: str | None,
    entity_types: Mapping[str, str],
    rules: Sequence[RuleScope] = (),
) -> frozenset[Anchor]:
    """The checks a fact is evidence for, from the story graph alone.

    Where something is goes to its timeline; who has what to the object's (and its
    timeline: an object is in one place at a time); a condition or an age to the
    subject's state (an object's to who has it); any other fact between entries to the
    state of each character it names. A fact of a kind a rule reads, about an entry in
    the rule's scope, also goes to that rule's check.
    """
    found: set[Anchor] = set()
    subject_kind = entity_types.get(subject_id)
    object_kind = entity_types.get(object_id) if object_id else None
    if predicate in PLACE_KINDS:
        if subject_kind in BODY_TYPES:
            found.add(Anchor("timeline", subject_id))
    elif predicate in POSSESSION_KINDS and object_id is not None:
        if object_kind in RESOURCE_TYPES:
            found.update({Anchor("resource", object_id), Anchor("timeline", object_id)})
        if subject_kind in CHARACTER_TYPES:
            found.add(Anchor("state", subject_id))
    elif predicate in CONDITION_KINDS:
        if subject_kind in CHARACTER_TYPES:
            found.add(Anchor("state", subject_id))
        elif subject_kind in RESOURCE_TYPES:
            found.add(Anchor("resource", subject_id))
    else:
        for end, kind in ((subject_id, subject_kind), (object_id, object_kind)):
            if end is not None and kind in CHARACTER_TYPES:
                found.add(Anchor("state", end))
    for rule in rules:
        ends = {subject_id} | ({object_id} if object_id else set())
        if predicate in rule.predicates and ends & rule.entity_ids:
            found.add(Anchor("rule", rule.rule_id))
    return frozenset(found)


def review_units(
    text: str,
    frame: SceneFrame,
    mentions: Sequence[tuple[int, int, str]],
    entity_types: Mapping[str, str],
    *,
    rules: Sequence[RuleScope] = (),
    max_tokens: int = MAX_UNIT_TOKENS,
) -> list[ReviewUnit]:
    """A scene's text as review units, each with the entries named in it and its checks.

    ``mentions`` are (start, end, entry id) for each place an entry is named, as the
    deterministic name-finding gives them (explicit marks, names, aliases and answers).
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    for match in _PARAGRAPH.finditer(text):
        if text[cursor : match.start()].strip():
            spans.append((cursor, match.start()))
        cursor = match.end()
    if text[cursor:].strip():
        spans.append((cursor, len(text)))
    # Mentions in order of where they start, so each unit finds its own by halving
    # rather than reading every mention of the scene again.
    ordered = sorted(mentions, key=lambda item: item[0])
    mention_starts = [item[0] for item in ordered]
    units: list[ReviewUnit] = []
    for p_start, p_end in spans:
        for start, end, cut in _cuts(text, p_start, p_end, max(1, max_tokens)):
            words = text[start:end]
            if not words.strip():
                continue
            first = bisect.bisect_left(mention_starts, start)
            last = bisect.bisect_left(mention_starts, end, first)
            named = frozenset(item[2] for item in ordered[first:last])
            index = len(units)
            units.append(
                ReviewUnit(
                    id=f"{frame.scene_id}#{index}",
                    scene_id=frame.scene_id,
                    order=frame.order,
                    index=index,
                    start=start,
                    end=end,
                    digest=digest(words),
                    tokens=tokens_of(words),
                    entity_ids=named,
                    anchors=anchors_for(named, frame, entity_types, rules),
                    cut=cut,
                )
            )
    return units


# ------------------------------------------------------------------ groups


@dataclass(frozen=True)
class Bundle:
    id: str
    family: str
    # What it is about: the anchor ids it was made for (several after a merge).
    anchors: tuple[str, ...]
    unit_ids: tuple[str, ...]
    evidence_keys: tuple[str, ...]
    # Of those records, the secrets: labelled for the author only.
    author_only: tuple[str, ...]
    tokens: int
    part: int = 1
    parts: int = 1
    # Why it exists when it is not an ordinary check: "unit" or "window".
    fallback: str | None = None


@dataclass(frozen=True)
class UnitCoverage:
    unit_id: str
    scene_id: str
    digest: str
    # Anchor key → the groups that carry it with this unit.
    covered: dict[str, tuple[str, ...]]
    # Anchors it needs that no group carries (only when the group cap was reached).
    missing: tuple[str, ...] = ()
    fallback: bool = False


@dataclass(frozen=True)
class Coverage:
    units: tuple[UnitCoverage, ...]
    complete: bool
    evidence_used: tuple[str, ...]
    # Records no group needed, each with why.
    evidence_unused: tuple[tuple[str, str], ...]
    # False when the checks were not chosen from the story graph (no graph at all).
    from_graph: bool = True


@dataclass(frozen=True)
class BundlePlan:
    policy: str
    budget: int
    strategy: str
    units: tuple[ReviewUnit, ...]
    bundles: tuple[Bundle, ...]
    coverage: Coverage
    warnings: tuple[str, ...] = ()

    @property
    def tokens(self) -> int:
        return sum(bundle.tokens for bundle in self.bundles)

    def bundles_with(self, unit_id: str) -> list[Bundle]:
        return [bundle for bundle in self.bundles if unit_id in bundle.unit_ids]


@dataclass
class _Draft:
    family: str
    anchors: list[str]
    units: list[str]
    evidence: list[str]
    fallback: str | None = None


def _ordered_units(units: Sequence[ReviewUnit]) -> list[ReviewUnit]:
    return sorted(units, key=lambda unit: (unit.order, unit.index, unit.id))


def _group_tokens(
    unit_ids: Iterable[str],
    evidence_keys: Iterable[str],
    unit_by_id: Mapping[str, ReviewUnit],
    evidence_by_key: Mapping[str, Evidence],
) -> int:
    return (
        INSTRUCTION_TOKENS
        + sum(unit_by_id[unit_id].tokens for unit_id in unit_ids)
        + sum(evidence_by_key[key].tokens for key in evidence_keys)
    )


def _chunks(
    items: Sequence[str], size: Callable[[str], int], room: int, scene_of: Callable[[str], str]
) -> list[list[str]]:
    """Items in order, cut into runs that fit ``room``; a run ends at a scene's end when
    the next scene would not fit whole."""
    runs: list[list[str]] = []
    current: list[str] = []
    used = 0
    for position, item in enumerate(items):
        cost = size(item)
        if current and used + cost > room:
            runs.append(current)
            current, used = [], 0
        elif current and scene_of(item) != scene_of(current[-1]):
            # A new scene starts: keep it whole in the next run if it would not fit here.
            rest = [other for other in items[position:] if scene_of(other) == scene_of(item)]
            scene_cost = sum(size(other) for other in rest)
            if used + scene_cost > room and scene_cost <= room:
                runs.append(current)
                current, used = [], 0
        current.append(item)
        used += cost
    if current:
        runs.append(current)
    return runs


def _split(
    draft: _Draft,
    budget: int,
    unit_by_id: Mapping[str, ReviewUnit],
    evidence_by_key: Mapping[str, Evidence],
    warnings: list[str],
) -> list[tuple[list[str], list[str]]]:
    """A group as parts that fit the budget: units in reading order, and records in order
    when they alone take more than half of it. Every records part meets every units part."""
    total = _group_tokens(draft.units, draft.evidence, unit_by_id, evidence_by_key)
    if total <= budget:
        return [(draft.units, draft.evidence)]
    room = max(1, budget - INSTRUCTION_TOKENS)
    evidence_tokens = sum(evidence_by_key[key].tokens for key in draft.evidence)
    if evidence_tokens > room // 2:
        ordered = sorted(draft.evidence, key=lambda key: (evidence_by_key[key].order, key))
        evidence_parts = _chunks(
            ordered, lambda key: evidence_by_key[key].tokens, room // 2, lambda _key: ""
        )
        unit_room = room - max(
            sum(evidence_by_key[key].tokens for key in part) for part in evidence_parts
        )
        warnings.append(
            f"The records for “{FAMILY_WORDS.get(draft.family, draft.family)}” "
            f"({', '.join(draft.anchors[:3])}) do not fit one group; they are split in "
            f"{len(evidence_parts)}, so a check may miss two records that only disagree "
            "with each other."
        )
    else:
        evidence_parts = [draft.evidence]
        unit_room = room - evidence_tokens
    unit_parts = _chunks(
        draft.units,
        lambda unit_id: unit_by_id[unit_id].tokens,
        max(1, unit_room),
        lambda unit_id: unit_by_id[unit_id].scene_id,
    )
    if len(unit_parts) > 1 and len(evidence_parts) == 1:
        warnings.append(
            f"The passages for “{FAMILY_WORDS.get(draft.family, draft.family)}” "
            f"({', '.join(draft.anchors[:3])}) do not fit one group; they are read in "
            f"{len(unit_parts)} parts with the same records."
        )
    return [(units, evidence) for evidence in evidence_parts for units in unit_parts]


def _merge(drafts: list[_Draft], fits: Callable[[_Draft], bool]) -> list[_Draft]:
    """Same family over the same units: one group. Mostly the same units: one, if it fits."""
    by_units: dict[tuple[str, tuple[str, ...]], _Draft] = {}
    for draft in drafts:
        key = (draft.family, tuple(draft.units))
        if key in by_units:
            kept = by_units[key]
            kept.anchors.extend(a for a in draft.anchors if a not in kept.anchors)
            kept.evidence.extend(e for e in draft.evidence if e not in kept.evidence)
        else:
            by_units[key] = _Draft(
                draft.family, list(draft.anchors), list(draft.units), list(draft.evidence)
            )
    merged = list(by_units.values())
    changed = True
    while changed:
        changed = False
        for i, first in enumerate(merged):
            for j in range(i + 1, len(merged)):
                second = merged[j]
                if first.family != second.family:
                    continue
                a, b = set(first.units), set(second.units)
                if len(a & b) / len(a | b) < MERGE_OVERLAP:
                    continue
                candidate = _Draft(
                    first.family,
                    first.anchors + [x for x in second.anchors if x not in first.anchors],
                    first.units + [unit_id for unit_id in second.units if unit_id not in a],
                    first.evidence + [x for x in second.evidence if x not in first.evidence],
                )
                if not fits(candidate):
                    continue
                merged[i] = candidate
                del merged[j]
                changed = True
                break
            if changed:
                break
    return merged


def plan_bundles(
    units: Sequence[ReviewUnit],
    evidence: Sequence[Evidence],
    *,
    budget: int = DEFAULT_BUDGET,
    graph: bool = True,
    max_groups: int = MAX_GROUPS,
) -> BundlePlan:
    """Groups of units and records, one check each, within ``budget`` tokens a group.

    ``graph`` False means the story's connections could not be read: units are then
    reviewed in reading-order windows with no records, and the coverage says so.
    """
    budget = max(budget, INSTRUCTION_TOKENS + MAX_UNIT_TOKENS)
    ordered = _ordered_units(units)
    unit_by_id = {unit.id: unit for unit in ordered}
    evidence_by_key = {item.key: item for item in sorted(evidence, key=lambda e: e.key)}
    position = {unit.id: number for number, unit in enumerate(ordered)}
    warnings: list[str] = []

    if not graph:
        return _windows(ordered, evidence_by_key, budget, unit_by_id)

    drafts: dict[Anchor, _Draft] = {}
    for unit in ordered:
        for anchor in sorted(unit.anchors):
            drafts.setdefault(anchor, _Draft(anchor.family, [anchor.id], [], [])).units.append(
                unit.id
            )
    for item in evidence_by_key.values():
        for anchor in sorted(item.anchors):
            if anchor in drafts:
                drafts[anchor].evidence.append(item.key)

    # Units no check claims: each on its own, with the records about what it names.
    lonely = [unit for unit in ordered if not unit.anchors]
    fallback_drafts: list[_Draft] = []
    for unit in lonely:
        related = [
            item.key for item in evidence_by_key.values() if item.entity_ids & unit.entity_ids
        ]
        fallback_drafts.append(
            _Draft(FALLBACK_UNIT, [unit.id], [unit.id], related, fallback=FALLBACK_UNIT)
        )

    def fits(draft: _Draft) -> bool:
        return _group_tokens(draft.units, draft.evidence, unit_by_id, evidence_by_key) <= budget

    families = {family: number for number, family in enumerate(FAMILIES)}
    checks = _merge(
        sorted(drafts.values(), key=lambda d: (families.get(d.family, 99), d.anchors)), fits
    )
    for draft in checks:
        draft.units.sort(key=lambda unit_id: position[unit_id])

    bundles: list[Bundle] = []
    uncovered: list[tuple[str, str]] = []
    for draft in [*checks, *fallback_drafts]:
        parts = _split(draft, budget, unit_by_id, evidence_by_key, warnings)
        if len(bundles) + len(parts) > max_groups:
            uncovered.extend(
                (unit_id, f"{draft.family}:{anchor}")
                for unit_id in draft.units
                for anchor in draft.anchors
            )
            continue
        base = f"{draft.family}:{'+'.join(draft.anchors)}"
        for number, (part_units, part_evidence) in enumerate(parts, start=1):
            bundles.append(
                Bundle(
                    id=base if len(parts) == 1 else f"{base}#{number}",
                    family=draft.family,
                    anchors=tuple(draft.anchors),
                    unit_ids=tuple(part_units),
                    evidence_keys=tuple(part_evidence),
                    author_only=tuple(
                        key for key in part_evidence if evidence_by_key[key].author_only
                    ),
                    tokens=_group_tokens(part_units, part_evidence, unit_by_id, evidence_by_key),
                    part=number,
                    parts=len(parts),
                    fallback=draft.fallback,
                )
            )
    if uncovered:
        warnings.append(
            f"More than {max_groups} groups would be needed; {len(uncovered)} checks of "
            "passages are not covered. Review a smaller part of the story."
        )
    if lonely:
        warnings.append(
            f"{len(lonely)} passages name nothing a check is about; each is read on its own."
        )
    coverage = _coverage(ordered, bundles, evidence_by_key, uncovered)
    return BundlePlan(
        policy=POLICY_VERSION,
        budget=budget,
        strategy="deterministic",
        units=tuple(ordered),
        bundles=tuple(bundles),
        coverage=coverage,
        warnings=tuple(warnings),
    )


def _windows(
    ordered: list[ReviewUnit],
    evidence_by_key: Mapping[str, Evidence],
    budget: int,
    unit_by_id: Mapping[str, ReviewUnit],
) -> BundlePlan:
    runs = _chunks(
        [unit.id for unit in ordered],
        lambda unit_id: unit_by_id[unit_id].tokens,
        budget - INSTRUCTION_TOKENS,
        lambda unit_id: unit_by_id[unit_id].scene_id,
    )
    bundles = tuple(
        Bundle(
            id=f"{FALLBACK_WINDOW}:{number}",
            family=FALLBACK_WINDOW,
            anchors=(str(number),),
            unit_ids=tuple(run),
            evidence_keys=(),
            author_only=(),
            tokens=_group_tokens(run, (), unit_by_id, evidence_by_key),
            part=number,
            parts=len(runs),
            fallback=FALLBACK_WINDOW,
        )
        for number, run in enumerate(runs, start=1)
    )
    units = tuple(
        UnitCoverage(
            unit_id=unit.id,
            scene_id=unit.scene_id,
            digest=unit.digest,
            covered={
                f"{FALLBACK_WINDOW}:{b.anchors[0]}": (b.id,)
                for b in bundles
                if unit.id in b.unit_ids
            },
            fallback=True,
        )
        for unit in ordered
    )
    return BundlePlan(
        policy=POLICY_VERSION,
        budget=budget,
        strategy="deterministic",
        units=tuple(ordered),
        bundles=bundles,
        coverage=Coverage(
            units=units,
            complete=False,
            evidence_used=(),
            evidence_unused=tuple(
                (key, "the story's connections could not be read") for key in evidence_by_key
            ),
            from_graph=False,
        ),
        warnings=(
            "The story's connections could not be read, so passages are read in order "
            "without the records a check would need.",
        ),
    )


def _coverage(
    ordered: Sequence[ReviewUnit],
    bundles: Sequence[Bundle],
    evidence_by_key: Mapping[str, Evidence],
    uncovered: Sequence[tuple[str, str]],
    *,
    unchosen: bool = False,
) -> Coverage:
    """``unchosen``: a baseline that does not choose checks reads each unit for every
    check at once, so a group reading the unit carries all of its anchors."""
    by_unit: dict[str, list[Bundle]] = {}
    for bundle in bundles:
        for unit_id in bundle.unit_ids:
            by_unit.setdefault(unit_id, []).append(bundle)
    missing_by_unit: dict[str, list[str]] = {}
    for unit_id, anchor_key in uncovered:
        missing_by_unit.setdefault(unit_id, []).append(anchor_key)
    units: list[UnitCoverage] = []
    for unit in ordered:
        carried: dict[str, list[str]] = {}
        needed = {anchor.key for anchor in unit.anchors}
        for bundle in by_unit.get(unit.id, []):
            for anchor_id in bundle.anchors:
                carried.setdefault(f"{bundle.family}:{anchor_id}", []).append(bundle.id)
            if unchosen:
                for key in needed:
                    carried.setdefault(key, []).append(bundle.id)
        missing = sorted(
            {key for key in needed if key not in carried} | set(missing_by_unit.get(unit.id, []))
        )
        units.append(
            UnitCoverage(
                unit_id=unit.id,
                scene_id=unit.scene_id,
                digest=unit.digest,
                covered={key: tuple(ids) for key, ids in sorted(carried.items())},
                missing=tuple(missing),
                fallback=not unit.anchors,
            )
        )
    used = sorted({key for bundle in bundles for key in bundle.evidence_keys})
    unused = tuple(
        (key, "no passage under review is about it")
        for key in evidence_by_key
        if key not in set(used)
    )
    return Coverage(
        units=tuple(units),
        complete=all(not unit.missing for unit in units)
        and all(unit.id in by_unit for unit in ordered),
        evidence_used=tuple(used),
        evidence_unused=unused,
    )


# ------------------------------------------------------------------ baselines


def single_context(
    units: Sequence[ReviewUnit],
    evidence: Sequence[Evidence],
    *,
    budget: int,
) -> BundlePlan:
    """Everything in one prompt (a full-context critic), split in reading order only when
    it does not fit ``budget``; every part carries every record it can."""
    ordered = _ordered_units(units)
    unit_by_id = {unit.id: unit for unit in ordered}
    evidence_by_key = {item.key: item for item in sorted(evidence, key=lambda e: e.key)}
    warnings: list[str] = []
    draft = _Draft("all", ["story"], [unit.id for unit in ordered], list(evidence_by_key))
    parts = _split(draft, budget, unit_by_id, evidence_by_key, warnings)
    bundles = tuple(
        Bundle(
            id=f"all#{number}" if len(parts) > 1 else "all",
            family="all",
            anchors=("story",),
            unit_ids=tuple(part_units),
            evidence_keys=tuple(part_evidence),
            author_only=tuple(key for key in part_evidence if evidence_by_key[key].author_only),
            tokens=_group_tokens(part_units, part_evidence, unit_by_id, evidence_by_key),
            part=number,
            parts=len(parts),
        )
        for number, (part_units, part_evidence) in enumerate(parts, start=1)
    )
    return BundlePlan(
        policy=POLICY_VERSION,
        budget=budget,
        strategy="full_context",
        units=tuple(ordered),
        bundles=bundles,
        coverage=_coverage(ordered, bundles, evidence_by_key, (), unchosen=True),
        warnings=tuple(warnings),
    )


def nearest_evidence(
    units: Sequence[ReviewUnit],
    evidence: Sequence[Evidence],
    similarity: Callable[[ReviewUnit, Evidence], float],
    *,
    k: int = 8,
    budget: int = DEFAULT_BUDGET,
) -> BundlePlan:
    """Each unit with the ``k`` records most similar to it (a vector-only critic)."""
    ordered = _ordered_units(units)
    unit_by_id = {unit.id: unit for unit in ordered}
    evidence_by_key = {item.key: item for item in sorted(evidence, key=lambda e: e.key)}
    bundles: list[Bundle] = []
    for unit in ordered:
        ranked = sorted(
            evidence_by_key.values(), key=lambda item: (-similarity(unit, item), item.key)
        )
        chosen: list[str] = []
        for item in ranked[: max(0, k)]:
            if _group_tokens([unit.id], [*chosen, item.key], unit_by_id, evidence_by_key) > budget:
                break
            chosen.append(item.key)
        bundles.append(
            Bundle(
                id=f"nearest:{unit.id}",
                family="nearest",
                anchors=(unit.id,),
                unit_ids=(unit.id,),
                evidence_keys=tuple(chosen),
                author_only=tuple(key for key in chosen if evidence_by_key[key].author_only),
                tokens=_group_tokens([unit.id], chosen, unit_by_id, evidence_by_key),
            )
        )
    return BundlePlan(
        policy=POLICY_VERSION,
        budget=budget,
        strategy="vector_only",
        units=tuple(ordered),
        bundles=tuple(bundles),
        coverage=_coverage(ordered, bundles, evidence_by_key, (), unchosen=True),
    )


def seen_together(plan: BundlePlan, unit_ids: Iterable[str], evidence_keys: Iterable[str]) -> bool:
    """Whether one group shows a critic all of these units and records at once."""
    units, records = set(unit_ids), set(evidence_keys)
    return any(
        units <= set(bundle.unit_ids) and records <= set(bundle.evidence_keys)
        for bundle in plan.bundles
    )


def missing_for(
    plan: BundlePlan, unit_ids: Iterable[str], evidence_keys: Iterable[str]
) -> set[str]:
    """The records no group showing all these units carries (for the best such group)."""
    units, records = set(unit_ids), set(evidence_keys)
    best: set[str] | None = None
    for bundle in plan.bundles:
        if not units <= set(bundle.unit_ids):
            continue
        gap = records - set(bundle.evidence_keys)
        if best is None or len(gap) < len(best):
            best = gap
    return set(records) if best is None else best


# ------------------------------------------------------------------ preview


@dataclass(frozen=True)
class FamilyLine:
    family: str
    words: str
    groups: int
    tokens: int


@dataclass(frozen=True)
class Preview:
    """What a model check would read, before anything is spent, in plain words."""

    policy: str
    passages: int
    scenes: int
    groups: int
    tokens: int
    # Passages read in more than one group, and how many readings that is in all.
    repeated: int
    readings: int
    author_only: int
    complete: bool
    families: tuple[FamilyLine, ...]
    lines: tuple[str, ...] = field(default=())


def _count(number: int, word: str) -> str:
    return f"{number} {word}" if number == 1 else f"{number} {word}s"


def preview(plan: BundlePlan) -> Preview:
    readings: dict[str, int] = {}
    for bundle in plan.bundles:
        for unit_id in bundle.unit_ids:
            readings[unit_id] = readings.get(unit_id, 0) + 1
    families: dict[str, list[Bundle]] = {}
    for bundle in plan.bundles:
        families.setdefault(bundle.family, []).append(bundle)
    lines_by_family = tuple(
        FamilyLine(
            family=family,
            words=FAMILY_WORDS.get(family, family),
            groups=len(items),
            tokens=sum(item.tokens for item in items),
        )
        for family, items in sorted(families.items())
    )
    secrets = len({key for bundle in plan.bundles for key in bundle.author_only})
    scenes = len({unit.scene_id for unit in plan.units})
    repeated = sum(1 for count in readings.values() if count > 1)
    lines = [
        f"{_count(len(plan.units), 'passage')} from {_count(scenes, 'scene')}, read in "
        f"{_count(len(plan.bundles), 'group')}; about {plan.tokens} tokens in all.",
    ]
    if repeated:
        lines.append(
            f"{_count(repeated, 'passage')} {'is' if repeated == 1 else 'are'} read by more "
            "than one check."
        )
    if secrets:
        lines.append(
            f"{_count(secrets, 'secret')} {'is' if secrets == 1 else 'are'} shown to the "
            "checker, marked as for you only."
        )
    if not plan.coverage.complete:
        lines.append("Not every passage gets every check it needs; see the warnings.")
    lines.extend(plan.warnings)
    return Preview(
        policy=plan.policy,
        passages=len(plan.units),
        scenes=scenes,
        groups=len(plan.bundles),
        tokens=plan.tokens,
        repeated=repeated,
        readings=sum(readings.values()),
        author_only=secrets,
        complete=plan.coverage.complete,
        families=lines_by_family,
        lines=tuple(lines),
    )


__all__ = [
    "DEFAULT_BUDGET",
    "FAMILIES",
    "FAMILY_WORDS",
    "INSTRUCTION_TOKENS",
    "MAX_UNIT_TOKENS",
    "POLICY_VERSION",
    "Anchor",
    "Bundle",
    "BundlePlan",
    "Coverage",
    "Evidence",
    "Preview",
    "ReviewUnit",
    "RuleScope",
    "SceneFrame",
    "UnitCoverage",
    "anchors_for",
    "digest",
    "fact_anchors",
    "missing_for",
    "nearest_evidence",
    "plan_bundles",
    "preview",
    "review_units",
    "seen_together",
    "single_context",
    "tokens_of",
]
