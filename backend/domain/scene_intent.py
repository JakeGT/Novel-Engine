"""What the viewpoint character wants in a scene, what stands in the way, and how it ends (STY-003).

The shape is the common goal–conflict–outcome pattern of scene writing (Dwight Swain's
"scene and sequel", taught in Jack Bickham's *Scene & Structure*): the goal and what
stands in the way raise a question, and the outcome answers it. Guides name four
answers: yes; no; yes, but (they get it, with a complication); and no, and (they do
not, and things get worse). A plain yes or no is allowed, though guides note it tends
to stall a story; the hint on screen says so without enforcing anything.

All of it is optional, written by the author, and kept in the scene's details under a
reserved key, like its conditions, so it has history and undo and needs no new column.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

INTENT_KEY = "_intent"
# Kept short, and inside the plan's 2,000-character limit for one detail even when escaped.
MAX_INTENT_LENGTH = 400

Outcome = Literal["yes", "no", "yes_but", "no_and"]

# How each outcome reads to the assistant and to the author.
OUTCOME_WORDS: dict[str, str] = {
    "yes": "they get what they want",
    "no": "they do not get it",
    "yes_but": "they get it, but with a cost or a new complication",
    "no_and": "they do not get it, and things get worse",
}


class SceneIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # What the viewpoint character wants in this scene.
    goal: str = Field(default="", max_length=MAX_INTENT_LENGTH)
    # Who or what stands in the way.
    obstacle: str = Field(default="", max_length=MAX_INTENT_LENGTH)
    # How it ends for them: yes, no, yes_but or no_and. Null when not decided.
    outcome: Outcome | None = None

    def is_empty(self) -> bool:
        return not self.goal.strip() and not self.obstacle.strip() and self.outcome is None

    def cleaned(self) -> SceneIntent:
        return SceneIntent(
            goal=" ".join(self.goal.split()),
            obstacle=" ".join(self.obstacle.split()),
            outcome=self.outcome,
        )


def scene_intent(details: dict[str, str]) -> SceneIntent | None:
    """A scene's intent, or None. Anything unreadable is treated as none."""
    raw = details.get(INTENT_KEY)
    if not raw:
        return None
    try:
        intent = SceneIntent.model_validate_json(raw)
    except (ValidationError, ValueError):
        return None
    return None if intent.is_empty() else intent


def with_intent(details: dict[str, str], intent: SceneIntent | None) -> dict[str, str]:
    kept = {key: value for key, value in details.items() if key != INTENT_KEY}
    if intent is not None:
        cleaned = intent.cleaned()
        if not cleaned.is_empty():
            kept[INTENT_KEY] = cleaned.model_dump_json(exclude_defaults=True)
    return kept


def intent_sentences(intent: SceneIntent, who: str | None) -> str:
    """The intent in plain sentences, for the assistant's context."""
    person = who or "The viewpoint character"
    parts: list[str] = []
    if intent.goal:
        parts.append(f"{person} wants: {intent.goal.rstrip('.')}.")
    if intent.obstacle:
        parts.append(f"In the way: {intent.obstacle.rstrip('.')}.")
    if intent.outcome is not None:
        parts.append(f"How it ends: {OUTCOME_WORDS[intent.outcome]}.")
    return " ".join(parts)


__all__ = [
    "INTENT_KEY",
    "MAX_INTENT_LENGTH",
    "OUTCOME_WORDS",
    "Outcome",
    "SceneIntent",
    "intent_sentences",
    "scene_intent",
    "with_intent",
]
