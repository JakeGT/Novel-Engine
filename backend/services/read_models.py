"""Views of the story kept ready to read, their builder, and replay verification (QRY-001).

After each commit the outbox says a line of the story moved on; the consumer
(``ReadModelConsumer``, run by the projection worker's loop) rebuilds that line's views
from its current records and stores them at the line's head revision, the watermark.
The rows are derived and never canon: nothing here writes the story.

The views earn trust by replay: ``ReadModelService.verify`` (``make read-models-verify``)
rebuilds each stored view from the record history (``story_at``, the journal's replay)
at the stored watermark and compares the two. Until a view's builder has been verified
for a line, reads keep working everything out on read, as before; once it has, reads
answer from the view when its watermark is the line's head, and fall back to working
it out when the view lags, is missing, was built by another builder version, or its
last replay differed. Every answer says which it was and at which version.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from backend.core.logging import log_event
from backend.core.metrics import metrics
from backend.domain.branching import working_in
from backend.domain.errors import ValidationError
from backend.domain.identity import AccessScope
from backend.domain.knowledge import ReaderStep, reading_order, scene_reveals
from backend.domain.read_views import (
    BUILDER_VERSION,
    MAX_DEPTH,
    VIEWS,
    Dependent,
    Snapshot,
    at_moment,
    build_views,
    checksum,
    dependents,
    differences,
    interval_of,
)
from backend.domain.tenancy import Permission
from backend.domain.worldtime import describe
from backend.ports import CanonRepositoryPort

logger = logging.getLogger(__name__)

MAX_RECORDS = 10_000
MAX_PLAN_ITEMS = 2_000
BATCH = 20
POLL_BACKOFF_START = 4.0
MAX_BACKOFF_SECONDS = 300.0
# Attempts at working an answer out while commits keep landing, before answering with
# what was read and saying it is behind.
SETTLE_ATTEMPTS = 3
KINDS = frozenset({"entity", "fact", "belief", "event", "plan_item"})

Source = Literal["view", "worked_out"]


async def read_snapshot(
    reader: CanonRepositoryPort, workspace_id: str, project_id: str
) -> Snapshot:
    """The current records of the line being worked in, from today's tables."""
    entities = await reader.list_entities(
        workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
    )
    facts = await reader.list_facts(
        workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
    )
    beliefs = await reader.list_beliefs(
        workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
    )
    events = await reader.list_events(
        workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
    )
    items = await reader.list_plan_items(
        workspace_id=workspace_id, project_id=project_id, limit=MAX_PLAN_ITEMS
    )  # type: ignore[call-arg, unused-ignore]
    return Snapshot(
        entities={entity.id: entity for entity in entities},
        facts=list(facts),
        beliefs=list(beliefs),
        events=list(events),
        plan_items=list(items),
    )


async def replayed_snapshot(
    reader: CanonRepositoryPort, workspace_id: str, project_id: str, revision: int
) -> Snapshot:
    """The records of the line being worked in at ``revision``, from the record history."""
    from backend.services.story_at_version import story_at

    state = await story_at(reader, workspace_id, project_id, revision)
    return Snapshot(
        entities=state.story.entities,
        facts=state.story.facts,
        beliefs=state.beliefs,
        events=state.story.events,
        plan_items=state.plan_items,
    )


def snapshot_cut(snapshot: Snapshot) -> bool:
    """Whether a read hit its limit, so some records may be missing from it."""
    return (
        max(
            len(snapshot.entities), len(snapshot.facts), len(snapshot.beliefs), len(snapshot.events)
        )
        >= MAX_RECORDS
        or len(snapshot.plan_items) >= MAX_PLAN_ITEMS
    )


def _counted(view: str, source: Source) -> None:
    metrics.increment(
        "read_model_reads_total",
        "Reads of the story's ready views, by view and by whether the view answered.",
        view=view,
        source=source,
    )


