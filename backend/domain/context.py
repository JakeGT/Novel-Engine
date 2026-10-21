"""What the assistant is given, and why (ADR-021).

A context manifest is assembled deterministically from candidate items, each with a
section, a plain-language reason, an authority and a cost. Hard items are always kept;
the author's pins are kept; the author's exclusions and anything over budget are left
out and listed with the reason. Secrets the viewpoint character does not know sit in
their own section, left out unless the author includes them.

Rendering orders sections from stable to volatile, so a provider can reuse a cached
prefix, and the checksum of the rendering identifies exactly what was given.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.domain.context_parts import (
    REPRESENTATION_WORDS,
    TOKENS_PER_WORD,
    ContextChoice,
    Delivery,
    MediaPart,
    planned_representation,
)


class ContextSection(StrEnum):
    RULES = "rules"
    CAST = "cast"
    TRUE = "true"
    KNOWS = "knows"
    WRONG = "wrong"
    HAPPENED = "happened"
    PLAN = "plan"
    READER = "reader"
    SCENE = "scene"
    SECRETS = "secrets"
    PICTURES = "pictures"
    WRITING = "writing"
    # Summaries and event blocks of the scenes before this one (CTX-006).
    EARLIER = "earlier"
    # The end of the scene before, as written (CTX-006A).
    BEFORE = "before"
    # The ends of a few other recent scenes on the same viewpoint or thread (CTX-006A).
    NEARBY = "nearby"


# Stable first, volatile last, so a provider can reuse a cached prefix; and the scene
# itself last, because models attend best to the start and end of a long context
# (Liu et al., "Lost in the Middle", TACL 2024).
SECTION_ORDER: tuple[ContextSection, ...] = (
    ContextSection.RULES,
    ContextSection.CAST,
    ContextSection.TRUE,
    ContextSection.KNOWS,
    ContextSection.WRONG,
    ContextSection.HAPPENED,
    ContextSection.READER,
    ContextSection.EARLIER,
    ContextSection.PICTURES,
    ContextSection.WRITING,
    ContextSection.NEARBY,
    # Recent prose sits next to the scene, after the stable material (plan 04: "scene
    # contract/task, then recent prose and volatile evidence").
    ContextSection.BEFORE,
    ContextSection.PLAN,
    ContextSection.SECRETS,
    ContextSection.SCENE,
)

SECTION_TITLES: dict[ContextSection, str] = {
    ContextSection.RULES: "Rules of this story",
    ContextSection.CAST: "Who and what is in the scene",
    ContextSection.TRUE: "What is true in the story",
    ContextSection.KNOWS: "What the viewpoint character knows",
    ContextSection.WRONG: "What the viewpoint character believes wrongly",
    ContextSection.HAPPENED: "What has happened",
    ContextSection.PLAN: "Where the scene sits in the plan",
    ContextSection.READER: "What the reader has been shown",
    ContextSection.SCENE: "The scene",
    ContextSection.SECRETS: "For the author only: secrets the viewpoint character does not know",
    ContextSection.PICTURES: "Pictures",
    ContextSection.WRITING: "From the author's own writing",
    ContextSection.EARLIER: "What happened in earlier scenes",
    ContextSection.BEFORE: "How the scene before ends",
    ContextSection.NEARBY: "How other recent scenes end",
}

# The same sections as the author sees them: the titles above are written for the model,
# which is told about "the author"; on screen the author is "you".
SHOWN_SECTION_TITLES: dict[ContextSection, str] = {
    **SECTION_TITLES,
    ContextSection.WRITING: "From your writing",
    ContextSection.SECRETS: "Only for you: secrets the viewpoint character does not know",
}


class LeftOut(StrEnum):
    EXCLUDED = "excluded"
    BUDGET = "budget"
    SECRET = "secret"
    # It happens later in the story's time than the scene being written (CTX-004).
    LATER = "later"


class LaterInTime(StrEnum):
    """What a policy does with material from a scene placed later in the story's time
    than the one being written (CTX-004): leave it out (listed, so the author can still
    choose it), or give it ranked lower."""

    LEAVE_OUT = "leave_out"
    LOWER = "lower"


@dataclass(frozen=True)
class ContextPolicy:
    """How a kind of request assembles its context. Changing it means a new version."""

    version: str
    default_budget: int
    min_budget: int = 300
    max_budget: int = 12_000
    # Material from a scene certainly later in the story's time (CTX-004).
    later_in_story_time: LaterInTime = LaterInTime.LEAVE_OUT


# 2: earlier scenes' summaries and event blocks, the end of the scene before, passages
# from scenes on the same thread, and ranking by the author's importance, open
# continuity findings, distance in the story and diversity (CTX-006, CTX-006A, CTX-007).
# 3: material from scenes later in the story's time left out (listed, the author may
# still choose it), summaries of earlier arcs and beats, the ends of other recent scenes
# on the same viewpoint or thread, and decay by the story's time as well as reading order
# (CTX-004, CTX-006, CTX-006A, CTX-007).
SCENE_DRAFT_POLICY = ContextPolicy(version="scene-draft/3", default_budget=2500)


@dataclass(frozen=True)
class Signal:
    """One part of why an item ranks where it does (CTX-007).

    ``effect`` is how much this signal changed the item's score, so an item's signals
    add up to its score; ``note`` says it in the author's words. Weights are metadata
    on the item, never written into the index or the story.
    """

    name: str
    effect: float
    note: str


def with_signals(base: float, signals: Iterable[Signal]) -> float:
    return base + sum(signal.effect for signal in signals)


@dataclass(frozen=True)
class ContextItem:
    # Stable across requests, so a pin or an exclusion keeps meaning the same thing.
    key: str
    section: ContextSection
    text: str
    # Why it is included, in the author's words: "Mara is the scene's point of view."
    reason: str
    # fact, belief, plan, rule, entry, event, writing, picture, reader
    authority: str
    record_type: str
    record_id: str
    score: float = 0.0
    hard: bool = False
    # A pointer inside the source, such as a passage's character range.
    location: str | None = None
    # A picture (CTX-013): which exact version, and what the author chose for it. The
    # item's text is what a model that cannot see it is given; whether the picture itself
    # went is decided when a request is made, and recorded in its delivery.
    media: MediaPart | None = None
    # How its score was made, part by part (CTX-007); empty for a fixed score.
    signals: tuple[Signal, ...] = ()
    # It comes from a scene certainly later in the story's time than the one being
    # written (CTX-004); the policy says whether it is left out or only ranked lower.
    later: bool = False

    @property
    def words(self) -> int:
        """Its cost against the budget, in words. A picture the author chose to send
        costs what the picture would, so the budget holds whichever way it goes."""
        base = max(1, len(self.text.split()))
        if self.media is not None and self.media.choice is ContextChoice.SEND:
            return max(base, math.ceil(self.media.tokens / TOKENS_PER_WORD))
        return base


@dataclass(frozen=True)
class Omission:
    item: ContextItem
    reason: LeftOut


@dataclass
class ContextManifest:
    policy_version: str
    budget: int
    items: list[ContextItem] = field(default_factory=list)
    left_out: list[Omission] = field(default_factory=list)
    pinned: set[str] = field(default_factory=set)
    rendered: str = ""
    checksum: str = ""

    @property
    def words(self) -> int:
        return sum(item.words for item in self.items)


@dataclass(frozen=True)
class SavedManifest:
    """Exactly what was given for one request, kept so a draft can be traced to it."""

    id: str
    purpose: str
    plan_id: str | None
    policy_version: str
    checksum: str
    words: int
    budget: int
    manifest: dict[str, Any]
    created_by: str | None
    created_at: datetime
    # The what-if it was made in, or None for the main story.
    branch_id: str | None = None


def clamp_budget(budget: int | None, policy: ContextPolicy) -> int:
    if budget is None:
        return policy.default_budget
    return max(policy.min_budget, min(policy.max_budget, budget))


def assemble(
    candidates: Iterable[ContextItem],
    *,
    policy: ContextPolicy = SCENE_DRAFT_POLICY,
    budget: int | None = None,
    pins: Iterable[str] = (),
    excludes: Iterable[str] = (),
    include_secrets: bool = False,
) -> ContextManifest:
    """Choose what to give, within a budget, and say why for everything left out."""
    allowed = clamp_budget(budget, policy)
    pinned = set(pins)
    excluded = set(excludes) - pinned
    unique: dict[str, ContextItem] = {}
    for item in candidates:
        current = unique.get(item.key)
        if current is None or item.score > current.score:
            unique[item.key] = item
    manifest = ContextManifest(policy_version=policy.version, budget=allowed, pinned=pinned)
    eligible: list[ContextItem] = []
    for item in unique.values():
        if item.key in excluded and not item.hard:
            manifest.left_out.append(Omission(item, LeftOut.EXCLUDED))
        elif (
            item.section is ContextSection.SECRETS
            and not include_secrets
            and item.key not in pinned
        ):
            manifest.left_out.append(Omission(item, LeftOut.SECRET))
        elif (
            item.later
            and policy.later_in_story_time is LaterInTime.LEAVE_OUT
            and not item.hard
            and item.key not in pinned
        ):
            manifest.left_out.append(Omission(item, LeftOut.LATER))
        else:
            eligible.append(item)

    must = [item for item in eligible if item.hard or item.key in pinned]
    rest = sorted(
        (item for item in eligible if not (item.hard or item.key in pinned)),
        key=lambda item: (-item.score, item.key),
    )
    kept = list(must)
    used = sum(item.words for item in must)
    for item in rest:
        if used + item.words <= allowed:
            kept.append(item)
            used += item.words
        else:
            manifest.left_out.append(Omission(item, LeftOut.BUDGET))

    order = {section: index for index, section in enumerate(SECTION_ORDER)}
    manifest.items = sorted(kept, key=lambda item: (order[item.section], -item.score, item.key))
    manifest.left_out.sort(key=lambda omission: (omission.reason.value, -omission.item.score))
    manifest.rendered = render(manifest.items)
    manifest.checksum = hashlib.sha256(manifest.rendered.encode("utf-8")).hexdigest()
    return manifest


def text_digest(text: str) -> str:
    """A short fingerprint of an item's words, so a change can be seen without keeping them."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def picture_record(media: MediaPart) -> dict[str, Any]:
    """What a kept context says of a picture: which version and copy, never its bytes."""
    planned = planned_representation(media.choice, bool(media.fallback_text.strip()))
    return {
        "kind": media.kind,
        "asset_id": media.asset_id,
        "version_id": media.version_id,
        "rendition": media.rendition,
        "content_type": media.content_type,
        "checksum": media.checksum,
        "choice": media.choice.value,
        "representation": planned.value,
        "representation_words": REPRESENTATION_WORDS[planned],
    }


