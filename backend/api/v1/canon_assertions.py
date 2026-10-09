"""Fact, belief, and event canon APIs.

Product-scoped authoring over the atomic canonical mutation path. Reads accepted canon;
writes require project-write + CSRF. Truth agreement is a derived query result.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, ConfigDict

from backend.api.auth import get_project_scope, require_project_scope
from backend.api.dependencies import get_canon_assertion_service
from backend.domain.canon.assertions import Belief, Fact
from backend.domain.canon.events import Event, EventParticipant
from backend.domain.canon.propositions import Proposition
from backend.domain.identity import AccessScope
from backend.services.canon_assertion_service import CanonAssertionService

router = APIRouter(prefix="/projects/{project_id}", tags=["canon-assertions"])


class PropositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_id: str
    predicate: str
    object: dict

    def to_proposition(self) -> Proposition:
        return Proposition.model_validate(
            {"subject_id": self.subject_id, "predicate": self.predicate, "object": self.object}
        )


class AssertFactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposition: PropositionRequest
    expected_revision: int
    confidence: float = 1.0
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class CloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int


class BeliefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    holder_entity_id: str
    proposition: PropositionRequest
    source_ref: str
    expected_revision: int
    confidence: float = 1.0


class EventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    name: str
    expected_revision: int
    participants: list[EventParticipant] = []
    location_ids: list[str] = []
    cause_ids: list[str] = []
    outcome_ids: list[str] = []
    world_position: str | None = None


class AmendEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int
    name: str | None = None
    world_position: str | None = None


class FactModel(BaseModel):
    id: str
    subject_id: str
    predicate: str
    object: dict
    status: str
    confidence: float
    provenance_id: str
    source_ref: str | None = None
    created_revision: int
    closed_revision: int | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None


class BeliefModel(BaseModel):
    id: str
    holder_entity_id: str
    subject_id: str
    predicate: str
    object: dict
    confidence: float
    source_ref: str | None = None
    acquisition_provenance_id: str
    created_revision: int
    closed_revision: int | None = None


class EventModel(BaseModel):
    id: str
    kind: str
    name: str
    participants: list[EventParticipant]
    location_ids: list[str]
    cause_ids: list[str]
    outcome_ids: list[str]
    world_position: str | None = None
    provenance_id: str
    created_revision: int


def _fact_model(fact: Fact) -> FactModel:
    return FactModel(
        id=fact.id,
        subject_id=fact.proposition.subject_id,
        predicate=fact.proposition.predicate,
        object=fact.proposition.object.model_dump(mode="json"),
        status=fact.status.value,
        confidence=fact.confidence,
        provenance_id=fact.provenance_id,
        source_ref=None,
        created_revision=fact.created_revision,
        closed_revision=fact.closed_revision,
        valid_from=fact.valid_from,
        valid_until=fact.valid_until,
    )


def _belief_model(belief: Belief) -> BeliefModel:
    return BeliefModel(
        id=belief.id,
        holder_entity_id=belief.holder_entity_id,
        subject_id=belief.proposition.subject_id,
        predicate=belief.proposition.predicate,
        object=belief.proposition.object.model_dump(mode="json"),
        confidence=belief.confidence,
        source_ref=belief.source_ref,
        acquisition_provenance_id=belief.acquisition_provenance_id,
        created_revision=belief.created_revision,
        closed_revision=belief.closed_revision,
    )


def _event_model(event: Event) -> EventModel:
    return EventModel(
        id=event.id,
        kind=event.kind,
        name=event.name,
        participants=event.participants,
        location_ids=event.location_ids,
        cause_ids=event.cause_ids,
        outcome_ids=event.outcome_ids,
        world_position=event.world_position,
        provenance_id=event.provenance_id,
        created_revision=event.created_revision,
    )


# --- facts ---


@router.get("/facts")
async def list_facts(
    project_id: str,
    include_closed: bool = False,
    subject_id: str | None = None,
    predicate: str | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    scope: AccessScope = Depends(get_project_scope),
    service: CanonAssertionService = Depends(get_canon_assertion_service),
) -> dict:
    facts = await service.list_facts(
        scope,
        scope.workspace_id or "",
        project_id,
        include_closed=include_closed,
        subject_id=subject_id,
        predicate=predicate,
        limit=limit,
        offset=offset,
    )
    return {"items": [_fact_model(f) for f in facts], "limit": limit, "offset": offset}


@router.post("/facts", status_code=status.HTTP_201_CREATED)
async def assert_fact(
    project_id: str,
    body: AssertFactRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: CanonAssertionService = Depends(get_canon_assertion_service),
) -> FactModel:
    result, fact = await service.assert_fact(
        scope,
        scope.workspace_id or "",
        project_id,
        proposition=body.proposition.to_proposition(),
        expected_revision=body.expected_revision,
        confidence=body.confidence,
        valid_from=body.valid_from,
        valid_until=body.valid_until,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _fact_model(fact)


@router.get("/facts/{fact_id}")
async def get_fact(
    project_id: str,
    fact_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: CanonAssertionService = Depends(get_canon_assertion_service),
) -> FactModel:
    fact = await service.get_fact(scope, scope.workspace_id or "", project_id, fact_id)
    return _fact_model(fact)


@router.post("/facts/{fact_id}/close")
async def close_fact(
    project_id: str,
    fact_id: str,
    body: CloseRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: CanonAssertionService = Depends(get_canon_assertion_service),
) -> FactModel:
    result, fact = await service.close_fact(
        scope,
        scope.workspace_id or "",
        project_id,
        fact_id,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _fact_model(fact)


# --- beliefs ---


@router.get("/beliefs")
async def list_beliefs(
    project_id: str,
    holder_entity_id: str | None = None,
    include_closed: bool = False,
    limit: int = Query(50, le=200),
    offset: int = 0,
    scope: AccessScope = Depends(get_project_scope),
    service: CanonAssertionService = Depends(get_canon_assertion_service),
) -> dict:
    beliefs = await service.list_beliefs(
        scope,
        scope.workspace_id or "",
        project_id,
        holder_entity_id=holder_entity_id,
        include_closed=include_closed,
        limit=limit,
        offset=offset,
    )
    return {"items": [_belief_model(b) for b in beliefs], "limit": limit, "offset": offset}


@router.post("/beliefs", status_code=status.HTTP_201_CREATED)
async def assert_belief(
    project_id: str,
    body: BeliefRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: CanonAssertionService = Depends(get_canon_assertion_service),
) -> BeliefModel:
    result, belief = await service.assert_belief(
        scope,
        scope.workspace_id or "",
        project_id,
        holder_entity_id=body.holder_entity_id,
        proposition=body.proposition.to_proposition(),
        source_ref=body.source_ref,
        expected_revision=body.expected_revision,
        confidence=body.confidence,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _belief_model(belief)


@router.get("/beliefs/{belief_id}")
async def get_belief(
    project_id: str,
    belief_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: CanonAssertionService = Depends(get_canon_assertion_service),
) -> BeliefModel:
    belief = await service.get_belief(scope, scope.workspace_id or "", project_id, belief_id)
    return _belief_model(belief)


@router.get("/beliefs/{belief_id}/truth-agreement")
async def belief_truth_agreement(
    project_id: str,
    belief_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: CanonAssertionService = Depends(get_canon_assertion_service),
) -> dict:
    agreement = await service.truth_agreement(
        scope, scope.workspace_id or "", project_id, belief_id
    )
    return {"belief_id": belief_id, "truth_agreement": agreement.value}


@router.post("/beliefs/{belief_id}/close")
async def close_belief(
    project_id: str,
    belief_id: str,
    body: CloseRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: CanonAssertionService = Depends(get_canon_assertion_service),
) -> BeliefModel:
    result, belief = await service.close_belief(
        scope,
        scope.workspace_id or "",
        project_id,
        belief_id,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _belief_model(belief)


# --- events ---


@router.get("/events")
async def list_events(
    project_id: str,
    include_retired: bool = False,
    limit: int = Query(50, le=200),
    offset: int = 0,
    scope: AccessScope = Depends(get_project_scope),
    service: CanonAssertionService = Depends(get_canon_assertion_service),
) -> dict:
    events = await service.list_events(
        scope,
        scope.workspace_id or "",
        project_id,
        include_retired=include_retired,
        limit=limit,
        offset=offset,
    )
    return {"items": [_event_model(e) for e in events], "limit": limit, "offset": offset}


@router.post("/events", status_code=status.HTTP_201_CREATED)
async def create_event(
    project_id: str,
    body: EventRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: CanonAssertionService = Depends(get_canon_assertion_service),
) -> EventModel:
    result, event = await service.create_event(
        scope,
        scope.workspace_id or "",
        project_id,
        kind=body.kind,
        name=body.name,
        participants=body.participants,
        location_ids=body.location_ids,
        cause_ids=body.cause_ids,
        outcome_ids=body.outcome_ids,
        world_position=body.world_position,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _event_model(event)


@router.get("/events/{event_id}")
async def get_event(
    project_id: str,
    event_id: str,
    scope: AccessScope = Depends(get_project_scope),
    service: CanonAssertionService = Depends(get_canon_assertion_service),
) -> EventModel:
    event = await service.get_event(scope, scope.workspace_id or "", project_id, event_id)
    return _event_model(event)


@router.patch("/events/{event_id}")
async def amend_event(
    project_id: str,
    event_id: str,
    body: AmendEventRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: CanonAssertionService = Depends(get_canon_assertion_service),
) -> EventModel:
    result, event = await service.amend_event(
        scope,
        scope.workspace_id or "",
        project_id,
        event_id,
        name=body.name,
        world_position=body.world_position,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _event_model(event)


@router.post("/events/{event_id}/retire")
async def retire_event(
    project_id: str,
    event_id: str,
    body: CloseRequest,
    response: Response,
    scope: AccessScope = Depends(require_project_scope),
    service: CanonAssertionService = Depends(get_canon_assertion_service),
) -> EventModel:
    result, event = await service.retire_event(
        scope,
        scope.workspace_id or "",
        project_id,
        event_id,
        expected_revision=body.expected_revision,
    )
    response.headers["ETag"] = f'"{result.revision}"'
    return _event_model(event)
