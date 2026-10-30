"""Keeps conversations with the assistant so they can be resumed and read back.

A conversation is opened when the assistant first answers (with questions, or with
a proposal). While it is open only its author sees it and can add to it. Once it
has led to a set of suggested changes it is the evidence behind them, readable by
anyone who can read the project. Nothing here calls a model.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.adapters.system import uuid7
from backend.domain.assist import (
    AssistAnswer,
    AssistKind,
    AssistQuestion,
    AssistSession,
    AssistStatus,
    SchemaSuggestion,
)
from backend.domain.errors import ConflictError, NotFoundError
from backend.domain.identity import AccessScope
from backend.domain.tenancy import Permission

MAX_OPEN_LISTED = 20
# An unfinished conversation left untouched this long is erased.
OPEN_KEPT_DAYS = 30
_PREFIX = {
    AssistKind.CREATE: "brf",
    AssistKind.FILL_IN: "brf",
    AssistKind.CHANGE: "chg",
    AssistKind.PLAN: "pnv",
}


class AssistSessionService:
    def __init__(
        self,
        store: Any,
        *,
        tenancy: Any = None,
        local_mode: bool = False,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._tenancy = tenancy
        self._local_mode = local_mode
        self._now = now or (lambda: datetime.now(UTC))

    async def _authorize(
        self, scope: AccessScope, workspace_id: str, project_id: str, permission: Permission
    ) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(scope, workspace_id, project_id, permission)

    async def _own_open(
        self, scope: AccessScope, workspace_id: str, project_id: str, session_id: str
    ) -> AssistSession:
        session = await self._store.get(
            workspace_id=workspace_id, project_id=project_id, session_id=session_id
        )
        # Someone else's unfinished conversation is not acknowledged to exist.
        if session is None or session.actor_user_id != scope.actor_user_id:
            raise NotFoundError("Conversation not found.")
        if session.status is not AssistStatus.OPEN:
            raise ConflictError("This conversation is finished and cannot be changed.")
        return session

    async def begin(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        kind: AssistKind,
        session_id: str | None = None,
        description: str = "",
        entity_type: str | None = None,
        name: str | None = None,
        entity_id: str | None = None,
        questions: list[AssistQuestion] | None = None,
        answers: list[AssistAnswer] | None = None,
        understood: str = "",
        notes: list[str] | None = None,
        suggestions: list[SchemaSuggestion] | None = None,
    ) -> AssistSession:
        """Open a conversation, or carry an open one of the author's forward."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        now = self._now()
        if session_id is not None:
            current = await self._own_open(scope, workspace_id, project_id, session_id)
            session = current.model_copy(
                update={
                    "description": description,
                    "entity_type": entity_type,
                    "name": name,
                    "entity_id": entity_id,
                    "questions": current.questions if questions is None else questions,
                    "answers": current.answers if answers is None else answers,
                    "understood": understood,
                    "notes": notes or [],
                    "suggestions": current.suggestions if suggestions is None else suggestions,
                    "updated_at": now,
                }
            )
        else:
            session = AssistSession(
                id=f"{_PREFIX[kind]}_{uuid7()}",
                workspace_id=workspace_id,
                project_id=project_id,
                actor_user_id=scope.actor_user_id,
                kind=kind,
                description=description,
                entity_type=entity_type,
                name=name,
                entity_id=entity_id,
                questions=questions or [],
                answers=answers or [],
                understood=understood,
                notes=notes or [],
                suggestions=suggestions or [],
                created_at=now,
                updated_at=now,
            )
        return await self._store.save(session)

    async def save_answers(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        session_id: str,
        answers: list[AssistAnswer],
    ) -> AssistSession:
        """Keep what the author has typed so far, so nothing is lost by closing."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        session = await self._own_open(scope, workspace_id, project_id, session_id)
        return await self._store.save(
            session.model_copy(update={"answers": answers, "updated_at": self._now()})
        )

    async def proposed(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        session_id: str,
        *,
        mutation_id: str,
        suggestions: list[SchemaSuggestion] | None = None,
    ) -> AssistSession:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        session = await self._own_open(scope, workspace_id, project_id, session_id)
        return await self._store.save(
            session.model_copy(
                update={
                    "status": AssistStatus.PROPOSED,
                    "mutation_id": mutation_id,
                    "suggestions": session.suggestions if suggestions is None else suggestions,
                    "updated_at": self._now(),
                }
            )
        )

    async def close(
        self, scope: AccessScope, workspace_id: str, project_id: str, session_id: str
    ) -> AssistSession:
        """Put an unfinished conversation away. Its text is erased, not hidden: the
        author is told it cannot be brought back, so nothing of it is kept."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        session = await self._own_open(scope, workspace_id, project_id, session_id)
        await self._store.delete(
            workspace_id=workspace_id, project_id=project_id, session_id=session_id
        )
        return session.model_copy(update={"status": AssistStatus.CLOSED, "updated_at": self._now()})

    async def erase_words(
        self, scope: AccessScope, workspace_id: str, project_id: str, session_ids: list[str]
    ) -> int:
        """Erase what the author said in these conversations of theirs (their thread was
        cleared): an unfinished one goes; one behind suggested changes stays as their
        evidence, with its words blanked. Anyone else's is left alone."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        erased = 0
        for session_id in dict.fromkeys(session_ids):
            session = await self._store.get(
                workspace_id=workspace_id, project_id=project_id, session_id=session_id
            )
            if session is None or session.actor_user_id != scope.actor_user_id:
                continue
            if session.status is AssistStatus.OPEN:
                await self._store.delete(
                    workspace_id=workspace_id, project_id=project_id, session_id=session_id
                )
            else:
                await self._store.save(
                    session.model_copy(
                        update={
                            "description": "",
                            "questions": [
                                item.model_copy(update={"question": ""})
                                for item in session.questions
                            ],
                            "answers": [
                                item.model_copy(update={"question": "", "answer": ""})
                                for item in session.answers
                            ],
                            "understood": "",
                            "notes": [],
                            "updated_at": self._now(),
                        }
                    )
                )
            erased += 1
        return erased

    async def list_open(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> list[AssistSession]:
        """The author's own unfinished conversations, most recent first."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        # An unfinished conversation is an author's private notes; it is not kept forever.
        await self._store.delete_stale_open(
            workspace_id=workspace_id,
            project_id=project_id,
            before=self._now() - timedelta(days=OPEN_KEPT_DAYS),
        )
        return await self._store.list_open(
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id=scope.actor_user_id,
            limit=MAX_OPEN_LISTED,
        )

    async def get(
        self, scope: AccessScope, workspace_id: str, project_id: str, session_id: str
    ) -> AssistSession:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        session = await self._store.get(
            workspace_id=workspace_id, project_id=project_id, session_id=session_id
        )
        if session is None:
            raise NotFoundError("Conversation not found.")
        # Evidence behind a proposal is for every reader; anything else is its author's.
        if (
            session.status is not AssistStatus.PROPOSED
            and session.actor_user_id != scope.actor_user_id
        ):
            raise NotFoundError("Conversation not found.")
        return session

    async def for_mutation(
        self, scope: AccessScope, workspace_id: str, project_id: str, mutation_id: str
    ) -> AssistSession | None:
        """The conversation a set of suggested changes came from, if it was kept."""
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._store.for_mutation(
            workspace_id=workspace_id, project_id=project_id, mutation_id=mutation_id
        )
