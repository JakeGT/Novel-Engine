"""What-ifs: try a different turn of the story without touching it (ADR-020).

A what-if is a branch. It starts as a copy of the main story at its current
version; changes made in it are reviewed and saved like any others, but only in
the what-if. It can be compared with the main story, and its changes can be
brought back as one proposal that the author reviews before anything changes.
Records changed in both lines since the what-if started are conflicts; the author
chooses which version to keep for each before the proposal is made.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from backend.adapters.system import uuid7
from backend.domain.branching import working_in
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.phrasing import phrase
from backend.domain.errors import ConflictError, NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.journal import Branch, MutationSet
from backend.domain.operations import CanonOperation, EvidenceRef, parse_operation
from backend.domain.projection import RecordVersion
from backend.domain.tenancy import Permission
from backend.ports import CanonJournalPort, CanonRepositoryPort

MAX_NAME = 120
MAX_DESCRIPTION = 2000
MAX_OPERATIONS = 2000
_PAGE = 500

# What a change to a record did, in the words the comparison uses.
_ADDED = {"create_entity", "assert_fact", "assert_belief", "create_event", "create_plan_item"}
_ENDED = {"close_fact", "close_belief"}
_ARCHIVED = {"retire_entity", "retire_event", "retire_plan_item", "retire_asset_link"}


class ChangeKind(StrEnum):
    ADDED = "added"
    CHANGED = "changed"
    ENDED = "ended"
    ARCHIVED = "archived"


class Keep(StrEnum):
    """For a record changed in both lines: whose version to keep."""

    WHAT_IF = "what_if"
    MAIN = "main"


@dataclass(frozen=True)
class RecordChange:
    record_type: str
    record_id: str
    kind: ChangeKind
    label: str
    # For a fact or belief: its subject, kind of fact and object, so a screen can phrase it.
    parts: dict[str, Any] | None = None
    # For a record changed in both lines: how the main story has it.
    main_label: str | None = None
    main_parts: dict[str, Any] | None = None

    @property
    def key(self) -> str:
        return f"{self.record_type}:{self.record_id}"


@dataclass
class Comparison:
    branch: Branch
    main_head: int
    in_what_if: list[RecordChange] = field(default_factory=list)
    in_main: list[RecordChange] = field(default_factory=list)
    # Records changed on both sides since the what-if started.
    conflicts: list[RecordChange] = field(default_factory=list)


@dataclass(frozen=True)
class WhatIf:
    branch: Branch
    changes: int


class WhatIfService:
    def __init__(
        self,
        *,
        journal: CanonJournalPort,
        canon: CanonRepositoryPort,
        mutation: Any,
        schema_store: Any = None,
        tenancy: Any = None,
        local_mode: bool = False,
    ) -> None:
        self._journal = journal
        self._canon = canon
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

    async def _main(self, workspace_id: str, project_id: str) -> Branch:
        main = await self._journal.ensure_root_branch(
            workspace_id=workspace_id, project_id=project_id
        )
        return await self._current(workspace_id, project_id, main)

    async def _current(self, workspace_id: str, project_id: str, branch: Branch) -> Branch:
        """The branch with its head as the canon store has it now."""
        head = await self._canon.get_head(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch.id
        )
        return branch.model_copy(update={"head_revision": max(head, branch.head_revision)})

    async def _what_if(self, workspace_id: str, project_id: str, branch_id: str) -> Branch:
        branch = await self._journal.get_branch(
            workspace_id=workspace_id, project_id=project_id, branch_id=branch_id
        )
        if branch is None or branch.is_root:
            raise NotFoundError("That what-if was not found.")
        return await self._current(workspace_id, project_id, branch)

    # ---------------------------------------------------------------- listing

    async def what_ifs(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> list[WhatIf]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        await self._main(workspace_id, project_id)
        branches = await self._journal.list_branches(
            workspace_id=workspace_id, project_id=project_id
        )
        result = []
        for branch in branches:
            if branch.is_root:
                continue
            branch = await self._current(workspace_id, project_id, branch)
            changes = branch.head_revision - (branch.fork_revision or 0)
            result.append(WhatIf(branch=branch, changes=changes))
        result.sort(key=lambda item: (item.branch.status != "active", item.branch.created_at))
        return result

    async def get(
        self, scope: AccessScope, workspace_id: str, project_id: str, branch_id: str
    ) -> Branch:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._what_if(workspace_id, project_id, branch_id)

    # --------------------------------------------------------------- starting

    async def start(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        name: str,
        description: str | None = None,
    ) -> Branch:
        """Start a what-if from the main story as it is now."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        cleaned = " ".join(name.split())
        if not cleaned:
            raise ValidationError("Give the what-if a name, such as “What if Mara refused?”.")
        if len(cleaned) > MAX_NAME:
            raise ValidationError(f"Keep the name under {MAX_NAME} characters.")
        if cleaned.lower() == "main":
            raise ValidationError("“main” is the name of the story itself; choose another.")
        if description is not None and len(description) > MAX_DESCRIPTION:
            raise ValidationError(f"Keep the description under {MAX_DESCRIPTION} characters.")
        main = await self._main(workspace_id, project_id)
        existing = await self._journal.list_branches(
            workspace_id=workspace_id, project_id=project_id
        )
        if any(branch.name.lower() == cleaned.lower() for branch in existing):
            raise ConflictError("There is already a what-if with that name.")
        branch_id = format_canon_id("branch", str(uuid7()))
        await self._canon.fork_branch(  # type: ignore[attr-defined]
            workspace_id=workspace_id,
            project_id=project_id,
            parent_branch_id=main.id,
            branch_id=branch_id,
            name=cleaned,
            description=(description or "").strip() or None,
            actor_user_id=scope.actor_user_id,
        )
        return await self._what_if(workspace_id, project_id, branch_id)

    async def set_aside(
        self, scope: AccessScope, workspace_id: str, project_id: str, branch_id: str, aside: bool
    ) -> Branch:
        """Put a what-if away, or take it out again. Nothing in it is lost either way."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        await self._what_if(workspace_id, project_id, branch_id)
        await self._canon.set_branch_status(  # type: ignore[attr-defined]
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch_id,
            status="set_aside" if aside else "active",
        )
        return await self._what_if(workspace_id, project_id, branch_id)

    # -------------------------------------------------------------- comparing

    async def _versions_since(
        self, workspace_id: str, project_id: str, branch: Branch, since: int
    ) -> list[RecordVersion]:
        versions: list[RecordVersion] = []
        upper: int | None = None
        with working_in(None if branch.is_root else branch.id):
            while True:
                page = await self._canon.list_record_versions(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    min_revision=since,
                    max_revision=upper,
                    limit=_PAGE,
                )
                versions.extend(page)
                if len(page) < _PAGE:
                    break
                upper = min(version.revision for version in page) - 1
                if upper <= since:
                    break
        # Oldest first; a page boundary can repeat a revision, so drop duplicates.
        unique = {
            (version.revision, version.record_type, version.record_id, version.ordinal): version
            for version in versions
        }
        return sorted(unique.values(), key=lambda version: (version.revision, version.ordinal))

    async def _labels(
        self, workspace_id: str, project_id: str, branch: Branch
    ) -> tuple[dict[tuple[str, str], str], dict[tuple[str, str], dict[str, Any]]]:
        """Plain names for the records of one line of the story, and each fact's parts."""
        labels: dict[tuple[str, str], str] = {}
        parts: dict[tuple[str, str], dict[str, Any]] = {}
        with working_in(None if branch.is_root else branch.id):
            entities = await self._canon.list_entities(
                workspace_id=workspace_id, project_id=project_id, include_retired=True, limit=10_000
            )
            names = {entity.id: entity.name for entity in entities}
            for entity in entities:
                labels[("entity", entity.id)] = entity.name
            facts = await self._canon.list_facts(
                workspace_id=workspace_id, project_id=project_id, include_closed=True, limit=10_000
            )
            for fact in facts:
                labels[("fact", fact.id)] = _sentence(
                    names, fact.proposition.subject_id, fact.proposition
                )
                parts[("fact", fact.id)] = _parts(fact.proposition)
            beliefs = await self._canon.list_beliefs(
                workspace_id=workspace_id, project_id=project_id, include_closed=True, limit=10_000
            )
            for belief in beliefs:
                holder = names.get(belief.holder_entity_id, "Someone")
                labels[("belief", belief.id)] = f"{holder} believes " + _sentence(
                    names, belief.proposition.subject_id, belief.proposition
                )
            events = await self._canon.list_events(
                workspace_id=workspace_id, project_id=project_id, include_retired=True, limit=10_000
            )
            for event in events:
                labels[("event", event.id)] = event.name
            items = await self._canon.list_plan_items(  # type: ignore[call-arg]
                workspace_id=workspace_id, project_id=project_id, include_retired=True
            )
            for item in items:
                labels[("plan_item", item.id)] = item.title
        return labels, parts

    @staticmethod
    def _summarise(
        versions: list[RecordVersion],
        named: tuple[dict[tuple[str, str], str], dict[tuple[str, str], dict[str, Any]]],
    ) -> dict[str, RecordChange]:
        labels, parts = named
        changes: dict[str, RecordChange] = {}
        for version in versions:
            key = f"{version.record_type}:{version.record_id}"
            earlier = changes.get(key)
            op = version.operation_type
            if op in _ADDED:
                kind = ChangeKind.ADDED
            elif earlier is not None and earlier.kind is ChangeKind.ADDED:
                # Added and then changed on the same side reads as added.
                kind = ChangeKind.ADDED if op not in _ENDED | _ARCHIVED else ChangeKind.ENDED
            elif op in _ENDED:
                kind = ChangeKind.ENDED
            elif op in _ARCHIVED:
                kind = ChangeKind.ARCHIVED
            else:
                kind = ChangeKind.CHANGED
            record = (version.record_type, version.record_id)
            changes[key] = RecordChange(
                version.record_type,
                version.record_id,
                kind,
                labels.get(record) or "A record",
                parts=parts.get(record),
            )
        return changes

    async def compare(
        self, scope: AccessScope, workspace_id: str, project_id: str, branch_id: str
    ) -> Comparison:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        branch = await self._what_if(workspace_id, project_id, branch_id)
        main = await self._main(workspace_id, project_id)
        since = branch.fork_revision or 0
        mine = self._summarise(
            await self._versions_since(workspace_id, project_id, branch, since),
            await self._labels(workspace_id, project_id, branch),
        )
        theirs = self._summarise(
            await self._versions_since(workspace_id, project_id, main, since),
            await self._labels(workspace_id, project_id, main),
        )
        comparison = Comparison(branch=branch, main_head=main.head_revision)
        comparison.in_what_if = sorted(mine.values(), key=lambda change: change.label.lower())
        comparison.in_main = sorted(theirs.values(), key=lambda change: change.label.lower())
        comparison.conflicts = sorted(
            (
                replace(mine[key], main_label=theirs[key].label, main_parts=theirs[key].parts)
                for key in mine.keys() & theirs.keys()
            ),
            key=lambda change: change.label.lower(),
        )
        return comparison

    # ----------------------------------------------------------- bringing back

    async def _operations_since(
        self, workspace_id: str, project_id: str, branch: Branch
    ) -> list[CanonOperation]:
        since = branch.fork_revision or 0
        commits = await self._canon.list_commits(  # type: ignore[attr-defined]
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch.id,
            after_revision=since,
            limit=10_000,
        )
        operations: list[CanonOperation] = []
        for commit in sorted(commits, key=lambda item: item.revision):
            envelope = await self._canon.get_envelope(commit.commit_id)  # type: ignore[attr-defined]
            if envelope is None:
                continue
            operations.extend(parse_operation(item) for item in json.loads(envelope.canonical))
        return operations

    async def bring_back(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        branch_id: str,
        *,
        keep: dict[str, Keep],
    ) -> MutationSet:
        """Propose the what-if's changes for the main story, to be reviewed there.

        ``keep`` says, for each record changed in both lines, whose version wins.
        Nothing changes in the main story until the proposal is approved.
        """
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        comparison = await self.compare(scope, workspace_id, project_id, branch_id)
        undecided = [change.label for change in comparison.conflicts if change.key not in keep]
        if undecided:
            raise ValidationError(
                "Choose which version to keep for each thing changed in both: "
                + ", ".join(undecided[:5])
                + ("…" if len(undecided) > 5 else "")
            )
        keep_main = {key for key, choice in keep.items() if choice is Keep.MAIN}
        branch = comparison.branch
        evidence = EvidenceRef(source_artefact_id=branch.id, location="what_if")
        operations: list[CanonOperation] = []
        for operation in await self._operations_since(workspace_id, project_id, branch):
            if _record_key(operation) in keep_main:
                continue
            if len(operation.evidence) < 10:
                operation = operation.model_copy(
                    update={"evidence": [*operation.evidence, evidence]}
                )
            operations.append(operation)
        if not operations:
            raise ValidationError("Nothing in this what-if would change the story.")
        if len(operations) > MAX_OPERATIONS:
            raise ValidationError("This what-if changes too much to bring back in one go.")
        main = await self._main(workspace_id, project_id)
        schema_version_id = None
        if self._schema_store is not None:
            published = await self._schema_store.get_published(
                workspace_id=workspace_id, project_id=project_id
            )
            schema_version_id = published.id if published else None
        with working_in(None):
            return await self._mutation.create_draft(  # type: ignore[no-any-return]
                scope,
                workspace_id=workspace_id,
                project_id=project_id,
                operations=operations,
                expected_head=main.head_revision,
                branch_id=main.id,
                schema_version_id=schema_version_id,
                idempotency_key=f"bring-back:{branch.id}:{branch.head_revision}:{main.head_revision}",
            )


