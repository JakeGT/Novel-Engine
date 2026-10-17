"""PostgreSQL store for the scene studio (ADR-022): which text holds a scene's prose,
drafts waiting to be kept, findings from a check with the assistant, and the author's
note on how they write."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.studio import AssistantFinding, SceneDraft

_DRAFT = (
    "SELECT workspace_id, project_id, id, branch_id, plan_id, job_id, manifest_id, number, "
    "content, model, status, kept_version_id, created_by, created_at FROM scene_drafts"
)
_FINDING = (
    "SELECT id, target_kind, target_id, job_id, category, message, suggestion, start_char, "
    "end_char, created_at FROM draft_findings"
)


def _draft(row: Any) -> SceneDraft:
    return SceneDraft(
        workspace_id=row[0],
        project_id=row[1],
        id=row[2],
        branch_id=row[3],
        plan_id=row[4],
        job_id=row[5],
        manifest_id=row[6],
        number=int(row[7]),
        content=row[8],
        model=row[9],
        status=row[10],
        kept_version_id=row[11],
        created_by=row[12],
        created_at=row[13],
    )


def _finding(row: Any) -> AssistantFinding:
    return AssistantFinding(
        id=row[0],
        target_kind=row[1],
        target_id=row[2],
        job_id=row[3],
        category=row[4],
        message=row[5],
        suggestion=row[6],
        start=row[7],
        end=row[8],
        created_at=row[9],
    )


class PostgresStudioStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------ manuscripts
    async def manuscript(
        self, *, workspace_id: str, project_id: str, branch_id: str, plan_id: str
    ) -> str | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT source_id FROM scene_manuscripts WHERE workspace_id = :w "
                        "AND project_id = :p AND branch_id = :b AND plan_id = :plan"
                    ),
                    {"w": workspace_id, "p": project_id, "b": branch_id, "plan": plan_id},
                )
            ).first()
        return str(row[0]) if row else None

    async def manuscripts_for(
        self, *, workspace_id: str, project_id: str, branch_id: str
    ) -> list[tuple[str, str]]:
        """Every (scene, text) link in one line of the story."""
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT plan_id, source_id FROM scene_manuscripts WHERE workspace_id = :w "
                        "AND project_id = :p AND branch_id = :b ORDER BY plan_id"
                    ),
                    {"w": workspace_id, "p": project_id, "b": branch_id},
                )
            ).all()
        return [(str(row[0]), str(row[1])) for row in rows]

    async def link(
        self, *, workspace_id: str, project_id: str, branch_id: str, plan_id: str, source_id: str
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "INSERT INTO scene_manuscripts (workspace_id, project_id, branch_id, plan_id, "
                    "source_id) VALUES (:w, :p, :b, :plan, :s) "
                    "ON CONFLICT (workspace_id, project_id, branch_id, plan_id) "
                    "DO UPDATE SET source_id = EXCLUDED.source_id"
                ),
                {
                    "w": workspace_id,
                    "p": project_id,
                    "b": branch_id,
                    "plan": plan_id,
                    "s": source_id,
                },
            )

    async def relink(
        self,
        *,
        workspace_id: str,
        project_id: str,
        from_source_id: str,
        to_source_id: str,
        except_branch_id: str,
    ) -> None:
        """Point every other line of the story that shares a text at its copy."""
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "UPDATE scene_manuscripts SET source_id = :to WHERE workspace_id = :w "
                    "AND project_id = :p AND source_id = :from AND branch_id <> :b"
                ),
                {
                    "w": workspace_id,
                    "p": project_id,
                    "to": to_source_id,
                    "from": from_source_id,
                    "b": except_branch_id,
                },
            )

    async def linked_elsewhere(
        self, *, workspace_id: str, project_id: str, branch_id: str, source_id: str
    ) -> bool:
        """Whether another line of the story uses the same text (so it must be copied first)."""
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT 1 FROM scene_manuscripts WHERE workspace_id = :w AND project_id = :p "
                        "AND source_id = :s AND branch_id <> :b LIMIT 1"
                    ),
                    {"w": workspace_id, "p": project_id, "s": source_id, "b": branch_id},
                )
            ).first()
        return row is not None

    # ----------------------------------------------------------------- drafts
    async def add_draft(self, draft: SceneDraft) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "INSERT INTO scene_drafts (workspace_id, project_id, id, branch_id, plan_id, "
                    "job_id, manifest_id, number, content, model, created_by) VALUES (:w, :p, :id, "
                    ":b, :plan, :job, :manifest, :n, :content, :model, :by)"
                ),
                {
                    "w": draft.workspace_id,
                    "p": draft.project_id,
                    "id": draft.id,
                    "b": draft.branch_id,
                    "plan": draft.plan_id,
                    "job": draft.job_id,
                    "manifest": draft.manifest_id,
                    "n": draft.number,
                    "content": draft.content,
                    "model": draft.model,
                    "by": draft.created_by,
                },
            )

    async def drafts(
        self, *, workspace_id: str, project_id: str, branch_id: str | None, plan_id: str
    ) -> list[SceneDraft]:
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        _DRAFT + " WHERE workspace_id = :w AND project_id = :p AND plan_id = :plan "
                        "AND branch_id IS NOT DISTINCT FROM :b AND status = 'proposed' "
                        "ORDER BY created_at, number"
                    ),
                    {"w": workspace_id, "p": project_id, "plan": plan_id, "b": branch_id},
                )
            ).all()
        return [_draft(row) for row in rows]

    async def get_draft(
        self, *, workspace_id: str, project_id: str, draft_id: str
    ) -> SceneDraft | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(_DRAFT + " WHERE workspace_id = :w AND project_id = :p AND id = :id"),
                    {"w": workspace_id, "p": project_id, "id": draft_id},
                )
            ).first()
        return _draft(row) if row else None

    async def drafts_for_job(
        self, *, workspace_id: str, project_id: str, job_id: str
    ) -> list[SceneDraft]:
        """Every draft one job has stored so far, waiting or kept."""
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        _DRAFT + " WHERE workspace_id = :w AND project_id = :p "
                        "AND job_id = :job ORDER BY number"
                    ),
                    {"w": workspace_id, "p": project_id, "job": job_id},
                )
            ).all()
        return [_draft(row) for row in rows]

    async def mark_kept(
        self, *, workspace_id: str, project_id: str, draft_id: str, version_id: str
    ) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "UPDATE scene_drafts SET status = 'kept', kept_version_id = :v, content = '' "
                    "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                ),
                {"w": workspace_id, "p": project_id, "id": draft_id, "v": version_id},
            )

    async def discard(self, *, workspace_id: str, project_id: str, draft_id: str) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "DELETE FROM draft_findings WHERE workspace_id = :w AND project_id = :p "
                    "AND target_kind = 'draft' AND target_id = :id"
                ),
                {"w": workspace_id, "p": project_id, "id": draft_id},
            )
            await db.execute(
                text(
                    "DELETE FROM scene_drafts WHERE workspace_id = :w AND project_id = :p "
                    "AND id = :id"
                ),
                {"w": workspace_id, "p": project_id, "id": draft_id},
            )

    # --------------------------------------------------------------- findings
    async def add_findings(
        self, *, workspace_id: str, project_id: str, findings: list[AssistantFinding]
    ) -> None:
        async with session_scope(self._session_factory) as db:
            for finding in findings:
                await db.execute(
                    text(
                        "INSERT INTO draft_findings (workspace_id, project_id, id, target_kind, "
                        "target_id, job_id, category, message, suggestion, start_char, end_char) "
                        "VALUES (:w, :p, :id, :kind, :target, :job, :category, :message, "
                        ":suggestion, :start, :end)"
                    ),
                    {
                        "w": workspace_id,
                        "p": project_id,
                        "id": finding.id,
                        "kind": finding.target_kind,
                        "target": finding.target_id,
                        "job": finding.job_id,
                        "category": finding.category,
                        "message": finding.message,
                        "suggestion": finding.suggestion,
                        "start": finding.start,
                        "end": finding.end,
                    },
                )

    async def findings(
        self, *, workspace_id: str, project_id: str, target_kind: str, target_id: str
    ) -> list[AssistantFinding]:
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        _FINDING + " WHERE workspace_id = :w AND project_id = :p "
                        "AND target_kind = :kind AND target_id = :target ORDER BY start_char NULLS LAST"
                    ),
                    {"w": workspace_id, "p": project_id, "kind": target_kind, "target": target_id},
                )
            ).all()
        return [_finding(row) for row in rows]

    # ------------------------------------------------------------------ voice
    async def voice(self, *, workspace_id: str, project_id: str) -> str:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        "SELECT notes FROM story_voices WHERE workspace_id = :w AND project_id = :p"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).first()
        return str(row[0]) if row else ""

    async def set_voice(self, *, workspace_id: str, project_id: str, notes: str) -> None:
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "INSERT INTO story_voices (workspace_id, project_id, notes) VALUES (:w, :p, :n) "
                    "ON CONFLICT (workspace_id, project_id) DO UPDATE SET notes = EXCLUDED.notes, "
                    "updated_at = now()"
                ),
                {"w": workspace_id, "p": project_id, "n": notes},
            )


__all__ = ["PostgresStudioStore"]