def stored_manifest(
    manifest: ContextManifest, point_of_view: str | None, delivery: Delivery | None = None
) -> dict[str, Any]:
    """What is kept of a manifest: every item, why it was given, and how to tell it changed.

    The words of an item are kept too, except a passage of the author's writing: the
    passage index never holds the author's words (ADR-021), and the manifest does not
    either. A passage is told apart by its fingerprint and where it sits in its text.
    A picture is kept as the exact version it points at and what the author chose; with
    ``delivery``, also how each part actually reached the model (CTX-013).
    """
    kept = {
        "items": [
            {
                "key": item.key,
                "section": item.section.value,
                "reason": item.reason,
                "authority": item.authority,
                "record_type": item.record_type,
                "record_id": item.record_id,
                "location": item.location,
                "words": item.words,
                "score": item.score,
                "digest": text_digest(item.text),
                **({} if item.authority == "writing" else {"text": item.text}),
                **({"picture": picture_record(item.media)} if item.media is not None else {}),
                **(
                    {
                        "signals": [
                            {"name": sig.name, "effect": round(sig.effect, 3), "note": sig.note}
                            for sig in item.signals
                        ]
                    }
                    if item.signals
                    else {}
                ),
            }
            for item in manifest.items
        ],
        "left_out": [
            {"key": omission.item.key, "reason": omission.reason.value}
            for omission in manifest.left_out
        ],
        "point_of_view": point_of_view,
    }
    if delivery is not None and delivery.parts:
        kept["delivery"] = delivery.as_dict()
    return kept


