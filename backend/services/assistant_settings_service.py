"""How much the assistant may do and spend in a story without being asked (CON-009, OPS-002,
AST-EXP-011).

A story's settings are chosen by anyone who can write in it; a workspace owner's ceilings
(the row with no story) apply to every story in the workspace, the lower of the two
winning. Nothing here calls a model.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from backend.domain.identity import AccessScope
from backend.domain.tenancy import Permission
from backend.domain.usage_controls import (
    AssistantSettings,
    Limits,
    limits_for,
    require_confirmation,
    require_within_conversation,
)

# Roughly the most a reply from the assistant takes: a set of questions or of suggested
# changes. An upper estimate, so the figure shown before sending is not an undercount.
REPLY_TOKENS = 2_000


class AssistantSettingsService:
    def __init__(self, store: Any, *, tenancy: Any = None, local_mode: bool = False) -> None:
        self._store = store
        self._tenancy = tenancy
        self._local_mode = local_mode

    async def _authorize(
        self, scope: AccessScope, workspace_id: str, project_id: str, permission: Permission
    ) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(scope, workspace_id, project_id, permission)

    async def for_story(self, workspace_id: str, project_id: str) -> AssistantSettings:
        """Callers have authorized reading the story."""
        return await self._store.get(workspace_id=workspace_id, project_id=project_id)  # type: ignore[no-any-return]

    async def story(
        self, scope: AccessScope, workspace_id: str, project_id: str
    ) -> AssistantSettings:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self.for_story(workspace_id, project_id)

    async def set_story(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        settings: AssistantSettings,
    ) -> AssistantSettings:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        settings = settings.validated()
        await self._store.set(
            workspace_id=workspace_id,
            project_id=project_id,
            settings=settings,
            actor_user_id=scope.actor_user_id,
        )
        return settings

    async def workspace(self, workspace_id: str) -> AssistantSettings:
        return await self._store.get(workspace_id=workspace_id, project_id="")  # type: ignore[no-any-return]

    async def set_workspace(
        self,
        workspace_id: str,
        *,
        confirm_above_tokens: int | None,
        conversation_tokens: int | None,
        actor_user_id: str | None,
    ) -> AssistantSettings:
        """An owner's ceilings for everyone. Callers have checked the person is an owner."""
        settings = replace(
            await self.workspace(workspace_id),
            confirm_above_tokens=confirm_above_tokens,
            conversation_tokens=conversation_tokens,
        ).validated()
        await self._store.set(
            workspace_id=workspace_id,
            project_id="",
            settings=settings,
            actor_user_id=actor_user_id,
        )
        return settings

    async def limits(self, workspace_id: str, project_id: str) -> Limits:
        return limits_for(
            await self.for_story(workspace_id, project_id), await self.workspace(workspace_id)
        )

    async def confirm(
        self,
        workspace_id: str,
        project_id: str,
        *,
        estimated_tokens: int,
        confirmed_tokens: int | None,
        doing: str,
    ) -> None:
        """Refuse a request over the threshold the author or the workspace set, unless the
        author confirmed at least its size."""
        found = await self.limits(workspace_id, project_id)
        require_confirmation(
            estimated_tokens=estimated_tokens,
            threshold=found.confirm_above_tokens,
            confirmed_tokens=confirmed_tokens,
            doing=doing,
        )


@dataclass
class AssistGuard:
    """One request to the assistant: counts what its calls used, for its conversation."""

    sessions: Any
    workspace_id: str
    project_id: str
    estimated_tokens: int = 0
    used: int = 0

    def counting(self, sink: Any) -> Any:
        async def counted(ai_usage: Any, model: str, *args: Any, **kwargs: Any) -> None:
            self.used += int(ai_usage.prompt_tokens) + int(ai_usage.completion_tokens)
            await sink(ai_usage, model, *args, **kwargs)

        return counted

    async def record(self, session_id: str | None) -> None:
        """Add what this request used to its conversation's count."""
        if not session_id or not self.used or self.sessions is None:
            return
        await self.sessions.add_tokens(
            workspace_id=self.workspace_id,
            project_id=self.project_id,
            session_id=session_id,
            tokens=self.used,
        )
        self.used = 0


class AssistBudget:
    """The confirmation threshold and the conversation budget, for requests answered at once."""

    def __init__(self, settings: AssistantSettingsService, sessions: Any = None) -> None:
        self._settings = settings
        self._sessions = sessions

    async def conversation_used(
        self, scope: AccessScope, workspace_id: str, project_id: str, session_id: str | None
    ) -> int:
        """What one of the person's own conversations has used; 0 for anyone else's."""
        if not session_id or self._sessions is None:
            return 0
        session = await self._sessions.get(
            workspace_id=workspace_id, project_id=project_id, session_id=session_id
        )
        if session is None or session.actor_user_id != scope.actor_user_id:
            return 0
        return int(
            await self._sessions.tokens_used(
                workspace_id=workspace_id, project_id=project_id, session_id=session_id
            )
        )

    async def check(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        reads: Callable[[], Awaitable[int]],
        doing: str,
        confirmed_tokens: int | None = None,
        session_id: str | None = None,
    ) -> AssistGuard:
        """Before a reply: refused over the conversation's budget, and over the threshold
        unless confirmed. ``reads`` works out what the request reads; it is asked only when
        a limit is set, so a story with none pays nothing for the estimate."""
        guard = AssistGuard(self._sessions, workspace_id, project_id)
        found = await self._settings.limits(workspace_id, project_id)
        if found.confirm_above_tokens is None and found.conversation_tokens is None:
            return guard
        guard.estimated_tokens = await reads() + REPLY_TOKENS
        require_confirmation(
            estimated_tokens=guard.estimated_tokens,
            threshold=found.confirm_above_tokens,
            confirmed_tokens=confirmed_tokens,
            doing=doing,
        )
        require_within_conversation(
            used=await self.conversation_used(scope, workspace_id, project_id, session_id),
            estimated_tokens=guard.estimated_tokens,
            budget=found.conversation_tokens,
        )
        return guard


__all__ = ["REPLY_TOKENS", "AssistBudget", "AssistGuard", "AssistantSettingsService"]
