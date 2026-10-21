"""A scene's checks that need no model, worked out only where their inputs changed (CON-007).

``SceneCheckRunner.run`` gives exactly what the studio's checks give (``StudioService._check``
over its brief), but:

1. it reads what the checks read (the words, the names, the brief without the continuity
   checks, the line of the story and its version, the clock and travel table) and
   fingerprints each input;
2. it works out again only the checks whose fingerprint has no kept result, and takes
   each of the others from the cache. Continuity, which reads the whole of story time,
   runs only when the story, its clock or the scene's card changed; a scene where
   nothing changed runs no check at all.

The brief is always read, never kept: whatever a check comes to read through it is in
its fingerprint, so a kept result is never used for inputs it did not see.

What each check found is kept against its fingerprint (``check_cache``), never with the
words: a finding keeps its code, message, offsets and subject, as a run does.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any

from backend.core.logging import log_event
from backend.domain.canon.plans import PlanItem
from backend.domain.check_fingerprints import (
    ALL_CHECKS,
    OTHER,
    SCENE_CHECKS,
    SceneCheck,
    brief_inputs,
    check_fingerprints,
    digest_of,
    names_fingerprint,
    prose_fingerprint,
    timeline_fingerprint,
)
from backend.domain.consistency_runs import RecordedCheck
from backend.domain.drafting import DraftBrief, DraftFinding, Severity, check_draft
from backend.domain.sources import find_mentions

logger = logging.getLogger(__name__)

# Kept results per scene and check; older ones are let go.
KEEP_PER_CHECK = 6


@dataclass
class CheckWork:
    """What the checks found, and which were worked out now and which were reused."""

    findings: list[DraftFinding]
    inputs: dict[str, str] = field(default_factory=dict)
    checks: dict[str, str] = field(default_factory=dict)
    ran: set[str] = field(default_factory=set)
    reused: set[str] = field(default_factory=set)

    @property
    def unchanged(self) -> bool:
        """Nothing a check reads has changed: every check was reused."""
        return not self.ran

    def as_json(self) -> dict[str, Any]:
        return {
            "inputs": dict(sorted(self.inputs.items())),
            "checks": dict(sorted(self.checks.items())),
            "ran": sorted(self.ran),
            "reused": sorted(self.reused),
            "unchanged": self.unchanged,
        }


def _recorded(plan_id: str, found: list[DraftFinding]) -> list[RecordedCheck]:
    return [
        RecordedCheck(
            plan_id=plan_id,
            code=item.code,
            severity=item.severity.value,
            message=item.message,
            start=item.start,
            end=item.end,
            subject=item.subject,
        )
        for item in found
    ]


def _finding(check: RecordedCheck) -> DraftFinding:
    try:
        severity = Severity(check.severity)
    except ValueError:
        severity = Severity.NOTE
    return DraftFinding(
        check.code, severity, check.message, check.start, check.end, subject=check.subject
    )


_ORDER = {Severity.WARNING: 0, Severity.NOTE: 1}


def ordered(findings: list[DraftFinding]) -> list[DraftFinding]:
    """As ``check_draft`` orders them: warnings first, then by where they are."""
    return sorted(
        findings, key=lambda f: (_ORDER[f.severity], f.start if f.start is not None else 10**9)
    )


def _sliced(check: SceneCheck, brief: DraftBrief) -> DraftBrief:
    """Only what one check reads of the brief, so running it runs nothing else's input."""
    if check.name == "secrets":
        return DraftBrief(
            point_of_view=brief.point_of_view,
            point_of_view_names=brief.point_of_view_names,
            secrets=list(brief.secrets),
            ids=dict(brief.ids),
            also=dict(brief.also),
        )
    if check.name == "knowledge":
        return DraftBrief(
            learned_later=list(brief.learned_later), ids=dict(brief.ids), also=dict(brief.also)
        )
    if check.name == "reveals":
        return DraftBrief(reveals=list(brief.reveals), ids=dict(brief.ids), also=dict(brief.also))
    if check.name == "presence":
        return DraftBrief(
            point_of_view=brief.point_of_view,
            point_of_view_names=brief.point_of_view_names,
            place=brief.place,
            place_names=brief.place_names,
            ids=dict(brief.ids),
            also=dict(brief.also),
        )
    if check.name == "length":
        return DraftBrief(words_asked=brief.words_asked)
    if check.name == OTHER.name:
        return replace(brief, continuity=[])
    return DraftBrief()


