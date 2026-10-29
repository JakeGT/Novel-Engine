"""Review and wider dismissals, carried by a project archive (CON-016).

A scene's own dismissals already travel with the writing of each line of the story. This
carries the other two kinds:

* **On the review screen** (``ReviewDismissal``): an advisory finding the author dismissed
  on a proposal. It travels with the saved change that proposal became (its commit id),
  since a proposal is restored as the change it was saved as; one on a proposal that was
  never saved is not carried (the proposal itself is not).
* **Beyond one scene** (``ScopedDismissal``): a kind of finding dismissed for a character
  or a stretch of scenes, in the line of the story it was made in ("main" or the
  what-if's id in the archive).

Withdrawn ones travel too, so a restored story keeps the same record, and the review
screen's "how often" counts stay true. Each holds digests of what it was about, never the
words; ids are kept, and one already there is left as it is, so a restore can be run again.
Who made one is not trusted from the archive: each is restored as the restoring author's.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from backend.domain.dismissals import (
    MAX_NOTE,
    MAX_SCOPED_DISMISSALS,
    DismissalScope,
    DismissReason,
    FindingKind,
    ReviewDismissal,
    ScopedDismissal,
)

MAX_REVIEW = 5_000
_DIGEST = re.compile(r"[0-9a-f]{64}")
_REASONS = {item.value for item in DismissReason}
_KINDS = {item.value for item in FindingKind}
_SCOPES = {item.value for item in DismissalScope}


class ArchiveDismissalsError(ValueError):
    """An archive's dismissals are malformed; said in plain words."""


def _when(value: object) -> datetime | None:
    return datetime.fromisoformat(str(value)) if value else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def export_review(item: ReviewDismissal, commit_id: str) -> dict[str, Any]:
    return {
        "id": item.id,
        "commit_id": commit_id,
        "fingerprint": item.fingerprint,
        "depends_on": item.depends_on,
        "code": item.code,
        "reason": item.reason.value,
        "note": item.note,
        "created_by": item.created_by,
        "created_at": item.created_at.isoformat(),
        "withdrawn_at": _iso(item.withdrawn_at),
    }


def export_scoped(item: ScopedDismissal, line: str) -> dict[str, Any]:
    return {
        "id": item.id,
        "line": line,
        "kind": item.kind.value,
        "code": item.code,
        "scope": item.scope.value,
        "entity_id": item.entity_id,
        "from_plan_id": item.from_plan_id,
        "to_plan_id": item.to_plan_id,
        "reason": item.reason.value,
        "note": item.note,
        "expires_at": _iso(item.expires_at),
        "created_by": item.created_by,
        "created_at": item.created_at.isoformat(),
        "withdrawn_at": _iso(item.withdrawn_at),
    }


def _times(raw: dict[str, Any], keys: tuple[str, ...], what: str) -> None:
    try:
        for key in keys:
            if key == "created_at" or raw.get(key) is not None:
                datetime.fromisoformat(str(raw.get(key)))
    except ValueError as error:
        raise ArchiveDismissalsError(f"{what} is malformed") from error


def _common(raw: object, what: str) -> dict[str, Any]:
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("id"), str)
        or not raw["id"]
        or len(raw["id"]) > 200
        or not isinstance(raw.get("code"), str)
        or not 1 <= len(raw["code"]) <= 120
        or raw.get("reason") not in _REASONS
        or not isinstance(raw.get("note", ""), str)
        or len(raw.get("note", "")) > MAX_NOTE
        or (raw.get("created_by") is not None and not isinstance(raw["created_by"], str))
    ):
        raise ArchiveDismissalsError(f"{what} is malformed")
    return raw


def validate_review(raw: object) -> None:
    """Refuse malformed review dismissals before anything is written."""
    what = "a dismissed finding on the review screen"
    if not isinstance(raw, list) or len(raw) > MAX_REVIEW:
        raise ArchiveDismissalsError("the dismissed findings on the review screen are malformed")
    seen: set[str] = set()
    for item in raw:
        entry = _common(item, what)
        if (
            entry["id"] in seen
            or not isinstance(entry.get("commit_id"), str)
            or not entry["commit_id"]
            or not _DIGEST.fullmatch(str(entry.get("fingerprint")))
            or not _DIGEST.fullmatch(str(entry.get("depends_on")))
        ):
            raise ArchiveDismissalsError(f"{what} is malformed")
        seen.add(entry["id"])
        _times(entry, ("created_at", "withdrawn_at"), what)


