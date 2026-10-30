"""PostgreSQL store for what a model saw in a picture (AST-005) and each picture's
choice for the assistant's context (AST-007). The words are the author's data: they are
stored here and archived, never logged."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text

from backend.core.database import session_scope
from backend.domain.context_parts import DEFAULT_CHOICE, ContextChoice
from backend.domain.media_observations import MediaObservation, Region

_COLUMNS = (
    "workspace_id, project_id, id, asset_id, asset_version_id, segment_id, kind, text, "
    "subject_record_type, subject_record_id, region, confidence, model, prompt_name, "
    "prompt_version, job_id, state, corrected_text, reviewed_by, reviewed_at, superseded_by, "
    "proposal_mutation_set_id, created_at"
)


def _observation(row: Any) -> MediaObservation:
    region = row.region
    return MediaObservation(
        workspace_id=row.workspace_id,
        project_id=row.project_id,
        id=row.id,
        asset_id=row.asset_id,
        asset_version_id=row.asset_version_id,
        segment_id=row.segment_id,
        kind=row.kind,
        text=row.text,
        subject_record_type=row.subject_record_type,
        subject_record_id=row.subject_record_id,
        region=Region.model_validate(region) if region else None,
        confidence=row.confidence,
        model=row.model,
        prompt_name=row.prompt_name,
        prompt_version=row.prompt_version,
        job_id=row.job_id,
        state=row.state,
        corrected_text=row.corrected_text,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        superseded_by=row.superseded_by,
        proposal_mutation_set_id=row.proposal_mutation_set_id,
        created_at=row.created_at,
    )


def _params(observation: MediaObservation) -> dict[str, Any]:
    return {
        "w": observation.workspace_id,
        "p": observation.project_id,
        "id": observation.id,
        "asset_id": observation.asset_id,
        "asset_version_id": observation.asset_version_id,
        "segment_id": observation.segment_id,
        "kind": observation.kind.value,
        "text": observation.text,
        "subject_record_type": observation.subject_record_type,
        "subject_record_id": observation.subject_record_id,
        "region": observation.region.model_dump_json() if observation.region else None,
        "confidence": observation.confidence,
        "model": observation.model,
        "prompt_name": observation.prompt_name,
        "prompt_version": observation.prompt_version,
        "job_id": observation.job_id,
        "state": observation.state.value,
        "corrected_text": observation.corrected_text,
        "reviewed_by": observation.reviewed_by,
        "reviewed_at": observation.reviewed_at,
        "superseded_by": observation.superseded_by,
        "proposal_mutation_set_id": observation.proposal_mutation_set_id,
        "created_at": observation.created_at,
    }


class PostgresObservationStore:
    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory

    async def add(self, observations: Sequence[MediaObservation]) -> None:
        """Store new observations; one already stored under the same id is kept as it is
        (a job taken over after a lost lease writes the same ids again)."""
        if not observations:
            return
        async with session_scope(self._session_factory) as db:
            for observation in observations:
                await db.execute(
                    text(
                        f"INSERT INTO media_observations ({_COLUMNS}) VALUES (:w, :p, :id, "  # noqa: S608 - fixed columns
                        ":asset_id, :asset_version_id, :segment_id, :kind, :text, "
                        ":subject_record_type, :subject_record_id, CAST(:region AS JSONB), "
                        ":confidence, :model, :prompt_name, :prompt_version, :job_id, :state, "
                        ":corrected_text, :reviewed_by, :reviewed_at, :superseded_by, "
                        ":proposal_mutation_set_id, :created_at) "
                        "ON CONFLICT (workspace_id, project_id, id) DO NOTHING"
                    ),
                    _params(observation),
                )

    async def save(self, observation: MediaObservation) -> None:
        """Write a decision: state, correction, who and when, supersession, proposal."""
        async with session_scope(self._session_factory) as db:
            await db.execute(
                text(
                    "UPDATE media_observations SET state = :state, "
                    "corrected_text = :corrected_text, reviewed_by = :reviewed_by, "
                    "reviewed_at = :reviewed_at, superseded_by = :superseded_by, "
                    "proposal_mutation_set_id = :proposal_mutation_set_id "
                    "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                ),
                _params(observation),
            )

    async def get(
        self, *, workspace_id: str, project_id: str, observation_id: str
    ) -> MediaObservation | None:
        async with session_scope(self._session_factory) as db:
            row = (
                await db.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM media_observations "  # noqa: S608 - fixed columns
                        "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                    ),
                    {"w": workspace_id, "p": project_id, "id": observation_id},
                )
            ).first()
        return _observation(row) if row else None

    async def for_assets(
        self, *, workspace_id: str, project_id: str, asset_ids: Sequence[str]
    ) -> list[MediaObservation]:
        """Every observation of these pictures, oldest first."""
        if not asset_ids:
            return []
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM media_observations "  # noqa: S608 - fixed columns
                        "WHERE workspace_id = :w AND project_id = :p "
                        "AND asset_id = ANY(:ids) ORDER BY seq"
                    ),
                    {"w": workspace_id, "p": project_id, "ids": list(asset_ids)},
                )
            ).all()
        return [_observation(row) for row in rows]

    async def delete_for_asset(self, *, workspace_id: str, project_id: str, asset_id: str) -> int:
        """Remove what a model said about one picture (AST-008, once it is deleted)."""
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    "DELETE FROM media_observations "
                    "WHERE workspace_id = :w AND project_id = :p AND asset_id = :a"
                ),
                {"w": workspace_id, "p": project_id, "a": asset_id},
            )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def all(self, *, workspace_id: str, project_id: str) -> list[MediaObservation]:
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        f"SELECT {_COLUMNS} FROM media_observations "  # noqa: S608 - fixed columns
                        "WHERE workspace_id = :w AND project_id = :p ORDER BY seq"
                    ),
                    {"w": workspace_id, "p": project_id},
                )
            ).all()
        return [_observation(row) for row in rows]

    async def get_choices(
        self, *, workspace_id: str, project_id: str, asset_ids: Sequence[str]
    ) -> dict[str, ContextChoice]:
        """Each picture's choice for the assistant's context; a picture not listed has
        the default (its description)."""
        if not asset_ids:
            return {}
        async with session_scope(self._session_factory) as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT id, context_choice FROM media_assets "
                        "WHERE workspace_id = :w AND project_id = :p AND id = ANY(:ids)"
                    ),
                    {"w": workspace_id, "p": project_id, "ids": list(asset_ids)},
                )
            ).all()
        found: dict[str, ContextChoice] = {}
        for row in rows:
            try:
                found[row.id] = ContextChoice(row.context_choice)
            except ValueError:
                found[row.id] = DEFAULT_CHOICE
        return found

    async def set_choice(
        self, *, workspace_id: str, project_id: str, asset_id: str, choice: ContextChoice
    ) -> bool:
        async with session_scope(self._session_factory) as db:
            result = await db.execute(
                text(
                    "UPDATE media_assets SET context_choice = :choice "
                    "WHERE workspace_id = :w AND project_id = :p AND id = :id"
                ),
                {"w": workspace_id, "p": project_id, "id": asset_id, "choice": choice.value},
            )
        return bool(getattr(result, "rowcount", 0))


def dumps(value: Any) -> str:
    return json.dumps(value)


__all__ = ["PostgresObservationStore"]