class SceneCheckRunner:
    """The studio's checks that need no model, incrementally; built on ``StudioService``."""

    def __init__(self, *, studio: Any, cache: Any) -> None:
        self._studio = studio
        self._cache = cache
        # What the last run did, for tests and the log; never author text.
        self.last: CheckWork | None = None

    async def _line(self, workspace_id: str, project_id: str) -> tuple[str, int]:
        branch = await self._studio._journal.working_branch(
            workspace_id=workspace_id, project_id=project_id
        )
        head = await self._studio._reader.get_head(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
        )
        return str(branch.id), int(head)

    async def _clock(self, workspace_id: str, project_id: str) -> tuple[str, Any]:
        from backend.services.continuity_service import how_long_of, unit_of

        clocks = getattr(self._studio._plan, "clocks", None)
        unit = await unit_of(clocks, workspace_id, project_id)
        how_long, version = await how_long_of(clocks, workspace_id, project_id)
        return unit, {"version": version, "table": how_long}

    async def run(
        self,
        workspace_id: str,
        project_id: str,
        scene: PlanItem,
        source_id: str | None,
        content: str,
        *,
        words: int | None = None,
    ) -> CheckWork:
        studio = self._studio
        from backend.services.studio_service import _phrases_meaning

        names, _, _ = await studio._sources.index_for(
            workspace_id, project_id, source_id or "", scene_id=scene.id
        )
        branch_id, head = await self._line(workspace_id, project_id)
        unit, how_long = await self._clock(workspace_id, project_id)
        inputs: dict[str, str] = {
            "prose": prose_fingerprint(content),
            "names": names_fingerprint(names),
            "timeline": timeline_fingerprint(
                branch_id=branch_id, head=head, scene=scene, unit=unit, how_long=how_long
            ),
            "length": digest_of(words),
        }
        brief = await studio.brief(workspace_id, project_id, scene, words, continuity=False)
        # Other phrases that mean the scene's people and places here, as the studio reads them.
        brief.also = {
            name: _phrases_meaning(names, entity_id) for name, entity_id in brief.ids.items()
        }
        inputs.update(brief_inputs(brief))
        fingerprints = check_fingerprints(inputs)
        kept = await self._cache.cached(
            workspace_id=workspace_id,
            project_id=project_id,
            plan_id=scene.id,
            keys=[(name, value) for name, value in fingerprints.items()],
        )
        mentions: list[Any] | None = None
        work = CheckWork(findings=[], inputs=inputs, checks=fingerprints)
        by_check: dict[str, list[DraftFinding]] = {}
        fresh: list[tuple[str, str, list[RecordedCheck]]] = []
        for check in ALL_CHECKS:
            fingerprint = fingerprints[check.name]
            if (check.name, fingerprint) in kept:
                by_check[check.name] = [
                    _finding(item) for item in kept[(check.name, fingerprint)]["findings"]
                ]
                work.reused.add(check.name)
                continue
            if check.name == "continuity":
                from backend.services.story_time_service import read_story

                story = await read_story(studio._reader, workspace_id, project_id)
                found = list(await studio._continuity(workspace_id, project_id, scene, story))
                _, read_at = await self._line(workspace_id, project_id)
                if read_at != head:
                    # The story moved on while it was read: what was found may be of a
                    # newer version than the fingerprint says, so it is not kept.
                    by_check[check.name] = found
                    work.ran.add(check.name)
                    continue
            else:
                if mentions is None:
                    mentions = find_mentions(content, names)
                reads_names = "names" in check.reads
                everything = check_draft(
                    content, _sliced(check, brief), mentions if reads_names else []
                )
                if check is OTHER:
                    found = [
                        item
                        for item in everything
                        if not any(named.owns(item.code) for named in SCENE_CHECKS)
                    ]
                else:
                    found = [item for item in everything if check.owns(item.code)]
            by_check[check.name] = found
            work.ran.add(check.name)
            fresh.append((check.name, fingerprint, _recorded(scene.id, found)))
        # Put back in the order one run of every check gives them.
        for check in ALL_CHECKS:
            work.findings.extend(by_check.get(check.name, []))
        work.findings = ordered(work.findings)
        await self._cache.keep_cached(
            workspace_id=workspace_id,
            project_id=project_id,
            plan_id=scene.id,
            entries=[
                {
                    "check": name,
                    "fingerprint": value,
                    "findings": found,
                }
                for name, value, found in fresh
            ],
            keep=KEEP_PER_CHECK,
        )
        log_event(
            logger,
            "checks.incremental",
            ran=len(work.ran),
            reused=len(work.reused),
            findings=len(work.findings),
        )
        self.last = work
        return work


__all__ = ["KEEP_PER_CHECK", "CheckWork", "SceneCheckRunner", "ordered"]
