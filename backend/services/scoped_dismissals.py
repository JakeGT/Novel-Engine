"""Dismissing one kind of finding wider than one scene (CON-016).

The author can say a kind of finding is not a problem wherever it is about one character
("Eye, hair or beard colour, wherever it is about Wren"), or in every scene of a stretch
of the story ("Stock phrasing, from “The Docks” to “The Wait”"). Each dismissal says, in
words, what it applies to; it holds for findings found later too, until it runs out or is
withdrawn, and withdrawing it keeps the record. It belongs to one line of the story and
is forked with a what-if.

Only advisory findings are ever dismissed: the checks of the studio and the assistant's
notes. The checks that guard the story itself are never offered here (ADR-013).

A finding is about a character when what it is about (its subject, never the rest of
what it says) names them, by their name or any alias (the story bible's own words, never
a guess); an assistant's note has no subject, so it is about a character when it names
them and no one else. A scene is in a stretch by its place in reading order now, and a
stretch whose first or last scene left the plan, or a dismissal for a character no longer
in the story, no longer applies (it can still be undone). Nothing here changes what any check does: dismissed findings are still
found, kept in the check history, and counted in how often each check is dismissed.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.domain.canon.plans import PlanKind
from backend.domain.check_fingerprints import CODE_WORDS, code_words
from backend.domain.check_passes import PASS_LABELS, CheckPass
from backend.domain.dismissals import (
    MAX_NOTE,
    MAX_SCOPED_DISMISSALS,
    DismissalScope,
    DismissReason,
    FindingDismissal,
    FindingKind,
    ScopedDismissal,
    expiry,
    scope_covers,
)
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.knowledge import reading_order
from backend.domain.tenancy import Permission

logger = logging.getLogger(__name__)

# The kinds of entry a finding can be dismissed for, wherever it is about them.
SCOPED_TYPES = frozenset({"Character"})
# How many characters are read to tell whom a finding names.
MAX_ENTRIES = 2000
# The studio's checks that need no model, by code, beyond those ``CODE_WORDS`` names; a
# continuity finding's code is ``continuity_`` and its own. Only these can be dismissed.
STUDIO_CODES = frozenset(CODE_WORDS) | {
    "after_not_shown",
    "resource_held_elsewhere",
    "resource_used_up",
    "supply_count_differs",
    "supply_overspent",
    "time_words_before",
}
_CONTINUITY_CODE = re.compile(r"^continuity_[a-z][a-z_]{0,60}$")


def is_studio_code(code: str) -> bool:
    """Whether a code is one of the studio's own checks (CON-016)."""
    return code in STUDIO_CODES or bool(_CONTINUITY_CODE.match(code))


@dataclass(frozen=True)
class ScopedView:
    """A wider dismissal, with what it applies to in words and whether it still holds."""

    dismissal: ScopedDismissal
    applies_to: str
    standing: bool
    # Not withdrawn, but it covers nothing now: a scene it names left the plan, or the
    # character left the story. It can still be undone.
    lapsed: bool = False


@dataclass
class Scoping:
    """What the wider dismissals of one line of the story need to tell what they cover:
    the standing ones, each scene's place in reading order and every character's names."""

    standing: list[ScopedView] = field(default_factory=list)
    position: dict[str, int] = field(default_factory=dict)
    names: dict[str, tuple[str, ...]] = field(default_factory=dict)
    _pattern: re.Pattern[str] | None = field(default=None, init=False, repr=False)
    _owners: dict[str, set[str]] = field(default_factory=dict, init=False, repr=False)

    def named_in(self, words: str) -> frozenset[str]:
        """Every character (entry id) some words name, by any of their names, as whole
        words. One pass over the words for all the story's names."""
        if self._pattern is None:
            for entity_id, names in self.names.items():
                for name in names:
                    folded = " ".join(name.split()).casefold()
                    if folded:
                        self._owners.setdefault(folded, set()).add(entity_id)
            if not self._owners:
                return frozenset()
            ordered = sorted(self._owners, key=len, reverse=True)
            self._pattern = re.compile(
                r"(?<!\w)(?:" + "|".join(re.escape(name) for name in ordered) + r")(?!\w)"
            )
        folded = " ".join(unicodedata.normalize("NFC", words).split()).casefold()
        found: set[str] = set()
        for match in self._pattern.finditer(folded):
            found |= self._owners.get(match.group(0), set())
        return frozenset(found)

    def about_ids(self, kind: FindingKind, words: str) -> frozenset[str]:
        """Whom a finding is about: the characters its subject names (a check), or the one
        character a note names when it names no one else (a note has no subject)."""
        named = self.named_in(words)
        if kind is FindingKind.NOTE and len(named) != 1:
            return frozenset()
        return named

    def covering(
        self, plan_id: str, kind: FindingKind, code: str, words: str
    ) -> FindingDismissal | None:
        """The wider dismissal that covers this finding in this scene, as a dismissal of it.
        ``words`` are what it is about: ``finding_words`` of a check, a note's message."""
        about: frozenset[str] | None = None
        for view in self.standing:
            item = view.dismissal
            if item.kind is not kind or item.code != code:
                continue
            if item.scope is DismissalScope.CHARACTER and about is None:
                about = self.about_ids(kind, words)
            span: tuple[int, int] | None = None
            if item.scope is DismissalScope.RANGE:
                first = self.position.get(item.from_plan_id or "")
                last = self.position.get(item.to_plan_id or "")
                if first is not None and last is not None:
                    span = (first, last)
            if scope_covers(
                item,
                kind=kind,
                code=code,
                about=about or frozenset(),
                position=self.position.get(plan_id),
                span=span,
            ):
                return FindingDismissal(
                    workspace_id=item.workspace_id,
                    project_id=item.project_id,
                    branch_id=item.branch_id,
                    id=item.id,
                    plan_id=plan_id,
                    fingerprint="",
                    kind=kind,
                    code=code,
                    reason=item.reason,
                    note=item.note,
                    expires_at=item.expires_at,
                    created_by=item.created_by,
                    created_at=item.created_at,
                    scope_id=item.id,
                    applies_to=view.applies_to,
                )
        return None

    def about(
        self, words: str, people: Sequence[str], kind: FindingKind = FindingKind.CHECK
    ) -> list[tuple[str, str]]:
        """The characters among ``people`` (entry ids) a finding is about, as
        ``about_ids`` tells, each with their name."""
        named = self.about_ids(kind, words)
        return [
            (entity_id, self.names[entity_id][0])
            for entity_id in people
            if entity_id in named and self.names.get(entity_id)
        ]


