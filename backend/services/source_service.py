"""Texts an author brings in: keep them, find the names in them, remember who is who.

Nothing here calls a model. Pasting a text, seeing which entries it names,
answering "is 'the Fox' Mara?", and adding a marked name as a new entry all work
with no provider connected. A text is evidence, not canon: only a change the
author keeps makes anything true in the story.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.domain.branching import working_branch_id, working_in
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.naming import normalize_name
from backend.domain.canon.plans import PlanKind
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.knowledge import reading_order
from backend.domain.operations import EvidenceRef
from backend.domain.sources import (
    MAX_EXAMPLE_LENGTH,
    MAX_EXAMPLES,
    MAX_PHRASE_LENGTH,
    MAX_SOURCE_LENGTH,
    MAX_SOURCES_PER_PROJECT,
    MAX_TITLE_LENGTH,
    MAX_VERSIONS_PER_SOURCE,
    AliasBinding,
    Excerpt,
    Mention,
    NameExample,
    NameIndex,
    NamePlace,
    NameQuestion,
    Section,
    SourceKind,
    SourceText,
    SourceVersion,
    Verdict,
    VersionOrigin,
    applicable,
    build_index,
    clean_content,
    content_hash,
    example_holds,
    excerpt,
    find_mentions,
    location_for,
    name_questions,
    parse_location,
    split_sections,
)
from backend.domain.tenancy import Permission
from backend.ports import CanonRepositoryPort
from backend.services.canon_entity_service import CanonEntityService

logger = logging.getLogger(__name__)

MAX_ENTITIES = 2000


@dataclass(frozen=True)
class SourceReading:
    """One version of a text, with everything derived from it."""

    source: SourceText
    version: SourceVersion
    versions: list[SourceVersion]
    sections: list[Section]
    mentions: list[Mention]
    questions: list[NameQuestion]
    names: dict[str, str]


@dataclass(frozen=True)
class EvidenceReading:
    """The passage a kept change points back to."""

    source_id: str
    title: str
    version_number: int | None
    # False when the author has since removed the text: the pointer survives, the words do not.
    available: bool
    passage: Excerpt | None


@dataclass(frozen=True)
class NameScope:
    """Where a new answer holds. Every limit is optional; none means everywhere."""

    source_id: str | None = None
    # None: kept to the what-if being worked in, if any. False: every line of the story.
    what_if_only: bool | None = None
    from_scene_id: str | None = None
    until_scene_id: str | None = None
    viewpoint_id: str | None = None


@dataclass(frozen=True)
class ExampleRequest:
    """A passage the author confirms, picked in a text or typed out."""

    # Picked in a text: the words are taken from that version, never from the request.
    source_id: str | None = None
    version_id: str | None = None
    start: int | None = None
    end: int | None = None
    # Typed out on the names page; must use the phrase.
    quote: str | None = None
    scene_id: str | None = None
    # Whether the phrase means the answer's entry here; by default what the answer says.
    means: bool | None = None


@dataclass(frozen=True)
class ExampleCheck:
    holds: bool
    # What the phrase resolves to there today.
    now: list[str]


@dataclass
class NameMemory:
    bindings: list[AliasBinding]
    names: dict[str, str]
    # Titles of the scenes the answers and examples mention, in the line being worked in.
    scenes: dict[str, str] = field(default_factory=dict)
    checks: dict[str, ExampleCheck] = field(default_factory=dict)


@dataclass
class LineNames:
    """Who each phrase means, text by text, in one line of the story (CTX-016).

    Built once for a request that reads many texts (the scene's context), so the story
    bible, the answers and the plan are loaded once.
    """

    entities: list[CanonEntity]
    bindings: list[AliasBinding]
    branch_id: str | None
    order: dict[str, int]
    # Text id → the scene it holds in this line; scene id → whose eyes it is seen through.
    scene_of: dict[str, str]
    viewpoints: dict[str, tuple[str, ...]]
    _cache: dict[str, NameIndex] = field(default_factory=dict)

    def place(self, source_id: str) -> NamePlace:
        scene_id = self.scene_of.get(source_id)
        if scene_id not in self.order:
            scene_id = None
        return NamePlace(
            source_id=source_id,
            branch_id=self.branch_id,
            scene_id=scene_id,
            viewpoint_ids=self.viewpoints.get(scene_id, ()) if scene_id else (),
            order=self.order,
        )

    def index(self, source_id: str) -> NameIndex:
        if source_id not in self._cache:
            self._cache[source_id] = build_index(
                self.entities, self.bindings, place=self.place(source_id)
            )
        return self._cache[source_id]

    def phrases_for(self, entity_ids: Iterable[str]) -> list[str]:
        """Phrases that mean one of these entries only somewhere: in a what-if, a stretch of
        scenes or one viewpoint. A shared index cannot know them for every line."""
        wanted = set(entity_ids)
        return sorted(
            {
                item.phrase
                for item in self.bindings
                if item.verdict is Verdict.IS
                and item.entity_id in wanted
                and (
                    item.branch_id or item.from_scene_id or item.until_scene_id or item.viewpoint_id
                )
            }
        )


def _limited(bindings: Iterable[AliasBinding]) -> bool:
    """Whether any answer holds only in a what-if, a stretch of scenes or one viewpoint.

    A model's guess resolves nothing, so one kept in a what-if limits nothing either."""
    return any(
        item.verdict is not Verdict.SUGGESTED
        and (item.branch_id or item.from_scene_id or item.until_scene_id or item.viewpoint_id)
        for item in bindings
    )


_EXAMPLE_MARGIN = 140


def _quote_around(content: str, start: int, end: int) -> tuple[str, tuple[int, int]]:
    """The words around a phrase, cut at word boundaries, and where the phrase sits in them."""
    budget = max(0, MAX_EXAMPLE_LENGTH - (end - start)) // 2
    margin = min(_EXAMPLE_MARGIN, budget)
    left = max(0, start - margin)
    right = min(len(content), end + margin)
    if left > 0:
        space = content.find(" ", left, start)
        left = space + 1 if space != -1 else left
    if right < len(content):
        space = content.rfind(" ", end, right)
        right = space if space != -1 else right
    raw = content[left:right]
    # Line breaks become spaces, one for one, so the offsets still hold.
    quote = raw.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return quote, (start - left, end - left)


class SourceService:
    def __init__(
        self,
        store: object,
        *,
        reader: CanonRepositoryPort,
        entities: CanonEntityService,
        tenancy: object | None = None,
        local_mode: bool = False,
    ) -> None:
        self._store = store
        self._reader = reader
        self._entities = entities
        # The passage index (ADR-021), attached by the container once both exist.
        self.passages: object | None = None
        # The studio, attached by the container: which scene a text holds (CTX-016).
        self.studio: object | None = None
        self._tenancy = tenancy
        self._local_mode = local_mode

    async def _authorize(
        self, scope: AccessScope, workspace_id: str, project_id: str, permission: Permission
    ) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(scope, workspace_id, project_id, permission)  # type: ignore[attr-defined]

    async def _source(self, workspace_id: str, project_id: str, source_id: str) -> SourceText:
        source = await self._store.get_source(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id, source_id=source_id
        )
        if source is None or source.removed_at is not None:
            raise NotFoundError("That text was not found.")
        return source  # type: ignore[no-any-return]

    async def _all_entities(self, workspace_id: str, project_id: str) -> list[CanonEntity]:
        return list(
            await self._reader.list_entities(
                workspace_id=workspace_id, project_id=project_id, limit=MAX_ENTITIES
            )
        )

    @staticmethod
    def _checked(title: str, content: str) -> tuple[str, str]:
        title = " ".join(title.split())
        if not title:
            raise ValidationError("Give the text a title.")
        if len(title) > MAX_TITLE_LENGTH:
            raise ValidationError(f"Keep the title under {MAX_TITLE_LENGTH} characters.")
        return title, SourceService._checked_content(content)

    @staticmethod
    def _checked_content(content: str) -> str:
        content = clean_content(content)
        if not content.strip():
            raise ValidationError("There is no text to keep.")
        if "\x00" in content:
            raise ValidationError("This does not look like plain text.")
        if len(content) > MAX_SOURCE_LENGTH:
            raise ValidationError(
                f"This is longer than {MAX_SOURCE_LENGTH:,} characters. "
                "Bring it in a chapter at a time."
            )
        return content

    # ------------------------------------------------------------- sources
    async def texts(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> list[SourceText]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._store.list_sources(  # type: ignore[attr-defined,no-any-return]
            workspace_id=workspace_id, project_id=project_id
        )

    async def all_texts(self, workspace_id: str, project_id: str) -> list[SourceText]:
        """Every text, for the passage index; callers have already been authorized."""
        return await self._store.list_sources(  # type: ignore[attr-defined,no-any-return]
            workspace_id=workspace_id, project_id=project_id
        )

    async def _reindex(
        self, workspace_id: str, project_id: str, source_id: str, *, gone: bool = False
    ) -> None:
        """Keep the passage index in step. It is derived: a failure never loses the text."""
        if self.passages is None:
            return
        try:
            if gone:
                await self.passages.forget_source(workspace_id, project_id, source_id)  # type: ignore[attr-defined]
            else:
                await self.passages.index_source(workspace_id, project_id, source_id)  # type: ignore[attr-defined]
        except Exception as error:  # noqa: BLE001 - the index can be rebuilt; the text is safe
            log_event(logger, "passages.index_failed", error_type=type(error).__name__)

    async def add(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        title: str,
        content: str,
        kind: SourceKind = SourceKind.PASTE,
    ) -> SourceText:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if kind is SourceKind.SCENE:
            # A scene's text is started from the scene, in the studio.
            raise ValidationError("Write a scene from the story plan, in the scene's studio.")
        title, content = self._checked(title, content)
        existing = await self._store.list_sources(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id
        )
        if len(existing) >= MAX_SOURCES_PER_PROJECT:
            raise ValidationError("This story has as many texts as it can hold. Remove one first.")
        source_id = format_canon_id("source_text", str(uuid7()))
        await self._store.add_source(  # type: ignore[attr-defined]
            workspace_id=workspace_id,
            project_id=project_id,
            source_id=source_id,
            title=title,
            kind=kind,
            actor_user_id=scope.actor_user_id,
        )
        await self._store.add_version(  # type: ignore[attr-defined]
            workspace_id=workspace_id,
            project_id=project_id,
            version_id=format_canon_id("source_version", str(uuid7())),
            source_id=source_id,
            content=content,
            content_hash=content_hash(content),
            actor_user_id=scope.actor_user_id,
        )
        # Sizes only: an author's prose never reaches a log.
        log_event(logger, "source.added", source_id=source_id, characters=len(content))
        await self._reindex(workspace_id, project_id, source_id)
        return await self._source(workspace_id, project_id, source_id)

    async def revise(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        source_id: str,
        *,
        content: str | None = None,
        title: str | None = None,
    ) -> SourceText:
        """A new version of the words, or a new title. Earlier versions are kept as they were."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        source = await self._source(workspace_id, project_id, source_id)
        if title is not None:
            title = " ".join(title.split())
            if not title or len(title) > MAX_TITLE_LENGTH:
                raise ValidationError("Give the text a title.")
            await self._store.rename(  # type: ignore[attr-defined]
                workspace_id=workspace_id, project_id=project_id, source_id=source_id, title=title
            )
        if content is not None:
            if source.kind is SourceKind.SCENE:
                # A scene's words may be shared with a what-if; the studio copies before changing.
                raise ValidationError("This is a scene's text. Change it in the scene's studio.")
            content = self._checked_content(content)
            versions = await self._store.list_versions(  # type: ignore[attr-defined]
                workspace_id=workspace_id, project_id=project_id, source_id=source_id
            )
            if len(versions) >= MAX_VERSIONS_PER_SOURCE:
                raise ValidationError("This text has too many versions. Bring it in as a new text.")
            digest = content_hash(content)
            if not versions or versions[-1].content_hash != digest:
                await self._store.add_version(  # type: ignore[attr-defined]
                    workspace_id=workspace_id,
                    project_id=project_id,
                    version_id=format_canon_id("source_version", str(uuid7())),
                    source_id=source.id,
                    content=content,
                    content_hash=digest,
                    actor_user_id=scope.actor_user_id,
                )
                await self._reindex(workspace_id, project_id, source_id)
        return await self._source(workspace_id, project_id, source_id)

    # -------------------------------------------------- for the scene studio (ADR-022)
    async def _room_for_another(self, workspace_id: str, project_id: str) -> None:
        existing = await self._store.list_sources(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id
        )
        if len(existing) >= MAX_SOURCES_PER_PROJECT:
            raise ValidationError("This story has as many texts as it can hold. Remove one first.")

    async def copy_text(
        self,
        workspace_id: str,
        project_id: str,
        source_id: str,
        *,
        actor_user_id: str | None,
    ) -> SourceText:
        """A new text with every version of another, each remembering where its words came from."""
        source, _, versions = await self.version(workspace_id, project_id, source_id)
        await self._room_for_another(workspace_id, project_id)
        copy_id = format_canon_id("source_text", str(uuid7()))
        await self._store.add_source(  # type: ignore[attr-defined]
            workspace_id=workspace_id,
            project_id=project_id,
            source_id=copy_id,
            title=source.title,
            kind=source.kind,
            actor_user_id=actor_user_id,
        )
        previous: str | None = None
        for version in versions:
            added = await self._store.add_version(  # type: ignore[attr-defined]
                workspace_id=workspace_id,
                project_id=project_id,
                version_id=format_canon_id("source_version", str(uuid7())),
                source_id=copy_id,
                content=version.content,
                content_hash=version.content_hash,
                actor_user_id=version.created_by,
                origin=version.origin,
                parent_version_id=previous,
                draft_id=version.draft_id,
                manifest_id=version.manifest_id,
            )
            previous = added.id
        await self._reindex(workspace_id, project_id, copy_id)
        return await self._source(workspace_id, project_id, copy_id)

    async def create_text(
        self,
        workspace_id: str,
        project_id: str,
        *,
        title: str,
        kind: SourceKind,
        content: str,
        actor_user_id: str | None,
        origin: VersionOrigin = VersionOrigin.AUTHOR,
        draft_id: str | None = None,
        manifest_id: str | None = None,
    ) -> SourceText:
        """A new text with its first version. Callers have already authorized the author."""
        title = " ".join(title.split())[:MAX_TITLE_LENGTH] or "Untitled"
        content = self._checked_content(content)
        await self._room_for_another(workspace_id, project_id)
        source_id = format_canon_id("source_text", str(uuid7()))
        await self._store.add_source(  # type: ignore[attr-defined]
            workspace_id=workspace_id,
            project_id=project_id,
            source_id=source_id,
            title=title,
            kind=kind,
            actor_user_id=actor_user_id,
        )
        await self._store.add_version(  # type: ignore[attr-defined]
            workspace_id=workspace_id,
            project_id=project_id,
            version_id=format_canon_id("source_version", str(uuid7())),
            source_id=source_id,
            content=content,
            content_hash=content_hash(content),
            actor_user_id=actor_user_id,
            origin=origin,
            draft_id=draft_id,
            manifest_id=manifest_id,
        )
        await self._reindex(workspace_id, project_id, source_id)
        return await self._source(workspace_id, project_id, source_id)

    async def write_version(
        self,
        workspace_id: str,
        project_id: str,
        source_id: str,
        *,
        content: str,
        actor_user_id: str | None,
        origin: VersionOrigin = VersionOrigin.AUTHOR,
        parent_version_id: str | None = None,
        draft_id: str | None = None,
        manifest_id: str | None = None,
    ) -> SourceVersion:
        """A new version with where it came from. Callers have already authorized the author."""
        content = self._checked_content(content)
        versions = await self._store.list_versions(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id, source_id=source_id
        )
        if len(versions) >= MAX_VERSIONS_PER_SOURCE:
            raise ValidationError("This scene has too many versions to keep another.")
        version = await self._store.add_version(  # type: ignore[attr-defined]
            workspace_id=workspace_id,
            project_id=project_id,
            version_id=format_canon_id("source_version", str(uuid7())),
            source_id=source_id,
            content=content,
            content_hash=content_hash(content),
            actor_user_id=actor_user_id,
            origin=origin,
            parent_version_id=parent_version_id,
            draft_id=draft_id,
            manifest_id=manifest_id,
        )
        await self._reindex(workspace_id, project_id, source_id)
        return version  # type: ignore[no-any-return]

    async def remove(
        self, scope: AccessScope, workspace_id: str, project_id: str, source_id: str
    ) -> None:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        source = await self._source(workspace_id, project_id, source_id)
        if source.kind is SourceKind.SCENE:
            raise ValidationError("This is a scene's text. It belongs to the scene in the plan.")
        await self._store.remove_source(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id, source_id=source_id
        )
        log_event(logger, "source.removed", source_id=source_id)
        await self._reindex(workspace_id, project_id, source_id, gone=True)

    # ------------------------------------------------------------- reading
    async def line_names(self, workspace_id: str, project_id: str) -> LineNames | None:
        """Names read text by text in the line being worked in; None when no answer is
        limited to a what-if, scenes or a viewpoint (then the shared index is exact)."""
        bindings = await self._store.list_bindings(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id
        )
        if not _limited(bindings):
            return None
        entities = await self._all_entities(workspace_id, project_id)
        scenes = reading_order(
            await self._reader.list_plan_items(workspace_id=workspace_id, project_id=project_id)
        )
        links: dict[str, str] = {}
        if self.studio is not None:
            links = await self.studio.scene_texts(workspace_id, project_id)  # type: ignore[attr-defined]
        return LineNames(
            entities=entities,
            bindings=bindings,
            branch_id=working_branch_id(),
            order={scene.id: number for number, scene in enumerate(scenes, start=1)},
            scene_of=links,
            viewpoints={scene.id: tuple(scene.refs_as("point_of_view")) for scene in scenes},
        )

    async def place_for(
        self,
        workspace_id: str,
        project_id: str,
        *,
        source_id: str | None = None,
        scene_id: str | None = None,
    ) -> NamePlace:
        """Where a phrase is read: the line of the story, and the scene a text holds."""
        if scene_id is None and source_id and self.studio is not None:
            links = await self.studio.scene_texts(workspace_id, project_id)  # type: ignore[attr-defined]
            scene_id = links.get(source_id)
        items = await self._reader.list_plan_items(workspace_id=workspace_id, project_id=project_id)
        scenes = reading_order(items)
        order = {scene.id: number for number, scene in enumerate(scenes, start=1)}
        scene = next((item for item in scenes if item.id == scene_id), None)
        return NamePlace(
            source_id=source_id or None,
            branch_id=working_branch_id(),
            scene_id=scene_id if scene is not None else None,
            viewpoint_ids=tuple(scene.refs_as("point_of_view")) if scene is not None else (),
            order=order,
        )

    async def _index(
        self,
        workspace_id: str,
        project_id: str,
        source_id: str | None,
        *,
        scene_id: str | None = None,
        own_line: bool = False,
    ) -> tuple[NameIndex, list[CanonEntity], list[AliasBinding], NamePlace]:
        entities = await self._all_entities(workspace_id, project_id)
        bindings = await self._store.list_bindings(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id
        )
        place = NamePlace(source_id=source_id or None, branch_id=working_branch_id())
        # Only an answer limited to some scenes needs to know which scene this is.
        if any(item.from_scene_id or item.until_scene_id or item.viewpoint_id for item in bindings):
            if own_line:
                place = await self._own_line(workspace_id, project_id, source_id or "")
            else:
                place = await self.place_for(
                    workspace_id, project_id, source_id=source_id, scene_id=scene_id
                )
        elif own_line:
            place = await self._own_line(workspace_id, project_id, source_id or "", scenes=False)
        return build_index(entities, bindings, place=place), entities, bindings, place

    async def _own_line(
        self, workspace_id: str, project_id: str, source_id: str, *, scenes: bool = True
    ) -> NamePlace:
        """The line of the story a text belongs to, for indexes every line shares.

        A text the main story uses is read as the main story reads it; a scene's text
        that only a what-if holds (it was changed there) is read as that what-if does.
        """
        if not scenes:
            if self.studio is None or working_branch_id() is None:
                return NamePlace(source_id=source_id)
            with working_in(None):
                main = await self.studio.scene_texts(workspace_id, project_id)  # type: ignore[attr-defined]
            if source_id in main:
                return NamePlace(source_id=source_id)
            mine = await self.studio.scene_texts(workspace_id, project_id)  # type: ignore[attr-defined]
            if source_id in mine:
                return NamePlace(source_id=source_id, branch_id=working_branch_id())
            return NamePlace(source_id=source_id)
        with working_in(None):
            main = await self.place_for(workspace_id, project_id, source_id=source_id)
        if main.scene_id is not None or working_branch_id() is None:
            return main
        mine = await self.place_for(workspace_id, project_id, source_id=source_id)
        return mine if mine.scene_id is not None else main

    async def index_for(
        self,
        workspace_id: str,
        project_id: str,
        source_id: str | None,
        *,
        scene_id: str | None = None,
        own_line: bool = False,
    ) -> tuple[NameIndex, list[CanonEntity], list[AliasBinding]]:
        """Who each phrase means where it is read: in this text, scene and line of the story.

        ``own_line`` reads a text as the line of the story it belongs to, for the passage
        index that every line shares.
        """
        index, entities, bindings, _ = await self._index(
            workspace_id, project_id, source_id, scene_id=scene_id, own_line=own_line
        )
        return index, entities, bindings

    async def version(
        self,
        workspace_id: str,
        project_id: str,
        source_id: str,
        version_id: str | None = None,
    ) -> tuple[SourceText, SourceVersion, list[SourceVersion]]:
        source = await self._source(workspace_id, project_id, source_id)
        versions = await self._store.list_versions(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id, source_id=source_id
        )
        if not versions:
            raise NotFoundError("That text was not found.")
        chosen = versions[-1]
        if version_id is not None:
            matches = [item for item in versions if item.id == version_id]
            if not matches:
                raise NotFoundError("That version of the text was not found.")
            chosen = matches[0]
        return source, chosen, versions

    async def latest_version(
        self, workspace_id: str, project_id: str, source: SourceText
    ) -> SourceVersion | None:
        """Only a text's newest version, read by the id its listing already carries."""
        if source.latest_version_id is None:
            return None
        return await self._store.get_version(  # type: ignore[attr-defined,no-any-return]
            workspace_id=workspace_id, project_id=project_id, version_id=source.latest_version_id
        )

    async def read(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        source_id: str,
        *,
        version_id: str | None = None,
    ) -> SourceReading:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        source, chosen, versions = await self.version(
            workspace_id, project_id, source_id, version_id
        )
        index, entities, bindings, place = await self._index(workspace_id, project_id, source_id)
        mentions = find_mentions(chosen.content, index)
        scoped = applicable(bindings, place)
        questions = name_questions(mentions, scoped, content=chosen.content, index=index)
        wanted = {entity_id for mention in mentions for entity_id in mention.entity_ids}
        wanted.update(candidate for question in questions for candidate in question.candidates)
        names = {entity.id: entity.name for entity in entities if entity.id in wanted}
        return SourceReading(
            source=source,
            version=chosen,
            versions=versions,
            sections=split_sections(chosen.content),
            mentions=mentions,
            questions=questions,
            names=names,
        )

    async def evidence(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        version_id: str,
        location: str | None,
    ) -> EvidenceReading:
        """The passage behind a change. A removed text still says which text it was."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        version = await self._store.get_version(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id, version_id=version_id
        )
        if version is None:
            # The words are gone, or this was never a text. Say so without guessing.
            raise NotFoundError("The text this came from is no longer kept.")
        source = await self._store.get_source(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id, source_id=version.source_id
        )
        span = parse_location(location)
        return EvidenceReading(
            source_id=version.source_id,
            title=source.title if source is not None else "",
            version_number=version.number,
            available=True,
            passage=excerpt(version.content, span[0], span[1]) if span is not None else None,
        )

    # ---------------------------------------------------------- name memory
    async def bindings(self, scope: AccessScope, workspace_id: str, project_id: str) -> NameMemory:
        """Everything remembered, with each example checked against today's answers."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        everything = await self._store.list_bindings(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id
        )
        bindings = [item for item in everything if item.verdict is not Verdict.SUGGESTED]
        entities = await self._all_entities(workspace_id, project_id)
        wanted = {item.entity_id for item in bindings} | {item.viewpoint_id for item in bindings}
        names = {entity.id: entity.name for entity in entities if entity.id in wanted}

        scene_ids = {
            value
            for item in bindings
            for value in (
                item.from_scene_id,
                item.until_scene_id,
                *(example.scene_id for example in item.examples),
            )
            if value
        }
        scenes: dict[str, str] = {}
        if scene_ids:
            for plan in await self._reader.list_plan_items(
                workspace_id=workspace_id, project_id=project_id
            ):
                if plan.id in scene_ids and plan.kind is PlanKind.SCENE and plan.is_active:
                    scenes[plan.id] = plan.title

        checks: dict[str, ExampleCheck] = {}
        places: dict[tuple[str | None, ...], NamePlace] = {}
        for binding in bindings:
            for example in binding.examples:
                place = await self._example_place(
                    workspace_id, project_id, binding, example, places
                )
                index = build_index(entities, everything, place=place)
                holds, now = example_holds(binding, example, index)
                checks[example.id] = ExampleCheck(holds=holds, now=now)
                names.update({entity.id: entity.name for entity in entities if entity.id in now})
        return NameMemory(bindings=bindings, names=names, scenes=scenes, checks=checks)

    async def _example_place(
        self,
        workspace_id: str,
        project_id: str,
        binding: AliasBinding,
        example: NameExample,
        cache: dict[tuple[str | None, ...], NamePlace],
    ) -> NamePlace:
        """Where an example was read; a typed one with no scene is read within its answer's own
        limits, which checks that no narrower answer has since taken over there."""
        line = binding.branch_id if binding.branch_id is not None else working_branch_id()
        if example.source_id is None and example.scene_id is None:
            within = binding.from_scene_id or binding.until_scene_id
            key: tuple[str | None, ...] = ("own", binding.id)
            scene_id = within
            source_id = binding.source_id
        else:
            key = ("at", line, example.source_id, example.scene_id)
            scene_id = example.scene_id
            source_id = example.source_id
        if key not in cache:
            with working_in(line):
                place = await self.place_for(
                    workspace_id, project_id, source_id=source_id, scene_id=scene_id
                )
            if key[0] == "own":
                # Read as if inside every limit the answer sets.
                place = replace(
                    place,
                    source_id=binding.source_id,
                    viewpoint_ids=(binding.viewpoint_id,)
                    if binding.viewpoint_id
                    else place.viewpoint_ids,
                )
            cache[key] = place
        return cache[key]

    async def _scoped(
        self, workspace_id: str, project_id: str, where: NameScope
    ) -> dict[str, str | None]:
        """Check each limit of a new answer and turn it into what is stored."""
        working = working_branch_id()
        if where.what_if_only and working is None:
            raise ValidationError(
                "You are working in the main story, so there is no what-if to keep this to."
            )
        branch_id = working if where.what_if_only in (None, True) else None
        if where.from_scene_id or where.until_scene_id:
            items = await self._reader.list_plan_items(
                workspace_id=workspace_id, project_id=project_id
            )
            order = {scene.id: number for number, scene in enumerate(reading_order(items), start=1)}
            for scene_id in (where.from_scene_id, where.until_scene_id):
                if scene_id and scene_id not in order:
                    raise NotFoundError("That scene is not in the plan.")
            if (
                where.from_scene_id
                and where.until_scene_id
                and order[where.from_scene_id] > order[where.until_scene_id]
            ):
                raise ValidationError("The first scene comes after the last one. Swap them.")
        if where.viewpoint_id:
            viewer = await self._reader.get_entity(
                workspace_id=workspace_id, project_id=project_id, entity_id=where.viewpoint_id
            )
            if viewer is None or not viewer.is_active:
                raise NotFoundError("That character was not found.")
        return {
            "branch_id": branch_id,
            "from_scene_id": where.from_scene_id or None,
            "until_scene_id": where.until_scene_id or None,
            "viewpoint_id": where.viewpoint_id or None,
        }

    async def decide(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        phrase: str,
        verdict: Verdict,
        entity_id: str | None = None,
        source_id: str | None = None,
        where: NameScope | None = None,
        example: ExampleRequest | None = None,
    ) -> AliasBinding:
        """Remember an answer: this phrase is that entry, is not, or is not a name."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if verdict is Verdict.SUGGESTED:
            raise ValidationError("Choose who this is, or say it is not a name.")
        where = where or NameScope()
        limits = await self._scoped(workspace_id, project_id, where)
        binding = await self.remember(
            workspace_id,
            project_id,
            phrase=phrase,
            verdict=verdict,
            entity_id=entity_id,
            source_id=source_id if source_id is not None else where.source_id,
            actor_user_id=scope.actor_user_id,
            example=example,
            **limits,
        )
        await self._reindex_phrase(workspace_id, project_id, binding.normalized)
        return binding

    async def remember(
        self,
        workspace_id: str,
        project_id: str,
        *,
        phrase: str,
        verdict: Verdict,
        entity_id: str | None,
        source_id: str | None,
        actor_user_id: str | None,
        branch_id: str | None = None,
        from_scene_id: str | None = None,
        until_scene_id: str | None = None,
        viewpoint_id: str | None = None,
        example: ExampleRequest | None = None,
    ) -> AliasBinding:
        phrase = " ".join(phrase.split())
        normalized = normalize_name(phrase)
        if not normalized or len(phrase) > MAX_PHRASE_LENGTH:
            raise ValidationError("That is not a phrase that can be remembered.")
        if verdict is Verdict.IGNORE:
            entity_id = None
        else:
            if entity_id is None:
                raise ValidationError("Choose the entry this means.")
            entity = await self._reader.get_entity(
                workspace_id=workspace_id, project_id=project_id, entity_id=entity_id
            )
            if entity is None or not entity.is_active:
                raise NotFoundError("That entry was not found.")
        if source_id is not None:
            await self._source(workspace_id, project_id, source_id)
        binding = AliasBinding(
            workspace_id=workspace_id,
            project_id=project_id,
            id=format_canon_id("alias_binding", str(uuid7())),
            phrase=phrase,
            normalized=normalized,
            verdict=verdict,
            entity_id=entity_id,
            source_id=source_id,
            decided_by=actor_user_id,
            decided_at=datetime.now(UTC),
            branch_id=branch_id,
            from_scene_id=from_scene_id,
            until_scene_id=until_scene_id,
            viewpoint_id=viewpoint_id,
        )
        # The same answer given again keeps the examples confirmed for it before.
        kept: tuple[NameExample, ...] = ()
        if verdict is not Verdict.SUGGESTED:
            for earlier in await self._store.list_bindings(  # type: ignore[attr-defined]
                workspace_id=workspace_id, project_id=project_id
            ):
                if (
                    earlier.scope_key == binding.scope_key
                    and earlier.entity_id == entity_id
                    and earlier.verdict is verdict
                ):
                    kept = earlier.examples
        if example is not None:
            made = await self._example(
                workspace_id, project_id, binding, example, actor_user_id=actor_user_id
            )
            kept = _with_example(kept, made)
        binding = replace(binding, examples=kept)
        await self._store.put_binding(binding)  # type: ignore[attr-defined]
        return binding

    async def _example(
        self,
        workspace_id: str,
        project_id: str,
        binding: AliasBinding,
        request: ExampleRequest,
        *,
        actor_user_id: str | None,
    ) -> NameExample:
        """A confirmed passage, with its words taken from the text or checked to use the phrase."""
        means = request.means
        if means is None:
            means = binding.verdict is Verdict.IS
        if binding.verdict is Verdict.IGNORE:
            means = False
        scene_id = request.scene_id or None
        if request.source_id is not None:
            if request.start is None or request.end is None:
                raise ValidationError("Say where in the text the example is.")
            _, version, _ = await self.version(
                workspace_id, project_id, request.source_id, request.version_id
            )
            start, end = request.start, request.end
            if not 0 <= start < end <= len(version.content):
                raise ValidationError("That place is not in the text.")
            if binding.normalized not in normalize_name(version.content[start:end]):
                raise ValidationError(f"Those words do not use “{binding.phrase}”.")
            quote, at = _quote_around(version.content, start, end)
            if scene_id is None and self.studio is not None:
                links = await self.studio.scene_texts(workspace_id, project_id)  # type: ignore[attr-defined]
                scene_id = links.get(request.source_id)
            source_id: str | None = request.source_id
            version_id: str | None = version.id
        else:
            quote = " ".join((request.quote or "").split())
            if not quote:
                raise ValidationError("Write the passage, or pick it in a text.")
            if len(quote) > MAX_EXAMPLE_LENGTH:
                raise ValidationError(
                    f"Keep an example under {MAX_EXAMPLE_LENGTH} characters; "
                    "a sentence or two is enough."
                )
            found = normalize_name(quote).find(binding.normalized)
            if found == -1:
                raise ValidationError(f"The example has to use “{binding.phrase}”.")
            located = _locate(quote, binding.phrase)
            at = located if located is not None else (0, 0)
            source_id = version_id = None
        if scene_id is not None:
            items = await self._reader.list_plan_items(
                workspace_id=workspace_id, project_id=project_id
            )
            if not any(
                item.id == scene_id and item.kind is PlanKind.SCENE and item.is_active
                for item in items
            ):
                raise NotFoundError("That scene is not in the plan.")
        return NameExample(
            id=f"nex_{uuid7()}",
            quote=quote,
            means=means,
            source_id=source_id,
            version_id=version_id,
            scene_id=scene_id,
            at=at if at != (0, 0) else None,
            noted_by=actor_user_id,
            noted_at=datetime.now(UTC),
        )

    async def _binding(self, workspace_id: str, project_id: str, binding_id: str) -> AliasBinding:
        for item in await self._store.list_bindings(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id
        ):
            if item.id == binding_id and item.verdict is not Verdict.SUGGESTED:
                return item  # type: ignore[no-any-return]
        raise NotFoundError("That answer was not found.")

    async def add_example(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        binding_id: str,
        request: ExampleRequest,
    ) -> AliasBinding:
        """Keep a passage the author confirms with an answer."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        binding = await self._binding(workspace_id, project_id, binding_id)
        made = await self._example(
            workspace_id, project_id, binding, request, actor_user_id=scope.actor_user_id
        )
        changed = replace(binding, examples=_with_example(binding.examples, made))
        if not await self._store.set_examples(changed):  # type: ignore[attr-defined]
            raise NotFoundError("That answer was not found.")
        return changed

    async def remove_example(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        binding_id: str,
        example_id: str,
    ) -> AliasBinding:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        binding = await self._binding(workspace_id, project_id, binding_id)
        left = tuple(item for item in binding.examples if item.id != example_id)
        if len(left) == len(binding.examples):
            raise NotFoundError("That example was not found.")
        changed = replace(binding, examples=left)
        if not await self._store.set_examples(changed):  # type: ignore[attr-defined]
            raise NotFoundError("That answer was not found.")
        return changed

    async def forget(
        self, scope: AccessScope, workspace_id: str, project_id: str, binding_id: str
    ) -> None:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        known = await self._store.list_bindings(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id
        )
        removed = await self._store.delete_binding(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id, binding_id=binding_id
        )
        if not removed:
            raise NotFoundError("That was not found.")
        gone = next((item for item in known if item.id == binding_id), None)
        if gone is not None:
            await self._reindex_phrase(workspace_id, project_id, gone.normalized)

    async def _reindex_phrase(self, workspace_id: str, project_id: str, normalized: str) -> None:
        """An answer changed: the passage index is read again for the texts that use the phrase,
        so the scene context's passages follow the author's answer."""
        if self.passages is None or not normalized:
            return
        for text in await self.all_texts(workspace_id, project_id):
            if text.removed_at is not None or text.length == 0:
                continue
            try:
                _, version, _ = await self.version(workspace_id, project_id, text.id)
            except NotFoundError:
                continue
            if normalized in normalize_name(version.content):
                await self._reindex(workspace_id, project_id, text.id)

    async def add_entry(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        source_id: str,
        *,
        phrase: str,
        entity_type: str,
        expected_revision: int,
    ) -> CanonEntity:
        """A name in the text that is not in the story bible yet becomes an entry.

        The entry's history points back at the first place the text uses the name.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        _, chosen, _ = await self.version(workspace_id, project_id, source_id)
        phrase = " ".join(phrase.split())
        if not phrase or len(phrase) > MAX_PHRASE_LENGTH:
            raise ValidationError("That is not a name that can be added.")
        index, _, _ = await self.index_for(workspace_id, project_id, source_id)
        wanted = normalize_name(phrase)
        first = next(
            (
                mention
                for mention in find_mentions(chosen.content, index)
                if normalize_name(mention.phrase) == wanted
            ),
            None,
        )
        evidence = [
            EvidenceRef(
                source_artefact_id=chosen.id,
                location=location_for(first.start, first.end) if first is not None else None,
            )
        ]
        _, entity = await self._entities.create(
            scope,
            workspace_id,
            project_id,
            entity_type=entity_type,
            name=phrase,
            attributes={"_needs_detail": True},
            expected_revision=expected_revision,
            evidence=evidence,
        )
        return entity


def _with_example(examples: tuple[NameExample, ...], new: NameExample) -> tuple[NameExample, ...]:
    """Add an example; the same words again replace the earlier one; the oldest go first."""
    same = normalize_name(new.quote)
    kept = tuple(item for item in examples if normalize_name(item.quote) != same)
    return (*kept, new)[-MAX_EXAMPLES:]


def _locate(quote: str, phrase: str) -> tuple[int, int] | None:
    pattern = re.escape(" ".join(phrase.split())).replace(r"\ ", r"\s+")
    match = re.search(pattern, quote, re.IGNORECASE)
    return (match.start(), match.end()) if match is not None else None
