"""Checks kept as runs, carried by a project archive (CON-001).

Each run travels with the line of the story it was made in ("main" or the what-if's id
in the archive), what it read (scene, text and version ids, which the archive keeps), the
passes it ran, what the checks that need no model found, its checking profile and its
input fingerprints. The job behind a run is not carried (jobs are left out of an
archive), so a restored run stands on its own and the assistant's notes do not come
with it. A run's id is kept; a run already there is left as it is.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.domain.consistency_runs import ConsistencyRun, RecordedCheck, RunInput

MAIN_LINE = "main"
MAX_RUNS = 2_000
MAX_TEXT = 2_000
_KINDS = {"checks", "assistant", "audit"}
_SCOPES = {"scene", "part", "story"}


class ArchiveRunsError(ValueError):
    """An archive's runs are malformed; said in plain words."""


def export_run(run: ConsistencyRun, line: str) -> dict[str, Any]:
    return {
        "id": run.id,
        "line": line,
        "kind": run.kind,
        "scope": run.scope,
        "plan_id": run.plan_id,
        "story_revision": run.story_revision,
        "inputs": [item.as_json() for item in run.inputs],
        "passes": list(run.passes),
        "findings": [item.as_json() for item in run.findings],
        "created_by": run.created_by,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "profile": dict(run.profile),
        "fingerprints": dict(run.fingerprints),
    }


def validate_runs(raw: object, lines: set[str]) -> None:
    """Refuse malformed runs before anything is written. ``lines`` are the lines of the
    story the archive holds ("main" and each what-if's id)."""
    if not isinstance(raw, list) or len(raw) > MAX_RUNS:
        raise ArchiveRunsError("the history of checks is malformed")
    seen: set[tuple[str, str]] = set()
    for run in raw:
        if not isinstance(run, dict):
            raise ArchiveRunsError("a check in the history is malformed")
        key = (str(run.get("line")), str(run.get("id")))
        if (
            not isinstance(run.get("id"), str)
            or not run["id"]
            or key in seen
            or run.get("line") not in lines
            or run.get("kind") not in _KINDS
            or run.get("scope") not in _SCOPES
            or not isinstance(run.get("story_revision"), int)
            or run["story_revision"] < 0
            or (run.get("plan_id") is not None and not isinstance(run["plan_id"], str))
            or not isinstance(run.get("inputs", []), list)
            or not isinstance(run.get("passes", []), list)
            or not isinstance(run.get("findings", []), list)
            or not isinstance(run.get("profile", {}), dict)
            or not isinstance(run.get("fingerprints", {}), dict)
        ):
            raise ArchiveRunsError("a check in the history is malformed")
        seen.add(key)
        for item in run.get("inputs", []):
            if not isinstance(item, dict) or not isinstance(item.get("plan_id"), str):
                raise ArchiveRunsError("a check in the history is malformed")
        for item in run.get("findings", []):
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("code"), str)
                or not isinstance(item.get("message", ""), str)
                or len(item.get("message", "")) > MAX_TEXT
                or len(str(item.get("subject", ""))) > MAX_TEXT
            ):
                raise ArchiveRunsError("a check in the history is malformed")
        if not all(isinstance(item, str) for item in run.get("passes", [])):
            raise ArchiveRunsError("a check in the history is malformed")
        if run.get("created_at") is not None:
            try:
                datetime.fromisoformat(str(run["created_at"]))
            except ValueError as error:
                raise ArchiveRunsError("a check in the history is malformed") from error


def restored_run(
    raw: dict[str, Any], *, workspace_id: str, project_id: str, branch_id: str
) -> ConsistencyRun:
    created = raw.get("created_at")
    return ConsistencyRun(
        workspace_id=workspace_id,
        project_id=project_id,
        id=raw["id"],
        branch_id=branch_id,
        kind=raw["kind"],
        scope=raw["scope"],
        plan_id=raw.get("plan_id"),
        story_revision=int(raw["story_revision"]),
        inputs=tuple(RunInput.from_json(item) for item in raw.get("inputs", [])),
        passes=tuple(str(item) for item in raw.get("passes", [])),
        findings=tuple(RecordedCheck.from_json(item) for item in raw.get("findings", [])),
        # The job is not carried: the run stands on its own.
        job_id=None,
        created_by=raw.get("created_by"),
        created_at=datetime.fromisoformat(str(created)) if created else None,
        profile=dict(raw.get("profile") or {}),
        fingerprints=dict(raw.get("fingerprints") or {}),
    )


class RunArchive:
    """Export and restore of the runs in every line of a story; built on the run store."""

    def __init__(self, store: Any) -> None:
        self._store = store

    async def export_runs(
        self, workspace_id: str, project_id: str, lines: dict[str, str]
    ) -> list[dict[str, Any]]:
        """``lines``: branch id → the line's name in the archive."""
        found: list[dict[str, Any]] = []
        for branch_id, line in lines.items():
            for run in await self._store.all_in(
                workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
            ):
                found.append(export_run(run, line))
        return found[:MAX_RUNS]

    async def restore_runs(
        self,
        workspace_id: str,
        project_id: str,
        raw: list[dict[str, Any]],
        branches: dict[str, str],
    ) -> int:
        """``branches``: the line's name in the archive → the branch id it was restored as.
        A run of a line that was not restored is left out."""
        restored = 0
        for item in raw:
            branch_id = branches.get(str(item.get("line")))
            if branch_id is None:
                continue
            added = await self._store.add(
                restored_run(
                    item, workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
                )
            )
            restored += 1 if added else 0
        return restored


__all__ = [
    "MAIN_LINE",
    "ArchiveRunsError",
    "RunArchive",
    "export_run",
    "restored_run",
    "validate_runs",
]
