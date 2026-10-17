"""PostgreSQL store for story rules. A rule's wording is versioned; versions never change."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.rules import RuleBody, RuleExample, RuleStatus, StoryRule
from backend.domain.validation import RuleEnforcement

_SELECT = """
    SELECT r.workspace_id, r.project_id, r.id, r.title, r.sentence, r.status, v.number, v.body,
           v.examples, r.created_by, r.created_at, r.updated_at, r.enforcement
    FROM story_rules r
    JOIN story_rule_versions v ON v.workspace_id = r.workspace_id
     AND v.project_id = r.project_id AND v.rule_id = r.id
     AND v.number = (
        SELECT max(number) FROM story_rule_versions
        WHERE workspace_id = r.workspace_id AND project_id = r.project_id AND rule_id = r.id
     )
"""


def _rule(row: Any) -> StoryRule:
    return StoryRule(
        workspace_id=row[0],
        project_id=row[1],
        id=row[2],
        title=row[3],
        sentence=row[4],
        status=RuleStatus(row[5]),
        version=row[6],
        body=RuleBody.model_validate(row[7]),
        examples=[RuleExample.model_validate(item) for item in (row[8] or [])],
        created_by=row[9],
        created_at=row[10],
        updated_at=row[11],
        enforcement=RuleEnforcement(row[12]),
    )


class PostgresRuleStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def list_rules(self, *, workspace_id: str, project_id: str) -> list[StoryRule]:
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        _SELECT + " WHERE r.workspace_id = :ws AND r.project_id = :pj "
                        "ORDER BY r.created_at, r.id"
                    ),
                    {"ws": workspace_id, "pj": project_id},
                )
            ).all()
        return [_rule(row) for row in rows]

    async def get_rule(
        self, *, workspace_id: str, project_id: str, rule_id: str
    ) -> StoryRule | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        _SELECT + " WHERE r.workspace_id = :ws AND r.project_id = :pj "
                        "AND r.id = :id"
                    ),
                    {"ws": workspace_id, "pj": project_id, "id": rule_id},
                )
            ).first()
        return _rule(row) if row is not None else None

    async def list_versions(
        self, *, workspace_id: str, project_id: str, rule_id: str
    ) -> list[tuple[int, RuleBody, list[RuleExample]]]:
        """Every wording the rule has had, oldest first."""
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT number, body, examples FROM story_rule_versions "
                        "WHERE workspace_id = :ws AND project_id = :pj AND rule_id = :id "
                        "ORDER BY number"
                    ),
                    {"ws": workspace_id, "pj": project_id, "id": rule_id},
                )
            ).all()
        return [
            (
                row[0],
                RuleBody.model_validate(row[1]),
                [RuleExample.model_validate(item) for item in (row[2] or [])],
            )
            for row in rows
        ]

    async def add_rule(
        self,
        *,
        workspace_id: str,
        project_id: str,
        rule_id: str,
        title: str,
        sentence: str,
        body: RuleBody,
        examples: list[RuleExample],
        actor_user_id: str | None,
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "INSERT INTO story_rules (workspace_id, project_id, id, title, sentence, "
                    "created_by) VALUES (:ws, :pj, :id, :title, :sentence, :actor)"
                ),
                {
                    "ws": workspace_id,
                    "pj": project_id,
                    "id": rule_id,
                    "title": title,
                    "sentence": sentence,
                    "actor": actor_user_id,
                },
            )
            await self._version(
                db, workspace_id, project_id, rule_id, body, examples, actor_user_id
            )

    @staticmethod
    async def _version(
        db: Any,
        workspace_id: str,
        project_id: str,
        rule_id: str,
        body: RuleBody,
        examples: list[RuleExample],
        actor_user_id: str | None,
    ) -> None:
        await db.execute(
            text(
                """
                INSERT INTO story_rule_versions
                    (workspace_id, project_id, rule_id, number, body, examples, created_by)
                SELECT :ws, :pj, :id, coalesce(max(number), 0) + 1, CAST(:body AS jsonb),
                       CAST(:examples AS jsonb), :actor
                FROM story_rule_versions
                WHERE workspace_id = :ws AND project_id = :pj AND rule_id = :id
                """
            ),
            {
                "ws": workspace_id,
                "pj": project_id,
                "id": rule_id,
                "body": body.model_dump_json(),
                "examples": json.dumps([item.model_dump() for item in examples]),
                "actor": actor_user_id,
            },
        )

    async def change_rule(
        self,
        *,
        workspace_id: str,
        project_id: str,
        rule_id: str,
        title: str | None = None,
        sentence: str | None = None,
        status: RuleStatus | None = None,
        body: RuleBody | None = None,
        examples: list[RuleExample] | None = None,
        actor_user_id: str | None = None,
        enforcement: RuleEnforcement | None = None,
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    """
                    UPDATE story_rules SET
                        title = coalesce(:title, title),
                        sentence = coalesce(:sentence, sentence),
                        status = coalesce(:status, status),
                        enforcement = coalesce(:enforcement, enforcement),
                        updated_at = now()
                    WHERE workspace_id = :ws AND project_id = :pj AND id = :id
                    """
                ),
                {
                    "ws": workspace_id,
                    "pj": project_id,
                    "id": rule_id,
                    "title": title,
                    "sentence": sentence,
                    "status": status.value if status is not None else None,
                    "enforcement": enforcement.value if enforcement is not None else None,
                },
            )
            if body is not None:
                await self._version(
                    db, workspace_id, project_id, rule_id, body, examples or [], actor_user_id
                )

    async def remove_rule(self, *, workspace_id: str, project_id: str, rule_id: str) -> bool:
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    "DELETE FROM story_rules WHERE workspace_id = :ws AND project_id = :pj "
                    "AND id = :id"
                ),
                {"ws": workspace_id, "pj": project_id, "id": rule_id},
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    async def delete_project(self, workspace_id: str, project_id: str) -> int:
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text("DELETE FROM story_rules WHERE workspace_id = :ws AND project_id = :pj"),
                {"ws": workspace_id, "pj": project_id},
            )
            return result.rowcount or 0  # type: ignore[attr-defined]

    async def delete_workspace(self, workspace_id: str) -> int:
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text("DELETE FROM story_rules WHERE workspace_id = :ws"), {"ws": workspace_id}
            )
            return result.rowcount or 0  # type: ignore[attr-defined]