@dataclass(frozen=True)
class KeptItem:
    """An item as a kept manifest recorded it. ``text`` is None where words were not kept."""

    key: str
    section: ContextSection | None
    reason: str
    authority: str
    words: int
    text: str | None
    digest: str | None


@dataclass(frozen=True)
class ChangedItem:
    now: ContextItem
    then: KeptItem
    # What differs, in plain words: "the words", "why it is given", "where it sits".
    what: list[str]


@dataclass
class ContextComparison:
    """The context now against the context kept for a draft: what was added, removed, changed."""

    kept: SavedManifest
    now: ContextManifest
    added: list[ContextItem] = field(default_factory=list)
    # Given then, not now; with why it is left out now, if it is still found at all.
    removed: list[tuple[KeptItem, Omission | None]] = field(default_factory=list)
    changed: list[ChangedItem] = field(default_factory=list)
    unchanged: int = 0
    same: bool = False


def _kept_item(raw: dict[str, Any]) -> KeptItem:
    try:
        section: ContextSection | None = ContextSection(str(raw.get("section", "")))
    except ValueError:
        section = None
    return KeptItem(
        key=str(raw.get("key", "")),
        section=section,
        reason=str(raw.get("reason", "")),
        authority=str(raw.get("authority", "")),
        words=int(raw.get("words") or 0),
        text=raw.get("text") if isinstance(raw.get("text"), str) else None,
        digest=raw.get("digest") if isinstance(raw.get("digest"), str) else None,
    )