def validate_scoped(raw: object, lines: set[str]) -> None:
    """Refuse malformed wider dismissals before anything is written. ``lines`` are the
    lines of the story the archive holds ("main" and each what-if's id)."""
    what = "a finding dismissed beyond one scene"
    if not isinstance(raw, list) or len(raw) > MAX_SCOPED_DISMISSALS * (len(lines) or 1):
        raise ArchiveDismissalsError("the findings dismissed beyond one scene are malformed")
    seen: set[tuple[str, str]] = set()
    counts: dict[str, int] = {}
    for item in raw:
        entry = _common(item, what)
        key = (str(entry.get("line")), entry["id"])
        scope = entry.get("scope")

        def text(name: str, entry: dict[str, Any] = entry) -> bool:
            value = entry.get(name)
            return isinstance(value, str) and 0 < len(value) <= 200

        if (
            key in seen
            or entry.get("line") not in lines
            or entry.get("kind") not in _KINDS
            or scope not in _SCOPES
            or (scope == DismissalScope.CHARACTER.value and not text("entity_id"))
            or (
                scope == DismissalScope.RANGE.value
                and not (text("from_plan_id") and text("to_plan_id"))
            )
        ):
            raise ArchiveDismissalsError(f"{what} is malformed")
        seen.add(key)
        counts[key[0]] = counts.get(key[0], 0) + 1
        if counts[key[0]] > MAX_SCOPED_DISMISSALS:
            raise ArchiveDismissalsError("the findings dismissed beyond one scene are malformed")
        _times(entry, ("created_at", "expires_at", "withdrawn_at"), what)


class DismissalArchive:
    """Export and restore of review and wider dismissals; built on their stores."""

    def __init__(self, *, review: Any = None, scoped: Any = None) -> None:
        self._review = review
        self._scoped = scoped

    async def export_review(
        self, workspace_id: str, project_id: str, mutation_commits: dict[str, str]
    ) -> list[dict[str, Any]]:
        """Dismissals on proposals that were saved, by the change each became."""
        if self._review is None:
            return []
        found = [
            export_review(item, mutation_commits[item.mutation_id])
            for item in await self._review.for_project(
                workspace_id=workspace_id, project_id=project_id
            )
            if item.mutation_id in mutation_commits
        ]
        return found[:MAX_REVIEW]

    async def export_scoped(
        self, workspace_id: str, project_id: str, lines: dict[str, str]
    ) -> list[dict[str, Any]]:
        """``lines``: branch id → the line's name in the archive."""
        if self._scoped is None:
            return []
        found: list[dict[str, Any]] = []
        for branch_id, line in lines.items():
            items = await self._scoped.list(
                workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
            )
            found.extend(export_scoped(item, line) for item in items[:MAX_SCOPED_DISMISSALS])
        return found

    async def restore_review(
        self,
        workspace_id: str,
        project_id: str,
        raw: list[dict[str, Any]],
        mutation_for: Callable[[str | None], Awaitable[str | None]],
        *,
        actor_user_id: str | None = None,
    ) -> int:
        """Each onto the restored proposal behind its saved change, as the restoring
        author's (``actor_user_id``); one whose change was not restored is left out."""
        if self._review is None:
            return 0
        restored = 0
        for item in raw:
            mutation_id = await mutation_for(item.get("commit_id"))
            if mutation_id is None:
                continue
            added = await self._review.restore(
                ReviewDismissal(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    id=item["id"],
                    mutation_id=mutation_id,
                    fingerprint=item["fingerprint"],
                    depends_on=item["depends_on"],
                    code=item["code"],
                    reason=DismissReason(item["reason"]),
                    note=item.get("note", ""),
                    # Who the archive names is not trusted.
                    created_by=actor_user_id,
                    created_at=datetime.fromisoformat(item["created_at"]),
                    withdrawn_at=_when(item.get("withdrawn_at")),
                )
            )
            restored += 1 if added else 0
        return restored

    async def restore_scoped(
        self,
        workspace_id: str,
        project_id: str,
        raw: list[dict[str, Any]],
        branches: dict[str, str],
        *,
        actor_user_id: str | None = None,
    ) -> int:
        """``branches``: the line's name in the archive → the branch id it was restored as.
        A dismissal of a line that was not restored is left out. Each is restored as the
        restoring author's (``actor_user_id``)."""
        if self._scoped is None:
            return 0
        restored = 0
        for item in raw:
            branch_id = branches.get(str(item.get("line")))
            if branch_id is None:
                continue
            added = await self._scoped.restore(
                ScopedDismissal(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    branch_id=branch_id,
                    id=item["id"],
                    kind=FindingKind(item["kind"]),
                    code=item["code"],
                    scope=DismissalScope(item["scope"]),
                    reason=DismissReason(item["reason"]),
                    note=item.get("note", ""),
                    created_at=datetime.fromisoformat(item["created_at"]),
                    entity_id=item.get("entity_id"),
                    from_plan_id=item.get("from_plan_id"),
                    to_plan_id=item.get("to_plan_id"),
                    expires_at=_when(item.get("expires_at")),
                    created_by=actor_user_id,
                    withdrawn_at=_when(item.get("withdrawn_at")),
                )
            )
            restored += 1 if added else 0
        return restored


__all__ = [
    "ArchiveDismissalsError",
    "DismissalArchive",
    "export_review",
    "export_scoped",
    "validate_review",
    "validate_scoped",
]
