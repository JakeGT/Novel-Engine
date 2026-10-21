"""What a change touches, before it is approved (CON-017).

Worked out on read from the plan, story rules, kept drafts, beliefs, events, pictures,
the studio's kept checks and notes, and the passage index.
Nothing here calls a model or changes the story.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.api.auth import get_project_scope
from backend.api.dependencies import get_container
from backend.container import Container
from backend.domain.identity import AccessScope
from backend.domain.impact import Dependent, Impact
from backend.services.impact_service import ImpactService

router = APIRouter(prefix="/projects/{project_id}", tags=["impact"])


def get_impact_service(container: Container = Depends(get_container)) -> ImpactService:
    """Built from the stores the container already has; it keeps nothing of its own."""
    return ImpactService(
        reader=container.canon_repository,
        mutation=container.mutation_service,
        journal=container.journal,
        rules=container.rules,
        sources=container.source_service,
        passage_store=container.passage_store,
        manifests=container.manifests,
        studio=container.studio_store,
        tenancy=container.tenancy_service,
        local_mode=not container.settings.is_hosted,
        runs=container.consistency_run_store,
        media=container.media_store,
    )


class DependentModel(BaseModel):
    # scene, plan, thread, rule, draft, belief (what a character believes or knows; ``id``
    # is the character), event, picture (``id`` is the picture), finding (checks and notes in
    # a scene's studio; ``plan_id`` is the scene), scene_text or text.
    kind: Literal[
        "scene",
        "plan",
        "thread",
        "rule",
        "draft",
        "belief",
        "event",
        "picture",
        "finding",
        "scene_text",
        "text",
    ]
    # affected: the story's own records name it. may_be_affected: writing mentions it.
    level: Literal["affected", "may_be_affected"]
    id: str
    title: str
    reasons: list[str]
    plan_id: str | None = None
    source_id: str | None = None
    version_id: str | None = None
    start: int | None = None
    end: int | None = None
    count: int = 0


class ImpactModel(BaseModel):
    """What depends on a change: affected, may be affected, and what was not checked."""

    about: list[str]
    affected: list[DependentModel]
    may_be_affected: list[DependentModel]
    # What the index does not cover. Never empty: the answer is not exhaustive.
    not_checked: list[str]


def _dependent(item: Dependent) -> DependentModel:
    return DependentModel(
        kind=item.kind,
        level=item.level,
        id=item.id,
        title=item.title,
        reasons=item.reasons,
        plan_id=item.plan_id,
        source_id=item.source_id,
        version_id=item.version_id,
        start=item.start,
        end=item.end,
        count=item.count,
    )


def _model(impact: Impact) -> ImpactModel:
    return ImpactModel(
        about=impact.about,
        affected=[_dependent(item) for item in impact.affected],
        may_be_affected=[_dependent(item) for item in impact.may_be_affected],
        not_checked=impact.not_checked,
    )


@router.get("/mutations/{mutation_id}/impact", response_model=ImpactModel)
async def mutation_impact(
    project_id: str,
    mutation_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: ImpactService = Depends(get_impact_service),
) -> ImpactModel:
    """What the changes still included in a proposal touch, in the proposal's line of the story."""
    return _model(await service.for_mutation(scope, mutation_id))


@router.get("/entities/{entity_id}/impact", response_model=ImpactModel)
async def entry_impact(
    project_id: str,
    entity_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: ImpactService = Depends(get_impact_service),
) -> ImpactModel:
    """What depends on one entry, so an edit can be weighed before it is saved."""
    return _model(await service.for_entry(scope, scope.workspace_id or "", project_id, entity_id))