def compare_contexts(current: ContextManifest, kept: SavedManifest) -> ContextComparison:
    """Item by item, by key: a key stands for the same thing across requests.

    A manifest kept before fingerprints were recorded can still tell an item was added,
    removed, moved or re-explained, and a change of length; it cannot see a rewording
    that kept the same number of words.
    """
    then = [_kept_item(raw) for raw in kept.manifest.get("items", []) if isinstance(raw, dict)]
    then_by_key = {item.key: item for item in then}
    now_by_key = {item.key: item for item in current.items}
    left_now = {omission.item.key: omission for omission in current.left_out}
    result = ContextComparison(kept=kept, now=current, same=current.checksum == kept.checksum)
    for item in current.items:
        before = then_by_key.get(item.key)
        if before is None:
            result.added.append(item)
            continue
        what: list[str] = []
        if before.digest is not None:
            if before.digest != text_digest(item.text):
                what.append("the words")
        elif before.words != item.words:
            what.append("the words")
        if before.section is not None and before.section is not item.section:
            what.append("where it sits")
        if before.reason and before.reason != item.reason:
            what.append("why it is given")
        if what:
            result.changed.append(ChangedItem(now=item, then=before, what=what))
        else:
            result.unchanged += 1
    for before in then:
        if before.key not in now_by_key:
            result.removed.append((before, left_now.get(before.key)))
    return result


def render(items: Iterable[ContextItem]) -> str:
    """The context as text, section by section, stable first. Deterministic."""
    lines: list[str] = []
    current: ContextSection | None = None
    for item in items:
        if item.section is not current:
            current = item.section
            if lines:
                lines.append("")
            lines.append(f"## {SECTION_TITLES[current]}")
        lines.append(f"- {item.text}")
    return "\n".join(lines) + ("\n" if lines else "")


__all__ = [
    "SCENE_DRAFT_POLICY",
    "SETTINGS_KEY",
    "ChangedItem",
    "ContextComparison",
    "KeptItem",
    "compare_contexts",
    "picture_record",
    "stored_manifest",
    "text_digest",
    "ContextSettings",
    "context_settings",
    "with_context_settings",
    "SECTION_ORDER",
    "SECTION_TITLES",
    "SHOWN_SECTION_TITLES",
    "ContextItem",
    "ContextManifest",
    "ContextPolicy",
    "ContextSection",
    "LaterInTime",
    "LeftOut",
    "Omission",
    "PartSummary",
    "SavedManifest",
    "assemble",
    "clamp_budget",
    "render",
]