@dataclass(frozen=True)
class Answer:
    """Rows answering a question, and where they came from."""

    rows: list[dict[str, Any]]
    source: Source
    # The story version the rows reflect, and the line's latest version.
    version: int
    latest: int
    extra: dict[str, Any] = field(default_factory=dict)
    # True when the story has more records than one read takes, so rows may be missing.
    # Only a worked-out answer can be cut: a cut view never matches its replay, so it is
    # never trusted to answer.
    cut: bool = False


@dataclass(frozen=True)
class ViewState:
    view: str
    version: int | None
    latest: int
    rows: int
    trusted: bool
    verified_at: Any = None
    verified_version: int | None = None
    outcome: str | None = None
    differences: int | None = None

    @property
    def behind(self) -> int:
        return max(0, self.latest - (self.version or 0))

    @property
    def answers(self) -> bool:
        """Whether reads use this view now."""
        return self.trusted and self.version == self.latest


@dataclass(frozen=True)
class Verification:
    """One view's replay check; counts only, never content."""

    workspace_id: str
    project_id: str
    branch_id: str
    view: str
    revision: int
    matched: bool
    differences: int
    rows: int
    skipped: str | None = None


class ReadModelConsumer:
    """Builds the views of every line of the story the outbox says has moved on."""

    def __init__(self, *, store: Any, reader: CanonRepositoryPort) -> None:
        self._store = store
        self._reader = reader
        self._backoff: dict[tuple[str, str, str], tuple[float, float]] = {}

    async def run_once(
        self,
        *,
        now: float | None = None,
        lock: Any = None,
        budget_seconds: float | None = None,
    ) -> int:
        """Build what is waiting. Returns how many lines were brought up to date.

        ``lock`` is the journal's per-story lock (``story_lock``): a story another
        server holds is left for the next poll. With ``budget_seconds``, no new line is
        started once that long has gone (one always is), so one poll never starves the
        loop it shares.
        """
        clock = time.monotonic() if now is None else now
        started = time.monotonic()
        waiting = sum(1 for at, _ in self._backoff.values() if at > clock)
        pending = await self._store.pending(
            builder_version=BUILDER_VERSION, views=len(VIEWS), limit=BATCH + waiting
        )
        built = tried = 0
        for line in pending:
            if (
                tried
                and budget_seconds is not None
                and time.monotonic() - started >= budget_seconds
            ):
                break  # the rest are built next poll; each poll builds at least one
            key = (line.workspace_id, line.project_id, line.branch_id)
            held = self._backoff.get(key)
            if held is not None and held[0] > clock:
                continue
            tried += 1
            try:
                if lock is None:
                    done = await self.build_line(
                        line.workspace_id, line.project_id, line.branch_id, is_root=line.is_root
                    )
                else:
                    async with lock(
                        workspace_id=line.workspace_id, project_id=line.project_id
                    ) as mine:
                        if not mine:
                            continue  # another server is working on this story now
                        done = await self.build_line(
                            line.workspace_id,
                            line.project_id,
                            line.branch_id,
                            is_root=line.is_root,
                        )
            except Exception as error:  # noqa: BLE001 - one story never stops the others
                log_event(
                    logger,
                    "read_models.build_failed",
                    level=logging.WARNING,
                    error_type=type(error).__name__,
                )
                wait = min(MAX_BACKOFF_SECONDS, held[1] * 2 if held else POLL_BACKOFF_START)
                self._backoff[key] = (clock + wait, wait)
                continue
            self._backoff.pop(key, None)
            built += 1 if done else 0
        if built:
            metrics.increment(
                "read_model_builds_total", "Lines of the story whose views were built.", built
            )
        return built

    async def build_line(
        self, workspace_id: str, project_id: str, branch_id: str, *, is_root: bool
    ) -> bool:
        """Build one line's views at its head. False when a commit landed while reading
        (the next round builds the newer state)."""
        with working_in(None if is_root else branch_id):
            before = await self._reader.get_head(  # type: ignore[attr-defined]
                workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
            )
            snapshot = await read_snapshot(self._reader, workspace_id, project_id)
            after = await self._reader.get_head(  # type: ignore[attr-defined]
                workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
            )
        if before != after:
            return False
        views = await asyncio.to_thread(build_views, snapshot)
        await self._store.save(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch_id,
            revision=int(before),
            builder_version=BUILDER_VERSION,
            views=views,
            checksums={name: checksum(rows) for name, rows in views.items()},
        )
        log_event(
            logger,
            "read_models.built",
            revision=int(before),
            rows=sum(len(rows) for rows in views.values()),
            cut=snapshot_cut(snapshot),
        )
        return True


