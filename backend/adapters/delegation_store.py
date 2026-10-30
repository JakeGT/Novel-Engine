"""PostgreSQL store for "keep the safe ones for me" (AST-EXP-015, OSS-007; migration 0042)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.delegation import DelegationRecord, DelegationSettings, RuleState

# The workspace's own row has no story.
WORKSPACE = ""


def settings_from_row(rules: Any, allow_clients: Any, forbidden: Any) -> DelegationSettings:
    loaded = rules if isinstance(rules, dict) else json.loads(rules or "{}")
    return DelegationSettings(
        rules={
            kind: RuleState(since=datetime.fromisoformat(value["since"]), by=value.get("by"))
            for kind, value in loaded.items()
        },
        allow_clients=bool(allow_clients),
        forbidden=bool(forbidden),
    )


def rules_json(rules: dict[str, RuleState]) -> str:
    return json.dumps(
        {kind: {"since": state.since.isoformat(), "by": state.by} for kind, state in rules.items()},
        sort_keys=True,
    )


_RECORD_COLUMNS = (
    "workspace_id, project_id, mutation_id, how, state, kinds, branch_id, actor_user_id, "
    "revision, undo_mutation_id, created_at, via, reason_code, reason"
)


def _record(row: Any) -> DelegationRecord:
    return DelegationRecord(
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        mutation_id=row.mutation_id,
        how=row.how,
        state=row.state,
        kinds=tuple(row.kinds or ()),
        branch_id=row.branch_id,
        actor_user_id=row.actor_user_id,
        revision=row.revision,
        undo_mutation_id=row.undo_mutation_id,
        created_at=row.created_at,
        via=row.via,
        reason_code=row.reason_code,
        reason=row.reason,
    )


class PostgresDelegationStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def get(self, *, workspace_id: str, project_id: str = WORKSPACE) -> DelegationSettings:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT rules, allow_clients, forbidden FROM delegation_settings "
                        "WHERE workspace_id = :w AND project_id = :p"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).first()
        if row is None:
            return DelegationSettings()
        return settings_from_row(row.rules, row.allow_clients, row.forbidden)

    async def set(
        self,
        *,
        workspace_id: str,
        project_id: str = WORKSPACE,
        settings: DelegationSettings,
        actor_user_id: str | None,
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    """
                    INSERT INTO delegation_settings (
                        workspace_id, project_id, rules, allow_clients, forbidden, updated_by
                    ) VALUES (:w, :p, CAST(:rules AS JSONB), :clients, :forbidden, :by)
                    ON CONFLICT (workspace_id, project_id) DO UPDATE SET
                        rules = EXCLUDED.rules,
                        allow_clients = EXCLUDED.allow_clients,
                        forbidden = EXCLUDED.forbidden,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = now()
                    """
                ),
                {
                    "w": workspace_id,
                    "p": project_id,
                    "rules": rules_json(settings.rules),
                    "clients": settings.allow_clients,
                    "forbidden": settings.forbidden,
                    "by": actor_user_id,
                },
            )

    async def hand_counts(self, *, workspace_id: str, project_id: str) -> dict[str, int]:
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT kind, count(*) AS n FROM delegation_records, unnest(kinds) AS kind "
                        "WHERE workspace_id = :w AND project_id = :p AND how = 'by_hand' "
                        "GROUP BY kind"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).all()
        return {row.kind: int(row.n) for row in rows}

    async def get_record(
        self, *, workspace_id: str, project_id: str, mutation_id: str
    ) -> DelegationRecord | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        f"SELECT {_RECORD_COLUMNS} FROM delegation_records "
                        "WHERE workspace_id = :w AND project_id = :p AND mutation_id = :m"
                    ),
                    {"w": workspace_id, "p": project_id, "m": mutation_id},
                )
            ).first()
        return None if row is None else _record(row)

    async def put_record(
        self,
        *,
        workspace_id: str,
        project_id: str,
        mutation_id: str,
        how: str,
        state: str,
        kinds: Sequence[str],
        branch_id: str,
        actor_user_id: str | None,
        via: str = "session",
        reason_code: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Insert or replace how a suggestion was decided. ``created_at`` is when it was
        first decided, except that being kept starts the clock again, so "kept since you
        last looked" counts from the keeping."""
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    """
                    INSERT INTO delegation_records (
                        workspace_id, project_id, mutation_id, how, state, kinds, branch_id,
                        actor_user_id, via, reason_code, reason
                    ) VALUES (:w, :p, :m, :how, :state, :kinds, :b, :by, :via, :code, :reason)
                    ON CONFLICT (workspace_id, project_id, mutation_id) DO UPDATE SET
                        how = EXCLUDED.how,
                        state = EXCLUDED.state,
                        kinds = EXCLUDED.kinds,
                        branch_id = EXCLUDED.branch_id,
                        actor_user_id = EXCLUDED.actor_user_id,
                        via = EXCLUDED.via,
                        reason_code = EXCLUDED.reason_code,
                        reason = EXCLUDED.reason,
                        created_at = CASE WHEN EXCLUDED.how = 'kept' THEN now()
                                          ELSE delegation_records.created_at END,
                        updated_at = now()
                    """
                ),
                {
                    "w": workspace_id,
                    "p": project_id,
                    "m": mutation_id,
                    "how": how,
                    "state": state,
                    "kinds": list(kinds),
                    "b": branch_id,
                    "by": actor_user_id,
                    "via": via,
                    "code": reason_code,
                    "reason": reason,
                },
            )

    async def update_record(
        self,
        *,
        workspace_id: str,
        project_id: str,
        mutation_id: str,
        state: str | None = None,
        revision: int | None = None,
        undo_mutation_id: str | None = None,
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    """
                    UPDATE delegation_records SET
                        state = COALESCE(:state, state),
                        revision = COALESCE(:revision, revision),
                        undo_mutation_id = COALESCE(:undo, undo_mutation_id),
                        updated_at = now()
                    WHERE workspace_id = :w AND project_id = :p AND mutation_id = :m
                    """
                ),
                {
                    "w": workspace_id,
                    "p": project_id,
                    "m": mutation_id,
                    "state": state,
                    "revision": revision,
                    "undo": undo_mutation_id,
                },
            )

    async def list_records(
        self,
        *,
        workspace_id: str,
        project_id: str | None,
        how: str,
        states: Sequence[str],
        limit: int = 100,
        offset: int = 0,
    ) -> list[DelegationRecord]:
        """Newest first. ``project_id`` None means every story in the workspace."""
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        f"SELECT {_RECORD_COLUMNS} FROM delegation_records "
                        "WHERE workspace_id = :w AND (CAST(:p AS TEXT) IS NULL OR project_id = :p) "
                        "AND how = :how AND state = ANY(:states) "
                        "ORDER BY created_at DESC, mutation_id LIMIT :limit OFFSET :offset"
                    ),
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "how": how,
                        "states": list(states),
                        "limit": limit,
                        "offset": offset,
                    },
                )
            ).all()
        return [_record(row) for row in rows]

    async def count_kept_since(
        self, *, workspace_id: str, project_id: str, since: datetime | None
    ) -> int:
        async with session_scope(self._session_factory) as db:
            value = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM delegation_records WHERE workspace_id = :w "
                        "AND project_id = :p AND how = 'kept' AND state = 'kept' "
                        "AND (CAST(:since AS TIMESTAMPTZ) IS NULL OR created_at > :since)"
                    ),
                    {"w": workspace_id, "p": project_id, "since": since},
                )
            ).scalar_one()
        return int(value)

    async def get_seen(
        self, *, workspace_id: str, project_id: str, user_id: str
    ) -> datetime | None:
        async with session_scope(self._session_factory) as db:
            value = (
                await db.execute(
                    text(
                        "SELECT seen_at FROM delegation_seen WHERE workspace_id = :w "
                        "AND project_id = :p AND user_id = :u"
                    ),
                    {"w": workspace_id, "p": project_id, "u": user_id},
                )
            ).scalar_one_or_none()
        return value  # type: ignore[no-any-return]

    async def set_seen(self, *, workspace_id: str, project_id: str, user_id: str) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    """
                    INSERT INTO delegation_seen (workspace_id, project_id, user_id)
                    VALUES (:w, :p, :u)
                    ON CONFLICT (workspace_id, project_id, user_id) DO UPDATE SET seen_at = now()
                    """
                ),
                {"w": workspace_id, "p": project_id, "u": user_id},
            )


__all__ = ["WORKSPACE", "DelegationRecord", "PostgresDelegationStore", "settings_from_row"]