# ------------------------------------------------------------- the author's controls

SETTINGS_KEY = "_context"
MAX_CHOICES = 200


class ContextSettings(BaseModel):
    """What the author has said about a scene's context, kept with the scene."""

    model_config = ConfigDict(extra="forbid")

    pins: list[str] = Field(default_factory=list, max_length=MAX_CHOICES)
    excludes: list[str] = Field(default_factory=list, max_length=MAX_CHOICES)
    budget: int | None = Field(default=None, ge=0, le=100_000)
    include_secrets: bool = False


def context_settings(details: dict[str, str]) -> ContextSettings:
    raw = details.get(SETTINGS_KEY)
    if not raw:
        return ContextSettings()
    try:
        return ContextSettings.model_validate_json(raw)
    except ValueError:
        return ContextSettings()


def with_context_settings(details: dict[str, str], settings: ContextSettings) -> dict[str, str]:
    kept = {key: value for key, value in details.items() if key != SETTINGS_KEY}
    if settings != ContextSettings():
        kept[SETTINGS_KEY] = settings.model_dump_json()
    return kept


# ------------------------------------------------------------------ ranking (CTX-007)
#
# A candidate's score starts from a fixed amount for what it is (a scene role, a pin, a
# plan link, a passage naming someone). The signals below then move it, and each one is
# kept on the item as a ``Signal`` with its effect and a note in plain words, so the
# context can say why an item ranks where it does. Weights are metadata: nothing here
# changes the index, the story or the author's words.

# The author's importance mark on an entry (``backend/domain/canon/marks.py``).
IMPORTANCE_WEIGHT = {"main": 1.5, "supporting": 1.2, "minor": 0.85, "background": 0.7}
IMPORTANCE_WORDS = {
    "main": "one of your main entries",
    "supporting": "a supporting entry",
    "minor": "a minor entry",
    "background": "in the background",
}
# Narrative distance: prose from a scene loses half its weight every
# ``DISTANCE_HALF_LIFE`` scenes away in reading order, never below ``DISTANCE_FLOOR``;
# prose from a scene that comes later than this one is weighed by ``LATER_WEIGHT`` (it
# may tell what has not happened yet).
DISTANCE_HALF_LIFE = 4.0
DISTANCE_FLOOR = 0.3
LATER_WEIGHT = 0.5
# Continuity impact: something with an open continuity warning or a broken story rule
# at or near this scene (``NEAR_SCENES`` either side, in reading order).
CONTINUITY_BOOST = 2.0
RULE_BOOST = 1.5
NEAR_SCENES = 2
# Diversity: a passage much like one ranked above it is lowered by
# ``DIVERSITY_WEIGHT`` times how alike they are (maximal marginal relevance, Carbonell
# and Goldstein, SIGIR 1998), once they share at least ``DIVERSITY_FROM`` of their
# three-word runs.
DIVERSITY_WEIGHT = 0.6
DIVERSITY_FROM = 0.25

_NUMBER_WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")


def count_words(number: int, one: str, many: str) -> str:
    """ "one scene", "four scenes", "12 scenes"."""
    said = _NUMBER_WORDS[number] if 0 <= number < len(_NUMBER_WORDS) else str(number)
    return f"{said} {one if number == 1 else many}"


def importance_signal(score: float, level: str | None, name: str) -> Signal | None:
    """How the author's importance mark on an entry moves an item about it."""
    weight = IMPORTANCE_WEIGHT.get(level or "")
    if weight is None or weight == 1.0:
        return None
    return Signal(
        "importance",
        round(score * (weight - 1.0), 3),
        f"You marked {name} as {IMPORTANCE_WORDS[level or '']}.",
    )