class ReadModelService:
    """Answers from the views when they are trusted and current, else works it out."""

    def __init__(
        self,
        *,
        store: Any,
        reader: CanonRepositoryPort,
        journal: Any,
        tenancy: Any = None,
        local_mode: bool = False,
    ) -> None:
        self._store = store
        self._reader = reader
        self._journal = journal
        self._tenancy = tenancy
        self._local_mode = local_mode

    async def _authorize(self, scope: AccessScope, workspace_id: str, project_id: str) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(
            scope, workspace_id, project_id, Permission.PROJECT_READ
        )

    async def _line(self, workspace_id: str, project_id: str) -> tuple[str, int]:
        branch = await self._journal.working_branch(
            workspace_id=workspace_id, project_id=project_id
        )
        head = await self._reader.get_head(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
        )
        return branch.id, int(head)

    async def _fresh(self, workspace_id: str, project_id: str, view: str) -> tuple[Any, int]:
        """The stored view when reads may use it now, else None; and the line's head."""
        branch_id, head = await self._line(workspace_id, project_id)
        stored = await self._store.get(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id, view=view
        )
        if stored is not None and stored.trusted(BUILDER_VERSION) and stored.revision == head:
            return stored, head
        return None, head

    async def _still_at(self, workspace_id: str, project_id: str, head: int) -> bool:
        """Whether the line is still at ``head`` after records were read alongside a view."""
        _, now = await self._line(workspace_id, project_id)
        return now == head

    async def rows(self, workspace_id: str, project_id: str, view: str) -> Answer:
        """One view's rows for the line being worked in: stored, or worked out now."""
        if view not in VIEWS:
            raise ValidationError("There is no such view of the story.")
        stored, head = await self._fresh(workspace_id, project_id, view)
        if stored is not None:
            _counted(view, "view")
            return Answer(rows=list(stored.rows), source="view", version=head, latest=head)
        latest = head
        for attempt in range(SETTLE_ATTEMPTS):
            snapshot = await read_snapshot(self._reader, workspace_id, project_id)
            _, latest = await self._line(workspace_id, project_id)
            if latest == head:
                break
            # A commit landed while reading, so the rows may mix two versions: read
            # again. After the last attempt the answer says it is behind (version below
            # latest) instead of claiming the newer version.
            if attempt < SETTLE_ATTEMPTS - 1:
                head = latest
        built = await asyncio.to_thread(build_views, snapshot, [view])
        _counted(view, "worked_out")
        return Answer(
            rows=built[view],
            source="worked_out",
            version=head,
            latest=latest,
            cut=snapshot_cut(snapshot),
        )

    # ------------------------------------------------------------ questions
    async def where(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        entity_id: str,
        at: float | None,
    ) -> Answer:
        """Where someone or something is, at a moment of story time (or ever)."""
        await self._authorize(scope, workspace_id, project_id)
        answer = await self.rows(workspace_id, project_id, "location")
        return _matching(answer, at_moment(answer.rows, at, entity_id=entity_id))

    async def holders(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        item_id: str | None,
        holder_id: str | None,
        at: float | None,
    ) -> Answer:
        """Who holds a thing, or what someone holds, at a moment (or ever)."""
        await self._authorize(scope, workspace_id, project_id)
        if not item_id and not holder_id:
            raise ValidationError("Say which thing, or whose belongings.")
        answer = await self.rows(workspace_id, project_id, "possession")
        match = {"item_id": item_id} if item_id else {"holder_id": holder_id or ""}
        return _matching(answer, at_moment(answer.rows, at, **match))

    async def relationships(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        entity_id: str,
        at: float | None,
    ) -> Answer:
        await self._authorize(scope, workspace_id, project_id)
        answer = await self.rows(workspace_id, project_id, "relationship")
        found = at_moment(answer.rows, at, a=entity_id) + at_moment(answer.rows, at, b=entity_id)
        return _matching(answer, found)

    async def dependents(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        record: str,
        depth: int = MAX_DEPTH,
    ) -> tuple[Answer, list[Dependent], bool]:
        """What leans on a record (``kind:id``), nearest first."""
        await self._authorize(scope, workspace_id, project_id)
        kind, _, record_id = record.partition(":")
        if kind not in KINDS or not record_id:
            raise ValidationError("Name a record as kind:id, such as entity:… or fact:….")
        answer = await self.rows(workspace_id, project_id, "dependency")
        found, cut = dependents(answer.rows, record, depth=max(1, min(depth, MAX_DEPTH)))
        return answer, found, cut

    async def disclosures(
        self, scope: AccessScope, workspace_id: str, project_id: str, fact_id: str
    ) -> Answer:
        """The scenes that show or hint at a fact, in reading order, and the first that
        shows it plainly."""
        await self._authorize(scope, workspace_id, project_id)
        answer = await self.rows(workspace_id, project_id, "disclosure")
        rows = []
        first: float | None = None
        for row in sorted(answer.rows, key=lambda item: float(item["number"])):
            how = (
                "shown"
                if fact_id in row["shown"]
                else "hinted"
                if fact_id in row["hinted"]
                else None
            )
            if how is None:
                continue
            if how == "shown" and first is None:
                first = float(row["number"])
            rows.append({"scene_id": row["scene_id"], "number": row["number"], "how": how})
        return Answer(
            rows=rows,
            source=answer.source,
            version=answer.version,
            latest=answer.latest,
            extra={"first_shown": int(first) if first is not None else None},
            cut=answer.cut,
        )

    # --------------------------------------------------------- existing reads
    async def timeline(self, workspace_id: str, project_id: str, unit: str) -> Any:
        """The timeline from the ready view, or None when it cannot answer now."""
        from backend.domain.worldtime import TimeProblem
        from backend.services.story_time_service import (
            _PROBLEM_WORDS,
            PlacedEvent,
            SpannedFact,
            TimeIssue,
            Timeline,
        )

        stored, head = await self._fresh(workspace_id, project_id, "timeline")
        if stored is None:
            _counted("timeline", "worked_out")
            return None
        events = [
            event
            for event in await self._reader.list_events(
                workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
            )
            if event.retired_revision is None
        ]
        facts = list(
            await self._reader.list_facts(
                workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
            )
        )
        rows = {(row["kind"], row["id"]): row for row in stored.rows}
        spanned = [fact for fact in facts if fact.starts is not None or fact.ends is not None]
        if (
            {key for key in rows if key[0] == "event"} != {("event", e.id) for e in events}
            or {key for key in rows if key[0] == "span"} != {("span", fact.id) for fact in spanned}
            or not await self._still_at(workspace_id, project_id, head)
        ):
            # A commit landed between the head and the records being read: the view and
            # the records would be of two versions, so it is worked out instead.
            _counted("timeline", "worked_out")
            return None
        names = {event.id: event.name for event in events}
        timeline = Timeline(unit=unit)
        order: dict[str, float] = {}
        for event in events:
            row = rows[("event", event.id)]
            interval = interval_of(row["at"])
            placed = PlacedEvent(
                event=event,
                words=describe(event.when, unit=unit, event_name=names.get),
                earliest=_finite(interval.lo) if interval else None,
                latest=_finite(interval.hi) if interval else None,
                problem=row["problem"],
            )
            if interval is None:
                timeline.unplaced.append(placed)
            else:
                timeline.placed.append(placed)
                order[event.id] = float(row["order"] or 0)
            if row["problem"]:
                problem = TimeProblem(row["problem"])
                timeline.issues.append(
                    TimeIssue(
                        problem.value, f"{event.name} {_PROBLEM_WORDS[problem]}.", (event.id,)
                    )
                )
        timeline.placed.sort(key=lambda item: order[item.event.id])
        timeline.unplaced.sort(key=lambda item: item.event.name.lower())
        for fact in spanned:
            timeline.spans.append(
                SpannedFact(
                    fact=fact,
                    starts=describe(fact.starts, unit=unit, event_name=names.get)
                    if fact.starts
                    else None,
                    ends=describe(fact.ends, unit=unit, event_name=names.get)
                    if fact.ends
                    else None,
                )
            )
        _counted("timeline", "view")
        return timeline

    async def journey(self, workspace_id: str, project_id: str) -> Any:
        """The reader's journey from the ready view, or None when it cannot answer now."""
        from backend.services.knowledge_service import Journey

        stored, head = await self._fresh(workspace_id, project_id, "disclosure")
        if stored is None:
            _counted("disclosure", "worked_out")
            return None
        items = await self._reader.list_plan_items(
            workspace_id=workspace_id, project_id=project_id, limit=MAX_PLAN_ITEMS
        )  # type: ignore[call-arg, unused-ignore]
        scenes = reading_order(items)
        steps_rows = sorted(stored.rows, key=lambda row: float(row["number"]))
        if [row["scene_id"] for row in steps_rows] != [scene.id for scene in scenes]:
            _counted("disclosure", "worked_out")
            return None
        facts = {
            fact.id: fact
            for fact in await self._reader.list_facts(
                workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
            )
            if fact.closed_revision is None
        }
        entities = await self._reader.list_entities(
            workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
        )
        if not await self._still_at(workspace_id, project_id, head):
            # A commit landed while the records were read: the view is of another version.
            _counted("disclosure", "worked_out")
            return None
        known: set[str] = set()
        steps: list[ReaderStep] = []
        for scene, row in zip(scenes, steps_rows, strict=True):
            reveals = [reveal for reveal in scene_reveals(scene.details) if reveal.fact_id in facts]
            known |= {reveal.fact_id for reveal in reveals if reveal.how == "shown"}
            steps.append(
                ReaderStep(
                    scene=scene,
                    number=int(row["number"]),
                    point_of_view=row["point_of_view"],
                    shown=reveals,
                    reader_knows=set(known),
                    irony=list(row["irony"]),
                    withheld=list(row["withheld"]),
                    repeated=_in_reveal_order(row["repeated"], reveals),
                    flashback=bool(row["flashback"]),
                )
            )
        _counted("disclosure", "view")
        return Journey(
            steps=steps, names={entity.id: entity.name for entity in entities}, facts=facts
        )

    # ------------------------------------------------------------ operations
    async def status(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> list[ViewState]:
        """Each view of the line being worked in: how far it is, and whether reads use it."""
        await self._authorize(scope, workspace_id, project_id)
        branch_id, head = await self._line(workspace_id, project_id)
        stored = {
            view.view: view
            for view in await self._store.list_views(
                workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
            )
        }
        states = []
        for name in VIEWS:
            view = stored.get(name)
            states.append(
                ViewState(
                    view=name,
                    version=view.revision if view else None,
                    latest=head,
                    rows=view.row_count if view else 0,
                    trusted=bool(view and view.trusted(BUILDER_VERSION)),
                    verified_at=view.verified_at if view else None,
                    verified_version=view.verified_revision if view else None,
                    outcome=view.verify_outcome if view else None,
                    differences=view.differences if view else None,
                )
            )
        return states

    async def verify(
        self,
        workspace_id: str,
        project_id: str,
        *,
        branch_id: str | None = None,
        repair: bool = False,
    ) -> list[Verification]:
        """Rebuild each stored view of a story from its record history and compare.

        Operator work (no access check): the command line and tests call it. With
        ``repair``, a line whose views differ is forgotten so it is built afresh; it is
        trusted again only after a later verification matches.
        """
        results: list[Verification] = []
        stored = await self._store.list_views(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
        )
        lines: dict[str, list[Any]] = {}
        for view in stored:
            lines.setdefault(view.branch_id, []).append(view)
        for line_id, views in lines.items():
            branch = await self._journal.get_branch(
                workspace_id=workspace_id, project_id=project_id, branch_id=line_id
            )
            if branch is None:
                continue
            revision = min(view.revision for view in views)
            with working_in(None if branch.is_root else line_id):
                snapshot = await replayed_snapshot(self._reader, workspace_id, project_id, revision)
            rebuilt = await asyncio.to_thread(build_views, snapshot)
            differed = False
            for summary in views:
                full = await self._store.get(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    branch_id=line_id,
                    view=summary.view,
                )
                if full is None or full.revision != revision:
                    results.append(
                        Verification(
                            workspace_id,
                            project_id,
                            line_id,
                            summary.view,
                            revision,
                            matched=False,
                            differences=0,
                            rows=0,
                            skipped="moved on while checking; checked next time",
                        )
                    )
                    continue
                if full.builder_version != BUILDER_VERSION:
                    results.append(
                        Verification(
                            workspace_id,
                            project_id,
                            line_id,
                            summary.view,
                            revision,
                            matched=False,
                            differences=0,
                            rows=full.row_count,
                            skipped="built by another version; rebuilt first",
                        )
                    )
                    continue
                fresh = rebuilt[summary.view]
                matched = checksum(fresh) == full.checksum
                count = 0 if matched else differences(full.rows, fresh)
                differed = differed or not matched
                await self._store.record_verification(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    branch_id=line_id,
                    view=summary.view,
                    revision=revision,
                    builder_version=BUILDER_VERSION,
                    matched=matched,
                    differences=count,
                )
                metrics.increment(
                    "read_model_verifications_total",
                    "Replay checks of the story's ready views, by outcome.",
                    outcome="matched" if matched else "differed",
                )
                if not matched:
                    log_event(
                        logger,
                        "read_models.verify_differed",
                        level=logging.WARNING,
                        view=summary.view,
                        differences=count,
                    )
                results.append(
                    Verification(
                        workspace_id,
                        project_id,
                        line_id,
                        summary.view,
                        revision,
                        matched=matched,
                        differences=count,
                        rows=full.row_count,
                    )
                )
            if differed and repair:
                await self._store.forget(
                    workspace_id=workspace_id, project_id=project_id, branch_id=line_id
                )
        return results

    async def forget(self, workspace_id: str, project_id: str) -> int:
        """Remove a story's views so they are built again (after a restore, say)."""
        return int(await self._store.forget(workspace_id=workspace_id, project_id=project_id))

    async def lines(self) -> list[tuple[str, str, str]]:
        return list(await self._store.lines())


def _finite(value: float) -> float | None:
    return None if math.isinf(value) else value


def _in_reveal_order(fact_ids: Iterable[str], reveals: Sequence[Any]) -> list[str]:
    wanted = set(fact_ids)
    return [
        reveal.fact_id for reveal in reveals if reveal.how == "shown" and reveal.fact_id in wanted
    ]


def _matching(answer: Answer, found: Iterable[tuple[Mapping[str, Any], Any]]) -> Answer:
    rows = []
    seen: set[int] = set()
    for row, truth in found:
        if id(row) in seen:
            continue
        seen.add(id(row))
        rows.append({**row, "holds": truth.value})
    return Answer(
        rows=rows,
        source=answer.source,
        version=answer.version,
        latest=answer.latest,
        cut=answer.cut,
    )


__all__ = [
    "Answer",
    "ReadModelConsumer",
    "ReadModelService",
    "Verification",
    "ViewState",
    "read_snapshot",
    "replayed_snapshot",
    "snapshot_cut",
]
