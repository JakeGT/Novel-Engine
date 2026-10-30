"""Notices for the author, shown in the app (CON-011).

When an audit the author scheduled finishes, it leaves a notice: what it read, what it
found and what it used, so the author learns of it on the story's home page and the
audit's own page without having to look. A notice holds counts and identifiers only;
the part of the story it audited is named when it is read, so a renamed part is named
as it is now, and no words of the story are kept here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

NoticeKind = Literal["audit_finished"]
NoticeStatus = Literal["succeeded", "failed", "cancelled"]
# The most notices one listing gives back.
MAX_LISTED = 20


@dataclass(frozen=True)
class Notice:
    workspace_id: str
    project_id: str
    id: str
    # Whom it is for: the author the scheduled audit ran as.
    user_id: str
    kind: NoticeKind
    job_id: str
    status: NoticeStatus
    branch_id: str | None = None
    schedule_id: str | None = None
    plan_id: str = ""
    scenes: int = 0
    # Notes the assistant found, and findings of the checks that need no model.
    notes: int = 0
    checks: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    created_at: datetime | None = None
    seen_at: datetime | None = None

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _count(number: int, one: str, many: str) -> str:
    return f"{number:,} {one if number == 1 else many}"


def notice_sentence(notice: Notice, part: str) -> str:
    """What a notice says, in plain words: "Your scheduled audit of “Act One” finished.
    It read 5 scenes and found 3 notes from the assistant and 2 from the checks without
    it. It used 12,400 tokens (about $0.02)." ``part`` names what it audited."""
    finished = {
        "succeeded": "finished",
        "failed": "stopped because something went wrong, and kept what it finished",
        "cancelled": "was stopped, and kept what it finished",
    }[notice.status]
    said = [f"Your scheduled audit of {part} {finished}."]
    found = (
        f"found {_count(notice.notes, 'note', 'notes')} from the assistant and "
        f"{_count(notice.checks, 'finding', 'findings')} from the checks without it"
    )
    if notice.notes == 0 and notice.checks == 0:
        found = "found nothing to look at"
    said.append(f"It read {_count(notice.scenes, 'scene', 'scenes')} and {found}.")
    if notice.tokens:
        cost = f" (about ${notice.cost_usd:,.2f})" if notice.cost_usd else ""
        said.append(f"It used {notice.tokens:,} tokens{cost}.")
    else:
        said.append("It used no tokens.")
    return " ".join(said)


__all__ = ["MAX_LISTED", "Notice", "NoticeKind", "NoticeStatus", "notice_sentence"]
