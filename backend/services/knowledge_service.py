"""Who knows what, telling, and what the reader is shown (ADR-020).

Reads are derived each time from facts, beliefs and the plan, so they cannot
drift. The one write, telling, is an ordinary reviewed change: an event of one
character telling others, and a belief for each listener, saved as one version.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.adapters.system import uuid7
from backend.domain.canon.assertions import Belief, Fact, FactVisibility
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import EventParticipant
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.propositions import Proposition
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.knowledge import (
    CharacterKnowledge,
    FactKnowledge,
    ReaderStep,
    knowledge_of,
    reader_journey,
    reading_order,
    scene_time,
    who_knows,
)
from backend.domain.operations import AssertBeliefOp, CanonOperation, CreateEventOp
from backend.domain.tenancy import Permission
from backend.domain.worldtime import Interval, Order, compare
from backend.ports import CanonRepositoryPort
from backend.services.mutation_service import CommitResult, MutationService
from backend.services.story_time_service import event_times, fact_span, read_story, time_of

MAX_RECORDS = 10_000
MAX_LISTENERS = 12
# Someone who overhears is less sure than someone who was told.
OVERHEARD_CONFIDENCE = 0.6


@dataclass(frozen=True)
class Telling:
    result: CommitResult
    event_id: str
    beliefs: int
    # Listeners who already believed it, so nothing was added for them.
    already_knew: list[str]


@dataclass(frozen=True)
class Journey:
    steps: list[ReaderStep]
    names: dict[str, str]
    facts: dict[str, Fact]


@dataclass(frozen=True)
class Guide:
    title: str
    markdown: str


class KnowledgeService:
    def __init__(
        self,
        *,
        reader: CanonRepositoryPort,
        mutation: MutationService,
        schema_store: Any = None,
        tenancy: Any = None,
        local_mode: bool = False,
    ) -> None:
        self._reader = reader
        self._mutation = mutation
        self._schema_store = schema_store
        self._tenancy = tenancy
        self._local_mode = local_mode

    async def _authorize(
        self, scope: AccessScope, workspace_id: str, project_id: str, permission: Permission
    ) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(scope, workspace_id, project_id, permission)

    async def _beliefs(self, workspace_id: str, project_id: str) -> list[Belief]:
        return list(
            await self._reader.list_beliefs(
                workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
            )
        )

    async def _character(self, workspace_id: str, project_id: str, entity_id: str) -> CanonEntity:
        entity = await self._reader.get_entity(
            workspace_id=workspace_id, project_id=project_id, entity_id=entity_id
        )
        if entity is None or not entity.is_active:
            raise NotFoundError("That entry was not found.")
        if entity.type != "Character":
            raise ValidationError("Only a character knows or believes things.")
        return entity

    # ----------------------------------------------------------------- reads

    async def character(
        self, scope: AccessScope, workspace_id: str, project_id: str, entity_id: str
    ) -> CharacterKnowledge:
        """What one character knows, gets wrong, and has no idea of."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        await self._character(workspace_id, project_id, entity_id)
        facts = await self._reader.list_facts(
            workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
        )
        return knowledge_of(entity_id, facts, await self._beliefs(workspace_id, project_id))

    async def fact(
        self, scope: AccessScope, workspace_id: str, project_id: str, fact_id: str
    ) -> FactKnowledge:
        """Who knows one fact, and who believes otherwise."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        fact = await self._reader.get_fact(
            workspace_id=workspace_id, project_id=project_id, fact_id=fact_id
        )
        if fact is None:
            raise NotFoundError("That fact was not found.")
        return who_knows(fact, await self._beliefs(workspace_id, project_id))

    async def journey(self, scope: AccessScope, workspace_id: str, project_id: str) -> Journey:
        """The plan's scenes in reading order, with what the reader has been shown."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        story = await read_story(self._reader, workspace_id, project_id)
        items = await self._reader.list_plan_items(  # type: ignore[call-arg]
            workspace_id=workspace_id, project_id=project_id
        )
        scenes = reading_order(items)
        times = event_times(story.events)
        moments: dict[str, Interval | None] = {}
        for scene in scenes:
            when = scene_time(scene.details)
            moments[scene.id] = time_of(when, times)
        steps = reader_journey(
            scenes, story.facts, await self._beliefs(workspace_id, project_id), moments
        )
        names = {entity.id: entity.name for entity in story.entities.values()}
        return Journey(steps=steps, names=names, facts={fact.id: fact for fact in story.facts})

    # ----------------------------------------------------------------- guide

    async def guide(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        up_to_scene: str,
        viewpoint: str | None = None,
    ) -> Guide:
        """A story bible a reader could see after a scene, or as one character knows it.

        Leaves out secrets the reader has not been shown by then, anything dated after
        the scene, and entries that have not appeared. With a viewpoint, only what that
        character knows is kept.
        """
        journey = await self.journey(scope, workspace_id, project_id)
        step = next((item for item in journey.steps if item.scene.id == up_to_scene), None)
        if step is None:
            raise NotFoundError("That scene is not in the plan.")
        story = await read_story(self._reader, workspace_id, project_id)
        times = event_times(story.events)
        scene_moment = None
        when = scene_time(step.scene.details)
        if when is not None:
            scene_moment = time_of(when, times)

        appeared: set[str] = set()
        for earlier in journey.steps[: step.number]:
            appeared |= {ref.entity_id for ref in earlier.scene.refs}
        shown = step.reader_knows
        known: set[str] | None = None
        if viewpoint is not None:
            await self._character(workspace_id, project_id, viewpoint)
            known = knowledge_of(
                viewpoint, story.facts, await self._beliefs(workspace_id, project_id)
            ).known_ids()

        kept: list[Fact] = []
        for fact in story.facts:
            if fact.closed_revision is not None:
                continue
            if known is not None and fact.id not in known:
                continue
            if fact.visibility is FactVisibility.SECRET and fact.id not in shown:
                continue
            if scene_moment is not None:
                span = fact_span(fact, times)
                if span.start is not None and compare(span.start, scene_moment) is Order.AFTER:
                    continue
            if fact.id not in shown:
                ends = [fact.proposition.subject_id]
                target = getattr(fact.proposition.object, "entity_id", None)
                if target:
                    ends.append(target)
                if not all(entity_id in appeared for entity_id in ends):
                    continue
            kept.append(fact)
        for fact in kept:
            appeared.add(fact.proposition.subject_id)
            target = getattr(fact.proposition.object, "entity_id", None)
            if target:
                appeared.add(target)

        names = journey.names
        who = names.get(viewpoint or "", "")
        title = (
            f"As {who} knows it, after “{step.scene.title}”"
            if viewpoint
            else f"The story so far, after “{step.scene.title}”"
        )
        lines = [f"# {title}", ""]
        lines.append(
            "Leaves out secrets not yet shown to the reader, anything dated after this "
            "scene, and anyone who has not yet appeared."
            if not viewpoint
            else f"Only what {who} knows."
        )
        by_subject: dict[str, list[Fact]] = {}
        for fact in kept:
            by_subject.setdefault(fact.proposition.subject_id, []).append(fact)
        entries = sorted(
            (story.entities[entity_id] for entity_id in appeared if entity_id in story.entities),
            key=lambda entity: (entity.type, entity.name.lower()),
        )
        current_type = None
        for entity in entries:
            if entity.type != current_type:
                current_type = entity.type
                lines += ["", f"## {_plural(entity.type)}"]
            lines += ["", f"### {entity.name}"]
            for fact in sorted(
                by_subject.get(entity.id, []), key=lambda f: f.proposition.predicate
            ):
                lines.append(f"- {_sentence(fact.proposition, names)}")
        return Guide(title=title, markdown="\n".join(lines).rstrip() + "\n")

    # ---------------------------------------------------------------- telling

    async def tell(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        speaker_id: str,
        listener_ids: list[str],
        overheard_by: list[str] | None = None,
        proposition: Proposition,
        expected_revision: int,
    ) -> Telling:
        """One character tells others something. Each listener comes to believe it.

        Whether it was true is not stored: it is read from the facts, like any belief,
        so a lie is simply a telling whose listeners now believe something false.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        overheard_by = overheard_by or []
        hearers = [*listener_ids, *overheard_by]
        if not listener_ids:
            raise ValidationError("Say who is told.")
        if len(hearers) > MAX_LISTENERS:
            raise ValidationError(f"At most {MAX_LISTENERS} people can hear it at once.")
        if len(set(hearers)) != len(hearers) or speaker_id in hearers:
            raise ValidationError("Each person can be the teller or a hearer, once.")
        speaker = await self._character(workspace_id, project_id, speaker_id)
        listeners = [await self._character(workspace_id, project_id, i) for i in hearers]
        beliefs = await self._beliefs(workspace_id, project_id)
        event_id = format_canon_id("event", str(uuid7()))
        names = ", ".join(entity.name for entity in listeners[: len(listener_ids)])
        operations: list[CanonOperation] = [
            CreateEventOp(
                id=event_id,
                kind="telling",
                name=f"{speaker.name} tells {names}",
                participants=[
                    EventParticipant(entity_id=speaker_id, role="speaker"),
                    *(EventParticipant(entity_id=i, role="listener") for i in listener_ids),
                    *(EventParticipant(entity_id=i, role="overheard") for i in overheard_by),
                ],
            )
        ]
        already: list[str] = []
        for hearer in hearers:
            holds = any(
                belief.holder_entity_id == hearer
                and belief.closed_revision is None
                and belief.proposition == proposition
                for belief in beliefs
            )
            if holds:
                already.append(hearer)
                continue
            operations.append(
                AssertBeliefOp(
                    id=f"tmp:belief-{hearer}",
                    holder_entity_id=hearer,
                    proposition=proposition,
                    confidence=OVERHEARD_CONFIDENCE if hearer in overheard_by else 1.0,
                    source_ref=event_id,
                )
            )
        schema_version_id = None
        if self._schema_store is not None:
            published = await self._schema_store.get_published(
                workspace_id=workspace_id, project_id=project_id
            )
            schema_version_id = published.id if published else None
        mutation = await self._mutation.create_draft(
            scope,
            workspace_id=workspace_id,
            project_id=project_id,
            operations=operations,
            expected_head=expected_revision,
            schema_version_id=schema_version_id,
        )
        result = await self._mutation.commit_author_change(scope, mutation.id)
        return Telling(
            result=result,
            event_id=event_id,
            beliefs=len(operations) - 1,
            already_knew=already,
        )


def _plural(entity_type: str) -> str:
    return {"Character": "People", "Location": "Places", "Faction": "Groups"}.get(
        entity_type, f"{entity_type}s"
    )


def _sentence(proposition: Proposition, names: dict[str, str]) -> str:
    subject = names.get(proposition.subject_id, "Someone")
    phrase = proposition.predicate.replace("_", " ").lower()
    obj = proposition.object
    target = names.get(getattr(obj, "entity_id", ""), None)
    if target is None:
        value = getattr(obj, "value", None)
        target = str(value) if value is not None else ""
    return f"{subject} {phrase} {target}".strip() + "."


__all__ = ["Guide", "Journey", "KnowledgeService", "Telling"]
