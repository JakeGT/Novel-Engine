"""What must be true before a planned scene, and what it should leave true afterwards.

A condition is about one entry and uses the same checks as a story rule, run by
the same evaluator, so a scene's conditions and the story's rules can never
disagree about what a fact pattern means. Conditions are part of the plan
(intent), kept with the scene they belong to and governed like the rest of it.

"Before" conditions say whether the story is ready for the scene. "After"
conditions say whether what the scene was meant to change has been recorded.
Neither ever blocks anything.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.domain.rules import Check, RuleBody, Selector, World, read_rule, violations

# Kept inside the scene's details under a reserved key, like an entry's marks.
CONDITIONS_KEY = "_conditions"
MAX_CONDITIONS = 6


class SceneCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    when: Literal["before", "after"]
    entity_id: str = Field(min_length=1, max_length=200)
    check: Check


@dataclass(frozen=True)
class ConditionState:
    plan_id: str
    when: str
    # In ordinary words: "Mara must have this: it is located at Lantern Harbor."
    reading: str
    # None when the entry it is about is no longer in the story bible.
    holds: bool | None


def scene_conditions(details: dict[str, str]) -> list[SceneCondition]:
    """A scene's conditions. Anything unreadable is treated as no conditions."""
    raw = details.get(CONDITIONS_KEY)
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    found: list[SceneCondition] = []
    for item in items if isinstance(items, list) else []:
        try:
            found.append(SceneCondition.model_validate(item))
        except ValidationError:
            continue
    return found[:MAX_CONDITIONS]


def with_conditions(details: dict[str, str], conditions: list[SceneCondition]) -> dict[str, str]:
    kept = {key: value for key, value in details.items() if key != CONDITIONS_KEY}
    if conditions:
        kept[CONDITIONS_KEY] = json.dumps(
            [item.model_dump(exclude_none=True) for item in conditions], separators=(",", ":")
        )
    return kept


def plain_details(details: dict[str, str]) -> dict[str, str]:
    """A plan item's own notes, without anything kept under a reserved key."""
    return {key: value for key, value in details.items() if not key.startswith("_")}


def condition_body(condition: SceneCondition) -> RuleBody:
    return RuleBody(applies_to=Selector(entity_id=condition.entity_id), checks=[condition.check])


def condition_state(plan_id: str, condition: SceneCondition, world: World) -> ConditionState:
    names = {entry.id: entry.name for entry in world.entries.values()}
    holds: bool | None = None
    if condition.entity_id in world.entries:
        holds = not violations(plan_id, condition_body(condition), world)
    return ConditionState(
        plan_id=plan_id,
        when=condition.when,
        reading=read_rule(condition_body(condition), names),
        holds=holds,
    )
