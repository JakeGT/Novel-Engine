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
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
    ContextSection.PICTURES,
    ContextSection.WRITING,
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
}


class LeftOut(StrEnum):
    EXCLUDED = "excluded"
    BUDGET = "budget"
    SECRET = "secret"


@dataclass(frozen=True)
class ContextPolicy:
    """How a kind of request assembles its context. Changing it means a new version."""

    version: str
    default_budget: int
    min_budget: int = 300
    max_budget: int = 12_000


SCENE_DRAFT_POLICY = ContextPolicy(version="scene-draft/1", default_budget=2500)


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

    @property
    def words(self) -> int:
        return max(1, len(self.text.split()))


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
    "ContextSettings",
    "context_settings",
    "with_context_settings",
    "SECTION_ORDER",
    "SECTION_TITLES",
    "ContextItem",
    "ContextManifest",
    "ContextPolicy",
    "ContextSection",
    "LeftOut",
    "Omission",
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
