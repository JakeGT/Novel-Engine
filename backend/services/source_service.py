"""Texts an author brings in: keep them, find the names in them, remember who is who.

Nothing here calls a model. Pasting a text, seeing which entries it names,
answering "is 'the Fox' Mara?", and adding a marked name as a new entry all work
with no provider connected. A text is evidence, not canon: only a change the
author keeps makes anything true in the story.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.domain.canon.entities import CanonEntity
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.naming import normalize_name
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.operations import EvidenceRef
from backend.domain.sources import (
    MAX_PHRASE_LENGTH,
    MAX_SOURCE_LENGTH,
    MAX_SOURCES_PER_PROJECT,
    MAX_TITLE_LENGTH,
    MAX_VERSIONS_PER_SOURCE,
    AliasBinding,
    Excerpt,
    Mention,
    NameIndex,
    NameQuestion,
    Section,
    SourceKind,
    SourceText,
    SourceVersion,
    Verdict,
    build_index,
    clean_content,
    content_hash,
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
        return await self._source(workspace_id, project_id, source_id)

    async def remove(
        self, scope: AccessScope, workspace_id: str, project_id: str, source_id: str
    ) -> None:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        await self._source(workspace_id, project_id, source_id)
        await self._store.remove_source(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id, source_id=source_id
        )
        log_event(logger, "source.removed", source_id=source_id)

    # ------------------------------------------------------------- reading
    async def index_for(
        self, workspace_id: str, project_id: str, source_id: str
    ) -> tuple[NameIndex, list[CanonEntity], list[AliasBinding]]:
        entities = await self._all_entities(workspace_id, project_id)
        bindings = await self._store.list_bindings(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id
        )
        return build_index(entities, bindings, source_id=source_id), entities, bindings

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
        index, entities, bindings = await self.index_for(workspace_id, project_id, source_id)
        mentions = find_mentions(chosen.content, index)
        scoped = [item for item in bindings if item.source_id in (None, source_id)]
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
    async def bindings(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> tuple[list[AliasBinding], dict[str, str]]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        bindings = [
            item
            for item in await self._store.list_bindings(  # type: ignore[attr-defined]
                workspace_id=workspace_id, project_id=project_id
            )
            if item.verdict is not Verdict.SUGGESTED
        ]
        entities = await self._all_entities(workspace_id, project_id)
        wanted = {item.entity_id for item in bindings}
        return bindings, {entity.id: entity.name for entity in entities if entity.id in wanted}

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
    ) -> AliasBinding:
        """Remember an answer: this phrase is that entry, is not, or is not a name."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if verdict is Verdict.SUGGESTED:
            raise ValidationError("Choose who this is, or say it is not a name.")
        return await self.remember(
            workspace_id,
            project_id,
            phrase=phrase,
            verdict=verdict,
            entity_id=entity_id,
            source_id=source_id,
            actor_user_id=scope.actor_user_id,
        )

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
        )
        await self._store.put_binding(binding)  # type: ignore[attr-defined]
        return binding

    async def forget(
        self, scope: AccessScope, workspace_id: str, project_id: str, binding_id: str
    ) -> None:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        removed = await self._store.delete_binding(  # type: ignore[attr-defined]
            workspace_id=workspace_id, project_id=project_id, binding_id=binding_id
        )
        if not removed:
            raise NotFoundError("That was not found.")

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
