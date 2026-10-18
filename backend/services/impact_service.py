"""What a change touches, worked out on read from data the story already keeps (CON-017).

The dependency index (CON-007) is a query, not a store: every answer is computed
from the plan, the story rules, the kept contexts of drafts and the passage index,
in the line of the story the change belongs to. Nothing is cached, so nothing can
be stale; what the index cannot see is said, never hidden.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from backend.core.logging import log_event
from backend.domain.branching import working_in
from backend.domain.errors import NotFoundError
from backend.domain.identity import AccessScope
from backend.domain.impact import (
    Impact,
    KeptDraft,
    Mention,
    StoryFacts,
    Touched,
    assemble,
    draft_dependents,
    own_text_dependents,
    plan_dependents,
    rule_dependents,
    standing_gaps,
    text_dependents,
    touched_by,
    touched_entry,
)
from backend.domain.operations import CanonOperation, parse_operation
from backend.domain.review import effective_operations
from backend.domain.rules import World, apply_operations, world_from_canon
from backend.domain.scene_conditions import scene_conditions
from backend.domain.sources import SourceKind
from backend.domain.tenancy import Permission

logger = logging.getLogger(__name__)

MAX_ENTITIES = 2000
MAX_FACTS = 10_000
MAX_PASSAGES = 500


class ImpactService:
    def __init__(
        self,
        *,
        reader: Any,
        mutation: Any,
        journal: Any,
        rules: Any = None,
        sources: Any = None,
        passage_store: Any = None,
        manifests: Any = None,
        studio: Any = None,
        tenancy: Any = None,
        local_mode: bool = False,
    ) -> None:
        self._reader = reader
        self._mutation = mutation
        self._journal = journal
        self._rules = rules
        self._sources = sources
        self._passage_store = passage_store
        self._manifests = manifests
        self._studio = studio
        self._tenancy = tenancy
        self._local_mode = local_mode

    async def _authorize(self, scope: AccessScope, workspace_id: str, project_id: str) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(
            scope, workspace_id, project_id, Permission.PROJECT_READ
        )

    # --------------------------------------------------------------------- entry points
    async def for_mutation(self, scope: AccessScope, mutation_id: str) -> Impact:
        """What the changes still included in a proposal touch, in the proposal's own line."""
        review = await self._mutation.review(scope, mutation_id)  # authorizes reading
        mutation = review.mutation
        accepted = effective_operations(review.operations, review.operation_decisions.values())
        operations: list[CanonOperation] = []
        unreadable = 0
        for item in accepted:
            try:
                operations.append(parse_operation(item.payload))
            except Exception:  # noqa: BLE001 - an unreadable change is reported, not fatal
                unreadable += 1
        branch = await self._journal.get_branch(
            workspace_id=mutation.workspace_id,
            project_id=mutation.project_id,
            branch_id=mutation.branch_id,
        )
        with working_in(None if branch is None or branch.is_root else branch.id):
            story, before, complete = await self._story(mutation.workspace_id, mutation.project_id)
            touched = touched_by(operations, story)
            impact = await self._impact(
                mutation.workspace_id,
                mutation.project_id,
                touched,
                story,
                before,
                apply_operations(before, operations),
                complete=complete,
            )
        if unreadable:
            impact.not_checked.insert(
                0,
                "One change could not be read, so what it touches is not listed."
                if unreadable == 1
                else f"{unreadable} changes could not be read, so what they touch is not listed.",
            )
        return impact

    async def for_entry(
        self, scope: AccessScope, workspace_id: str, project_id: str, entity_id: str
    ) -> Impact:
        """What depends on one entry, in the line of the story the request works in."""
        await self._authorize(scope, workspace_id, project_id)
        story, world, complete = await self._story(workspace_id, project_id)
        if entity_id not in story.names:
            raise NotFoundError("That entry was not found.")
        return await self._impact(
            workspace_id,
            project_id,
            touched_entry(entity_id, story),
            story,
            world,
            world,
            complete=complete,
        )

    # ------------------------------------------------------------------------- reading
    async def _story(self, workspace_id: str, project_id: str) -> tuple[StoryFacts, World, bool]:
        entities = await self._reader.list_entities(
            workspace_id=workspace_id,
            project_id=project_id,
            include_retired=True,
            limit=MAX_ENTITIES,
        )
        facts = await self._reader.list_facts(
            workspace_id=workspace_id, project_id=project_id, limit=MAX_FACTS
        )
        beliefs = await self._reader.list_beliefs(
            workspace_id=workspace_id, project_id=project_id, limit=MAX_FACTS
        )
        events = await self._reader.list_events(
            workspace_id=workspace_id, project_id=project_id, limit=MAX_FACTS
        )
        plans = await self._reader.list_plan_items(workspace_id=workspace_id, project_id=project_id)
        world = world_from_canon(entities, facts)
        story = StoryFacts(
            names={entity.id: entity.name for entity in entities},
            facts=dict(world.facts),
            beliefs={
                belief.id: (belief.holder_entity_id, belief.proposition.subject_id)
                for belief in beliefs
            },
            events={
                event.id: (
                    *(item.entity_id for item in event.participants),
                    *event.location_ids,
                )
                for event in events
            },
            plans={item.id: item for item in plans if item.is_active},
        )
        complete = len(entities) < MAX_ENTITIES and len(facts) < MAX_FACTS
        return story, world, complete

    async def _manuscripts(self, workspace_id: str, project_id: str) -> dict[str, str]:
        """Plan id -> the text holding that scene's prose, in the line being worked in."""
        if self._studio is None:
            return {}
        branch = await self._journal.working_branch(
            workspace_id=workspace_id, project_id=project_id
        )
        links = await self._studio.manuscripts_for(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
        )
        return dict(links)

    async def _drafts(
        self, workspace_id: str, project_id: str, manuscripts: dict[str, str], story: StoryFacts
    ) -> tuple[list[KeptDraft], int]:
        """The newest kept draft of each scene, and how many older kept drafts were not read.

        Only one kept context is read per scene, so a read stays one request per scene
        however many drafts were kept; the older ones are counted and said."""
        if self._manifests is None or self._sources is None:
            return [], 0
        drafts: list[KeptDraft] = []
        older = 0
        for plan_id, source_id in manuscripts.items():
            scene = story.plans.get(plan_id)
            if scene is None:
                continue
            try:
                _, _, versions = await self._sources.version(workspace_id, project_id, source_id)
            except NotFoundError:
                continue
            kept = sorted(
                (version for version in versions if version.manifest_id),
                key=lambda version: version.number,
                reverse=True,
            )
            if not kept:
                continue
            newest = kept[0]
            older += len(kept) - 1
            saved = await self._manifests.get(
                workspace_id=workspace_id,
                project_id=project_id,
                manifest_id=newest.manifest_id,
            )
            if saved is None:
                continue
            records = frozenset(
                str(item.get("record_id"))
                for item in saved.manifest.get("items", [])
                if isinstance(item, dict) and item.get("record_id")
            )
            drafts.append(
                KeptDraft(
                    plan_id=plan_id,
                    title=scene.title,
                    version_id=newest.id,
                    version_number=newest.number,
                    records=records,
                )
            )
        return drafts, older

    async def _mentions(
        self, workspace_id: str, project_id: str, touched: Touched, manuscripts: dict[str, str]
    ) -> tuple[list[Mention], list[str], bool]:
        """Passages naming a changed entry, texts the index is behind on, and whether it stopped."""
        if self._passage_store is None or self._sources is None:
            return [], [], False
        texts = {
            text.id: text
            for text in await self._sources.all_texts(workspace_id, project_id)
            if text.removed_at is None
        }
        scene_of = {source_id: plan_id for plan_id, source_id in manuscripts.items()}
        usable = {
            text_id: text
            for text_id, text in texts.items()
            if text.kind != SourceKind.SCENE or text_id in scene_of
        }
        indexed = await self._passage_store.indexed_versions(
            workspace_id=workspace_id, project_id=project_id
        )
        behind = sorted(
            text.title
            for text_id, text in usable.items()
            if text.length > 0 and indexed.get(text_id, ("", 0))[0] != text.latest_version_id
        )
        if not touched.entries:
            return [], behind, False
        passages = await self._passage_store.mentioning(
            workspace_id=workspace_id,
            project_id=project_id,
            entity_ids=sorted(touched.entries),
            limit=MAX_PASSAGES,
        )
        found: list[Mention] = []
        for passage in passages:
            text = usable.get(passage.source_id)
            if text is None or passage.version_id != text.latest_version_id:
                continue  # another line's scene, or an older version the index is catching up on
            found.append(
                Mention(
                    source_id=passage.source_id,
                    title=text.title,
                    version_id=passage.version_id,
                    start=passage.start,
                    end=passage.end,
                    entity_ids=tuple(passage.entity_ids),
                    plan_id=scene_of.get(passage.source_id),
                )
            )
        return found, behind, len(passages) >= MAX_PASSAGES

    async def _other_lines(self, workspace_id: str, project_id: str) -> str | None:
        try:
            branches = await self._journal.list_branches(
                workspace_id=workspace_id, project_id=project_id
            )
        except AttributeError:
            return None
        if len(branches) <= 1:
            return None
        working = await self._journal.working_branch(
            workspace_id=workspace_id, project_id=project_id
        )
        if working.is_root:
            return (
                "Your what-ifs. Each is its own line of the story and is looked at from inside it."
            )
        return "The main story and other what-ifs. Only this what-if is looked at."

    async def _impact(
        self,
        workspace_id: str,
        project_id: str,
        touched: Touched,
        story: StoryFacts,
        before: World,
        after: World,
        *,
        complete: bool,
    ) -> Impact:
        manuscripts = await self._manuscripts(workspace_id, project_id)
        rules: Sequence[Any] = []
        if self._rules is not None and touched.entries:
            rules = await self._rules.list_rules(workspace_id=workspace_id, project_id=project_id)
        drafts, older = (
            await self._drafts(workspace_id, project_id, manuscripts, story)
            if touched.ids
            else ([], 0)
        )
        mentions, behind, stopped = await self._mentions(
            workspace_id, project_id, touched, manuscripts
        )
        titles = {
            plan_id: (source_id, story.plans[plan_id].title)
            for plan_id, source_id in manuscripts.items()
            if plan_id in story.plans
        }
        gaps = standing_gaps(touched)
        if behind:
            shown = ", ".join(f"“{title}”" for title in behind[:5])
            more = f" and {len(behind) - 5} more" if len(behind) > 5 else ""
            gaps.insert(
                0,
                f"Writing changed since names were last found in it: {shown}{more}. "
                "Rebuild the writing index to include it.",
            )
        if stopped:
            gaps.insert(
                0, f"Only the first {MAX_PASSAGES} passages that mention it were looked at."
            )
        if not complete:
            gaps.insert(
                0,
                "Your story is larger than this looks at, so some rules and scenes may be missing.",
            )
        in_words = sum(
            1
            for item in story.plans.values()
            for condition in scene_conditions(item.details)
            if condition.words is not None
        )
        if in_words and touched.entries:
            gaps.append(
                "A scene condition written in your own words, which is never read."
                if in_words == 1
                else f"{in_words} scene conditions written in your own words, which are never read."
            )
        if older:
            gaps.append(
                "An older kept draft: only each scene's newest kept draft is looked at."
                if older == 1
                else f"{older} older kept drafts: only each scene's newest kept draft is looked at."
            )
        other = await self._other_lines(workspace_id, project_id)
        if other:
            gaps.append(other)
        impact = assemble(
            touched,
            plan=plan_dependents(touched, story),
            rules=rule_dependents(touched, rules, before, after),
            drafts=draft_dependents(touched, drafts, story),
            texts=[*text_dependents(touched, mentions), *own_text_dependents(touched, titles)],
            not_checked=gaps,
        )
        # Counts only: never names, titles or words.
        log_event(
            logger,
            "impact.read",
            affected=len(impact.affected),
            may_be_affected=len(impact.may_be_affected),
            not_checked=len(impact.not_checked),
        )
        return impact


__all__ = ["ImpactService"]