def distance_weight(distance: int) -> float:
    """``distance`` scenes back in reading order (negative: that many scenes later)."""
    if distance < 0:
        return LATER_WEIGHT
    if distance <= 1:
        return 1.0
    return max(DISTANCE_FLOOR, 0.5 ** ((distance - 1) / DISTANCE_HALF_LIFE))


def distance_signal(score: float, distance: int, title: str) -> Signal | None:
    weight = distance_weight(distance)
    if weight == 1.0:
        return None
    back = count_words(distance, "scene", "scenes")
    if not title:
        # The item already names the scene: only how far away it is.
        note = (
            "It comes later in the story than this scene."
            if distance < 0
            else f"It is {back} back."
        )
    elif distance < 0:
        note = f"It is from “{title}”, which comes later in the story than this scene."
    else:
        note = f"It is from “{title}”, {back} back."
    return Signal("distance", round(score * (weight - 1.0), 3), note)


# Story time, the second clock (CTX-007). Reading order says how far back a scene is
# read; story time says how long before it happens. Prose from a scene long before this
# one in the story's time loses half its weight every ``STORY_TIME_SHARE`` of the time
# the line's placed scenes span (so the decay fits a story of days or of centuries),
# never below ``STORY_TIME_FLOOR``: a deliberate jump back in time is still worth
# something. The weights multiply with reading order's, like two decay functions in a
# search engine's scoring (each halves at its own scale). Material that may happen later
# than this scene (its time and this one's overlap, or it is open-ended) is weighed
# ``MAYBE_LATER_WEIGHT``; certainly later is the policy's (CTX-004), and
# ``LATER_IN_TIME_WEIGHT`` when the policy only lowers it.
STORY_TIME_SHARE = 0.25
STORY_TIME_FLOOR = 0.5
MAYBE_LATER_WEIGHT = 0.75
LATER_IN_TIME_WEIGHT = 0.5


def story_time_weight(gap: float, half_life: float | None) -> float:
    """``gap`` units of story time before this scene, halving every ``half_life``."""
    if half_life is None or half_life <= 0 or gap <= 0 or not math.isfinite(gap):
        return 1.0
    return max(STORY_TIME_FLOOR, 0.5 ** (gap / half_life))


def story_time_signal(
    score: float, gap: float, half_life: float | None, unit: str
) -> Signal | None:
    """How far back in the story's time something is, as a signal with its reason."""
    weight = story_time_weight(gap, half_life)
    if weight >= 1.0:
        return None
    amount = round(gap, 1)
    said = str(int(amount)) if amount == int(amount) else f"{amount:g}"
    units = unit if amount == 1 else f"{unit}s"
    return Signal(
        "story time",
        round(score * (weight - 1.0), 3),
        f"In the story's time it happens {said} {units} before this scene.",
    )


def later_in_time_signal(score: float, *, certain: bool) -> Signal:
    """Something that happens, or may happen, later in the story's time than this scene."""
    if certain:
        return Signal(
            "later in time",
            round(score * (LATER_IN_TIME_WEIGHT - 1.0), 3),
            "It happens later in the story's time than this scene.",
        )
    return Signal(
        "later in time",
        round(score * (MAYBE_LATER_WEIGHT - 1.0), 3),
        "It may happen later in the story's time than this scene.",
    )


def shingles(text: str, size: int = 3) -> frozenset[tuple[str, ...]]:
    words = [word.strip(".,;:!?\"'“”‘’()[]—–-").casefold() for word in text.split()]
    words = [word for word in words if word]
    if len(words) < size:
        return frozenset({tuple(words)}) if words else frozenset()
    return frozenset(tuple(words[i : i + size]) for i in range(len(words) - size + 1))


