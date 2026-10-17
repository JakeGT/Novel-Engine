"""Reading a written scene back into the story (Phase 6, ADR-023).

"Update the story from this scene" is a job. It reads the scene's version as it was when
the author asked, with the same reader as any text (``IntakeWorkflow.read_passage``):
only what it can quote is kept, every change points at the exact version and words, and
the changes go to the review queue, never straight into the story.

What counts as read: every line of the version the last complete reading read, and every
line a reading since then got through before it stopped. The reading is recorded as it
goes, one passage at a time, so a reading that is stopped, fails or is interrupted is
resumed by the next one rather than repeated, and nothing it proposed is proposed again.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, replace
from typing import Any

from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.jobs import Job, JobStatus
from backend.domain.reading import changed_passages, read_keys
from backend.domain.sources import Verdict, find_mentions
from backend.domain.studio import READ_JOB, SceneReading
from backend.domain.tenancy import Permission
from backend.ports import AIUsage
from backend.services.job_runner import JobContext, JobSubmission
from backend.services.provider_route import resolve_provider

logger = logging.getLogger(__name__)

# What one reading call costs beyond the passage itself: the entries listed, the
# predicates, the rules and the answer.
TOKENS_AROUND_PASSAGE = 6_000
CHARACTERS_PER_TOKEN = 4


def passage_tokens(characters: int) -> int:
    return math.ceil(characters / CHARACTERS_PER_TOKEN) + TOKENS_AROUND_PASSAGE


@dataclass(frozen=True)
class ReadingStatus:
    """What the studio says about reading this scene into the story."""

    last: SceneReading | None
    version_number: int | None
    # True when the current text has lines no reading has read yet.
    changed_since: bool


@dataclass(frozen=True)
class _Read:
    """Where things stand: the last complete reading, the ones since, and what is read."""

    last: SceneReading | None
    since: list[SceneReading]
    seen: set[str] | None


class SceneReadingService:
    def __init__(
        self,
        *,
        studio: Any,
        store: Any,
        sources: Any,
        workflow: Any,
        canon: Any,
        journal: Any,
        runner: Any,
        settings: Any,
        credentials: Any = None,
        usage: Any = None,
        jobs: Any = None,
    ) -> None:
        self._studio = studio
        self._store = store
        self._sources = sources
        self._workflow = workflow
        self._canon = canon
        self._journal = journal
        self._runner = runner
        self._settings = settings
        self._credentials = credentials
        self._usage = usage
        self._jobs = jobs

    def register_jobs(self) -> None:
        self._runner.register(READ_JOB, self.run)

    async def _branch_id(self, workspace_id: str, project_id: str) -> str:
        branch = await self._journal.working_branch(
            workspace_id=workspace_id, project_id=project_id
        )
        return str(branch.id)

    async def _words(self, workspace_id: str, project_id: str, reading: SceneReading) -> str | None:
        """The words of the version a reading read, or None when they are no longer kept."""
        try:
            _, version, _ = await self._sources.version(
                workspace_id, project_id, reading.source_id, reading.version_id
            )
        except NotFoundError:
            return None
        return str(version.content)

    async def _read(
        self, workspace_id: str, project_id: str, branch_id: str, plan_id: str
    ) -> _Read:
        readings = await self._store.readings(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id, plan_id=plan_id
        )
        last = next((r for r in readings if r.status == "complete"), None)
        since = [r for r in readings if r.status != "complete"]
        if last is not None:
            since = [r for r in since if r.created_at >= last.created_at]
        if last is None and not since:
            return _Read(last=None, since=[], seen=None)
        seen: set[str] = set()
        if last is not None and (words := await self._words(workspace_id, project_id, last)):
            seen |= read_keys(words)
        for partial in since:
            if partial.covered and (words := await self._words(workspace_id, project_id, partial)):
                seen |= read_keys(words, partial.covered)
        return _Read(last=last, since=since, seen=seen)

    async def status(self, workspace_id: str, project_id: str, plan_id: str) -> ReadingStatus:
        """Callers have authorized reading the project."""
        branch_id = await self._branch_id(workspace_id, project_id)
        read = await self._read(workspace_id, project_id, branch_id, plan_id)
        text = await self._studio._text(workspace_id, project_id, plan_id)  # noqa: SLF001
        if text is None:
            return ReadingStatus(last=read.last, version_number=None, changed_since=False)
        _, current, versions = await self._sources.version(workspace_id, project_id, text.id)
        last = read.last
        number = next((v.number for v in versions if last and v.id == last.version_id), None)
        changed = bool(changed_passages(current.content, read.seen))
        return ReadingStatus(last=last, version_number=number, changed_since=changed)

    async def _active(self, workspace_id: str, project_id: str, plan_id: str) -> bool:
        if self._jobs is None:
            return False
        line = await self._studio._draft_branch(workspace_id, project_id)  # noqa: SLF001
        recent = await self._jobs.recent(
            workspace_id=workspace_id,
            project_id=project_id,
            kind=READ_JOB,
            plan_id=plan_id,
            limit=5,
            branch_id=line,
        )
        return any(job.status in (JobStatus.QUEUED, JobStatus.RUNNING) for job in recent)

    async def start(
        self, scope: AccessScope, workspace_id: str, project_id: str, plan_id: str
    ) -> Job:
        await self._studio._authorize(  # noqa: SLF001
            scope, workspace_id, project_id, Permission.PROJECT_WRITE
        )
        await self._studio._scene(scope, workspace_id, project_id, plan_id)  # noqa: SLF001
        text = await self._studio._text(workspace_id, project_id, plan_id)  # noqa: SLF001
        if text is None:
            raise ValidationError("There is nothing written for this scene to read yet.")
        if await self._active(workspace_id, project_id, plan_id):
            raise ValidationError("This scene is already being read into the story.")
        branch_id = await self._branch_id(workspace_id, project_id)
        read = await self._read(workspace_id, project_id, branch_id, plan_id)
        _, current, _ = await self._sources.version(workspace_id, project_id, text.id)
        if not changed_passages(current.content, read.seen):
            raise ValidationError(
                "Nothing has changed since this scene was last read into the story."
            )
        if self._usage is not None:
            await self._usage.ensure_within_limits(
                workspace_id=workspace_id, actor_user_id=scope.actor_user_id
            )
        branch = await self._journal.working_branch(
            workspace_id=workspace_id, project_id=project_id
        )
        return await self._runner.submit(  # type: ignore[no-any-return]
            scope,
            workspace_id,
            project_id,
            JobSubmission(
                kind=READ_JOB,
                payload={
                    "plan_id": plan_id,
                    "branch_id": None if branch.is_root else branch.id,
                    "source_id": text.id,
                    "version_id": current.id,
                    "base_reading_id": read.last.id if read.last else None,
                },
            ),
        )

    async def run(self, ctx: JobContext) -> dict:
        job = ctx.job
        scope = ctx.scope
        workspace_id, project_id = job.workspace_id, job.project_id
        payload = job.payload
        plan_id = payload["plan_id"]
        await ctx.step("Finding what has not been read yet", 5)
        source_id, version_id = payload["source_id"], payload["version_id"]
        # Exactly the version the author asked about, even if they have written since.
        _, version, _ = await self._sources.version(workspace_id, project_id, source_id, version_id)
        branch_id = await self._branch_id(workspace_id, project_id)
        read = await self._read(workspace_id, project_id, branch_id, plan_id)
        if (read.last.id if read.last else None) != payload.get("base_reading_id"):
            # Another reading finished first; reading this older request now would propose
            # from words the author may since have changed.
            raise ValidationError(
                "The scene was read into the story again while this was waiting. "
                "Nothing more was read."
            )
        # This job's own record, when it is being resumed after an interruption.
        mine = next((r for r in read.since if r.job_id == job.id), None)
        if mine is None:
            mine = SceneReading(
                workspace_id=workspace_id,
                project_id=project_id,
                branch_id=branch_id,
                id=f"rdg_{uuid7()}",
                plan_id=plan_id,
                source_id=source_id,
                version_id=version_id,
                job_id=job.id,
                passages=0,
                proposals=(),
                discarded=0,
                created_by=job.actor_user_id,
                created_at=job.created_at,
                status="partial",
                base_reading_id=payload.get("base_reading_id"),
            )
            await self._store.save_reading(mine)
        passages = changed_passages(version.content, read.seen)
        index, entities, _ = await self._sources.index_for(workspace_id, project_id, source_id)
        mentions = find_mentions(version.content, index)
        first = passages[0].end - passages[0].start if passages else 0
        _, api_key, base_url, sink = await resolve_provider(
            settings=self._settings,
            credentials=self._credentials,
            usage=self._usage,
            scope=scope,
            estimated_tokens=passage_tokens(first),
            idempotency_key=f"{job.id}:{job.attempts}:1",
        )
        calls = 0

        async def recorded(ai_usage: AIUsage, model: str) -> None:
            nonlocal calls
            calls += 1
            await sink(ai_usage, model, f"{job.id}:{job.attempts}:{calls}")
            await ctx.used(ai_usage.prompt_tokens, ai_usage.completion_tokens)

        head = await self._canon.get_head(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
        )
        guesses = 0
        for number, passage in enumerate(passages, start=1):
            start = passage.start
            while start < passage.end and version.content[start : passage.end].strip():
                await ctx.step(
                    f"Reading part {number} of {len(passages)}"
                    if len(passages) > 1
                    else "Reading the scene",
                    10 + int(85 * (number - 1) / max(1, len(passages))),
                )
                if self._usage is not None:
                    # Checked before every call, with what this one is expected to cost.
                    await self._usage.ensure_within_limits(
                        workspace_id=workspace_id,
                        actor_user_id=scope.actor_user_id,
                        estimated_tokens=passage_tokens(passage.end - start),
                        idempotency_key=f"{job.id}:{job.attempts}:{calls + 1}",
                    )
                async with ctx.holding():
                    result = await self._workflow.read_passage(
                        scope,
                        workspace_id,
                        project_id,
                        version=version,
                        entities=entities,
                        mentions=mentions,
                        index=index,
                        start=start,
                        end=passage.end,
                        expected_revision=head,
                        credential=api_key,
                        provider_base_url=base_url,
                        usage_sink=recorded,
                    )
                    # Recorded at once, inside the lease: whatever happens next, these words
                    # count as read and this proposal is never made again.
                    mine = replace(
                        mine,
                        covered=(*mine.covered, (start, result.end)),
                        proposals=(
                            (*mine.proposals, result.mutation.id)
                            if result.mutation is not None
                            else mine.proposals
                        ),
                        discarded=mine.discarded + len(result.discarded),
                        passages=mine.passages + 1,
                    )
                    await self._store.save_reading(mine)
                for guess in result.guesses:
                    try:
                        await self._sources.remember(
                            workspace_id,
                            project_id,
                            phrase=guess.phrase,
                            verdict=Verdict.SUGGESTED,
                            entity_id=guess.entity_id,
                            source_id=source_id,
                            actor_user_id=None,
                        )
                        guesses += 1
                    except NotFoundError:
                        # The entry it guessed was removed meanwhile; the guess is dropped.
                        continue
                if not result.more or result.end <= start:
                    break
                start = result.end
        await ctx.step("Recording what was read", 97)
        mine = replace(mine, status="complete")
        await self._store.save_reading(mine)
        log_event(
            logger,
            "studio.read",
            passages=len(passages),
            proposals=len(mine.proposals),
            discarded=mine.discarded,
        )
        return {
            "passages": len(passages),
            "proposals": list(mine.proposals),
            "discarded": mine.discarded,
            "guesses": guesses,
        }


__all__ = ["ReadingStatus", "SceneReadingService", "passage_tokens"]
