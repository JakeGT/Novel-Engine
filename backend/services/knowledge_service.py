"""Who knows what, telling, claims, trust, and what the reader is shown (ADR-020).

Reads are derived each time from facts, beliefs and the plan, so they cannot
drift. Telling someone something, or seeing something happen, is never written
straight into the story: the event and a belief for each listener or witness,
worked out by their trust (``backend/domain/trust.py``), go to review as one
proposal, each belief with its reason (KNW-006). A claim (KNW-005) records what is
said without making it true; it becomes a fact only through a reviewed proposal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

from backend.adapters.system import uuid7
from backend.domain.acquisition import Learned, how_learned
from backend.domain.canon.assertions import Belief, Fact, FactStatus, FactVisibility
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.events import (
    CLAIM_KIND,
    CLAIM_NARRATOR,
    CLAIM_PERCEIVER,
    CLAIM_SPEAKER,
    INFERRED_KIND,
    INFERRED_THINKER,
    MAX_PREMISES,
    ClaimModality,
    EventClaim,
    EventParticipant,
)
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.phrasing import phrase
from backend.domain.canon.plans import PlanKind
from backend.domain.canon.propositions import Proposition
from backend.domain.canon.registry import CORE_PREDICATES, Cardinality
from backend.domain.canon.revisions import OriginKind
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.knowledge import (
    CharacterKnowledge,
    ClaimRecord,
    FactKnowledge,
    ReaderStep,
    claim_mentions,
    claims_of,
    knowledge_of,
    reader_journey,
    reading_order,
    scene_time,
    who_knows,
)
from backend.domain.operations import (
    AssertBeliefOp,
    AssertFactOp,
    CanonOperation,
    CloseFactOp,
    CreateEventOp,
    UpdateEntityOp,
)
from backend.domain.tenancy import Permission
from backend.domain.trust import (
    BELIEVED_MODALITIES,
    Hearer,
    ProposedBelief,
    TrustPolicy,
    propose_beliefs,
    suggested,
    trust_of,
    with_trust,
)
from backend.domain.worldtime import (
    At,
    Between,
    Interval,
    Order,
    Relative,
    Unknown,
    compare,
    describe,
    is_unknown,
)
from backend.ports import CanonRepositoryPort
from backend.services.mutation_service import CommitResult, MutationService
from backend.services.schema_compiler import compile_definition
from backend.services.story_at_version import StoryAt, story_at
from backend.services.story_time_service import event_times, fact_span, read_story, time_of

MAX_RECORDS = 10_000
MAX_LISTENERS = 12
MAX_PERCEIVERS = 12
MAX_EVENT_NAME = 200
MODALITY_WORDS: dict[str, str] = {
    "states": "says",
    "suggests": "suggests",
    "wonders": "wonders whether",
    "denies": "denies that",
}


@dataclass(frozen=True)
class Telling:
    """A telling or a witnessed event, proposed for review with a belief for each hearer."""

    mutation_id: str
    event_id: str
    # One per hearer or witness, in order: the belief proposed (or why none was).
    proposals: list[ProposedBelief]

    @property
    def beliefs(self) -> int:
        return sum(1 for item in self.proposals if item.confidence is not None)

    @property
    def already_knew(self) -> list[str]:
        return [item.holder_id for item in self.proposals if item.already]


@dataclass(frozen=True)
class RecordedClaim:
    result: CommitResult
    event_id: str
    # What its hearers would come to believe, waiting in review, when the author asked.
    beliefs: Telling | None = None


@dataclass(frozen=True)
class PremiseView:
    """One thing an inference was worked out from."""

    id: str
    # "fact", "belief", or "gone" when it is no longer in the story.
    kind: str
    sentence: str


@dataclass(frozen=True)
class LearnedView:
    """How and when a character came to hold one belief, in plain words (KNW-002)."""

    learned: Learned
    # "Told by Mara", "Saw it: The fire at the docks", "Worked it out".
    words: str
    scene_title: str | None = None
    # The story time it was learned, in the story's own words; None when not placed.
    when: str | None = None
    premises: list[PremiseView] = field(default_factory=list)
    # The current fact the belief agrees with, when the story says it is so.
    fact_id: str | None = None


@dataclass(frozen=True)
class Promotion:
    mutation_id: str


@dataclass(frozen=True)
class Journey:
    steps: list[ReaderStep]
    names: dict[str, str]
    facts: dict[str, Fact]


# How the line of the story is named when it is not a what-if.
MAIN_LINE = "the story itself"
# The rules a guide follows to leave things out. Raised whenever they change, and written
# into every export, so an old guide can be told apart from one made under new rules.
GUIDE_RULES = 1


@dataclass(frozen=True)
class GuideEntry:
    name: str
    lines: list[str]


@dataclass(frozen=True)
class GuideSection:
    entity_type: str
    heading: str
    entries: list[GuideEntry]


@dataclass(frozen=True)
class Guide:
    """A spoiler-safe story bible, bound to the line, version, scene and viewpoint it shows."""

    title: str
    markdown: str
    story_line: str = MAIN_LINE
    version: int = 0
    scene_id: str = ""
    scene_title: str = ""
    scene_number: int = 0
    scene_count: int = 0
    viewpoint_id: str | None = None
    viewpoint_name: str | None = None
    leaves_out: str = ""
    sections: list[GuideSection] = field(default_factory=list)
    rules: int = GUIDE_RULES
    # Which part of the guide (QRY-002): everything, characters, places, timeline, recap.
    part: str = "everything"

    @property
    def seen_as(self) -> str:
        return (
            f"as {self.viewpoint_name} knows it"
            if self.viewpoint_name
            else "as the reader knows it"
        )

    @property
    def bound_to(self) -> str:
        """One sentence naming exactly what this guide shows."""
        line = self.story_line[0].upper() + self.story_line[1:]
        return (
            f"{line}, as it stood at version {self.version}; up to scene {self.scene_number} "
            f"of {self.scene_count}, “{self.scene_title}”; {self.seen_as}."
        )


def _yaml(value: str) -> str:
    # A JSON string is a valid YAML double-quoted scalar, whatever the title holds.
    return json.dumps(value, ensure_ascii=False)


def guide_markdown(guide: Guide) -> str:
    """The guide as a Markdown file whose header says exactly what it shows.

    The header is a YAML metadata block, which Markdown tools such as Pandoc, Obsidian
    and Typora read as the document's title and details; the same binding is repeated
    in words under the title for anyone reading the file itself.
    """
    lines = [
        "---",
        f"title: {_yaml(guide.title)}",
        f"story_line: {_yaml(guide.story_line)}",
        f"version: {guide.version}",
        f"up_to_scene: {guide.scene_number}",
        f"of_scenes: {guide.scene_count}",
        f"scene: {_yaml(guide.scene_title)}",
        f"seen_as: {_yaml(guide.viewpoint_name or 'the reader')}",
        f"guide_rules: {guide.rules}",
        f"part: {guide.part}",
        "---",
        "",
        f"# {guide.title}",
        "",
        f"*{guide.bound_to}*",
        "",
        guide.leaves_out,
    ]
    for section in guide.sections:
        lines += ["", f"## {section.heading}"]
        for entry in section.entries:
            lines += ["", f"### {entry.name}"]
            lines += [f"- {line}" for line in entry.lines]
    return "\n".join(lines).rstrip() + "\n"


def _journey_of(state: StoryAt) -> Journey:
    story = state.story
    scenes = reading_order(state.plan_items)
    times = event_times(story.events)
    moments: dict[str, Interval | None] = {}
    for scene in scenes:
        moments[scene.id] = time_of(scene_time(scene.details), times)
    steps = reader_journey(scenes, story.facts, state.beliefs, moments)
    names = {entity.id: entity.name for entity in story.entities.values()}
    return Journey(steps=steps, names=names, facts={fact.id: fact for fact in story.facts})


class KnowledgeService:
    def __init__(
        self,
        *,
        reader: CanonRepositoryPort,
        mutation: MutationService,
        schema_store: Any = None,
        tenancy: Any = None,
        local_mode: bool = False,
        journal: Any = None,
        clocks: Any = None,
    ) -> None:
        self._clocks = clocks
        self._reader = reader
        # The story's ready views (QRY-001), set by the container: the reader's journey
        # answers from its view once that is verified and current.
        self.views: Any = None
        self._mutation = mutation
        self._journal = journal
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
        if self.views is not None:
            ready = await self.views.journey(workspace_id, project_id)
            if ready is not None:
                return ready  # type: ignore[no-any-return]
        state = await self._live(workspace_id, project_id)
        return _journey_of(state)

    async def _live(self, workspace_id: str, project_id: str) -> StoryAt:
        story = await read_story(self._reader, workspace_id, project_id)
        items = await self._reader.list_plan_items(  # type: ignore[call-arg]
            workspace_id=workspace_id, project_id=project_id
        )
        return StoryAt(
            story=story,
            beliefs=await self._beliefs(workspace_id, project_id),
            plan_items=list(items),
        )

    async def _line(self, workspace_id: str, project_id: str) -> tuple[str, int]:
        """The name of the line of the story being read, and its latest version."""
        if self._journal is None:
            return MAIN_LINE, 0
        branch = await self._journal.working_branch(
            workspace_id=workspace_id, project_id=project_id
        )
        head = await self._reader.get_head(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
        )
        return (MAIN_LINE if branch.is_root else f"the what-if “{branch.name}”"), int(head)

    async def _state_at(
        self, workspace_id: str, project_id: str, version: int | None
    ) -> tuple[StoryAt, str, int]:
        """The story at a version (the latest when none is asked for), bound to that version."""
        line, head = await self._line(workspace_id, project_id)
        if version is not None:
            if version < 1 or version > head:
                raise ValidationError(
                    f"There is no version {version} of this story; the latest is {head}."
                )
            if version < head:
                return (
                    await story_at(self._reader, workspace_id, project_id, version),
                    line,
                    version,
                )
        state = await self._live(workspace_id, project_id)
        _, after = await self._line(workspace_id, project_id)
        if after != head:
            # Something was saved while the story was being read: read the version the
            # guide will name instead, so its heading never claims more than it shows.
            state = await story_at(self._reader, workspace_id, project_id, head)
        return state, line, head

    # ----------------------------------------------------------------- guide

    async def guide(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        up_to_scene: str,
        viewpoint: str | None = None,
        version: int | None = None,
        part: str = "everything",
    ) -> Guide:
        """A story bible a reader could see after a scene, or as one character knows it.

        Leaves out secrets the reader has not been shown by then, anything dated after
        the scene, and entries that have not appeared. With a viewpoint, only what that
        character knows is kept. With a version, the story is read as it stood then, and
        the guide names that version, the line of the story, the scene and the viewpoint.
        ``part`` makes one part of it on its own: characters, places, a timeline of the
        scenes read so far, or a recap scene by scene (``reader_exports``).
        """
        from backend.services import reader_exports

        if part not in reader_exports.PARTS:
            raise ValidationError("Choose everything, characters, places, timeline or recap.")
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        state, line, bound = await self._state_at(workspace_id, project_id, version)
        story = state.story
        journey = _journey_of(state)
        step = next((item for item in journey.steps if item.scene.id == up_to_scene), None)
        if step is None:
            if version is not None:
                raise NotFoundError(f"That scene was not in the plan at version {bound}.")
            raise NotFoundError("That scene is not in the plan.")
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
            who_entity = story.entities.get(viewpoint)
            if who_entity is None:
                raise NotFoundError("That entry was not found.")
            if who_entity.type != "Character":
                raise ValidationError("Only a character knows or believes things.")
            known = knowledge_of(viewpoint, story.facts, state.beliefs).known_ids()

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
        scene_title = step.scene.title
        if part == "everything":
            title = (
                f"As {who} knows it, after “{scene_title}” — version {bound}"
                if viewpoint
                else f"The story so far, after “{scene_title}” — version {bound}"
            )
        else:
            label = reader_exports.PART_TITLES[part]
            title = (
                f"{label}, as {who} knows it, after “{scene_title}” — version {bound}"
                if viewpoint
                else f"{label}, after “{scene_title}” — version {bound}"
            )
        leaves_out = reader_exports.leaves_out(part, who if viewpoint else None)
        by_subject: dict[str, list[Fact]] = {}
        for fact in kept:
            by_subject.setdefault(fact.proposition.subject_id, []).append(fact)
        entries = sorted(
            (story.entities[entity_id] for entity_id in appeared if entity_id in story.entities),
            key=lambda entity: (entity.type, entity.name.lower()),
        )
        sections: list[GuideSection] = []
        for entity in entries:
            if not sections or sections[-1].entity_type != entity.type:
                sections.append(GuideSection(entity.type, _plural(entity.type), []))
            lines = [
                _sentence(fact.proposition, names)
                for fact in sorted(
                    by_subject.get(entity.id, []), key=lambda f: f.proposition.predicate
                )
            ]
            sections[-1].entries.append(GuideEntry(entity.name, lines))
        steps_so_far = journey.steps[: step.number]
        if part in reader_exports.PART_TYPES:
            sections = [
                section
                for section in sections
                if section.entity_type == reader_exports.PART_TYPES[part]
            ]
        elif part == "timeline":
            from backend.services.continuity_service import unit_of

            made = reader_exports.timeline_sections(
                steps_so_far,
                story.events,
                times,
                lambda when: time_of(when, times),
                await unit_of(self._clocks, workspace_id, project_id),
                {key: value for key, value in names.items() if key in appeared},
                viewpoint,
            )
            sections = [_guide_section(section) for section in made]
        elif part == "recap":
            made = reader_exports.recap_sections(
                steps_so_far,
                {fact.id: fact for fact in story.facts if fact.closed_revision is None},
                appeared,
                {key: value for key, value in names.items() if key in appeared},
                known,
                _sentence,
            )
            sections = [_guide_section(section) for section in made]
        guide = Guide(
            title=title,
            markdown="",
            story_line=line,
            version=bound,
            scene_id=step.scene.id,
            scene_title=scene_title,
            scene_number=step.number,
            scene_count=len(journey.steps),
            viewpoint_id=viewpoint,
            viewpoint_name=who or None,
            leaves_out=leaves_out,
            sections=sections,
            part=part,
        )
        return replace(guide, markdown=guide_markdown(guide))

    # ---------------------------------------------------------------- telling

    async def _schema_version_id(self, workspace_id: str, project_id: str) -> str | None:
        if self._schema_store is None:
            return None
        published = await self._schema_store.get_published(
            workspace_id=workspace_id, project_id=project_id
        )
        return published.id if published else None

    async def _one_at_a_time(self, workspace_id: str, project_id: str, predicate: str) -> bool:
        """Whether a kind of fact can hold only one value at a time, by the story's setup."""
        spec = None
        if self._schema_store is not None:
            published = await self._schema_store.get_published(
                workspace_id=workspace_id, project_id=project_id
            )
            if published is not None:
                compiled = compile_definition(
                    published.definition, workspace_id=workspace_id, project_id=project_id
                )
                spec = compiled.predicate_specs.get(predicate)
        spec = spec or CORE_PREDICATES.get(predicate)
        # Compared by value: the setup and the built-in kinds of fact share the words.
        return spec is not None and spec.cardinality == Cardinality.ONE.value

    async def _names(self, workspace_id: str, project_id: str) -> dict[str, str]:
        entities = await self._reader.list_entities(
            workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
        )
        return {entity.id: entity.name for entity in entities}

    async def _propose(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        event: CreateEventOp | None,
        event_id: str,
        proposition: Proposition,
        proposals: list[ProposedBelief],
        expected_revision: int,
    ) -> Telling:
        """One proposal in review: the event, and a belief for each hearer the policies allow.

        Its origin is ``derived``: worked out by the story's own rules, so it always needs
        the author's approval and is never committed here (KNW-006). ``event`` is None when
        the event is already in the story (a claim the author recorded).
        """
        operations: list[CanonOperation] = [event] if event is not None else []
        for item in proposals:
            if item.confidence is None:
                continue
            operations.append(
                AssertBeliefOp(
                    id=f"tmp:belief-{item.holder_id.replace(':', '-')}",
                    holder_entity_id=item.holder_id,
                    proposition=proposition,
                    confidence=item.confidence,
                    source_ref=event_id,
                    why=item.why[:300],
                )
            )
        mutation = await self._mutation.create_draft(
            scope,
            workspace_id=workspace_id,
            project_id=project_id,
            operations=operations,
            expected_head=expected_revision,
            origin=OriginKind.DERIVED.value,
            schema_version_id=await self._schema_version_id(workspace_id, project_id),
        )
        await self._mutation.submit(scope, mutation.id, reason="beliefs worked out from trust")
        return Telling(mutation_id=mutation.id, event_id=event_id, proposals=proposals)

    async def _trusts(
        self, workspace_id: str, project_id: str, ids: list[str]
    ) -> dict[str, TrustPolicy]:
        found: dict[str, TrustPolicy] = {}
        for entity_id in ids:
            entity = await self._character(workspace_id, project_id, entity_id)
            found[entity_id] = trust_of(entity.attributes)
        return found

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
        scene_id: str | None = None,
        when: Unknown | At | Between | Relative | None = None,
    ) -> Telling:
        """One character tells others something. Proposes what each hearer comes to believe.

        Each hearer's belief follows how they take what the speaker says (their trust);
        someone who overhears is less sure. Nothing is written until the proposal is
        approved. Whether it was true is not stored: a lie is simply a telling whose
        listeners come to believe something false.
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
        await self._scene_of(workspace_id, project_id, scene_id)
        trust = await self._trusts(workspace_id, project_id, hearers)
        names = await self._names(workspace_id, project_id)
        event_id = format_canon_id("event", str(uuid7()))
        told = ", ".join(names.get(i, "someone") for i in listener_ids)
        event = CreateEventOp(
            id=event_id,
            kind="telling",
            name=f"{speaker.name} tells {told}"[:MAX_EVENT_NAME],
            participants=[
                EventParticipant(entity_id=speaker_id, role="speaker"),
                *(EventParticipant(entity_id=i, role="listener") for i in listener_ids),
                *(EventParticipant(entity_id=i, role="overheard") for i in overheard_by),
            ],
            scene_id=scene_id,
            when=None if is_unknown(when) else when,
        )
        proposals = propose_beliefs(
            proposition,
            [
                *(Hearer(i, "told") for i in listener_ids),
                *(Hearer(i, "overheard") for i in overheard_by),
            ],
            speaker_id=speaker_id,
            trust=trust,
            beliefs=await self._beliefs(workspace_id, project_id),
            names=names,
        )
        return await self._propose(
            scope,
            workspace_id,
            project_id,
            event=event,
            event_id=event.id,
            proposition=proposition,
            proposals=proposals,
            expected_revision=expected_revision,
        )

    async def witness(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        name: str,
        present_ids: list[str],
        not_noticed: list[str] | None = None,
        proposition: Proposition,
        expected_revision: int,
        scene_id: str | None = None,
        when: Unknown | At | Between | Relative | None = None,
    ) -> Telling:
        """Something happens in front of people. Proposes a belief for each who perceives it.

        Everyone present perceives it unless the author says they did not notice; those
        are kept on the event as present, and given no belief.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        not_noticed = not_noticed or []
        title = " ".join(name.split())
        if not title:
            raise ValidationError("Say what happens.")
        if len(title) > MAX_EVENT_NAME:
            raise ValidationError(f"Say what happens in at most {MAX_EVENT_NAME} characters.")
        if not present_ids:
            raise ValidationError("Say who is there.")
        if len(present_ids) > MAX_PERCEIVERS:
            raise ValidationError(f"At most {MAX_PERCEIVERS} people can be there at once.")
        if len(set(present_ids)) != len(present_ids):
            raise ValidationError("Each person can be there once.")
        if not set(not_noticed) <= set(present_ids):
            raise ValidationError("Only someone who is there can fail to notice.")
        witnesses = [i for i in present_ids if i not in not_noticed]
        if not witnesses:
            raise ValidationError("Someone who is there has to notice it.")
        for entity_id in present_ids:
            await self._character(workspace_id, project_id, entity_id)
        await self._scene_of(workspace_id, project_id, scene_id)
        names = await self._names(workspace_id, project_id)
        event = CreateEventOp(
            id=format_canon_id("event", str(uuid7())),
            kind="witnessed",
            name=title,
            participants=[
                *(EventParticipant(entity_id=i, role="witness") for i in witnesses),
                *(EventParticipant(entity_id=i, role="present") for i in not_noticed),
            ],
            scene_id=scene_id,
            when=None if is_unknown(when) else when,
        )
        proposals = propose_beliefs(
            proposition,
            [Hearer(i, "saw") for i in witnesses],
            speaker_id=None,
            trust={},
            beliefs=await self._beliefs(workspace_id, project_id),
            names=names,
        )
        return await self._propose(
            scope,
            workspace_id,
            project_id,
            event=event,
            event_id=event.id,
            proposition=proposition,
            proposals=proposals,
            expected_revision=expected_revision,
        )

    async def _scene_of(self, workspace_id: str, project_id: str, scene_id: str | None) -> None:
        """Refuse, in plain words, a scene that is not a scene in the plan."""
        if scene_id is None:
            return
        scene = await self._reader.get_plan_item(  # type: ignore[call-arg]
            workspace_id=workspace_id, project_id=project_id, plan_id=scene_id
        )
        if scene is None or not scene.is_active or scene.kind is not PlanKind.SCENE:
            raise ValidationError("That scene is not in the plan.")

    async def infer(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        holder_id: str,
        proposition: Proposition,
        premises: list[str],
        scene_id: str | None = None,
        when: Unknown | At | Between | Relative | None = None,
        confidence: float = 1.0,
        expected_revision: int,
    ) -> tuple[CommitResult, str]:
        """The author records that a character worked something out from other things.

        It is the author's own change, saved at once: an event of kind ``inferred`` naming
        the facts and beliefs it was worked out from, and the belief it gave them. Whether
        what they worked out is right is derived from the facts, like any belief.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        holder = await self._character(workspace_id, project_id, holder_id)
        if not premises:
            raise ValidationError(f"Say what {holder.name} worked it out from.")
        if len(premises) > MAX_PREMISES:
            raise ValidationError(f"Name at most {MAX_PREMISES} things it was worked out from.")
        if len(set(premises)) != len(premises):
            raise ValidationError("Name each thing it was worked out from once.")
        if not 0 < confidence <= 1:
            raise ValidationError("Say how sure they are, from a little to completely.")
        await self._scene_of(workspace_id, project_id, scene_id)
        for premise in premises:
            fact = await self._reader.get_fact(
                workspace_id=workspace_id, project_id=project_id, fact_id=premise
            )
            if fact is not None:
                if fact.closed_revision is not None:
                    raise ValidationError("One of those facts is no longer in the story.")
                if fact.proposition == proposition:
                    raise ValidationError("Something cannot be worked out from itself.")
                continue
            belief = await self._reader.get_belief(
                workspace_id=workspace_id, project_id=project_id, belief_id=premise
            )
            if belief is None or belief.closed_revision is not None:
                raise ValidationError(
                    "Each thing it was worked out from must be a fact or a belief."
                )
            if belief.holder_entity_id != holder_id:
                raise ValidationError(
                    f"{holder.name} can only work it out from what they believe, not from "
                    "what someone else believes."
                )
            if belief.proposition == proposition:
                raise ValidationError("Something cannot be worked out from itself.")
        beliefs = await self._beliefs(workspace_id, project_id)
        if any(
            belief.holder_entity_id == holder_id
            and belief.closed_revision is None
            and belief.proposition == proposition
            for belief in beliefs
        ):
            raise ValidationError(f"{holder.name} already believes this.")
        names = await self._names(workspace_id, project_id)
        sentence = _sentence(proposition, names).rstrip(".")
        event = CreateEventOp(
            id=format_canon_id("event", str(uuid7())),
            kind=INFERRED_KIND,
            name=f"{holder.name} works out that {sentence}"[:MAX_EVENT_NAME],
            participants=[EventParticipant(entity_id=holder_id, role=INFERRED_THINKER)],
            premises=list(premises),
            scene_id=scene_id,
            when=None if is_unknown(when) else when,
        )
        operations: list[CanonOperation] = [
            event,
            AssertBeliefOp(
                id=f"tmp:belief-{holder_id.replace(':', '-')}",
                holder_entity_id=holder_id,
                proposition=proposition,
                confidence=confidence,
                source_ref=event.id,
            ),
        ]
        mutation = await self._mutation.create_draft(
            scope,
            workspace_id=workspace_id,
            project_id=project_id,
            operations=operations,
            expected_head=expected_revision,
            schema_version_id=await self._schema_version_id(workspace_id, project_id),
        )
        result = await self._mutation.commit_author_change(scope, mutation.id)
        return result, event.id

    async def learned(
        self, scope: AccessScope, workspace_id: str, project_id: str, entity_id: str
    ) -> list[LearnedView]:
        """How and when a character came to hold each of their beliefs (KNW-002)."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        await self._character(workspace_id, project_id, entity_id)
        held = [
            belief
            for belief in await self._reader.list_beliefs(
                workspace_id=workspace_id,
                project_id=project_id,
                holder_entity_id=entity_id,
                limit=MAX_RECORDS,
            )
            if belief.closed_revision is None
        ]
        if not held:
            return []
        story = await read_story(self._reader, workspace_id, project_id)
        events = {event.id: event for event in story.events}
        names = {entity.id: entity.name for entity in story.entities.values()}
        unit = "year"
        if self._clocks is not None:
            unit = (
                await self._clocks.get_unit(workspace_id=workspace_id, project_id=project_id)
                or "year"
            )
        items = await self._reader.list_plan_items(  # type: ignore[call-arg]
            workspace_id=workspace_id, project_id=project_id
        )
        scenes = {item.id: item for item in items if item.is_active}
        facts = {fact.id: fact for fact in story.facts}
        beliefs = {belief.id: belief for belief in await self._beliefs(workspace_id, project_id)}
        views: list[LearnedView] = []
        for belief in held:
            learned = how_learned(belief, events)
            event = events.get(learned.event_id or "")
            when_words: str | None = None
            if event is not None and event.when is not None and not is_unknown(event.when):
                when_words = describe(
                    event.when,
                    unit=unit,
                    event_name=lambda i: events[i].name if i in events else None,
                )
            scene = scenes.get(learned.scene_id or "")
            if when_words is None and scene is not None:
                scene_when = scene_time(scene.details)
                if scene_when is not None and not is_unknown(scene_when):
                    when_words = describe(
                        scene_when,
                        unit=unit,
                        event_name=lambda i: events[i].name if i in events else None,
                    )
            premises: list[PremiseView] = []
            for premise in learned.premises:
                if premise in facts:
                    premises.append(
                        PremiseView(premise, "fact", _sentence(facts[premise].proposition, names))
                    )
                elif premise in beliefs:
                    premises.append(
                        PremiseView(
                            premise,
                            "belief",
                            _sentence(beliefs[premise].proposition, names),
                        )
                    )
                else:
                    premises.append(
                        PremiseView(premise, "gone", "Something no longer in the story.")
                    )
            agreeing = next(
                (
                    fact.id
                    for fact in story.facts
                    if fact.status is FactStatus.ASSERTED
                    and fact.closed_revision is None
                    and fact.proposition == belief.proposition
                ),
                None,
            )
            views.append(
                LearnedView(
                    learned=learned,
                    words=_how_words(learned, names),
                    scene_title=scene.title if scene is not None else None,
                    when=when_words,
                    premises=premises,
                    fact_id=agreeing,
                )
            )
        return views

    # ----------------------------------------------------------------- trust

    async def trust(
        self, scope: AccessScope, workspace_id: str, project_id: str, entity_id: str
    ) -> TrustPolicy:
        """How a character takes what others tell them."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        entity = await self._character(workspace_id, project_id, entity_id)
        return trust_of(entity.attributes)

    async def set_trust(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        entity_id: str,
        *,
        policy: TrustPolicy,
        expected_revision: int,
    ) -> tuple[CommitResult, TrustPolicy]:
        """The author says how a character takes what others tell them.

        A setting on the character, saved as an ordinary change to them by the author;
        it changes nothing anyone believes until a telling is proposed and approved.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        entity = await self._character(workspace_id, project_id, entity_id)
        for speaker_id in policy.speakers:
            if speaker_id == entity_id:
                raise ValidationError("A character's trust is about other people.")
            await self._character(workspace_id, project_id, speaker_id)
        operation = UpdateEntityOp(id=entity_id, attributes=with_trust(entity.attributes, policy))
        mutation = await self._mutation.create_draft(
            scope,
            workspace_id=workspace_id,
            project_id=project_id,
            operations=[operation],
            expected_head=expected_revision,
            schema_version_id=await self._schema_version_id(workspace_id, project_id),
        )
        result = await self._mutation.commit_author_change(scope, mutation.id)
        return result, trust_of(operation.attributes)

    # ---------------------------------------------------------------- claims

    async def claims(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        entity_id: str | None = None,
        scene_id: str | None = None,
    ) -> list[ClaimRecord]:
        """What is said in the story, never as true, with whether the facts bear it out."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        events = await self._reader.list_events(
            workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
        )
        facts = await self._reader.list_facts(
            workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
        )
        found = claims_of(events, facts)
        if entity_id is not None:
            found = [record for record in found if claim_mentions(record, entity_id)]
        if scene_id is not None:
            found = [record for record in found if record.claim.scene_id == scene_id]
        return found

    async def record_claim(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        proposition: Proposition,
        modality: ClaimModality = "states",
        speaker_id: str | None = None,
        narrates: bool = False,
        perceiver_ids: list[str] | None = None,
        scene_id: str | None = None,
        hearers_believe: bool = False,
        expected_revision: int,
    ) -> RecordedClaim:
        """The author records something said: by the narration, a character, or a narrator.

        It is kept as a claim, never as a fact. No one comes to believe it unless the author
        asks (``hearers_believe``): then what each hearer would believe, by their trust in
        the speaker, goes to review as one proposal. Only a character speaking to others
        has hearers who can believe it; only what is stated or suggested is believed (a
        suggestion less firmly); a question or a denial gives no belief.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        perceiver_ids = perceiver_ids or []
        if len(perceiver_ids) > MAX_PERCEIVERS:
            raise ValidationError(f"At most {MAX_PERCEIVERS} people can hear it at once.")
        if len(set(perceiver_ids)) != len(perceiver_ids) or (
            speaker_id is not None and speaker_id in perceiver_ids
        ):
            raise ValidationError("Each person can be the one who says it or a hearer, once.")
        if narrates and speaker_id is None:
            raise ValidationError("Say which character narrates it.")
        if hearers_believe and (narrates or speaker_id is None or not perceiver_ids):
            raise ValidationError("Only a character speaking to others has hearers who believe it.")
        if hearers_believe and modality not in BELIEVED_MODALITIES:
            raise ValidationError("A question or a denial gives nobody anything to believe.")
        names = await self._names(workspace_id, project_id)
        participants: list[EventParticipant] = []
        if speaker_id is not None:
            await self._character(workspace_id, project_id, speaker_id)
            participants.append(
                EventParticipant(
                    entity_id=speaker_id, role=CLAIM_NARRATOR if narrates else CLAIM_SPEAKER
                )
            )
        for perceiver in perceiver_ids:
            await self._character(workspace_id, project_id, perceiver)
            participants.append(EventParticipant(entity_id=perceiver, role=CLAIM_PERCEIVER))
        source = names.get(speaker_id or "", "") or "The narration"
        sentence = _sentence(proposition, names).rstrip(".")
        event = CreateEventOp(
            id=format_canon_id("event", str(uuid7())),
            kind=CLAIM_KIND,
            name=f"{source} {MODALITY_WORDS[modality]} {sentence}"[:MAX_EVENT_NAME],
            participants=participants,
            claim=EventClaim(proposition=proposition, modality=modality, scene_id=scene_id),
        )
        mutation = await self._mutation.create_draft(
            scope,
            workspace_id=workspace_id,
            project_id=project_id,
            operations=[event],
            expected_head=expected_revision,
            schema_version_id=await self._schema_version_id(workspace_id, project_id),
        )
        result = await self._mutation.commit_author_change(scope, mutation.id)
        if not hearers_believe or speaker_id is None:
            return RecordedClaim(result=result, event_id=event.id)
        proposals = propose_beliefs(
            proposition,
            [Hearer(i, "told") for i in perceiver_ids],
            speaker_id=speaker_id,
            trust=await self._trusts(workspace_id, project_id, perceiver_ids),
            beliefs=await self._beliefs(workspace_id, project_id),
            names=names,
        )
        if modality == "suggests":
            proposals = [suggested(item) for item in proposals]
        beliefs = await self._propose(
            scope,
            workspace_id,
            project_id,
            event=None,
            event_id=event.id,
            proposition=proposition,
            proposals=proposals,
            expected_revision=result.revision,
        )
        return RecordedClaim(result=result, event_id=event.id, beliefs=beliefs)

    async def promote_claim(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        event_id: str,
        *,
        expected_revision: int,
    ) -> Promotion:
        """Propose making what a claim says a fact. It goes to review; nothing changes yet."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        event = await self._reader.get_event(
            workspace_id=workspace_id, project_id=project_id, event_id=event_id
        )
        if (
            event is None
            or event.kind != CLAIM_KIND
            or event.claim is None
            or event.retired_revision is not None
        ):
            raise NotFoundError("That claim was not found.")
        if event.claim.modality == "denies":
            raise ValidationError(
                "A denial says something is not so, so there is no fact to make from it."
            )
        facts = await self._reader.list_facts(
            workspace_id=workspace_id, project_id=project_id, limit=MAX_RECORDS
        )
        record = claims_of([event], facts)[0]
        if record.standing == "borne_out":
            raise ValidationError("Your facts already say this.")
        names = await self._names(workspace_id, project_id)
        source = names.get(record.speaker_id or "", "") or "the narration"
        proposition = event.claim.proposition
        operations: list[CanonOperation] = []
        # Something that can be only one thing at a time (where someone is) replaces what
        # the facts said; the author can skip that in review and keep both apart.
        if record.standing == "contradicted" and await self._one_at_a_time(
            workspace_id, project_id, proposition.predicate
        ):
            operations.extend(
                CloseFactOp(
                    id=fact.id,
                    why=f"Ended so what {source} says can be true instead."[:300],
                )
                for fact in facts
                if fact.status is FactStatus.ASSERTED
                and fact.closed_revision is None
                and fact.proposition.subject_id == proposition.subject_id
                and fact.proposition.predicate == proposition.predicate
                and fact.proposition.object != proposition.object
            )
        operations.append(
            AssertFactOp(
                id="tmp:fact-from-claim",
                proposition=proposition,
                why=f"Made a fact from what {source} says (“{event.name}”)."[:300],
            )
        )
        mutation = await self._mutation.create_draft(
            scope,
            workspace_id=workspace_id,
            project_id=project_id,
            operations=operations,
            expected_head=expected_revision,
            schema_version_id=await self._schema_version_id(workspace_id, project_id),
        )
        await self._mutation.submit(scope, mutation.id, reason="a claim made a fact")
        return Promotion(mutation_id=mutation.id)


def _how_words(learned: Learned, names: dict[str, str]) -> str:
    source = names.get(learned.source_id or "")
    event = learned.event_name or "an event"
    if learned.how == "told":
        return f"Told by {source}" if source else "Told"
    if learned.how == "overheard":
        return f"Overheard {source} telling it" if source else "Overheard it"
    if learned.how == "saw":
        return f"Saw it: {event}"
    if learned.how == "worked_out":
        return "Worked it out"
    if learned.how == "heard_said":
        return f"Heard {source} say it" if source else "Heard it said"
    if learned.how == "event":
        return f"From {event}"
    return "Recorded with no telling or event behind it"


def _guide_section(section: Any) -> GuideSection:
    return GuideSection(
        section.entity_type,
        section.heading,
        [GuideEntry(entry.name, list(entry.lines)) for entry in section.entries],
    )


def _plural(entity_type: str) -> str:
    return {"Character": "People", "Location": "Places", "Faction": "Groups"}.get(
        entity_type, f"{entity_type}s"
    )


def _sentence(proposition: Proposition, names: dict[str, str]) -> str:
    subject = names.get(proposition.subject_id, "Someone")
    verb = phrase(proposition.predicate)
    obj = proposition.object
    target = names.get(getattr(obj, "entity_id", ""), None)
    if target is None:
        value = getattr(obj, "value", None)
        target = str(value) if value is not None else ""
    return f"{subject} {verb} {target}".strip() + "."


__all__ = [
    "MODALITY_WORDS",
    "Promotion",
    "RecordedClaim",
    "GUIDE_RULES",
    "Guide",
    "GuideEntry",
    "GuideSection",
    "Journey",
    "KnowledgeService",
    "Telling",
    "guide_markdown",
]