def likeness(a: frozenset[tuple[str, ...]], b: frozenset[tuple[str, ...]]) -> float:
    """How alike two passages are: the share of three-word runs they have in common."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def diversify(items: list[ContextItem], words: dict[str, str] | None = None) -> list[ContextItem]:
    """Lower a passage that is much like one ranked above it (CTX-007).

    Greedy maximal marginal relevance: the best remaining passage is taken each time,
    after lowering every other by how much it is like any already taken. ``words``
    holds each item's words by key (a passage's words are not kept in its item when
    it is trimmed). Deterministic; an item no other resembles keeps its score.
    """
    remaining = sorted(items, key=lambda item: (-item.score, item.key))
    runs = {item.key: shingles((words or {}).get(item.key, item.text)) for item in remaining}
    taken: list[ContextItem] = []
    while remaining:
        best_index = 0
        best: tuple[float, ContextItem, Signal | None] | None = None
        for index, item in enumerate(remaining):
            closest, like = None, 0.0
            for other in taken:
                value = likeness(runs[item.key], runs[other.key])
                if value > like:
                    closest, like = other, value
            signal = None
            adjusted = item.score
            if closest is not None and like >= DIVERSITY_FROM:
                penalty = round(item.score * DIVERSITY_WEIGHT * like, 3)
                adjusted = item.score - penalty
                title = re.search(r"“([^”]+)”", closest.text)
                where = f", from “{title.group(1)}”" if title else ""
                signal = Signal(
                    "diversity",
                    -penalty,
                    f"Much of it is like another passage given{where}, so it ranks lower.",
                )
            if best is None or (adjusted, -index) > (best[0], -best_index):
                best, best_index = (adjusted, item, signal), index
        assert best is not None
        adjusted, item, signal = best
        remaining.pop(best_index)
        if signal is not None:
            item = replace(
                item,
                score=round(adjusted, 3),
                signals=(*item.signals, signal),
                reason=f"{item.reason} {signal.note}",
            )
        taken.append(item)
    return taken


# --------------------------------------------------------- the index's waiting list


@dataclass(frozen=True)
class WaitingText:
    """A text on the passage index's waiting list (CTX-005)."""

    source_id: str
    gone: bool
    generation: int
    attempts: int = 0


@dataclass(frozen=True)
class IndexWaiting:
    """How far behind the passage index is for one story."""

    count: int = 0
    # Seconds since the oldest waiting change was made; None when nothing waits.
    oldest_seconds: float | None = None
    # How many have failed at least once and are waiting to be tried again.
    failing: int = 0


# ---------------------------------------------------------------- scene summaries


class SummaryState(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    # An accepted summary another one has taken the place of.
    REPLACED = "replaced"


class SummaryOrigin(StrEnum):
    AUTHOR = "author"
    MODEL = "model"
    # Proposed by the assistant and corrected by the author before being kept.
    MODEL_CORRECTED = "model_corrected"


@dataclass(frozen=True)
class SceneSummary:
    """A summary of one exact version of a scene's prose (CTX-006): a derived layer
    with its provenance, never a story fact."""

    id: str
    plan_id: str
    source_id: str
    version_id: str
    body: str
    state: SummaryState
    origin: SummaryOrigin
    created_at: datetime
    model: str | None = None
    prompt_version: str | None = None
    job_id: str | None = None
    created_by: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    # The what-if it was written in; None for the main story. A what-if uses the main
    # story's kept summary of a text it shares until it keeps one of its own.
    branch_id: str | None = None


# ---------------------------------------------------------- summaries of arcs and beats


@dataclass(frozen=True)
class PartSummary:
    """A summary of a whole arc or beat (CTX-006): a derived layer, never a story fact.

    ``basis`` is which kept scene summaries it was written from (scene id -> summary id,
    in reading order) and ``basis_digest`` their fingerprint; when a scene summary in the
    part is kept anew, replaced or dropped, the digest no longer matches and the part
    summary is out of date until it is summarised again."""

    id: str
    plan_id: str
    body: str
    state: SummaryState
    origin: SummaryOrigin
    basis: tuple[tuple[str, str], ...]
    basis_digest: str
    created_at: datetime
    model: str | None = None
    prompt_version: str | None = None
    job_id: str | None = None
    created_by: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    branch_id: str | None = None


def basis_digest(basis: Iterable[tuple[str, str]]) -> str:
    """The fingerprint of which scene summaries a part summary stands on."""
    body = "\n".join(f"{scene_id}:{summary_id}" for scene_id, summary_id in basis)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]
