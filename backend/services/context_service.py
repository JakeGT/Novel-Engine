"""What the assistant would be given to write a scene, and why (ADR-021).

Candidates come from the story itself, in fixed ways, each with a reason in plain
words: the scene and where it sits in the plan, who and what is in it, what is true
of them at the scene's time, what the viewpoint character knows and gets wrong, what
has happened, what the reader has been shown, the story rules, pictures, and passages
of the author's writing that name the scene's people and places. Search by meaning,
when available, only adds to the score of a passage; it never adds a fact.

The author's pins, exclusions, budget and choice about secrets are kept with the
scene and changed through the ordinary reviewed path.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from backend.adapters.system import uuid7
from backend.domain.acquisition import held_at
from backend.domain.arcs import moves_in_scene
from backend.domain.branching import working_branch_id
from backend.domain.canon.assertions import FactVisibility
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.marks import details as plain_details_of
from backend.domain.canon.naming import normalize_name
from backend.domain.canon.phrasing import phrase
from backend.domain.canon.plans import PlanItem, PlanKind
from backend.domain.context import (
    SCENE_DRAFT_POLICY,
    ContextComparison,
    ContextItem,
    ContextManifest,
    ContextSection,
    ContextSettings,
    assemble,
    compare_contexts,
    context_settings,
    stored_manifest,
    with_context_settings,
)
from backend.domain.context_parts import (
    REPRESENTATION_WORDS,
    ContextChoice,
    Delivery,
    MediaLimits,
    planned_representation,
)
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.knowledge import knowledge_of, reader_journey, reading_order, scene_reveals
from backend.domain.knowledge import scene_time as scene_when
from backend.domain.rules import RuleStatus, read_rule
from backend.domain.scene_conditions import condition_reading, plain_details, scene_conditions
from backend.domain.scene_intent import intent_sentences, scene_intent
from backend.domain.sources import SourceKind, SourceText, find_mentions
from backend.domain.tenancy import Permission
from backend.domain.threads import thread_advances
from backend.domain.worldtime import (
    Interval,
    Order,
    Truth,
    compare,
    describe,
    is_unknown,
    parse_world_time,
    resolve,
    span_of,
    true_at,
)
from backend.services.knowledge_timing import StoryOrder
from backend.services.passage_index import IndexStatus
from backend.services.story_time_service import (
    anchor_lookup,
    event_times,
    fact_span,
    read_story,
    time_of,
)

MAX_PASSAGES = 40
MAX_EVENTS = 40
PASSAGE_WORDS = 220

ROLE_WORDS = {
    "point_of_view": "is the scene's point of view",
    "place": "is where the scene happens",
    "participant": "is in the scene",
    "about": "is what the scene is about",
}
ROLE_SCORE = {"point_of_view": 10.0, "place": 9.0, "participant": 8.0, "about": 6.0}
# A picture shown on a scene links to the plan item that is the scene.
SCENE_TARGET = "plan_item"
# Pictures found by meaning, at most, after those the author linked.
MAX_SIMILAR_PICTURES = 3


def _listing(names: list[str]) -> str:
    """ "Mara", "Mara and Tobin", "Mara, Tobin and Saltmere"."""
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


@dataclass(frozen=True)
class KeptContext:
    """The context a kept version of the scene was drafted from."""

    manifest_id: str
    version_id: str
    version_number: int
    kept_at: Any
    words: int


@dataclass(frozen=True)
class SceneContext:
    scene: PlanItem
    point_of_view: str | None
    settings: ContextSettings
    manifest: ContextManifest
    index: IndexStatus | None
    semantic: bool


def _phrase(predicate: str) -> str:
    return phrase(predicate)


def _value(obj: Any, names: dict[str, str]) -> str:
    target = getattr(obj, "entity_id", None)
    if target:
        return names.get(target, "something no longer in the story")
    value = getattr(obj, "value", None)
    if isinstance(value, dict):
        return " ".join(str(part) for part in value.values())
    return "" if value is None else str(value)


def _sentence(proposition: Any, names: dict[str, str]) -> str:
    subject = names.get(proposition.subject_id, "Something")
    return f"{subject} {_phrase(proposition.predicate)} {_value(proposition.object, names)}".strip()


def _trim(text: str, words: int) -> str:
    parts = text.split()
    return text.strip() if len(parts) <= words else " ".join(parts[:words]) + " …"


def _hidden(fact: Any, pov: str | None, known_ids: set[str]) -> bool:
    """A secret that must stay off the page: the viewpoint does not know it, or none is chosen."""
    return fact.visibility is FactVisibility.SECRET and (pov is None or fact.id not in known_ids)


def _secret_words(sentence: str, pov: str | None, names: dict[str, str]) -> tuple[str, str]:
    """The text and reason of a secret kept for the author alone."""
    if pov is None:
        return (
            f"{sentence}. It is a secret.",
            "It is a secret, and no point of view is chosen for the scene.",
        )
    who = names.get(pov)
    return (
        f"{sentence}. {who or 'The viewpoint character'} does not know this.",
        f"It is a secret {who or 'the viewpoint character'} does not know.",
    )


def _usable(source: SourceText, scene_texts: set[str]) -> bool:
    """Whether a text may shape a draft in this line of the story."""
    return source.kind != SourceKind.SCENE or source.id in scene_texts


class ContextService:
    def __init__(
        self,
        *,
        reader: Any,
        plan: Any,
        rules: Any = None,
        sources: Any = None,
        passages: Any = None,
        passage_store: Any = None,
        manifests: Any = None,
        clocks: Any = None,
        tenancy: Any = None,
        journal: Any = None,
        studio: Any = None,
        local_mode: bool = False,
    ) -> None:
        self._reader = reader
        self._plan = plan
        self._rules = rules
        self._sources = sources
        self._passages = passages
        self._passage_store = passage_store
        self._manifests = manifests
        self._clocks = clocks
        self._tenancy = tenancy
        self._journal = journal
        # The scene studio's store: which scene texts belong to which line of the story.
        # Set after construction, because the studio is built after this service.
        self.studio = studio
        # Pictures: their accepted descriptions, choices and bytes (CTX-013). Set after
        # construction, because the media services are built after this one.
        self.pictures: Any = None
        # Search by meaning for pictures (area B's ``MediaSearchService``); None leaves
        # the scene's context to explicit links only.
        self.search: Any = None
        self._local_mode = local_mode

    async def _authorize(
        self, scope: AccessScope, workspace_id: str, project_id: str, permission: Permission
    ) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(scope, workspace_id, project_id, permission)

    async def _scene(
        self, scope: AccessScope, workspace_id: str, project_id: str, plan_id: str
    ) -> PlanItem:
        scene = await self._plan.get(scope, workspace_id, project_id, plan_id)
        if scene.kind is not PlanKind.SCENE:
            raise ValidationError("Only a scene has something to write.")
        return scene  # type: ignore[no-any-return]

    # ------------------------------------------------------------------ preview

    async def for_scene(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        *,
        budget: int | None = None,
        include_secrets: bool | None = None,
    ) -> SceneContext:
        """What the assistant would be given for this scene now, and what it would not."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        scene = await self._scene(scope, workspace_id, project_id, plan_id)
        settings = context_settings(scene.details)
        candidates, pov = await self.candidates(workspace_id, project_id, scene)
        # A picture's own choice (AST-007): one to send is kept like a pin, one to leave
        # out is left out like an exclusion; the scene's own pins and exclusions still win.
        chosen_pins = [
            item.key
            for item in candidates
            if item.media is not None
            and item.media.choice is ContextChoice.SEND
            and item.key not in settings.excludes
        ]
        chosen_out = [
            item.key
            for item in candidates
            if item.media is not None and item.media.choice is ContextChoice.LEAVE_OUT
        ]
        manifest = assemble(
            candidates,
            policy=SCENE_DRAFT_POLICY,
            budget=budget if budget is not None else settings.budget,
            pins=[*settings.pins, *chosen_pins],
            excludes=[*settings.excludes, *chosen_out],
            include_secrets=settings.include_secrets
            if include_secrets is None
            else include_secrets,
        )
        index = None
        semantic = False
        if self._passages is not None:
            index = await self._passages.status(workspace_id, project_id)
            semantic = index.semantic
        return SceneContext(
            scene=scene,
            point_of_view=pov,
            settings=settings,
            manifest=manifest,
            index=index,
            semantic=semantic,
        )

    async def set_settings(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        *,
        settings: ContextSettings,
        expected_revision: int,
    ) -> PlanItem:
        """Keep the author's pins, exclusions, budget and choice about secrets with the scene."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        scene = await self._scene(scope, workspace_id, project_id, plan_id)
        overlap = set(settings.pins) & set(settings.excludes)
        if overlap:
            raise ValidationError("Something cannot be both always used and never used.")
        _result, item = await self._plan._set_scene_details(  # noqa: SLF001 - same plan path
            scope,
            workspace_id,
            project_id,
            scene.id,
            lambda details: with_context_settings(details, settings),
            expected_revision,
        )
        return item  # type: ignore[no-any-return]

    async def keep(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        context: SceneContext,
        *,
        purpose: str,
        delivery: Delivery | None = None,
    ) -> str:
        """Record exactly what was given, so what came of it can be traced back; with
        ``delivery``, how each picture reached the model (CTX-013)."""
        if self._manifests is None:
            raise ValidationError("Context cannot be kept here.")
        manifest_id = f"ctx_{uuid7()}"
        manifest = context.manifest
        await self._manifests.save(
            workspace_id=workspace_id,
            project_id=project_id,
            manifest_id=manifest_id,
            branch_id=working_branch_id(),
            purpose=purpose,
            plan_id=context.scene.id,
            policy_version=manifest.policy_version,
            checksum=manifest.checksum,
            words=manifest.words,
            budget=manifest.budget,
            manifest=stored_manifest(manifest, context.point_of_view, delivery),
            created_by=scope.actor_user_id,
        )
        return manifest_id

    # ---------------------------------------------------------------- comparing

    async def kept_for_scene(
        self, scope: AccessScope, workspace_id: str, project_id: str, plan_id: str
    ) -> list[KeptContext]:
        """The versions of this scene's prose that were kept from a draft, newest first.

        Only the line of the story being worked in: a what-if's scene text is its own.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        scene = await self._scene(scope, workspace_id, project_id, plan_id)
        if self._manifests is None or self._sources is None:
            return []
        _usable, own = await self._scene_texts(workspace_id, project_id, scene)
        if own is None:
            return []
        try:
            _source, _current, versions = await self._sources.version(workspace_id, project_id, own)
        except NotFoundError:
            return []
        kept: list[KeptContext] = []
        for version in reversed(versions):
            if not version.manifest_id:
                continue
            saved = await self._manifests.get(
                workspace_id=workspace_id, project_id=project_id, manifest_id=version.manifest_id
            )
            if saved is None:
                continue
            kept.append(
                KeptContext(
                    manifest_id=saved.id,
                    version_id=version.id,
                    version_number=version.number,
                    kept_at=version.created_at,
                    words=saved.words,
                )
            )
        return kept

    async def compare_kept(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        plan_id: str,
        manifest_id: str,
    ) -> ContextComparison:
        """The context now against the one kept for a draft of this scene.

        A kept context is read only for its own scene and its own line of the story: one
        made in this line, or one a version of this scene's prose here still points to
        (a what-if started after the draft was kept carries that version with it).
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        scene = await self._scene(scope, workspace_id, project_id, plan_id)
        missing = NotFoundError("That kept context was not found for this scene.")
        if self._manifests is None:
            raise missing
        saved = await self._manifests.get(
            workspace_id=workspace_id, project_id=project_id, manifest_id=manifest_id
        )
        if saved is None or saved.plan_id != scene.id:
            raise missing
        if saved.branch_id != working_branch_id():
            carried = {
                kept.manifest_id
                for kept in await self.kept_for_scene(scope, workspace_id, project_id, plan_id)
            }
            if saved.id not in carried:
                raise missing
        now = await self.for_scene(scope, workspace_id, project_id, plan_id)
        return compare_contexts(now.manifest, saved)

    # --------------------------------------------------------------- candidates

    async def candidates(
        self, workspace_id: str, project_id: str, scene: PlanItem
    ) -> tuple[list[ContextItem], str | None]:
        story = await read_story(self._reader, workspace_id, project_id)
        entities: dict[str, CanonEntity] = story.entities
        names = {entity.id: entity.name for entity in entities.values()}
        beliefs = await self._reader.list_beliefs(
            workspace_id=workspace_id, project_id=project_id, limit=10_000
        )
        items_all = await self._reader.list_plan_items(  # type: ignore[call-arg]
            workspace_id=workspace_id, project_id=project_id
        )
        plan_by_id = {item.id: item for item in items_all}
        unit = "year"
        if self._clocks is not None:
            unit = (
                await self._clocks.get_unit(workspace_id=workspace_id, project_id=project_id)
                or "year"
            )
        times = event_times(story.events)
        event_names = {event.id: event.name for event in story.events}
        moment: Interval | None = time_of(scene_when(scene.details), times)

        pov = next(iter(scene.refs_as("point_of_view")), None)
        cast: dict[str, str] = {}
        for ref in scene.refs:
            if ref.entity_id in entities and ref.entity_id not in cast:
                cast[ref.entity_id] = ref.role
        out: list[ContextItem] = []

        # The scene itself: always given, last in the rendering.
        when = describe(scene_when(scene.details), unit=unit, event_name=event_names.get)
        text = f"{scene.title}."
        if scene.summary:
            text += f" {scene.summary}"
        if scene_when(scene.details) is not None:
            text += f" It happens {when}."
        notes = plain_details(scene.details)
        if notes:
            text += " " + " ".join(f"{key}: {value}." for key, value in notes.items())
        out.append(
            ContextItem(
                key=f"scene:{scene.id}",
                section=ContextSection.SCENE,
                text=text,
                reason="This is the scene to write.",
                authority="plan",
                record_type="plan_item",
                record_id=scene.id,
                score=100,
                hard=True,
            )
        )
        intent = scene_intent(scene.details)
        if intent is not None:
            # What the viewpoint character wants, what is in the way, and how it ends:
            # the question the scene asks and the answer the author chose.
            out.append(
                ContextItem(
                    key=f"intent:{scene.id}",
                    section=ContextSection.SCENE,
                    text=intent_sentences(intent, names.get(pov) if pov else None),
                    reason="You said what the viewpoint character wants here and how it ends.",
                    authority="plan",
                    record_type="plan_item",
                    record_id=scene.id,
                    score=95,
                    hard=True,
                )
            )
        for index, condition in enumerate(scene_conditions(scene.details)):
            # What must be true first and what the scene should leave true (STY-005):
            # required ones are never cut for length; preferred ones may be.
            required = condition.strength == "hard"
            lead = {
                ("before", True): "Must be true before this scene",
                ("before", False): "Better true before this scene, though not needed",
                ("after", True): "This scene must leave this true",
                ("after", False): "This scene would ideally leave this true",
            }[(condition.when, required)]
            out.append(
                ContextItem(
                    key=f"condition:{scene.id}:{index}",
                    section=ContextSection.SCENE,
                    text=f"{lead}: {condition_reading(condition, names).rstrip('.')}.",
                    reason=(
                        "You said this must hold around the scene."
                        if required
                        else "You said you would like this to hold around the scene."
                    ),
                    authority="plan",
                    record_type="plan_item",
                    record_id=scene.id,
                    score=90 if required else 75,
                    hard=required,
                )
            )
        facts_by_id = {fact.id: fact for fact in story.facts}
        # What the viewpoint character knows by this scene: a secret they lack, or only
        # learn later (KNW-002, CON-006), stays the author's alone.
        if pov:
            placed = StoryOrder(items_all, story.events)
            beliefs = held_at(pov, scene.id, beliefs, placed.by_id, placed.where)
        known = knowledge_of(pov, story.facts, beliefs) if pov else None
        known_ids = known.known_ids() if known else set()
        # The viewpoint character's arc, where this scene moves it (STY-006): the stage
        # going in and the stage reached, so the change happens on the page.
        for move in moves_in_scene(items_all, scene.id):
            if pov is None or pov not in move.about:
                continue
            who = names.get(pov, "The viewpoint character")
            text = f"{who}'s arc “{move.arc_title}”: "
            if move.current is not None:
                text += f"going in, {who} is at “{move.current}”; in this scene, "
            else:
                text += "in this scene, "
            text += f"{who} reaches “{move.reached}”."
            tied = facts_by_id.get(move.fact_id) if move.fact_id else None
            if tied is not None and tied.closed_revision is not None:
                tied = None
            if tied is not None and _hidden(tied, pov, known_ids):
                # A secret the viewpoint does not know is not an instruction for the page:
                # it is kept apart, for the author, like any other secret.
                secret_text, secret_reason = _secret_words(
                    f"From here on: {_sentence(tied.proposition, names)}", pov, names
                )
                out.append(
                    ContextItem(
                        key=f"arc:{scene.id}:{move.arc_id}:fact",
                        section=ContextSection.SECRETS,
                        text=secret_text,
                        reason=f"The stage {who} reaches in this scene is tied to it. "
                        + secret_reason,
                        authority="plan",
                        record_type="fact",
                        record_id=tied.id,
                        score=5,
                    )
                )
            elif tied is not None:
                text += f" From here on: {_sentence(tied.proposition, names)}."
            out.append(
                ContextItem(
                    key=f"arc:{scene.id}:{move.arc_id}",
                    section=ContextSection.SCENE,
                    text=text,
                    reason=f"You said this scene moves {who}'s arc.",
                    authority="plan",
                    record_type="plan_item",
                    record_id=move.arc_id,
                    score=88,
                    hard=True,
                )
            )
        for reveal in scene_reveals(scene.details):
            fact = facts_by_id.get(reveal.fact_id)
            if fact is None:
                continue
            how = "shows the reader" if reveal.how == "shown" else "only hints at"
            out.append(
                ContextItem(
                    key=f"reveal:{scene.id}:{fact.id}",
                    section=ContextSection.SCENE,
                    text=f"This scene {how}: {_sentence(fact.proposition, names)}.",
                    reason="You said the reader learns this here.",
                    authority="plan",
                    record_type="fact",
                    record_id=fact.id,
                    score=85,
                    hard=True,
                )
            )

        # Who and what is in it.
        for entity_id, role in cast.items():
            entity = entities[entity_id]
            details = plain_details_of(entity.attributes)
            text = f"{entity.name} ({entity.type.lower()})"
            if entity.aliases:
                text += f", also called {', '.join(entity.aliases)}"
            if details:
                text += ": " + "; ".join(f"{key} {value}" for key, value in details.items())
            out.append(
                ContextItem(
                    key=f"entity:{entity_id}",
                    section=ContextSection.CAST,
                    text=text + ".",
                    reason=f"{entity.name} {ROLE_WORDS.get(role, 'is in the scene')}.",
                    authority="entry",
                    record_type="entity",
                    record_id=entity_id,
                    score=ROLE_SCORE.get(role, 5.0),
                    hard=role in ("point_of_view", "place"),
                )
            )

        # What is true of them at the scene's time; secrets the viewpoint does not know apart.
        linked: dict[str, int] = {}
        for fact in story.facts:
            if fact.closed_revision is not None:
                continue
            ends = [fact.proposition.subject_id]
            target = getattr(fact.proposition.object, "entity_id", None)
            if target:
                ends.append(target)
            inside = [entity_id for entity_id in ends if entity_id in cast]
            if not inside:
                continue
            truth = Truth.TRUE
            if moment is not None:
                truth = true_at(fact_span(fact, times), moment)
                if truth is Truth.FALSE:
                    continue
            sentence = _sentence(fact.proposition, names)
            if truth is Truth.MAYBE:
                sentence += " (around this time; when it starts or ends is not certain)"
            about = names.get(inside[0], "someone in the scene")
            # With no viewpoint chosen, nobody's knowledge says a secret may be on the
            # page, so every secret is the author's alone.
            if _hidden(fact, pov, known_ids):
                text, reason = _secret_words(sentence, pov, names)
                out.append(
                    ContextItem(
                        key=f"fact:{fact.id}",
                        section=ContextSection.SECRETS,
                        text=text,
                        reason=reason,
                        authority="fact",
                        record_type="fact",
                        record_id=fact.id,
                        score=4 + len(inside),
                    )
                )
                continue
            # Only an ordinary or common fact makes a neighbour worth naming: a secret's
            # other end, named on its own, would hint at the secret.
            for entity_id in ends:
                if entity_id not in cast:
                    linked[entity_id] = linked.get(entity_id, 0) + 1
            out.append(
                ContextItem(
                    key=f"fact:{fact.id}",
                    section=ContextSection.TRUE,
                    text=sentence + ".",
                    reason=f"It is about {about}, who is in the scene.",
                    authority="fact",
                    record_type="fact",
                    record_id=fact.id,
                    score=5 + len(inside) + (1 if fact.visibility is FactVisibility.COMMON else 0),
                )
            )
        # Entries one step away: named so the facts above make sense.
        for entity_id, count in sorted(linked.items(), key=lambda pair: (-pair[1], pair[0]))[:20]:
            neighbour = entities.get(entity_id)
            if neighbour is None or not neighbour.is_active:
                continue
            out.append(
                ContextItem(
                    key=f"entity:{entity_id}",
                    section=ContextSection.CAST,
                    text=f"{neighbour.name} ({neighbour.type.lower()}).",
                    reason=f"Someone in the scene is connected to {neighbour.name}.",
                    authority="entry",
                    record_type="entity",
                    record_id=entity_id,
                    score=2 + min(count, 3) * 0.5,
                )
            )

        # What the viewpoint character knows and gets wrong.
        if known is not None and pov is not None:
            who = names.get(pov, "The viewpoint character")
            for belief, fact in known.wrong:
                out.append(
                    ContextItem(
                        key=f"belief:{belief.id}",
                        section=ContextSection.WRONG,
                        text=(
                            f"{who} believes {_sentence(belief.proposition, names)}; "
                            f"in truth {_sentence(fact.proposition, names)}."
                        ),
                        reason=f"{who} sees the scene, and is wrong about this.",
                        authority="belief",
                        record_type="belief",
                        record_id=belief.id,
                        score=7,
                    )
                )
            for belief in known.unconfirmed:
                out.append(
                    ContextItem(
                        key=f"belief:{belief.id}",
                        section=ContextSection.KNOWS,
                        text=f"{who} believes {_sentence(belief.proposition, names)}.",
                        reason=f"{who} sees the scene; the story does not say whether it is so.",
                        authority="belief",
                        record_type="belief",
                        record_id=belief.id,
                        score=5,
                    )
                )
            for fact, _how in known.knows:
                if fact.visibility is FactVisibility.SECRET:
                    out.append(
                        ContextItem(
                            key=f"knows:{fact.id}",
                            section=ContextSection.KNOWS,
                            text=f"{who} knows a secret: {_sentence(fact.proposition, names)}.",
                            reason=f"{who} sees the scene and holds this secret.",
                            authority="belief",
                            record_type="fact",
                            record_id=fact.id,
                            score=6,
                        )
                    )

        # What has happened, nearest the scene's time first.
        involved = [
            event
            for event in story.events
            if any(part.entity_id in cast for part in event.participants)
            or any(location in cast for location in event.location_ids)
        ]
        for event in involved[:MAX_EVENTS]:
            interval = times[event.id].interval
            if moment is not None and interval is not None:
                if compare(interval, moment) is Order.AFTER:
                    continue
            words = describe(event.when, unit=unit, event_name=event_names.get)
            closeness = 0.0
            if moment is not None and interval is not None:
                closeness = 1 / (1 + abs(moment.lo - interval.hi))
            out.append(
                ContextItem(
                    key=f"event:{event.id}",
                    section=ContextSection.HAPPENED,
                    text=f"{event.name} ({words}).",
                    reason="Someone or somewhere in the scene was part of it.",
                    authority="event",
                    record_type="event",
                    record_id=event.id,
                    score=3 + 3 * closeness,
                )
            )

        # Where it sits in the plan.
        parent_id = scene.parent_id
        depth = 0
        while parent_id and parent_id in plan_by_id and depth < 5:
            parent = plan_by_id[parent_id]
            if parent.is_active:
                summary = f" {parent.summary}" if parent.summary else ""
                out.append(
                    ContextItem(
                        key=f"plan:{parent.id}",
                        section=ContextSection.PLAN,
                        text=f"Part of the {parent.kind.value} “{parent.title}”.{summary}",
                        reason=f"The scene sits under this {parent.kind.value}.",
                        authority="plan",
                        record_type="plan_item",
                        record_id=parent.id,
                        score=6 - depth,
                    )
                )
            parent_id = parent.parent_id
            depth += 1
        for thread_id, verb in (
            [(t, "opens") for t in scene.sets_up]
            + [(t, "moves") for t in thread_advances(scene.details)]
            + [(t, "resolves") for t in scene.pays_off]
        ):
            thread = plan_by_id.get(thread_id)
            if thread is not None and thread.is_active:
                along = " along" if verb == "moves" else ""
                out.append(
                    ContextItem(
                        key=f"thread:{scene.id}:{thread.id}",
                        section=ContextSection.PLAN,
                        text=f"This scene {verb} the thread “{thread.title}”{along}.",
                        reason=f"You said the scene {verb} it{along}.",
                        authority="plan",
                        record_type="plan_item",
                        record_id=thread.id,
                        score=8,
                    )
                )
        order = reading_order(items_all)
        position = next((i for i, item in enumerate(order) if item.id == scene.id), None)
        if position is not None and position > 0:
            before = order[position - 1]
            summary = f" {before.summary}" if before.summary else ""
            out.append(
                ContextItem(
                    key=f"plan:{before.id}",
                    section=ContextSection.PLAN,
                    text=f"The scene before: “{before.title}”.{summary}",
                    reason="It comes just before this one.",
                    authority="plan",
                    record_type="plan_item",
                    record_id=before.id,
                    score=6,
                )
            )

        # What the reader has already been shown, before this scene.
        if position is not None and position > 0:
            steps = reader_journey(order[: position + 1], story.facts, beliefs)
            shown_before = steps[position - 1].reader_knows if position >= 1 else set()
            for fact_id in sorted(shown_before):
                fact = facts_by_id.get(fact_id)
                if fact is None:
                    continue
                if _hidden(fact, pov, known_ids):
                    # The reader knowing a secret does not mean the viewpoint character
                    # does: it stays with the author, like any other secret.
                    text, reason = _secret_words(
                        f"An earlier scene showed the reader that {_sentence(fact.proposition, names)}",
                        pov,
                        names,
                    )
                    out.append(
                        ContextItem(
                            key=f"reader:{fact_id}",
                            section=ContextSection.SECRETS,
                            text=text,
                            reason="An earlier scene showed the reader this. " + reason,
                            authority="reader",
                            record_type="fact",
                            record_id=fact_id,
                            score=4,
                        )
                    )
                    continue
                out.append(
                    ContextItem(
                        key=f"reader:{fact_id}",
                        section=ContextSection.READER,
                        text=f"{_sentence(fact.proposition, names)}.",
                        reason="An earlier scene already showed the reader this.",
                        authority="reader",
                        record_type="fact",
                        record_id=fact_id,
                        score=4,
                    )
                )

        # The story's rules.
        if self._rules is not None:
            rules = await self._rules.list_rules(workspace_id=workspace_id, project_id=project_id)
            types = {entities[e].type for e in cast}
            for rule in rules:
                if rule.status is not RuleStatus.ON:
                    continue
                reading = rule.sentence or read_rule(rule.body, names)
                applies = (
                    rule.body.applies_to.type in types or rule.body.applies_to.entity_id in cast
                )
                out.append(
                    ContextItem(
                        key=f"rule:{rule.id}",
                        section=ContextSection.RULES,
                        text=reading.rstrip(".") + ".",
                        reason="It is one of your story rules"
                        + (" and is about someone or something in the scene." if applies else "."),
                        authority="rule",
                        record_type="story_rule",
                        record_id=rule.id,
                        score=6 if applies else 3,
                    )
                )

        # Pictures on the scene and of who and what is in it (AST-004, CTX-013).
        out.extend(
            await self._pictures(
                workspace_id,
                project_id,
                scene,
                cast,
                names,
                pov=pov,
                moment=moment,
                times=times,
                unit=unit,
                event_names=event_names,
                known_ids=known_ids,
            )
        )

        # The author's own writing that names the scene's people and places.
        out.extend(await self._writing(workspace_id, project_id, scene, cast, names))
        return out, pov

    async def _pictures(
        self,
        workspace_id: str,
        project_id: str,
        scene: PlanItem,
        cast: dict[str, str],
        names: dict[str, str],
        *,
        pov: str | None,
        moment: Interval | None,
        times: dict[str, Any],
        unit: str,
        event_names: dict[str, str],
        known_ids: set[str] | None = None,
    ) -> list[ContextItem]:
        """One item per picture: those on the scene, and those of who and what is in it.

        A link that applies only from a later story time is left out; one whose start
        the scene's time cannot settle is given with a note. A secret picture (AST-004)
        reaches the page only when the viewpoint character is among those who know what
        it shows; otherwise it sits with the secrets, for the author, and is never sent
        as a picture. A picture is one item however many of the scene's people it shows,
        so a portrait and its description are one piece of evidence, not two."""
        links = await self._reader.list_links(
            workspace_id=workspace_id, project_id=project_id, limit=500
        )
        found: dict[str, dict[str, Any]] = {}
        # Pictures of the scene left out by its story time: never brought back by meaning.
        skipped: set[str] = set()
        for link in links:
            if not link.is_active:
                continue
            on_scene = link.target_record_type == SCENE_TARGET and link.target_record_id == scene.id
            of_cast = link.target_record_type != SCENE_TARGET and link.target_record_id in cast
            if not (on_scene or of_cast):
                continue
            note = ""
            if link.applies_from is not None:
                start = parse_world_time(link.applies_from)
                if start is not None and not is_unknown(start):
                    from_words = describe(start, unit=unit, event_name=event_names.get)
                    if moment is None:
                        note = (
                            f" It applies from {from_words}; the scene has no time, so it may "
                            "not apply yet."
                        )
                    else:
                        truth = true_at(span_of(resolve(start, anchor_lookup(times)), None), moment)
                        if truth is Truth.FALSE:
                            skipped.add(link.asset_id)
                            continue
                        if truth is Truth.MAYBE:
                            note = f" It applies from {from_words}, around the scene's time."
            entry = found.setdefault(
                link.asset_id, {"targets": [], "scene": False, "secret": False, "notes": []}
            )
            if on_scene:
                entry["scene"] = True
            else:
                entry["targets"].append(names.get(link.target_record_id, "someone in the scene"))
            if link.is_secret and (pov is None or pov not in link.known_by):
                entry["secret"] = True
            if note and note not in entry["notes"]:
                entry["notes"].append(note)
        similar = await self._similar_pictures(
            workspace_id, project_id, scene, pov, set(found) | skipped, known_ids or set(), links
        )
        if not found and not similar:
            return []
        pictures: dict[str, Any] = {}
        if self.pictures is not None:
            pictures = await self.pictures.for_context(workspace_id, project_id, [*found, *similar])
        out: list[ContextItem] = []
        for asset_id, entry in found.items():
            picture = pictures.get(asset_id)
            if self.pictures is not None and picture is None:
                # Retired, or no current version: nothing to show or send.
                continue
            targets = list(dict.fromkeys(entry["targets"]))
            shows = _listing(targets) if targets else "this scene"
            title = f"“{picture.asset.name}”" if picture is not None else "a picture"
            label = f"{title}, version {picture.version.version}" if picture is not None else title
            description = picture.description if picture is not None else ""
            notes = "".join(entry["notes"])
            if description:
                text = f"A picture of {shows} ({title}): {description}{notes}"
            else:
                text = (
                    f"There is a picture of {shows} ({title}); it has no accepted "
                    f"description yet.{notes}"
                )
            why = "You put it on this scene." if entry["scene"] else f"It shows {shows}."
            key = f"picture:{asset_id}"
            # A version marked secret is kept from every point of view, as search keeps it.
            secret_version = picture is not None and picture.version.visibility == "secret"
            if entry["secret"] or secret_version:
                who = names.get(pov) if pov else None
                if entry["secret"]:
                    secret_reason = (
                        f"It is a secret picture {who or 'the viewpoint character'} does not know."
                        if pov
                        else "It is a secret picture, and no point of view is chosen for the scene."
                    )
                else:
                    secret_reason = "This version of the picture is marked secret."
                out.append(
                    ContextItem(
                        key=key,
                        section=ContextSection.SECRETS,
                        text=text,
                        reason=f"{why} {secret_reason} It is never sent as a picture.",
                        authority="picture",
                        record_type="asset",
                        record_id=asset_id,
                        score=3,
                    )
                )
                continue
            media = (
                self.pictures.context_part(picture, label=label, key=key)
                if picture is not None and self.pictures is not None
                else None
            )
            choice_words = ""
            if media is not None:
                choice_words = (
                    " "
                    + REPRESENTATION_WORDS[planned_representation(media.choice, bool(description))]
                )
            out.append(
                ContextItem(
                    key=key,
                    section=ContextSection.PICTURES,
                    text=text,
                    reason=why + choice_words,
                    authority="picture",
                    record_type="asset",
                    record_id=asset_id,
                    score=(3.0 if entry["scene"] else 1.5) + (0.5 if description else 0),
                    media=media,
                )
            )
        for asset_id, score in similar.items():
            picture = pictures.get(asset_id)
            # Found by meaning: only its accepted words are given, never the picture, and
            # never above a picture the author linked.
            if picture is None or not picture.description or self.pictures is None:
                continue
            if picture.version.visibility == "secret":
                continue
            key = f"picture:{asset_id}"
            media = self.pictures.context_part(
                picture,
                label=f"“{picture.asset.name}”, version {picture.version.version}",
                key=key,
            )
            # One the author chose to leave out stays out; one to send is only described.
            if media.choice is ContextChoice.LEAVE_OUT:
                continue
            if media.choice is ContextChoice.SEND:
                media = replace(media, choice=ContextChoice.DESCRIBE)
            out.append(
                ContextItem(
                    key=key,
                    section=ContextSection.PICTURES,
                    text=f"A picture like this scene (“{picture.asset.name}”): {picture.description}",
                    reason=(
                        "It was found by meaning, not linked by you, so only its accepted "
                        "description is given."
                    ),
                    authority="similar picture",
                    record_type="asset",
                    record_id=asset_id,
                    score=0.5 + min(max(score, 0.0), 1.0) * 0.4,
                    media=media,
                )
            )
        return out

    async def _similar_pictures(
        self,
        workspace_id: str,
        project_id: str,
        scene: PlanItem,
        pov: str | None,
        linked: set[str],
        known_ids: set[str],
        links: list[Any],
    ) -> dict[str, float]:
        """Pictures found by meaning for the scene (area B's search), behind every gate
        it applies, and this one too: a picture with a secret link the viewpoint is not
        among those who know is never suggested. Empty when search is not set up."""
        if self.search is None or self.pictures is None:
            return {}
        query = " ".join(part for part in (scene.title, scene.summary or "") if part).strip()
        if not query:
            return {}
        from backend.domain.embedding import SearchGate

        try:
            result = await self.search.pictures_for(
                workspace_id,
                project_id,
                query=query,
                gate=SearchGate(point_of_view=True, point_of_view_id=pov),
                limit=MAX_SIMILAR_PICTURES + len(linked),
            )
        except Exception:  # noqa: BLE001 - search by meaning is extra; links stand alone
            return {}
        secret = {
            link.asset_id
            for link in links
            if link.is_active and link.is_secret and (pov is None or pov not in link.known_by)
        }
        found: dict[str, float] = {}
        for candidate in result.pictures:
            if candidate.reason != "similar" or candidate.asset_id in linked:
                continue
            if candidate.asset_id in secret:
                continue
            found[candidate.asset_id] = float(candidate.score or 0.0)
            if len(found) >= MAX_SIMILAR_PICTURES:
                break
        return found

    async def deliver(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        context: SceneContext,
        limits: MediaLimits,
        *,
        sections: frozenset[ContextSection] | None = None,
    ) -> Any:
        """What a request made from this context carries for its pictures (CTX-013):
        each picture given, as the picture itself or its description, decided for the
        route the request uses. None when pictures are not set up here."""
        if self.pictures is None:
            return None
        parts = [
            item.media
            for item in context.manifest.items
            if item.media is not None and (sections is None or item.section in sections)
        ]
        return await self.pictures.deliver(scope, workspace_id, project_id, parts, limits)

    async def _writing(
        self,
        workspace_id: str,
        project_id: str,
        scene: PlanItem,
        cast: dict[str, str],
        names: dict[str, str],
    ) -> list[ContextItem]:
        if self._passage_store is None or self._sources is None or not cast:
            return []
        usable, own = await self._scene_texts(workspace_id, project_id, scene)
        passages = await self._passage_store.mentioning(
            workspace_id=workspace_id,
            project_id=project_id,
            entity_ids=list(cast),
            limit=MAX_PASSAGES * 4,
        )
        # The shared index reads each text as its own line does. When some answer holds only
        # in a what-if, a stretch of scenes or one viewpoint, each passage is read again as
        # this line reads it (CTX-016), and texts using such a phrase are looked at too.
        line = None
        if hasattr(self._sources, "line_names"):
            line = await self._sources.line_names(workspace_id, project_id)
        if line is not None:
            passages = await self._with_limited_phrases(
                workspace_id, project_id, passages, line.phrases_for(cast), usable
            )
        semantic: dict[str, float] = {}
        if self._passages is not None:
            semantic = await self._passages.semantic_scores(
                workspace_id, project_id, f"{scene.title}. {scene.summary}"
            )
        texts: dict[str, Any] = {}
        out: list[ContextItem] = []
        for passage in passages:
            if passage.source_id not in texts:
                try:
                    source, version, _ = await self._sources.version(
                        workspace_id, project_id, passage.source_id
                    )
                except NotFoundError:
                    continue
                texts[passage.source_id] = (source, version)
            source, version = texts[passage.source_id]
            if not _usable(source, usable):
                continue  # a scene's words from another line of the story
            if version.id != passage.version_id:
                continue  # an older version's passage; the index is catching up
            words = version.content[passage.start : passage.end]
            named_here = passage.entity_ids
            if line is not None:
                named_here = tuple(
                    mention.entity_ids[0]
                    for mention in find_mentions(words, line.index(passage.source_id))
                    if len(mention.entity_ids) == 1
                )
            shared = list(dict.fromkeys(entity_id for entity_id in named_here if entity_id in cast))
            if line is not None and not shared:
                continue  # in this line of the story the passage names nobody in the scene
            named = ", ".join(names.get(entity_id, "") for entity_id in shared[:3])
            out.append(
                ContextItem(
                    key=passage.key,
                    section=ContextSection.WRITING,
                    text=f"From “{source.title}”: {_trim(words, PASSAGE_WORDS)}",
                    reason=f"It mentions {named}." if named else "It is from your writing.",
                    authority="writing",
                    record_type="source_version",
                    record_id=version.id,
                    location=f"chars:{passage.start}-{passage.end}",
                    score=1 + len(shared) + 3 * semantic.get(passage.key, 0.0),
                )
            )
        out.sort(key=lambda item: (-item.score, item.key))
        out = out[:MAX_PASSAGES]
        # A sample of the author's voice, even when it names nobody in the scene: writers
        # report that re-feeding representative passages keeps voice from drifting.
        sample = await self._voice_sample(
            workspace_id, project_id, {item.key for item in out}, usable, own
        )
        if sample is not None:
            out.append(sample)
        return out

    async def _with_limited_phrases(
        self,
        workspace_id: str,
        project_id: str,
        passages: list[Any],
        phrases: list[str],
        usable: set[str],
    ) -> list[Any]:
        """Add the passages of texts that use a phrase meaning someone only somewhere."""
        if not phrases:
            return passages
        wanted = [normalize_name(phrase) for phrase in phrases]
        seen = {passage.key for passage in passages}
        out = list(passages)
        for source in await self._sources.all_texts(workspace_id, project_id):
            if len(out) >= MAX_PASSAGES * 6:
                break
            if source.removed_at is not None or not _usable(source, usable):
                continue
            # Only the newest version's words are read: every older one would be loaded
            # whole for nothing, once per text, every time a context is built.
            version = await self._sources.latest_version(workspace_id, project_id, source)
            if version is None:
                continue
            content = normalize_name(version.content)
            if not any(phrase in content for phrase in wanted):
                continue
            for passage in await self._passage_store.for_source(
                workspace_id=workspace_id, project_id=project_id, source_id=source.id
            ):
                if passage.key in seen:
                    continue
                words = normalize_name(version.content[passage.start : passage.end])
                if any(phrase in words for phrase in wanted):
                    seen.add(passage.key)
                    out.append(passage)
        return out

    async def _scene_texts(
        self, workspace_id: str, project_id: str, scene: PlanItem
    ) -> tuple[set[str], str | None]:
        """The scene texts of the line of the story being worked in, and this scene's own.

        A scene's words written in a what-if must never shape the main story's drafts, or
        another what-if's. Without the studio's links nothing says which line a scene text
        belongs to, so none is used.
        """
        if self.studio is None or self._journal is None:
            return set(), None
        branch = await self._journal.working_branch(
            workspace_id=workspace_id, project_id=project_id
        )
        links = await self.studio.manuscripts_for(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
        )
        own = next((source_id for plan_id, source_id in links if plan_id == scene.id), None)
        return {source_id for _, source_id in links}, own

    async def _voice_sample(
        self,
        workspace_id: str,
        project_id: str,
        taken: set[str],
        usable: set[str],
        own: str | None,
    ) -> ContextItem | None:
        texts = [
            text
            for text in await self._sources.all_texts(workspace_id, project_id)
            if _usable(text, usable)
        ]
        # The scene's own words are what is being rewritten: a sample of the author's
        # voice comes from elsewhere when there is anything else.
        others = [text for text in texts if text.id != own]
        texts = others or texts
        if not texts:
            return None
        newest = max(texts, key=lambda text: (text.created_at, text.id))
        indexed = await self._passage_store.for_source(
            workspace_id=workspace_id, project_id=project_id, source_id=newest.id
        )
        if not indexed:
            return None
        last = indexed[-1]
        if last.key in taken:
            return None
        try:
            _, version, _ = await self._sources.version(workspace_id, project_id, newest.id)
        except NotFoundError:
            return None
        if version.id != last.version_id:
            return None
        words = version.content[last.start : last.end]
        return ContextItem(
            key=last.key,
            section=ContextSection.WRITING,
            text=f"From “{newest.title}”: {_trim(words, PASSAGE_WORDS)}",
            reason="A sample of your voice, from your most recent writing.",
            authority="writing",
            record_type="source_version",
            record_id=version.id,
            location=f"chars:{last.start}-{last.end}",
            score=2.5,
        )


__all__ = ["ContextService", "KeptContext", "SceneContext"]