def what_words(kind: FindingKind, code: str) -> str:
    """The kind of finding, in the author's words."""
    if kind is FindingKind.NOTE:
        try:
            return f"The assistant's notes on {PASS_LABELS[CheckPass(code)].lower()}"
        except ValueError:
            return "The assistant's notes"
    return code_words(code)


class ScopedDismissalService:
    def __init__(self, *, store: Any, studio: Any) -> None:
        self._store = store
        self._studio = studio

    async def _line(self, workspace_id: str, project_id: str) -> str:
        return str(await self._studio._branch_id(workspace_id, project_id))

    async def _plan(self, workspace_id: str, project_id: str) -> tuple[list[Any], dict[str, Any]]:
        items = await self._studio._reader.list_plan_items(
            workspace_id=workspace_id, project_id=project_id
        )
        scenes = [item for item in reading_order(items) if item.kind is PlanKind.SCENE]
        return scenes, {item.id: item for item in scenes}

    async def _names(self, workspace_id: str, project_id: str) -> dict[str, tuple[str, ...]]:
        found: dict[str, tuple[str, ...]] = {}
        for entity_type in sorted(SCOPED_TYPES):
            entities = await self._studio._reader.list_entities(
                workspace_id=workspace_id,
                project_id=project_id,
                entity_type=entity_type,
                limit=MAX_ENTRIES,
            )
            for entity in entities:
                found[entity.id] = tuple(
                    dict.fromkeys([entity.name, *getattr(entity, "aliases", [])])
                )
        return found

    def _applies_to(
        self,
        item: ScopedDismissal,
        names: dict[str, tuple[str, ...]],
        titles: dict[str, str],
    ) -> str:
        what = what_words(item.kind, item.code)
        if item.scope is DismissalScope.CHARACTER:
            who = (names.get(item.entity_id or "") or ("someone no longer in the story",))[0]
            return f"{what}, wherever it is about {who}"
        first = titles.get(item.from_plan_id or "", "a scene no longer in the plan")
        last = titles.get(item.to_plan_id or "", "a scene no longer in the plan")
        if item.from_plan_id == item.to_plan_id:
            return f"{what}, in “{first}”"
        return f"{what}, in every scene from “{first}” to “{last}”"

    async def _views(
        self, workspace_id: str, project_id: str, rows: Sequence[ScopedDismissal]
    ) -> tuple[list[ScopedView], dict[str, int], dict[str, tuple[str, ...]]]:
        scenes, _ = await self._plan(workspace_id, project_id)
        names = await self._names(workspace_id, project_id)
        titles = {scene.id: scene.title for scene in scenes}
        now = datetime.now(UTC)
        views = []
        for item in rows:
            lapsed = item.applies(now) and _lapsed(item, names, titles)
            applies_to = self._applies_to(item, names, titles)
            if lapsed:
                applies_to += (
                    ". It no longer applies: the character is no longer in the story"
                    if item.scope is DismissalScope.CHARACTER
                    else ". It no longer applies: a scene it names is no longer in the plan"
                )
            views.append(ScopedView(item, applies_to, item.applies(now) and not lapsed, lapsed))
        return views, {scene.id: index for index, scene in enumerate(scenes)}, names

    async def scoping(self, workspace_id: str, project_id: str) -> Scoping:
        """Everything needed to tell which findings the standing wider dismissals cover."""
        branch_id = await self._line(workspace_id, project_id)
        rows = await self._store.list(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
        )
        now = datetime.now(UTC)
        standing = [item for item in rows if item.applies(now)]
        views, position, names = await self._views(workspace_id, project_id, standing)
        return Scoping(
            standing=[view for view in views if view.standing], position=position, names=names
        )

    async def list(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> list[ScopedView]:
        """Every wider dismissal in this line of the story, newest first, withdrawn ones too."""
        await self._studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        branch_id = await self._line(workspace_id, project_id)
        rows = await self._store.list(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
        )
        views, _, _ = await self._views(workspace_id, project_id, rows)
        views.sort(key=lambda view: view.dismissal.created_at, reverse=True)
        return views

    async def add(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        kind: FindingKind,
        code: str,
        applies: DismissalScope,
        reason: DismissReason,
        note: str = "",
        days: int | None = None,
        entity_id: str | None = None,
        from_plan_id: str | None = None,
        to_plan_id: str | None = None,
    ) -> ScopedView:
        await self._studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        note = " ".join(note.split())
        if reason is DismissReason.OTHER and not note:
            raise ValidationError("Say in a few words why this is not a problem.")
        if len(note) > MAX_NOTE:
            raise ValidationError(f"Keep the reason under {MAX_NOTE} characters.")
        if not code or len(code) > 120:
            raise ValidationError("Choose a kind of finding to dismiss.")
        if kind is FindingKind.CHECK and not is_studio_code(code):
            raise ValidationError("Choose a kind of finding the studio's checks give.")
        if kind is FindingKind.NOTE:
            try:
                CheckPass(code)
            except ValueError:
                raise ValidationError("Choose a kind of finding to dismiss.") from None
        now = datetime.now(UTC)
        try:
            expires_at = expiry(now, days)
        except ValueError:
            raise ValidationError("Choose how long it stays dismissed.") from None
        if applies is DismissalScope.CHARACTER:
            names = await self._names(workspace_id, project_id)
            if not entity_id or entity_id not in names:
                raise ValidationError("Choose a character in the story bible.")
            from_plan_id = to_plan_id = None
        else:
            _, scenes = await self._plan(workspace_id, project_id)
            if not from_plan_id or not to_plan_id:
                raise ValidationError("Choose the first and the last scene it applies to.")
            if from_plan_id not in scenes or to_plan_id not in scenes:
                raise ValidationError("Choose scenes that are in the plan.")
            entity_id = None
        branch_id = await self._line(workspace_id, project_id)
        if (
            await self._store.count(
                workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
            )
            >= MAX_SCOPED_DISMISSALS
        ):
            raise ValidationError("This story has as many wider dismissals as it can hold.")
        item = ScopedDismissal(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch_id,
            id=str(uuid7()),
            kind=kind,
            code=code,
            scope=applies,
            reason=reason,
            note=note,
            created_at=now,
            entity_id=entity_id,
            from_plan_id=from_plan_id,
            to_plan_id=to_plan_id,
            expires_at=expires_at,
            created_by=scope.actor_user_id,
        )
        await self._store.add(item)
        # The reason the author typed is their writing: only its kind is logged.
        log_event(
            logger,
            "checks.scoped_dismissal",
            kind=kind.value,
            code=code,
            scope=applies.value,
            reason=reason.value,
        )
        views, _, _ = await self._views(workspace_id, project_id, [item])
        return views[0]

    async def has(self, workspace_id: str, project_id: str, dismissal_id: str) -> bool:
        branch_id = await self._line(workspace_id, project_id)
        rows = await self._store.list(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
        )
        return any(item.id == dismissal_id for item in rows)

    async def withdraw(
        self, scope: AccessScope, workspace_id: str, project_id: str, dismissal_id: str
    ) -> ScopedView:
        """Bring the findings back everywhere it applied. The record stays, withdrawn."""
        await self._studio._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        branch_id = await self._line(workspace_id, project_id)
        withdrawn = await self._store.withdraw(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch_id,
            dismissal_id=dismissal_id,
            at=datetime.now(UTC),
        )
        if not withdrawn:
            raise NotFoundError("That was already brought back.")
        rows = await self._store.list(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
        )
        found = [item for item in rows if item.id == dismissal_id]
        views, _, _ = await self._views(workspace_id, project_id, found)
        return views[0]


def _lapsed(
    item: ScopedDismissal, names: dict[str, tuple[str, ...]], titles: dict[str, str]
) -> bool:
    """Whether a wider dismissal covers nothing now: its character, or a scene its
    stretch starts or ends at, is gone."""
    if item.scope is DismissalScope.CHARACTER:
        return (item.entity_id or "") not in names
    return (item.from_plan_id or "") not in titles or (item.to_plan_id or "") not in titles


def finding_words(subject: str) -> str:
    """What a check's finding is about, as the text to look for names in. Only its
    subject: a finding that merely mentions someone in what it says is not about them."""
    return subject.replace(chr(0x1E), " ")


Covering = Callable[[str, FindingKind, str, str], FindingDismissal | None]

__all__ = [
    "SCOPED_TYPES",
    "Covering",
    "ScopedDismissalService",
    "ScopedView",
    "Scoping",
    "finding_words",
    "is_studio_code",
    "what_words",
]
