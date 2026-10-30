"""Audits on a schedule and correction plans (CON-011), as records.

A **schedule** is an audit the author opted into running every few days, with the most
each run may use. It runs as the author who set it up, in the line of the story it was
set up in, through the job queue; each run is checked again against their access, the
story's and their own limits, and the schedule's own most. A run with nothing changed
since the last one is skipped.

A **correction plan** is the assistant's suggestion of which scenes to change and why,
from an audit's findings. Every item cites the findings it answers; the author keeps or
skips each. Nothing in the story changes: a kept item is a to-do with a link to the
scene, and the author makes the change themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

MIN_EVERY_DAYS = 1
MAX_EVERY_DAYS = 90
# The most a scheduled run may be allowed to use, whatever the author asks for.
MAX_SCHEDULE_TOKENS = 2_000_000
MAX_SCHEDULES_PER_STORY = 20

# What the last run of a schedule came to, in fixed words (shown as sentences).
ScheduleOutcome = Literal[
    "started",
    "unchanged",
    "nothing_written",
    "too_many_scenes",
    "over_most",
    "allowance",
    "needs_confirmation",
    "no_provider",
    "job_cap",
    "not_allowed",
    "gone",
    "not_started",
]

OUTCOME_WORDS: dict[str, str] = {
    "started": "Started an audit.",
    "unchanged": "Nothing written there had changed since the last audit, so nothing ran.",
    "nothing_written": "Nothing in that part of the story was written, so nothing ran.",
    "too_many_scenes": "That part of the story has more written scenes than an audit reads.",
    "over_most": "It would have used more than the most you set, so it did not run.",
    "allowance": "Your allowance would not cover it, so it did not run.",
    "needs_confirmation": (
        "It would have used more than the size you asked to confirm first, so it waits for you."
    ),
    "no_provider": "No AI key is set up, so it did not run.",
    "job_cap": "You already had three pieces of work going, so it waits for the next time.",
    "not_allowed": "You can no longer change this story, so the schedule was turned off.",
    "gone": "That part of the plan, or that what-if, is no longer there, so it was turned off.",
    "not_started": "It could not start this time; it will try again next time.",
}

ItemDecision = Literal["waiting", "kept", "skipped"]


@dataclass(frozen=True)
class AuditSchedule:
    workspace_id: str
    project_id: str
    id: str
    branch_id: str | None
    plan_id: str
    passes: tuple[str, ...]
    every_days: int
    max_tokens: int
    actor_user_id: str
    auth_mode: str
    next_run_at: datetime
    enabled: bool = True
    last_run_at: datetime | None = None
    last_job_id: str | None = None
    last_outcome: str | None = None
    last_inputs: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class CorrectionItem:
    """One scene to change and why, citing the findings it answers."""

    plan_id: str
    title: str
    change: str
    why: str
    # The findings it answers: ``note:<id>`` for the assistant's, ``check:<n>`` for the
    # checks without it (their place in that scene's list), each with its words.
    findings: tuple[str, ...] = ()
    # What each of those findings said, as it was when the plan was made.
    cited: tuple[str, ...] = ()
    decision: ItemDecision = "waiting"

    def as_json(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "title": self.title,
            "change": self.change,
            "why": self.why,
            "findings": list(self.findings),
            "cited": list(self.cited),
            "decision": self.decision,
        }

    @staticmethod
    def from_json(raw: dict[str, Any]) -> CorrectionItem:
        decision = raw.get("decision")
        return CorrectionItem(
            plan_id=str(raw.get("plan_id") or ""),
            title=str(raw.get("title") or ""),
            change=str(raw.get("change") or ""),
            why=str(raw.get("why") or ""),
            findings=tuple(str(item) for item in raw.get("findings") or []),
            cited=tuple(str(item) for item in raw.get("cited") or []),
            decision=decision if decision in ("waiting", "kept", "skipped") else "waiting",
        )


@dataclass(frozen=True)
class CorrectionPlan:
    workspace_id: str
    project_id: str
    id: str
    branch_id: str | None
    audit_job_id: str
    job_id: str | None
    summary: str
    items: tuple[CorrectionItem, ...] = field(default_factory=tuple)
    model: str | None = None
    prompt_version: str | None = None
    # Items the assistant suggested that were thrown away (no finding, or a scene the
    # audit did not read).
    dropped: int = 0
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


__all__ = [
    "MAX_EVERY_DAYS",
    "MAX_SCHEDULES_PER_STORY",
    "MAX_SCHEDULE_TOKENS",
    "MIN_EVERY_DAYS",
    "OUTCOME_WORDS",
    "AuditSchedule",
    "CorrectionItem",
    "CorrectionPlan",
    "ItemDecision",
    "ScheduleOutcome",
]
