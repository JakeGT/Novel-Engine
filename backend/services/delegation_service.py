"""Keep the safe ones for me (AST-EXP-015, OSS-007; ADR-014 amendment).

The author may let the assistant's suggestions of a few purely additive kinds be kept
without asking, one kind at a time, after approving that kind by hand often enough. A
kept suggestion goes through the ordinary path: it is approved with a decision that
says "Kept for you under your rule …", then committed, where the commit transaction
checks again that the rule is still on. Switching a rule off therefore stops a
suggestion that is still on its way in, and anything left approved but not yet
committed goes back to the review queue.

Nothing here calls a model. Rules and decisions live in ``delegation_store``; the pure
decision is ``backend.domain.delegation.decide``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.core.logging import log_event
from backend.domain.branching import working_branch_id
from backend.domain.canon.assertions import FactVisibility
from backend.domain.canon.naming import normalize_name
from backend.domain.canon.revisions import OriginKind
from backend.domain.delegation import (
    DAILY_LIMIT,
    HAND_APPROVALS_NEEDED,
    HOW_BY_HAND,
    HOW_KEPT,
    HOW_WAITED,
    KIND_NAMES,
    RULE_OFF_CODE,
    RULE_OFF_MESSAGE,
    VIA_SESSION,
    DelegationKind,
    DelegationRecord,
    DelegationSettings,
    OperationFacts,
    Proposal,
    Refusal,
    RuleState,
    changes_reserved,
    decide,
    has_no_details,
    kept_note,
    structural_kind,
)
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.journal import (
    HeadConflict,
    MutationOperation,
    MutationSet,
    MutationStatus,
    RevisionConflict,
)
from backend.domain.operations import (
    AssertFactOp,
    CanonOperation,
    CreateEntityOp,
    UpdateEntityOp,
    parse_operation,
)
from backend.domain.review import IllegalTransition, OperationDecisionKind
from backend.domain.tenancy import Permission
from backend.domain.validation import CanonValidationError

logger = logging.getLogger(__name__)

WORKSPACE = ""

# What the commit can refuse with; anything else is a real fault and is raised.
_COMMIT_REFUSALS = (CanonValidationError, HeadConflict, RevisionConflict, ValidationError)

# How many records are read at a time when going through every one.
_PAGE = 100
# How many facts are read at a time when looking for a secret.
_FACT_PAGE = 500
# How many entries are compared when looking for one with the same name.
_NAME_MATCHES = 500


@dataclass(frozen=True)
class RuleView:
    kind: str
    name: str
    on: bool
    hand_approvals: int
    since: datetime | None


@dataclass(frozen=True)
class DelegationView:
    rules: tuple[RuleView, ...]
    allow_clients: bool
    workspace_forbidden: bool
    kept_since_seen: int


@dataclass(frozen=True)
class Considered:
    """What happened to one suggestion: kept (and now in the story) or waiting."""

    mutation: MutationSet
    kept: bool
    kinds: tuple[str, ...] = ()
    refusal: Refusal | None = None


@dataclass(frozen=True)
class KeptItem:
    record: DelegationRecord
    operations: tuple[MutationOperation, ...]
    undo_status: str | None


def _effective(review: Any) -> list[MutationOperation]:
    rejected = OperationDecisionKind.REJECTED.value
    return [
        operation
        for operation in sorted(review.operations, key=lambda item: item.ordinal)
        if getattr(review.operation_decisions.get(operation.ordinal), "decision", None) != rejected
    ]


def _refusal_from(error: Exception) -> Refusal:
    if isinstance(error, CanonValidationError):
        codes = [finding.code for finding in error.report.findings]
        if RULE_OFF_CODE in codes:
            return Refusal(RULE_OFF_CODE, RULE_OFF_MESSAGE)
    if isinstance(error, HeadConflict | RevisionConflict):
        return Refusal("stale", "The story changed while it was being kept, so it waits for you.")
    return Refusal("could_not_keep", "It could not be kept as it is, so it waits for you.")


class DelegationService:
    def __init__(
        self,
        store: Any,
        *,
        mutation: Any,
        reader: Any,
        history: Any = None,
        tenancy: Any = None,
        local_mode: bool = False,
        clock: Any = None,
    ) -> None:
        self._store = store
        self._mutation = mutation
        self._reader = reader
        self._history = history
        self._tenancy = tenancy
        self._local_mode = local_mode
        self._clock = clock or (lambda: datetime.now(UTC))

    async def _authorize(
        self, scope: AccessScope, workspace_id: str, project_id: str, permission: Permission
    ) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(scope, workspace_id, project_id, permission)

    @staticmethod
    def _scope(scope: AccessScope, workspace_id: str, project_id: str) -> AccessScope:
        return replace(scope, workspace_id=workspace_id, project_id=project_id)

    # ----- settings -------------------------------------------------------------------

    async def _forbidden(self, workspace_id: str) -> bool:
        workspace = await self._store.get(workspace_id=workspace_id, project_id=WORKSPACE)
        return bool(workspace.forbidden)

    async def view(self, scope: AccessScope, workspace_id: str, project_id: str) -> DelegationView:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        story = await self._store.get(workspace_id=workspace_id, project_id=project_id)
        counts = await self._store.hand_counts(workspace_id=workspace_id, project_id=project_id)
        seen = await self._store.get_seen(
            workspace_id=workspace_id, project_id=project_id, user_id=scope.actor_user_id
        )
        kept_since = await self._store.count_kept_since(
            workspace_id=workspace_id, project_id=project_id, since=seen
        )
        return DelegationView(
            rules=tuple(
                RuleView(
                    kind=kind.value,
                    name=KIND_NAMES[kind.value],
                    on=kind.value in story.rules,
                    hand_approvals=counts.get(kind.value, 0),
                    since=story.rules[kind.value].since if kind.value in story.rules else None,
                )
                for kind in DelegationKind
            ),
            allow_clients=story.allow_clients,
            workspace_forbidden=await self._forbidden(workspace_id),
            kept_since_seen=kept_since,
        )

    async def set_story(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        kinds: list[str],
        allow_clients: bool,
    ) -> DelegationView:
        """Choose which kinds are kept. Switching a kind off takes effect at once."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if allow_clients:
            raise ValidationError(
                "Assistants connected with an access key can only suggest; "
                "their suggestions always wait for you."
            )
        wanted = list(dict.fromkeys(kinds))
        unknown = [kind for kind in wanted if kind not in KIND_NAMES]
        if unknown:
            raise ValidationError("That is not a kind of suggestion that can be kept for you.")
        current = await self._store.get(workspace_id=workspace_id, project_id=project_id)
        adding = [kind for kind in wanted if kind not in current.rules]
        if adding and await self._forbidden(workspace_id):
            raise ValidationError(
                "A workspace owner has turned off keeping suggestions for every story here."
            )
        counts = await self._store.hand_counts(workspace_id=workspace_id, project_id=project_id)
        for kind in adding:
            count = counts.get(kind, 0)
            if count < HAND_APPROVALS_NEEDED:
                raise ValidationError(
                    f"Approve “{KIND_NAMES[kind]}” by hand {HAND_APPROVALS_NEEDED} times first; "
                    f"so far you have approved it {count} "
                    f"time{'s' if count != 1 else ''}."
                )
        now = self._clock()
        rules = {
            kind: current.rules.get(kind) or RuleState(since=now, by=scope.actor_user_id)
            for kind in wanted
        }
        await self._store.set(
            workspace_id=workspace_id,
            project_id=project_id,
            settings=DelegationSettings(rules=rules, allow_clients=allow_clients),
            actor_user_id=scope.actor_user_id,
        )
        if any(kind not in rules for kind in current.rules):
            await self._withdraw_pending(scope, workspace_id, project_id)
        await self._audit(
            scope,
            workspace_id,
            action="delegation.rules_changed",
            target_type="project",
            target_id=project_id,
            detail={"before": sorted(current.rules), "after": sorted(rules)},
        )
        log_event(
            logger,
            "delegation.rules_changed",
            project_id=project_id,
            kinds=sorted(rules),
            allow_clients=allow_clients,
        )
        return await self.view(scope, workspace_id, project_id)

    async def _audit(
        self,
        scope: AccessScope,
        workspace_id: str,
        *,
        action: str,
        target_type: str,
        target_id: str,
        detail: dict[str, Any],
    ) -> None:
        """Write the change to the workspace's audit log, with who made it."""
        if self._tenancy is None:
            return
        await self._tenancy.record_workspace_audit(
            scope,
            workspace_id=workspace_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )

    async def workspace_forbidden(self, workspace_id: str) -> bool:
        return await self._forbidden(workspace_id)

    async def set_workspace_forbidden(
        self, scope: AccessScope, workspace_id: str, *, forbidden: bool
    ) -> bool:
        """An owner turns the feature off (or back on) for every story. Callers have
        checked the person is an owner. Stories keep their rules, unused, so turning it
        back on restores what each author chose."""
        current = await self._store.get(workspace_id=workspace_id, project_id=WORKSPACE)
        await self._store.set(
            workspace_id=workspace_id,
            project_id=WORKSPACE,
            settings=replace(current, forbidden=forbidden),
            actor_user_id=scope.actor_user_id,
        )
        if forbidden:
            await self._withdraw_pending(scope, workspace_id, None)
        await self._audit(
            scope,
            workspace_id,
            action="delegation.workspace_forbidden",
            target_type="workspace",
            target_id=workspace_id,
            detail={"before": bool(current.forbidden), "after": forbidden},
        )
        log_event(logger, "delegation.workspace_changed", forbidden=forbidden)
        return forbidden

    async def _withdraw_pending(
        self, scope: AccessScope, workspace_id: str, project_id: str | None
    ) -> None:
        """Anything approved under a rule but not yet in the story goes back to review.

        The commit transaction already refuses these once the rule is off; this also
        covers one left approved by a request that stopped part-way.
        """
        pending: list[DelegationRecord] = []
        while True:
            page = await self._store.list_records(
                workspace_id=workspace_id,
                project_id=project_id,
                how=HOW_KEPT,
                states=["pending"],
                limit=_PAGE,
                offset=len(pending),
            )
            pending.extend(page)
            if len(page) < _PAGE:
                break
        forbidden = await self._forbidden(workspace_id)
        for record in pending:
            story = await self._store.get(workspace_id=workspace_id, project_id=record.project_id)
            if not forbidden and all(kind in story.rules for kind in record.kinds):
                continue
            await self._back_to_review(
                self._scope(scope, workspace_id, record.project_id),
                record,
                Refusal(RULE_OFF_CODE, RULE_OFF_MESSAGE),
            )

    async def _back_to_review(
        self, scope: AccessScope, record: DelegationRecord, refusal: Refusal
    ) -> None:
        """The suggestion goes back to the review queue first, and only then is its record
        rewritten. A commit that got there first holds the suggestion's row, so it either
        finishes (the suggestion is in the story, its record already says kept, and nothing
        here changes it) or sees the suggestion waiting again and refuses it."""
        try:
            await self._mutation.return_to_review(scope, record.mutation_id, refusal.message)
        except IllegalTransition:
            return
        review = await self._mutation.review(scope, record.mutation_id)
        if review.mutation.status is MutationStatus.COMMITTED:
            return
        await self._store.put_record(
            workspace_id=record.workspace_id,
            project_id=record.project_id,
            mutation_id=record.mutation_id,
            how=HOW_WAITED,
            state="waiting",
            kinds=record.kinds,
            branch_id=record.branch_id,
            actor_user_id=record.actor_user_id,
            via=record.via,
            reason_code=refusal.code,
            reason=refusal.message,
        )

    # ----- keeping --------------------------------------------------------------------

    async def _facts(
        self, workspace_id: str, project_id: str, operation: CanonOperation
    ) -> OperationFacts:
        if isinstance(operation, UpdateEntityOp) and not operation.id.startswith("tmp:"):
            entity = await self._reader.get_entity(
                workspace_id=workspace_id, project_id=project_id, entity_id=operation.id
            )
            if entity is None:
                return OperationFacts(target_exists=False)
            return OperationFacts(
                target_empty=has_no_details(entity.attributes),
                changes_marks=changes_reserved(operation.attributes, entity.attributes),
            )
        if isinstance(operation, AssertFactOp):
            proposition = operation.proposition
            named = {proposition.subject_id}
            object_id = getattr(proposition.object, "entity_id", None)
            if object_id:
                named.add(object_id)
            return OperationFacts(
                secret_overlap=any(
                    [
                        await self._names_a_secret(workspace_id, project_id, entity_id)
                        for entity_id in sorted(named)
                        if not entity_id.startswith("tmp:")
                    ]
                )
            )
        if isinstance(operation, CreateEntityOp):
            return OperationFacts(
                looks_like_existing=await self._looks_like_existing(
                    workspace_id, project_id, operation
                )
            )
        return OperationFacts()

    async def _names_a_secret(self, workspace_id: str, project_id: str, entity_id: str) -> bool:
        """Whether any secret fact, held now or before, is about ``entity_id`` or points at it."""
        ordinary = {None, FactVisibility.ORDINARY, FactVisibility.ORDINARY.value}
        for side in ("subject_id", "object_entity_id"):
            offset = 0
            while True:
                facts = await self._reader.list_facts(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    include_closed=True,
                    limit=_FACT_PAGE,
                    offset=offset,
                    **{side: entity_id},
                )
                if any(getattr(fact, "visibility", None) not in ordinary for fact in facts):
                    return True
                if len(facts) < _FACT_PAGE:
                    break
                offset += _FACT_PAGE
        return False

    async def _looks_like_existing(
        self, workspace_id: str, project_id: str, operation: CreateEntityOp
    ) -> bool:
        """Whether the new entry's name or a nickname is an existing entry's name or nickname,
        compared without case or spacing, across every kind of entry."""
        wanted = {normalize_name(label) for label in (operation.name, *operation.aliases)}
        wanted.discard("")
        for label in sorted(wanted):
            found = await self._reader.search_entities(
                workspace_id=workspace_id,
                project_id=project_id,
                query=label,
                limit=_NAME_MATCHES,
            )
            for entity in found:
                labels = {normalize_name(entity.name), *map(normalize_name, entity.aliases)}
                if labels & wanted:
                    return True
        return False

    async def consider(
        self,
        scope: AccessScope,
        mutation_id: str,
        *,
        via: str = VIA_SESSION,
        validation: Any = None,
    ) -> Considered:
        """Keep a waiting suggestion for the author if their rules allow it; otherwise
        leave it waiting and say why. ``scope`` names the story."""
        workspace_id, project_id = scope.workspace_id or "", scope.project_id or ""
        review = await self._mutation.review(scope, mutation_id)
        mutation: MutationSet = review.mutation
        story = await self._store.get(workspace_id=workspace_id, project_id=project_id)
        forbidden = await self._forbidden(workspace_id)
        if not story.rules:
            # Nothing is switched on: nothing to decide and nothing to record.
            return Considered(mutation=mutation, kept=False)
        accepted = _effective(review)
        operations = tuple(parse_operation(item.payload) for item in accepted)
        if validation is None:
            validation = await self._mutation.validate(scope, mutation_id)
        facts = tuple(
            [await self._facts(workspace_id, project_id, operation) for operation in operations]
        )
        proposal = Proposal(
            origin=mutation.origin,
            intent=mutation.intent,
            status=mutation.status.value,
            via=via,
            stale=review.head_revision != mutation.expected_head,
            operations=operations,
            facts=facts,
            issues=tuple(
                (issue.code, issue.message, issue.severity == "blocking")
                for issue in validation.issues
            ),
            main_line=working_branch_id() is None,
            kept_today=await self._kept_today(workspace_id, project_id),
        )
        decided = decide(
            proposal,
            story=story,
            workspace_forbidden=forbidden,
            hand_approvals=await self._store.hand_counts(
                workspace_id=workspace_id, project_id=project_id
            ),
        )
        record_args = {
            "workspace_id": workspace_id,
            "project_id": project_id,
            "mutation_id": mutation_id,
            "branch_id": mutation.branch_id,
            "actor_user_id": scope.actor_user_id,
            "via": via,
        }
        if isinstance(decided, Refusal):
            await self._store.put_record(
                **record_args,
                how=HOW_WAITED,
                state="waiting",
                kinds=[],
                reason_code=decided.code,
                reason=decided.message,
            )
            log_event(logger, "delegation.waited", mutation_id=mutation_id, code=decided.code)
            return Considered(mutation=mutation, kept=False, refusal=decided)

        await self._store.put_record(**record_args, how=HOW_KEPT, state="pending", kinds=decided)
        # Counted again with this one marked, so two decided at once cannot both pass the
        # cap: each sees the other's mark (at worst both wait).
        if await self._kept_today(workspace_id, project_id) > DAILY_LIMIT:
            capped = Refusal(
                "daily_limit",
                f"{DAILY_LIMIT} suggestions were already kept for you today; the rest wait.",
            )
            await self._store.put_record(
                **record_args,
                how=HOW_WAITED,
                state="waiting",
                kinds=[],
                reason_code=capped.code,
                reason=capped.message,
            )
            log_event(logger, "delegation.waited", mutation_id=mutation_id, code=capped.code)
            return Considered(mutation=mutation, kept=False, refusal=capped)
        try:
            await self._mutation.approve(scope, mutation_id, kept_note(decided))
            await self._mutation.commit(scope, mutation_id)
        except _COMMIT_REFUSALS as error:
            refusal = _refusal_from(error)
            record = await self._store.get_record(
                workspace_id=workspace_id, project_id=project_id, mutation_id=mutation_id
            )
            if record is not None and record.how == HOW_WAITED and record.reason_code:
                # Switching the rule off already sent it back, and said why.
                refusal = Refusal(record.reason_code, record.reason or "")
            elif record is not None:
                await self._back_to_review(scope, record, refusal)
            log_event(
                logger,
                "delegation.not_kept",
                mutation_id=mutation_id,
                code=refusal.code,
                error_type=type(error).__name__,
            )
            refreshed = await self._mutation.review(scope, mutation_id)
            return Considered(mutation=refreshed.mutation, kept=False, refusal=refusal)
        log_event(
            logger,
            "delegation.kept",
            mutation_id=mutation_id,
            kinds=list(decided),
            operations=len(operations),
        )
        refreshed = await self._mutation.review(scope, mutation_id)
        return Considered(mutation=refreshed.mutation, kept=True, kinds=decided)

    async def _kept_today(self, workspace_id: str, project_id: str) -> int:
        """Suggestions kept, or on their way to being kept, in this story in the last day.

        Only whether this passes ``DAILY_LIMIT`` matters, so no more than one past it is read.
        """
        since = self._clock() - timedelta(days=1)
        records = await self._store.list_records(
            workspace_id=workspace_id,
            project_id=project_id,
            how=HOW_KEPT,
            states=["pending", "kept"],
            limit=DAILY_LIMIT + 1,
        )
        return sum(1 for record in records if record.created_at > since)

    async def _on_main_line(self, mutation: MutationSet) -> bool:
        """Whether the suggestion was made in the story itself rather than in a what-if."""
        if working_branch_id() is not None:
            return False
        journal = getattr(self._mutation, "_journal", None)
        if journal is None or not hasattr(journal, "get_branch"):
            return True
        branch = await journal.get_branch(
            workspace_id=mutation.workspace_id,
            project_id=mutation.project_id,
            branch_id=mutation.branch_id,
        )
        return branch is None or bool(branch.is_root)

    async def committed(
        self,
        scope: AccessScope,
        mutation: MutationSet,
        accepted: list[MutationOperation],
        revision: int,
    ) -> None:
        """Called by the mutation service after every commit of the assistant's work:
        a suggestion kept under a rule is now in the story; any other was approved by a
        person, and counts toward switching on the kinds it holds (only in the story
        itself: approving in a what-if earns nothing for the story).

        The commit transaction already marks a kept one ``kept``; this only makes sure,
        and never turns a kept one into one approved by hand."""
        if mutation.origin != OriginKind.MODEL.value:
            return
        record = await self._store.get_record(
            workspace_id=mutation.workspace_id,
            project_id=mutation.project_id,
            mutation_id=mutation.id,
        )
        if record is not None and record.how == HOW_KEPT:
            if record.state != "kept" or record.revision is None:
                await self._store.update_record(
                    workspace_id=mutation.workspace_id,
                    project_id=mutation.project_id,
                    mutation_id=mutation.id,
                    state="kept",
                    revision=revision,
                )
            return
        kinds: list[str] = []
        if await self._on_main_line(mutation):
            for item in accepted:
                kind = structural_kind(parse_operation(item.payload))
                if kind is not None and kind not in kinds:
                    kinds.append(kind)
        await self._store.put_record(
            workspace_id=mutation.workspace_id,
            project_id=mutation.project_id,
            mutation_id=mutation.id,
            how=HOW_BY_HAND,
            state="counted",
            kinds=kinds,
            branch_id=mutation.branch_id,
            actor_user_id=scope.actor_user_id,
            via=record.via if record is not None else VIA_SESSION,
            reason_code=record.reason_code if record is not None else None,
            reason=record.reason if record is not None else None,
        )
        await self._store.update_record(
            workspace_id=mutation.workspace_id,
            project_id=mutation.project_id,
            mutation_id=mutation.id,
            revision=revision,
        )

    # ----- what was kept ------------------------------------------------------------

    async def record(
        self, scope: AccessScope, workspace_id: str, project_id: str, mutation_id: str
    ) -> DelegationRecord | None:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._store.get_record(  # type: ignore[no-any-return]
            workspace_id=workspace_id, project_id=project_id, mutation_id=mutation_id
        )

    async def kept(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        limit: int = 100,
    ) -> list[KeptItem]:
        """What was kept for the author, newest first."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        records = await self._store.list_records(
            workspace_id=workspace_id,
            project_id=project_id,
            how=HOW_KEPT,
            states=["kept"],
            limit=limit,
        )
        story = self._scope(scope, workspace_id, project_id)
        items: list[KeptItem] = []
        for record in records:
            review = await self._mutation.review(story, record.mutation_id)
            undo_status: str | None = None
            if record.undo_mutation_id:
                try:
                    undo = await self._mutation.review(story, record.undo_mutation_id)
                    undo_status = undo.mutation.status.value
                except Exception:  # noqa: BLE001 - an undo removed with its story is just gone
                    undo_status = None
            items.append(
                KeptItem(
                    record=record,
                    operations=tuple(_effective(review)),
                    undo_status=undo_status,
                )
            )
        return items

    async def seen(self, scope: AccessScope, workspace_id: str, project_id: str) -> None:
        """This person has looked at what was kept, so the review queue stops counting it."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        await self._store.set_seen(
            workspace_id=workspace_id, project_id=project_id, user_id=scope.actor_user_id
        )

    async def undo(
        self, scope: AccessScope, workspace_id: str, project_id: str, mutation_id: str
    ) -> str:
        """Suggest undoing one kept suggestion. Nothing is undone yet: the undo waits in
        the review queue like any other (an undo is never kept for the author)."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        record = await self._store.get_record(
            workspace_id=workspace_id, project_id=project_id, mutation_id=mutation_id
        )
        if record is None or (record.how, record.state) != (HOW_KEPT, "kept"):
            raise NotFoundError("That was not kept for you.")
        story = self._scope(scope, workspace_id, project_id)
        if record.undo_mutation_id:
            try:
                existing = await self._mutation.review(story, record.undo_mutation_id)
                if existing.mutation.status not in {
                    MutationStatus.REJECTED,
                    MutationStatus.COMMITTED,
                }:
                    return record.undo_mutation_id
            except Exception:  # noqa: BLE001 - suggest a fresh undo instead
                pass
        if self._history is None or record.revision is None:
            raise ValidationError("This cannot be undone from here.")
        from backend.services.mutation_service import ReviewRequiredError

        head = await self._reader.get_head(
            workspace_id=workspace_id, project_id=project_id, branch_id=record.branch_id
        )
        try:
            await self._history.commit_revert(
                story,
                workspace_id,
                project_id,
                from_revision=record.revision - 1,
                to_revision=record.revision,
                expected_head=head,
                acknowledge_high_impact=False,
            )
        except ReviewRequiredError as parked:
            await self._store.update_record(
                workspace_id=workspace_id,
                project_id=project_id,
                mutation_id=mutation_id,
                undo_mutation_id=parked.mutation_id,
            )
            log_event(logger, "delegation.undo_suggested", mutation_id=mutation_id)
            return parked.mutation_id
        except ValidationError as error:
            raise ValidationError(
                "Something it added has changed since, so it cannot be undone in one step. "
                "Change it on its own page instead."
            ) from error
        # A revert always needs approval, so reaching here means nothing was parked.
        raise ValidationError("This cannot be undone from here.")


__all__ = ["Considered", "DelegationService", "DelegationView", "KeptItem", "RuleView"]
