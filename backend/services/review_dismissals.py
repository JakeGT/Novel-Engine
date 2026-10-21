"""Dismissing advisory findings on a proposal in review (CON-016).

The review screen's findings are worked out by the mutation service (``validate``). Each
advisory one (a possible duplicate, a fact already recorded, a story rule set to warn)
can be dismissed with a reason, for this proposal only. A dismissal is keyed by the
change as it stands (its ordinal, revision and a digest of its content) and keeps a
digest of what the finding said (and, for a story rule, the rule's version). It holds
while both are unchanged: an edited change, a changed rule, or a story that now makes
the finding say something else brings it back. A finding that blocks (an integrity
check, a rule set to block) can never be dismissed (ADR-013).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend.adapters.system import uuid7
from backend.core.logging import log_event
from backend.domain.branching import working_in
from backend.domain.check_fingerprints import digest_of, finding_dependency
from backend.domain.dismissals import (
    MAX_DISMISSALS_PER_SCENE,
    MAX_NOTE,
    DismissReason,
    ReviewDismissal,
    review_fingerprint,
)
from backend.domain.errors import NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.tenancy import Permission

logger = logging.getLogger(__name__)

ADVISORY = "advisory"


@dataclass(frozen=True)
class ReviewFinding:
    """An advisory finding on a proposal now, with the author's dismissal if it holds."""

    ordinal: int
    code: str
    message: str
    key: str
    dismissal: ReviewDismissal | None = None
    # Dismissed before, and back because the change or what the finding says changed.
    came_back: bool = False


def _rule_id(code: str) -> str | None:
    return code.split(":", 1)[1] if code.startswith("rule:") else None


def _identity(operation: Any) -> str:
    """The change as it stands: an edit is a new revision, so a new identity."""
    if operation is None:
        return ""
    return f"{operation.ordinal}:{operation.revision}:{digest_of(operation.payload)}"


def _depends(code: str, identity: str, message: str, versions: dict[str, int]) -> str:
    """What the finding says about this change, and the version of the rule it cites."""
    rule = _rule_id(code)
    cited = str(versions.get(rule, 0)) if rule else ""
    return finding_dependency(code, f"{identity}\x1e{cited}", message)


