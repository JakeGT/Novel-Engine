"""The assistant's kept threads and the record of suggestion runs.

A thread belongs to its author alone: no one else can read, add to or clear it, and it
is not acknowledged to exist for anyone else. One is kept per author, kind and line of
the story (a what-if has threads of its own). Clearing it erases it and its turns. A
person removed from a workspace has their threads in it erased. The project archive
carries only the exporting author's own threads, and restoring one gives them to the
author restoring it: an archive can never write into someone else's thread.

A suggestion run is recorded when suggestions are made, and settled when they are
decided: how many were kept is read from the set of suggested changes itself (what was
saved, less what the author skipped), so nothing is counted that the author did not do.
Suggestions a keep-for-me rule kept are counted apart, as kept for the author. Runs are
listed for the line of the story the author works in.

Nothing here calls a model, and nothing logs the author's words.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from backend.adapters.system import uuid7
from backend.domain.branching import working_branch_id
from backend.domain.conversations import (
    KEPT_FOR_YOU,
    MAX_TURN_LENGTH,
    RunKind,
    RunSource,
    SuggestionRun,
    Thread,
    ThreadKind,
    Turn,
    TurnAnswer,
    TurnRole,
)
from backend.domain.delegation import is_kept_note
from backend.domain.identity import AccessScope
from backend.domain.journal import MutationStatus
from backend.domain.review import effective_operations
from backend.domain.tenancy import Permission

MAX_RUNS_LISTED = 50
MAX_EXPORTED_THREADS = 2000


def _clip(turn: Turn) -> Turn:
    return turn.model_copy(
        update={
            "body": turn.body[:MAX_TURN_LENGTH],
            "questions": [item[:500] for item in turn.questions[:12]],
            "answers": [
                TurnAnswer(question=item.question[:500], answer=item.answer[:MAX_TURN_LENGTH])
                for item in turn.answers[:12]
            ],
            "notes": [item[:500] for item in turn.notes[:16]],
        }
    )


class ConversationService:
    def __init__(
        self,
        store: Any,
        *,
        journal: Any = None,
        tenancy: Any = None,
        local_mode: bool = False,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._journal = journal
        self._tenancy = tenancy
        self._local_mode = local_mode
        self._now = now or (lambda: datetime.now(UTC))
        if tenancy is not None and hasattr(tenancy, "attach_member_threads"):
            # A person removed from a workspace has their threads in it erased.
            tenancy.attach_member_threads(self)

    async def _authorize(
        self, scope: AccessScope, workspace_id: str, project_id: str, permission: Permission
    ) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(scope, workspace_id, project_id, permission)

    async def thread(
        self, scope: AccessScope, workspace_id: str, project_id: str, kind: ThreadKind
    ) -> Thread | None:
        """The caller's own running thread of this kind, in the line they work in."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._store.find_thread(
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=scope.actor_user_id,
            kind=kind,
            branch_id=working_branch_id(),
        )

    async def record(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        kind: ThreadKind,
        turns: Sequence[Turn],
    ) -> Thread:
        """Add turns to the caller's thread, starting one when there is none."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        now = self._now()
        thread = await self._store.find_thread(
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=scope.actor_user_id,
            kind=kind,
            branch_id=working_branch_id(),
        )
        if thread is None:
            thread = await self._store.start_thread(
                Thread(
                    id=f"thr_{uuid7()}",
                    workspace_id=workspace_id,
                    project_id=project_id,
                    actor_user_id=scope.actor_user_id,
                    kind=kind,
                    branch_id=working_branch_id(),
                    created_at=now,
                    updated_at=now,
                )
            )
        await self._store.add_turns(
            workspace_id=workspace_id,
            project_id=project_id,
            thread_id=thread.id,
            turns=[_clip(turn) for turn in turns],
            now=now,
        )
        found = await self._store.find_thread(
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=scope.actor_user_id,
            kind=kind,
            branch_id=working_branch_id(),
        )
        assert found is not None
        return found

    async def withdraw_for_member(
        self, scope: AccessScope, workspace_id: str, user_id: str
    ) -> list[str]:
        """Erase every thread a person kept in a workspace: they were removed from it.
        ``scope`` is whoever removed them (already allowed to); nothing is returned."""
        del scope
        await self._store.delete_member_threads(workspace_id=workspace_id, actor_user_id=user_id)
        return []

    async def clear(
        self, scope: AccessScope, workspace_id: str, project_id: str, kind: ThreadKind
    ) -> bool:
        """Erase the caller's thread of this kind: start again. True when there was one.
        The words its conversations kept are erased by the caller first (see the API)."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        thread = await self._store.find_thread(
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=scope.actor_user_id,
            kind=kind,
            branch_id=working_branch_id(),
        )
        if thread is None:
            return False
        await self._store.delete_thread(
            workspace_id=workspace_id, project_id=project_id, thread_id=thread.id
        )
        return True

    async def count_tokens(
        self, thread_id: str, workspace_id: str, project_id: str, tokens: int
    ) -> None:
        """Add what a reply used to its thread, for the conversation budget."""
        if tokens > 0:
            await self._store.add_tokens(
                workspace_id=workspace_id, project_id=project_id, thread_id=thread_id, tokens=tokens
            )

    # ------------------------------------------------------------------ runs

    async def record_run(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        kind: RunKind,
        suggested: int,
        mutation_id: str | None = None,
        source: RunSource = RunSource.LISTING,
        prompt_version: str | None = None,
        grounded: int | None = None,
        invented: int | None = None,
        details: dict[str, int] | None = None,
    ) -> SuggestionRun:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        return await self._store.add_run(  # type: ignore[no-any-return]
            SuggestionRun(
                id=f"run_{uuid7()}",
                workspace_id=workspace_id,
                project_id=project_id,
                kind=kind,
                actor_user_id=scope.actor_user_id,
                branch_id=working_branch_id(),
                mutation_id=mutation_id,
                source=source,
                prompt_version=prompt_version,
                suggested=max(0, suggested),
                grounded=grounded,
                invented=invented,
                created_at=self._now(),
                details={key: int(value) for key, value in (details or {}).items()},
            )
        )

    async def _settle(self, run: SuggestionRun) -> SuggestionRun:
        """How many of a run's suggestions were kept, once the author has decided."""
        if run.settled_at is not None or run.mutation_id is None or self._journal is None:
            return run
        mutation = await self._journal.get_mutation(
            workspace_id=run.workspace_id, project_id=run.project_id, mutation_id=run.mutation_id
        )
        if mutation is None:
            return run
        kept_for_you: int | None = None
        if mutation.status is MutationStatus.COMMITTED:
            kept = len(
                effective_operations(
                    await self._journal.list_operations(mutation_id=run.mutation_id),
                    await self._journal.list_operation_decisions(mutation_id=run.mutation_id),
                )
            )
            decisions = await self._journal.list_decisions(mutation_id=run.mutation_id)
            if any(
                decision.decision == "approved" and is_kept_note(decision.note)
                for decision in decisions
            ):
                # A keep-for-me rule kept it: the author decided nothing, so none of it
                # counts as kept by them.
                kept, kept_for_you = 0, min(kept, run.suggested)
        elif mutation.status is MutationStatus.REJECTED:
            kept = 0
        else:
            return run
        kept = min(kept, run.suggested)
        settled_at = self._now()
        await self._store.settle_run(
            workspace_id=run.workspace_id,
            project_id=run.project_id,
            run_id=run.id,
            kept=kept,
            settled_at=settled_at,
            kept_for_you=kept_for_you,
        )
        details = dict(run.details)
        if kept_for_you is not None:
            details[KEPT_FOR_YOU] = kept_for_you
        return run.model_copy(update={"kept": kept, "settled_at": settled_at, "details": details})

    async def runs(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        kind: RunKind | None = None,
        limit: int = 10,
    ) -> list[SuggestionRun]:
        """The latest runs in the line the author works in, newest first, each settled if
        it has been decided."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        found = await self._store.list_runs(
            workspace_id=workspace_id,
            project_id=project_id,
            kind=kind,
            limit=max(1, min(limit, MAX_RUNS_LISTED)),
            branch_id=working_branch_id(),
        )
        return [await self._settle(run) for run in found]

    # ------------------------------------------------------------------ archive

    async def export_threads(
        self,
        workspace_id: str,
        project_id: str,
        mutation_commits: dict[str, str],
        *,
        actor_user_id: str | None,
    ) -> list[dict[str, Any]]:
        """The exporting author's own kept threads for the project archive (no one
        else's: a thread is private to its author, owner or not). A turn's suggested
        changes travel as the saved change they became (or as nothing, if never saved)."""
        if not actor_user_id:
            return []
        threads = [
            thread
            for thread in await self._store.list_threads(
                workspace_id=workspace_id, project_id=project_id
            )
            if thread.actor_user_id == actor_user_id
        ]
        exported: list[dict[str, Any]] = []
        for thread in threads[:MAX_EXPORTED_THREADS]:
            entry = thread.model_dump(mode="json", exclude={"workspace_id", "project_id"})
            for turn in entry["turns"]:
                turn["commit_id"] = mutation_commits.get(turn.pop("mutation_id") or "")
            exported.append(entry)
        return exported

    async def restore_threads(
        self,
        workspace_id: str,
        project_id: str,
        raw: Any,
        branches: dict[str, str],
        mutation_for: Callable[[str | None], Awaitable[str | None]],
        *,
        actor_user_id: str | None,
    ) -> int:
        """Put back an archive's threads as the restoring author's own. Who an archive
        says a thread belonged to is never trusted (an archive can be edited): every
        thread restored is the restoring author's, so nothing is ever written into
        anyone else's thread. An older archive holding several people's threads gives
        back only the restoring author's. Idempotent: a thread the author already has for
        that kind and line is left as it is. A what-if's thread goes to that what-if as
        restored, or is left out when it is not there."""
        if not actor_user_id:
            return 0
        items = [item for item in (raw if isinstance(raw, list) else []) if isinstance(item, dict)]
        owners = {str(item.get("actor_user_id")) for item in items}
        if len(owners) > 1:
            items = [item for item in items if str(item.get("actor_user_id")) == actor_user_id]
        restored = 0
        for item in items:
            try:
                branch_id = item.get("branch_id")
                if branch_id:
                    branch_id = branches.get(str(branch_id))
                    if branch_id is None:
                        continue
                kind = ThreadKind(str(item["kind"]))
                actor = actor_user_id
                if await self._store.find_thread(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    actor_user_id=actor,
                    kind=kind,
                    branch_id=branch_id,
                ):
                    continue
                created = datetime.fromisoformat(str(item["created_at"]))
                thread = await self._store.start_thread(
                    Thread(
                        id=f"thr_{uuid7()}",
                        workspace_id=workspace_id,
                        project_id=project_id,
                        actor_user_id=actor,
                        kind=kind,
                        branch_id=branch_id,
                        created_at=created,
                        updated_at=datetime.fromisoformat(str(item["updated_at"])),
                    )
                )
                turns: list[Turn] = []
                for raw_turn in item.get("turns", []):
                    fields = {
                        key: value
                        for key, value in raw_turn.items()
                        if key not in {"commit_id", "position", "created_at"}
                    }
                    fields["mutation_id"] = await mutation_for(raw_turn.get("commit_id"))
                    turns.append(Turn.model_validate(fields))
                await self._store.add_turns(
                    workspace_id=workspace_id,
                    project_id=project_id,
                    thread_id=thread.id,
                    turns=[_clip(turn) for turn in turns],
                    now=created,
                )
                restored += 1
            except (KeyError, TypeError, ValueError):
                continue
        return restored


def author_turn(body: str, answers: Sequence[tuple[str, str]] = ()) -> Turn:
    return Turn(
        role=TurnRole.AUTHOR,
        body=body,
        answers=[TurnAnswer(question=q, answer=a) for q, a in answers],
    )


def assistant_turn(
    understood: str,
    *,
    questions: Sequence[str] = (),
    notes: Sequence[str] = (),
    mutation_id: str | None = None,
    operation_count: int = 0,
    session_id: str | None = None,
) -> Turn:
    return Turn(
        role=TurnRole.ASSISTANT,
        body=understood,
        questions=list(questions),
        notes=list(notes),
        mutation_id=mutation_id,
        operation_count=operation_count,
        session_id=session_id,
    )


__all__ = ["ConversationService", "assistant_turn", "author_turn"]
