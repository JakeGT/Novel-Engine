"""Checks kept as runs (CON-001).

A run is one check of the story as a record: what it covered (a scene, a part of the plan,
or the whole story), the story's version and the exact text versions it read, and the
passes it ran. The checks that need no model are kept as they were found, since they are
otherwise worked out afresh on every read; the assistant's notes stay where they always
were (``draft_findings``), under the run's job.

What a run's findings come to now is worked out when it is read, never stored, so it
cannot drift:

* **open**: still found (or, for an assistant's note, nothing says otherwise);
* **dismissed**: the author dismissed it, with their reason (CON-010, CON-016);
* **fixed**: the words it pointed at changed, or the story changed so that the check no
  longer finds it. An assistant's note can only be fixed by its words changing: knowing
  whether a change to the story answers it would take the assistant again.

A run is **out of date** for a scene when that scene's words have changed since, or the
story bible has changed in what the scene names (its people, places and the scene's own
plan). Out of date is only a sign to check again; nothing here ever blocks a change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from backend.domain.dismissals import FindingDismissal, anchor

RunKind = Literal["checks", "assistant", "audit"]
RunScope = Literal["scene", "part", "story"]
FindingStatus = Literal["open", "dismissed", "fixed"]
# What fixed a finding: its words changed, or the story did.
FixedBy = Literal["words", "story"]

# The passes of a run of the checks that need no model.
MODEL_FREE = "model_free"
# How many runs a history shows, and how many story changes are looked through to tell
# whether a run is out of date (past that, it is taken to be).
MAX_HISTORY = 20
MAX_CHANGES = 500


@dataclass(frozen=True)
class RunInput:
    """One scene a run read, and exactly which words."""

    plan_id: str
    source_id: str | None = None
    version_id: str | None = None
    # A draft waiting to be kept, when the run checked one instead of the scene's text.
    draft_id: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "source_id": self.source_id,
            "version_id": self.version_id,
            "draft_id": self.draft_id,
        }

    @staticmethod
    def from_json(raw: dict[str, Any]) -> RunInput:
        return RunInput(
            plan_id=str(raw.get("plan_id") or ""),
            source_id=raw.get("source_id"),
            version_id=raw.get("version_id"),
            draft_id=raw.get("draft_id"),
        )


@dataclass(frozen=True)
class RecordedCheck:
    """A check that needs no model, as the run found it."""

    plan_id: str
    code: str
    severity: str
    message: str
    start: int | None = None
    end: int | None = None
    subject: str = ""

    def as_json(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "start": self.start,
            "end": self.end,
            "subject": self.subject,
        }

    @staticmethod
    def from_json(raw: dict[str, Any]) -> RecordedCheck:
        return RecordedCheck(
            plan_id=str(raw.get("plan_id") or ""),
            code=str(raw.get("code") or ""),
            severity=str(raw.get("severity") or "note"),
            message=str(raw.get("message") or ""),
            start=raw.get("start"),
            end=raw.get("end"),
            subject=str(raw.get("subject") or ""),
        )


@dataclass(frozen=True)
class ConsistencyRun:
    workspace_id: str
    project_id: str
    id: str
    branch_id: str
    kind: RunKind
    scope: RunScope
    # The scene or part of the plan; None for the whole story.
    plan_id: str | None
    story_revision: int
    inputs: tuple[RunInput, ...] = ()
    passes: tuple[str, ...] = ()
    findings: tuple[RecordedCheck, ...] = ()
    job_id: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None

    def touches(self, plan_id: str) -> bool:
        return any(item.plan_id == plan_id for item in self.inputs)


def _normal(words: str) -> str:
    return " ".join(words.split())


def words_gone(then: str, now: str | None, start: int | None, end: int | None) -> bool:
    """Whether the sentence a finding pointed at is no longer in the text."""
    passage = anchor(then, start, end)
    if not passage:
        return False
    return now is None or passage not in _normal(now)


@dataclass(frozen=True)
class Judged:
    status: FindingStatus
    fixed_by: FixedBy | None = None
    dismissal: FindingDismissal | None = None


def judge_check(
    *,
    key: str,
    then: str,
    now: str | None,
    start: int | None,
    end: int | None,
    found_now: set[str],
    dismissed: dict[str, FindingDismissal],
) -> Judged:
    """A check that needs no model: fixed when it is no longer found, by the words or the
    story changing; else dismissed when the author said so; else open."""
    if key not in found_now:
        changed = now is None or now != then
        span = anchor(then, start, end)
        by: FixedBy = (
            "words" if changed and (not span or words_gone(then, now, start, end)) else "story"
        )
        return Judged("fixed", by)
    if key in dismissed:
        return Judged("dismissed", dismissal=dismissed[key])
    return Judged("open")


def judge_note(
    *,
    key: str,
    then: str,
    now: str | None,
    start: int | None,
    end: int | None,
    dismissed: dict[str, FindingDismissal],
) -> Judged:
    """An assistant's note: fixed only when the words it quoted are gone."""
    if words_gone(then, now, start, end):
        return Judged("fixed", "words")
    if key in dismissed:
        return Judged("dismissed", dismissal=dismissed[key])
    return Judged("open")


@dataclass
class Staleness:
    """Why a run may be out of date for one scene, if it is."""

    words_changed: bool = False
    story_changed: bool = False
    # The scene, its text or the draft it read is no longer there.
    gone: bool = False

    @property
    def stale(self) -> bool:
        return self.words_changed or self.story_changed or self.gone

    def reasons(self) -> list[str]:
        found: list[str] = []
        if self.gone:
            found.append("What it read is no longer there.")
        if self.words_changed:
            found.append("The words have changed since.")
        if self.story_changed:
            found.append("The story bible has changed since, in what this scene names.")
        return found


def story_change_touches(change: dict[str, Any], record_id: str, about: set[str]) -> bool:
    """Whether one recorded change to the story is about anything in ``about``: the record
    itself, or any entry or plan item its stored change names."""
    if record_id in about:
        return True
    return _mentions(change, about)


def _mentions(value: Any, about: set[str]) -> bool:
    if isinstance(value, str):
        return value in about
    if isinstance(value, dict):
        return any(_mentions(item, about) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_mentions(item, about) for item in value)
    return False


@dataclass
class SceneTally:
    open: int = 0
    dismissed: int = 0
    fixed: int = 0

    def add(self, status: FindingStatus) -> None:
        setattr(self, status, getattr(self, status) + 1)


@dataclass
class ScenePart:
    """What one run found in one scene, judged against the scene as it is now."""

    input: RunInput
    title: str
    in_plan: bool
    staleness: Staleness = field(default_factory=Staleness)
    tally: SceneTally = field(default_factory=SceneTally)


__all__ = [
    "MAX_CHANGES",
    "MAX_HISTORY",
    "MODEL_FREE",
    "ConsistencyRun",
    "FindingStatus",
    "FixedBy",
    "Judged",
    "RecordedCheck",
    "RunInput",
    "RunKind",
    "RunScope",
    "ScenePart",
    "SceneTally",
    "Staleness",
    "judge_check",
    "judge_note",
    "story_change_touches",
    "words_gone",
]