class ReviewDismissalService:
    def __init__(self, *, store: Any, mutations: Any, rules: Any = None) -> None:
        self._store = store
        self._mutations = mutations
        self._rules = rules

    async def _versions(self, scope: AccessScope) -> dict[str, int]:
        if self._rules is None:
            return {}
        listed = await self._rules.list_rules(
            workspace_id=scope.workspace_id or "", project_id=scope.project_id or ""
        )
        return {str(rule.id): int(rule.version) for rule in listed}

    async def _report(self, scope: AccessScope, mutation_id: str) -> Any:
        """What the review screen's own check finds on the proposal, for anyone who may
        read it: the check is a preview that changes nothing, as ``validate`` gives it
        (read in the proposal's own line of the story)."""
        mutations = self._mutations
        mutation = await mutations._require(scope, mutation_id, Permission.PROJECT_READ)
        branch = await mutations._journal.get_branch(
            workspace_id=mutation.workspace_id,
            project_id=mutation.project_id,
            branch_id=mutation.branch_id,
        )
        with working_in(None if branch is None or branch.is_root else branch.id):
            return await mutations._validate(mutation, mutation_id)

    async def _may_change(self, scope: AccessScope, mutation_id: str) -> None:
        """Dismissing and bringing back are changes: only someone who may write may."""
        await self._mutations._require(scope, mutation_id, Permission.PROJECT_WRITE)

    async def findings(self, scope: AccessScope, mutation_id: str) -> list[ReviewFinding]:
        """The advisory findings on this proposal now, each with its key and dismissal.
        Reading them needs only read access, like the proposal itself."""
        review = await self._mutations.review(scope, mutation_id)
        report = await self._report(scope, mutation_id)
        operations = {item.ordinal: item for item in review.operations}
        versions = await self._versions(scope)
        standing = [
            item
            for item in await self._store.for_mutation(
                workspace_id=scope.workspace_id or "",
                project_id=scope.project_id or "",
                mutation_id=mutation_id,
            )
            if item.standing
        ]
        by_key = {item.fingerprint: item for item in standing}
        found: list[ReviewFinding] = []
        # How many findings of each kind on each change came before, so two of the same
        # kind on one change are told apart.
        seen: dict[tuple[int, str], int] = {}
        for issue in report.issues:
            if issue.severity != ADVISORY:
                continue
            identity = _identity(operations.get(issue.ordinal))
            occurrence = seen.get((issue.ordinal, issue.code), 0)
            seen[(issue.ordinal, issue.code)] = occurrence + 1
            key = review_fingerprint(mutation_id, identity, issue.code, occurrence)
            depends = _depends(issue.code, identity, issue.message, versions)
            held = by_key.get(key)
            came_back = held is not None and held.depends_on != depends
            found.append(
                ReviewFinding(
                    ordinal=issue.ordinal,
                    code=issue.code,
                    message=issue.message,
                    key=key,
                    dismissal=None if came_back else held,
                    came_back=came_back,
                )
            )
        return found

    async def dismiss(
        self,
        scope: AccessScope,
        mutation_id: str,
        *,
        key: str,
        reason: DismissReason,
        note: str = "",
    ) -> list[ReviewFinding]:
        await self._may_change(scope, mutation_id)
        found = next(
            (item for item in await self.findings(scope, mutation_id) if item.key == key), None
        )
        if found is None:
            raise NotFoundError(
                "That finding is no longer there: the change or the story may have changed."
            )
        note = " ".join(note.split())
        if reason is DismissReason.OTHER and not note:
            raise ValidationError("Say in a few words why this is not a problem.")
        if len(note) > MAX_NOTE:
            raise ValidationError(f"Keep the reason under {MAX_NOTE} characters.")
        workspace_id, project_id = scope.workspace_id or "", scope.project_id or ""
        kept = [
            item
            for item in await self._store.for_mutation(
                workspace_id=workspace_id, project_id=project_id, mutation_id=mutation_id
            )
            if item.standing
        ]
        # Only those still standing count: one brought back holds nothing back.
        if len(kept) >= MAX_DISMISSALS_PER_SCENE:
            raise ValidationError("These changes have as many dismissed findings as they can hold.")
        versions = await self._versions(scope)
        review = await self._mutations.review(scope, mutation_id)
        operation = next((op for op in review.operations if op.ordinal == found.ordinal), None)
        identity = _identity(operation)
        await self._store.add(
            ReviewDismissal(
                workspace_id=workspace_id,
                project_id=project_id,
                id=str(uuid7()),
                mutation_id=mutation_id,
                fingerprint=key,
                depends_on=_depends(found.code, identity, found.message, versions),
                code=found.code,
                reason=reason,
                note=note,
                created_by=scope.actor_user_id,
                created_at=datetime.now(UTC),
            )
        )
        # The reason the author typed is their writing: only its kind is logged.
        log_event(logger, "review.finding_dismissed", code=found.code, reason=reason.value)
        return await self.findings(scope, mutation_id)

    async def undo(
        self, scope: AccessScope, mutation_id: str, dismissal_id: str
    ) -> list[ReviewFinding]:
        # Checks first that the proposal is there and the person may change it.
        await self._may_change(scope, mutation_id)
        withdrawn = await self._store.withdraw(
            workspace_id=scope.workspace_id or "",
            project_id=scope.project_id or "",
            mutation_id=mutation_id,
            dismissal_id=dismissal_id,
            at=datetime.now(UTC),
        )
        if not withdrawn:
            raise NotFoundError("That was already brought back.")
        return await self.findings(scope, mutation_id)


__all__ = ["ReviewDismissalService", "ReviewFinding"]