_RECORD_OF = {
    "create_entity": "entity",
    "update_entity": "entity",
    "retire_entity": "entity",
    "restore_entity": "entity",
    "assert_fact": "fact",
    "close_fact": "fact",
    "amend_fact": "fact",
    "assert_belief": "belief",
    "close_belief": "belief",
    "create_event": "event",
    "amend_event": "event",
    "retire_event": "event",
    "link_asset": "asset_link",
    "retire_asset_link": "asset_link",
    "create_plan_item": "plan_item",
    "update_plan_item": "plan_item",
    "retire_plan_item": "plan_item",
}


def _record_key(operation: CanonOperation) -> str:
    return f"{_RECORD_OF.get(operation.op, operation.op)}:{operation.id}"


def _parts(proposition: Any) -> dict[str, Any]:
    return {
        "subject_id": proposition.subject_id,
        "predicate": proposition.predicate,
        "object": proposition.object.model_dump(mode="json"),
    }


def _sentence(names: dict[str, str], subject_id: str, proposition: Any) -> str:
    subject = names.get(subject_id, "Something")
    verb = phrase(proposition.predicate)
    obj = proposition.object
    target = names.get(getattr(obj, "entity_id", ""), None)
    if target is None:
        value = getattr(obj, "value", None)
        target = str(value) if value is not None else ""
    return f"{subject} {verb} {target}".strip()


__all__ = ["ChangeKind", "Comparison", "Keep", "RecordChange", "WhatIf", "WhatIfService"]
