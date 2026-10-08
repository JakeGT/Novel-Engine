"""Canon revision history, per-record history, state-at-revision, and tags (2H)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_canon_history_service
from backend.domain.identity import AccessScope
from backend.services.canon_history_service import CanonHistoryService

router = APIRouter(prefix="/projects/{project_id}", tags=["canon-history"])


def _history_entry(entry) -> dict:
    return {
        "record_type": entry.record_type,
        "record_id": entry.record_id,
        "revision": entry.revision,
        "operation_type": entry.operation_type,
        "commit_id": entry.commit_id,
        "change": entry.change,
        "created_at": entry.created_at,
    }


def _revision(rev) -> dict:
    return {
        "revision": rev.revision,
        "commit_id": rev.commit_id,
        "expected_head": rev.expected_head,
        "result_head": rev.result_head,
        "schema_version_id": rev.schema_version_id,
        "envelope_checksum": rev.envelope_checksum,
        "operation_count": rev.operation_count,
        "created_at": rev.created_at,
    }


@router.get("/revisions")
async def list_revisions(
    project_id: str,
    limit: int = 50,
    offset: int = 0,
    scope: AccessScope = Depends(get_project_scope),
    history: CanonHistoryService = Depends(get_canon_history_service),
) -> dict:
    revisions = await history.list_revisions(
        scope, scope.workspace_id or "", project_id, limit=limit, offset=offset
    )
    return {"items": [_revision(r) for r in revisions]}


@router.get("/revisions/{commit_id}")
async def get_revision(
    project_id: str,
    commit_id: str,
    scope: AccessScope = Depends(get_project_scope),
    history: CanonHistoryService = Depends(get_canon_history_service),
) -> dict:
    rev = await history.get_revision(scope, scope.workspace_id or "", project_id, commit_id)
    return _revision(rev)


async def _record_history(
    project_id: str, record_type: str, record_id: str, scope, history
) -> dict:
    entries = await history.record_history(
        scope,
        scope.workspace_id or "",
        project_id,
        record_type=record_type,
        record_id=record_id,
    )
    return {"items": [_history_entry(e) for e in entries]}


@router.get("/entities/{entity_id}/history")
async def entity_history(
    project_id: str,
    entity_id: str,
    scope: AccessScope = Depends(get_project_scope),
    history: CanonHistoryService = Depends(get_canon_history_service),
) -> dict:
    return await _record_history(project_id, "entity", entity_id, scope, history)


@router.get("/facts/{fact_id}/history")
async def fact_history(
    project_id: str,
    fact_id: str,
    scope: AccessScope = Depends(get_project_scope),
    history: CanonHistoryService = Depends(get_canon_history_service),
) -> dict:
    return await _record_history(project_id, "fact", fact_id, scope, history)


@router.get("/beliefs/{belief_id}/history")
async def belief_history(
    project_id: str,
    belief_id: str,
    scope: AccessScope = Depends(get_project_scope),
    history: CanonHistoryService = Depends(get_canon_history_service),
) -> dict:
    return await _record_history(project_id, "belief", belief_id, scope, history)


@router.get("/events/{event_id}/history")
async def event_history(
    project_id: str,
    event_id: str,
    scope: AccessScope = Depends(get_project_scope),
    history: CanonHistoryService = Depends(get_canon_history_service),
) -> dict:
    return await _record_history(project_id, "event", event_id, scope, history)


@router.get("/entities/{entity_id}/at-revision/{revision}")
async def entity_at_revision(
    project_id: str,
    entity_id: str,
    revision: int,
    scope: AccessScope = Depends(get_project_scope),
    history: CanonHistoryService = Depends(get_canon_history_service),
) -> dict:
    entity = await history.entity_at_revision(
        scope, scope.workspace_id or "", project_id, entity_id, revision
    )
    return {
        "id": entity.id,
        "type": entity.type,
        "name": entity.name,
        "slug": entity.slug,
        "lifecycle": entity.lifecycle.value,
        "created_revision": entity.created_revision,
        "retired_revision": entity.retired_revision,
        "attributes": entity.attributes,
    }


@router.get("/revisions/{from_revision}/diff/{to_revision}")
async def revision_diff(
    project_id: str,
    from_revision: int,
    to_revision: int,
    scope: AccessScope = Depends(get_project_scope),
    history: CanonHistoryService = Depends(get_canon_history_service),
) -> dict:
    entries = await history.diff_revisions(
        scope,
        scope.workspace_id or "",
        project_id,
        from_revision=from_revision,
        to_revision=to_revision,
    )
    return {"items": [_history_entry(e) for e in entries]}


@router.get("/facts/{fact_id}/at-revision/{revision}")
async def fact_at_revision(
    project_id: str,
    fact_id: str,
    revision: int,
    scope: AccessScope = Depends(get_project_scope),
    history: CanonHistoryService = Depends(get_canon_history_service),
) -> dict:
    fact = await history.fact_at_revision(
        scope, scope.workspace_id or "", project_id, fact_id, revision
    )
    return {
        "id": fact.id,
        "proposition": fact.proposition.model_dump(mode="json"),
        "status": fact.status.value,
        "confidence": fact.confidence,
        "provenance_id": fact.provenance_id,
        "created_revision": fact.created_revision,
        "closed_revision": fact.closed_revision,
        "valid_from": fact.valid_from,
        "valid_until": fact.valid_until,
    }


@router.get("/beliefs/{belief_id}/at-revision/{revision}")
async def belief_at_revision(
    project_id: str,
    belief_id: str,
    revision: int,
    scope: AccessScope = Depends(get_project_scope),
    history: CanonHistoryService = Depends(get_canon_history_service),
) -> dict:
    belief = await history.belief_at_revision(
        scope, scope.workspace_id or "", project_id, belief_id, revision
    )
    return {
        "id": belief.id,
        "holder_entity_id": belief.holder_entity_id,
        "proposition": belief.proposition.model_dump(mode="json"),
        "confidence": belief.confidence,
        "acquisition_provenance_id": belief.acquisition_provenance_id,
        "source_ref": belief.source_ref,
        "created_revision": belief.created_revision,
        "closed_revision": belief.closed_revision,
    }


@router.get("/tags")
async def list_tags(
    project_id: str,
    scope: AccessScope = Depends(get_project_scope),
    history: CanonHistoryService = Depends(get_canon_history_service),
) -> dict:
    tags = await history.list_tags(scope, scope.workspace_id or "", project_id)
    return {
        "items": [
            {
                "name": t.name,
                "revision": t.revision,
                "commit_id": t.commit_id,
                "created_by_user_id": t.created_by_user_id,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
            }
            for t in tags
        ]
    }


@router.post("/tags", status_code=201)
async def set_tag(
    project_id: str,
    body: dict,
    scope: AccessScope = Depends(require_project_scope),
    history: CanonHistoryService = Depends(get_canon_history_service),
) -> dict:
    name = str(body.get("name", "")).strip()
    revision = body.get("revision")
    tag = await history.set_tag(
        scope,
        scope.workspace_id or "",
        project_id,
        name=name,
        revision=int(revision) if revision is not None else None,
    )
    return {
        "name": tag.name,
        "revision": tag.revision,
        "commit_id": tag.commit_id,
        "created_at": tag.created_at,
    }


@router.delete("/tags/{name}", status_code=204)
async def delete_tag(
    project_id: str,
    name: str,
    scope: AccessScope = Depends(require_project_scope),
    history: CanonHistoryService = Depends(get_canon_history_service),
) -> Response:
    await history.delete_tag(scope, scope.workspace_id or "", project_id, name)
    return Response(status_code=204)


@router.get("/tags/{name}/history")
async def tag_history(
    project_id: str,
    name: str,
    scope: AccessScope = Depends(get_project_scope),
    history: CanonHistoryService = Depends(get_canon_history_service),
) -> dict:
    entries = await history.tag_history(scope, scope.workspace_id or "", project_id, name)
    return {
        "items": [
            {
                "name": e.name,
                "revision": e.revision,
                "commit_id": e.commit_id,
                "replaced_at": e.replaced_at,
            }
            for e in entries
        ]
    }
