"""Comparing a what-if with the main story: an audit of the differences (CON-011).

Worked out on read, with no model, from both lines of the story as they stand:

* **The story bible**: entries added, archived or changed (name, kind, details, other
  names), facts true in one and not the other, and events added, removed or moved.
* **The plan**: parts added, removed or changed (title, summary, who and where, details).
* **The writing**: scenes whose words differ, with how many words each has, and how the
  what-if's version of a changed scene sounds against the author's usual (CON-018).
* **The checks**: continuity findings and the plan's own problems found in the what-if
  and not in the main story (new there), and the other way round (gone there).

Every difference names the record, never an identifier. Nothing is changed in either
line; moving a change from the what-if into the main story is the what-if page's own
"Bring into the main story", reviewed like any change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from backend.core.logging import log_event
from backend.domain.branching import working_in
from backend.domain.canon.phrasing import phrase
from backend.domain.errors import NotFoundError
from backend.domain.identity import AccessScope
from backend.domain.tenancy import Permission

logger = logging.getLogger(__name__)

MAX_LISTED = 100


@dataclass
class Difference:
    # entry, fact, event, plan, scene, finding
    kind: str
    # added (only in the what-if), removed (only in the main story), changed
    change: str
    title: str
    detail: str = ""
    plan_id: str | None = None


@dataclass
class SceneDifference:
    plan_id: str
    title: str
    main_words: int
    what_if_words: int
    # How the what-if's version sounds against the usual (CON-018), in sentences.
    voice: list[str] = field(default_factory=list)


@dataclass
class Comparison:
    what_if_id: str
    what_if_name: str
    bible: list[Difference] = field(default_factory=list)
    plan: list[Difference] = field(default_factory=list)
    scenes: list[SceneDifference] = field(default_factory=list)
    new_findings: list[str] = field(default_factory=list)
    gone_findings: list[str] = field(default_factory=list)
    # When a list was cut short, what was left out.
    left_out: list[str] = field(default_factory=list)

    @property
    def same(self) -> bool:
        return not (
            self.bible or self.plan or self.scenes or self.new_findings or self.gone_findings
        )


def _fact_reading(fact: Any, names: dict[str, str]) -> str:
    proposition = fact.proposition
    other_id = getattr(proposition.object, "entity_id", None)
    value = getattr(proposition.object, "value", None)
    other = names.get(other_id, "an entry") if other_id else ("" if value is None else str(value))
    return f"{names.get(proposition.subject_id, 'an entry')} {phrase(proposition.predicate)} {other}".strip()


def _fact_shape(fact: Any) -> Any:
    return (
        fact.proposition.model_dump(mode="json"),
        getattr(fact, "starts", None) and str(fact.starts),
        getattr(fact, "ends", None) and str(fact.ends),
        str(getattr(fact, "visibility", "")),
    )


class WhatIfAudit:
    def __init__(self, *, studio: Any, voice: Any = None) -> None:
        self._studio = studio
        self._voice = voice

    async def _line(self, workspace_id: str, project_id: str) -> dict[str, Any]:
        """Everything one line of the story holds, read in the current line."""
        from backend.services.continuity_service import story_continuity
        from backend.services.story_time_service import read_story

        studio = self._studio
        story = await read_story(studio._reader, workspace_id, project_id)
        items = list(
            await studio._reader.list_plan_items(workspace_id=workspace_id, project_id=project_id)
        )
        findings = await story_continuity(
            studio._reader,
            getattr(studio._plan, "clocks", None),
            workspace_id,
            project_id,
            story=story,
            items=items,
        )
        links = dict(
            await studio._store.manuscripts_for(
                workspace_id=workspace_id,
                project_id=project_id,
                branch_id=await studio._branch_id(workspace_id, project_id),
            )
        )
        return {"story": story, "items": items, "continuity": findings, "links": links}

    async def compare(
        self, scope: AccessScope, workspace_id: str, project_id: str, what_if_id: str
    ) -> Comparison:
        studio = self._studio
        await studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        branch = await studio._journal.get_branch(
            workspace_id=workspace_id, project_id=project_id, branch_id=what_if_id
        )
        if branch is None or branch.is_root:
            raise NotFoundError("That what-if was not found.")
        with working_in(None):
            main = await self._line(workspace_id, project_id)
            main_problems = await self._problems(scope, workspace_id, project_id)
        with working_in(branch.id):
            other = await self._line(workspace_id, project_id)
            other_problems = await self._problems(scope, workspace_id, project_id)
        found = Comparison(what_if_id=branch.id, what_if_name=str(getattr(branch, "name", "")))
        self._bible(found, main["story"], other["story"])
        self._plan(found, main["items"], other["items"])
        await self._scenes(found, workspace_id, project_id, main, other, branch.id)
        main_seen = {item.message for item in main["continuity"]} | set(main_problems)
        other_seen = {item.message for item in other["continuity"]} | set(other_problems)
        found.new_findings = sorted(other_seen - main_seen)[:MAX_LISTED]
        found.gone_findings = sorted(main_seen - other_seen)[:MAX_LISTED]
        for label, items in (("the story bible", found.bible), ("the plan", found.plan)):
            if len(items) > MAX_LISTED:
                found.left_out.append(
                    f"Only the first {MAX_LISTED} differences in {label} are listed."
                )
                del items[MAX_LISTED:]
        log_event(
            logger,
            "audit.what_if_compared",
            bible=len(found.bible),
            plan=len(found.plan),
            scenes=len(found.scenes),
            new=len(found.new_findings),
            gone=len(found.gone_findings),
        )
        return found

    async def _problems(self, scope: AccessScope, workspace_id: str, project_id: str) -> list[str]:
        try:
            reading = await self._studio._plan.outline(scope, workspace_id, project_id)
        except Exception as error:  # noqa: BLE001 - the plan's own checks are extra here
            log_event(logger, "audit.what_if_plan_skipped", error_type=type(error).__name__)
            return []
        return [item.message for item in reading.open if item.kind == "problem"]

    @staticmethod
    def _bible(found: Comparison, main: Any, other: Any) -> None:
        names = {
            **{key: entity.name for key, entity in main.entities.items()},
            **{key: entity.name for key, entity in other.entities.items()},
        }
        for entity_id in sorted(set(main.entities) | set(other.entities), key=lambda k: names[k]):
            before, after = main.entities.get(entity_id), other.entities.get(entity_id)
            if before is None or (not before.is_active and after is not None and after.is_active):
                if after is not None and after.is_active:
                    found.bible.append(Difference("entry", "added", after.name, after.type))
                continue
            if after is None or (before.is_active and not after.is_active):
                found.bible.append(
                    Difference("entry", "removed", before.name, "Archived in the what-if")
                )
                continue
            changed = []
            if before.name != after.name:
                changed.append(f"called “{after.name}” instead of “{before.name}”")
            if before.type != after.type:
                changed.append(f"a {after.type} instead of a {before.type}")
            if before.attributes != after.attributes:
                changed.append("its details differ")
            if sorted(before.aliases) != sorted(after.aliases):
                changed.append("its other names differ")
            if changed:
                found.bible.append(Difference("entry", "changed", before.name, "; ".join(changed)))
        active_main = {fact.id: fact for fact in main.facts if fact.closed_revision is None}
        active_other = {fact.id: fact for fact in other.facts if fact.closed_revision is None}
        for fact_id in sorted(set(active_main) | set(active_other)):
            before, after = active_main.get(fact_id), active_other.get(fact_id)
            if before is None and after is not None:
                found.bible.append(
                    Difference(
                        "fact", "added", _fact_reading(after, names), "True in the what-if only"
                    )
                )
            elif after is None and before is not None:
                found.bible.append(
                    Difference(
                        "fact",
                        "removed",
                        _fact_reading(before, names),
                        "True in the main story only",
                    )
                )
            elif (
                before is not None
                and after is not None
                and _fact_shape(before) != _fact_shape(after)
            ):
                found.bible.append(
                    Difference(
                        "fact",
                        "changed",
                        _fact_reading(after, names),
                        "When it holds, or who knows, differs",
                    )
                )
        events_main = {event.id: event for event in main.events}
        events_other = {event.id: event for event in other.events}
        for event_id in sorted(set(events_main) | set(events_other)):
            before, after = events_main.get(event_id), events_other.get(event_id)
            if before is None and after is not None:
                found.bible.append(Difference("event", "added", after.name))
            elif after is None and before is not None:
                found.bible.append(Difference("event", "removed", before.name))
            elif before is not None and after is not None:
                if before.model_dump(
                    exclude={"branch_id", "created_revision", "provenance_id"}
                ) != (after.model_dump(exclude={"branch_id", "created_revision", "provenance_id"})):
                    found.bible.append(
                        Difference("event", "changed", after.name, "Who, where or when differs")
                    )

    @staticmethod
    def _plan(found: Comparison, main: list[Any], other: list[Any]) -> None:
        before = {item.id: item for item in main if item.is_active}
        after = {item.id: item for item in other if item.is_active}
        order = {item.id: index for index, item in enumerate([*main, *other])}
        for plan_id in sorted(set(before) | set(after), key=lambda key: order.get(key, 0)):
            a, b = before.get(plan_id), after.get(plan_id)
            if a is None and b is not None:
                found.plan.append(Difference("plan", "added", b.title, b.kind.value, plan_id))
            elif b is None and a is not None:
                found.plan.append(Difference("plan", "removed", a.title, a.kind.value, plan_id))
            elif a is not None and b is not None:
                changed = []
                if a.title != b.title:
                    changed.append(f"called “{b.title}” instead of “{a.title}”")
                if a.summary != b.summary:
                    changed.append("what happens differs")
                if sorted((r.entity_id, r.role) for r in a.refs) != sorted(
                    (r.entity_id, r.role) for r in b.refs
                ):
                    changed.append("who or where differs")
                if a.details != b.details:
                    changed.append("its details differ")
                if a.parent_id != b.parent_id or getattr(a, "position", None) != getattr(
                    b, "position", None
                ):
                    changed.append("it sits elsewhere in the plan")
                if changed:
                    found.plan.append(
                        Difference("plan", "changed", b.title, "; ".join(changed), plan_id)
                    )

    async def _scenes(
        self,
        found: Comparison,
        workspace_id: str,
        project_id: str,
        main: dict[str, Any],
        other: dict[str, Any],
        branch_id: str,
    ) -> None:
        studio = self._studio
        titles = {item.id: item.title for item in [*main["items"], *other["items"]]}
        order = {item.id: index for index, item in enumerate([*main["items"], *other["items"]])}
        scene_ids = sorted(
            set(main["links"]) | set(other["links"]), key=lambda key: order.get(key, 0)
        )

        async def words(source_id: str | None) -> tuple[int, Any]:
            if not source_id:
                return 0, None
            try:
                _, version, _ = await studio._sources.version(workspace_id, project_id, source_id)
            except NotFoundError:
                return 0, None
            return len(version.content.split()), version

        for plan_id in scene_ids:
            first, second = main["links"].get(plan_id), other["links"].get(plan_id)
            if first == second:
                continue  # the what-if still shares the main story's words
            main_words, main_version = await words(first)
            what_if_words, version = await words(second)
            if (
                version is not None
                and main_version is not None
                and main_version.content == version.content
            ):
                continue
            item = SceneDifference(
                plan_id=plan_id,
                title=titles.get(plan_id, "A scene"),
                main_words=main_words,
                what_if_words=what_if_words,
            )
            if self._voice is not None and version is not None:
                with working_in(branch_id):
                    measured = await self._voice.for_versions(
                        workspace_id,
                        project_id,
                        [{"plan_id": plan_id, "source_id": second, "version_id": version.id}],
                    )
                item.voice = [note.message for _, notes in measured.rows for note in notes]
            found.scenes.append(item)


__all__ = ["Comparison", "Difference", "SceneDifference", "WhatIfAudit"]
