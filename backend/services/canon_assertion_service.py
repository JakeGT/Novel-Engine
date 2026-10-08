"""Fact, belief, and event authoring over the mutation commit saga.

Every write builds a typed operation and commits through ``MutationService``.
Validation enforces the core predicate registry (subject type, object shape,
cardinality), entity existence/lifecycle, belief-holder compatibility, and event
reference existence before reservation. Truth agreement is always a derived
query result, never a stored field.
"""

from __future__ import annotations

from datetime import datetime

from backend.adapters.system import uuid7
from backend.domain.canon.assertions import Belief, Fact, TruthAgreement, truth_agreement
from backend.domain.canon.entities import CanonEntity, EntityLifecycle
from backend.domain.canon.errors import CanonError
from backend.domain.canon.events import Event, EventParticipant
from backend.domain.canon.ids import format_canon_id
from backend.domain.canon.propositions import Proposition
from backend.domain.canon.registry import Cardinality, predicate_spec, validate_proposition
from backend.domain.errors import ConflictError, NotFoundError, ValidationError
from backend.domain.identity import AccessScope
from backend.domain.operations import (
    AmendEventOp,
    AssertBeliefOp,
    AssertFactOp,
    CloseBeliefOp,
    CloseFactOp,
    CreateEventOp,
    RetireEventOp,
)
from backend.domain.tenancy import Permission
from backend.ports import CanonRepositoryPort, SchemaVersionStorePort
from backend.services.mutation_service import CommitResult, MutationService


class CanonAssertionService:
    def __init__(
        self,
        *,
        mutation: MutationService,
        reader: CanonRepositoryPort,
        schema_store: SchemaVersionStorePort | None = None,
        tenancy: object | None = None,
        local_mode: bool = False,
    ) -> None:
        self._mutation = mutation
        self._reader = reader
        self._schema_store = schema_store
        self._tenancy = tenancy
        self._local_mode = local_mode

    async def _authorize(
        self, scope: AccessScope, workspace_id: str, project_id: str, permission: Permission
    ) -> None:
        if self._local_mode or self._tenancy is None:
            return
        await self._tenancy.require_project(scope, workspace_id, project_id, permission)  # type: ignore[attr-defined]

    async def _commit(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        operations: list,
        expected_revision: int,
        idempotency_key: str,
        schema_version_id: str | None = None,
    ) -> CommitResult:
        mutation = await self._mutation.create_draft(
            scope,
            workspace_id=workspace_id,
            project_id=project_id,
            operations=operations,
            expected_head=expected_revision,
            idempotency_key=idempotency_key,
            schema_version_id=schema_version_id,
        )
        return await self._mutation.commit(scope, mutation.id)

    async def _require_entity(
        self, workspace_id: str, project_id: str, entity_id: str
    ) -> CanonEntity:
        entity = await self._reader.get_entity(
            workspace_id=workspace_id, project_id=project_id, entity_id=entity_id
        )
        if entity is None or entity.lifecycle != EntityLifecycle.ACTIVE:
            raise NotFoundError("Referenced entity not found.")
        return entity

    @staticmethod
    def _validate_proposition(proposition: Proposition, subject_type: str) -> None:
        try:
            validate_proposition(proposition, subject_type=subject_type)
        except CanonError as error:
            raise ValidationError(str(error)) from error

    # --- facts ---

    async def assert_fact(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        proposition: Proposition,
        expected_revision: int,
        confidence: float = 1.0,
        valid_from: datetime | None = None,
        valid_until: datetime | None = None,
        idempotency_key: str = "",
    ) -> tuple[CommitResult, Fact]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        subject = await self._require_entity(workspace_id, project_id, proposition.subject_id)
        self._validate_proposition(proposition, subject.type)
        spec = predicate_spec(proposition.predicate)
        if spec is not None and spec.cardinality == Cardinality.ONE:
            existing = await self._reader.list_facts(
                workspace_id=workspace_id,
                project_id=project_id,
                subject_id=proposition.subject_id,
                predicate=proposition.predicate,
                include_closed=False,
            )
            if existing:
                raise ConflictError(
                    "A fact with this subject and predicate already exists (cardinality one)."
                )
        fact_id = format_canon_id("fact", str(uuid7()))
        operation = AssertFactOp(
            id=fact_id,
            proposition=proposition,
            confidence=confidence,
            valid_from=valid_from,
            valid_until=valid_until,
        )
        result = await self._commit(
            scope, workspace_id, project_id, [operation], expected_revision, idempotency_key
        )
        fact = await self._reader.get_fact(
            workspace_id=workspace_id, project_id=project_id, fact_id=fact_id
        )
        assert fact is not None
        return result, fact

    async def close_fact(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        fact_id: str,
        *,
        expected_revision: int,
        idempotency_key: str = "",
    ) -> tuple[CommitResult, Fact]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        existing = await self._reader.get_fact(
            workspace_id=workspace_id, project_id=project_id, fact_id=fact_id
        )
        if existing is None:
            raise NotFoundError("Fact not found.")
        operation = CloseFactOp(id=fact_id)
        result = await self._commit(
            scope, workspace_id, project_id, [operation], expected_revision, idempotency_key
        )
        fact = await self._reader.get_fact(
            workspace_id=workspace_id, project_id=project_id, fact_id=fact_id
        )
        assert fact is not None
        return result, fact

    async def get_fact(
        self, scope: AccessScope, workspace_id: str, project_id: str, fact_id: str
    ) -> Fact:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        fact = await self._reader.get_fact(
            workspace_id=workspace_id, project_id=project_id, fact_id=fact_id
        )
        if fact is None:
            raise NotFoundError("Fact not found.")
        return fact

    async def list_facts(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        include_closed: bool = False,
        subject_id: str | None = None,
        predicate: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Fact]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._reader.list_facts(
            workspace_id=workspace_id,
            project_id=project_id,
            include_closed=include_closed,
            subject_id=subject_id,
            predicate=predicate,
            limit=limit,
            offset=offset,
        )

    # --- beliefs ---

    async def assert_belief(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        holder_entity_id: str,
        proposition: Proposition,
        expected_revision: int,
        source_ref: str | None = None,
        confidence: float = 1.0,
        idempotency_key: str = "",
    ) -> tuple[CommitResult, Belief]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        if not source_ref:
            raise ValidationError("A belief requires an explicit acquisition source.")
        holder = await self._require_entity(workspace_id, project_id, holder_entity_id)
        if holder.type != "Character":
            raise ValidationError("Belief holder must be a Character entity.")
        subject = await self._require_entity(workspace_id, project_id, proposition.subject_id)
        self._validate_proposition(proposition, subject.type)
        belief_id = format_canon_id("belief", str(uuid7()))
        operation = AssertBeliefOp(
            id=belief_id,
            holder_entity_id=holder_entity_id,
            proposition=proposition,
            confidence=confidence,
            source_ref=source_ref,
        )
        result = await self._commit(
            scope, workspace_id, project_id, [operation], expected_revision, idempotency_key
        )
        belief = await self._reader.get_belief(
            workspace_id=workspace_id, project_id=project_id, belief_id=belief_id
        )
        assert belief is not None
        return result, belief

    async def close_belief(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        belief_id: str,
        *,
        expected_revision: int,
        idempotency_key: str = "",
    ) -> tuple[CommitResult, Belief]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        existing = await self._reader.get_belief(
            workspace_id=workspace_id, project_id=project_id, belief_id=belief_id
        )
        if existing is None:
            raise NotFoundError("Belief not found.")
        operation = CloseBeliefOp(id=belief_id)
        result = await self._commit(
            scope, workspace_id, project_id, [operation], expected_revision, idempotency_key
        )
        belief = await self._reader.get_belief(
            workspace_id=workspace_id, project_id=project_id, belief_id=belief_id
        )
        assert belief is not None
        return result, belief

    async def get_belief(
        self, scope: AccessScope, workspace_id: str, project_id: str, belief_id: str
    ) -> Belief:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        belief = await self._reader.get_belief(
            workspace_id=workspace_id, project_id=project_id, belief_id=belief_id
        )
        if belief is None:
            raise NotFoundError("Belief not found.")
        return belief

    async def list_beliefs(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        holder_entity_id: str | None = None,
        include_closed: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Belief]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._reader.list_beliefs(
            workspace_id=workspace_id,
            project_id=project_id,
            holder_entity_id=holder_entity_id,
            include_closed=include_closed,
            limit=limit,
            offset=offset,
        )

    async def truth_agreement(
        self, scope: AccessScope, workspace_id: str, project_id: str, belief_id: str
    ) -> TruthAgreement:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        belief = await self.get_belief(scope, workspace_id, project_id, belief_id)
        proposition = belief.proposition
        facts = await self._reader.list_facts(
            workspace_id=workspace_id,
            project_id=project_id,
            subject_id=proposition.subject_id,
            predicate=proposition.predicate,
            include_closed=False,
        )
        return truth_agreement(belief, facts)

    # --- events ---

    async def create_event(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        kind: str,
        name: str,
        expected_revision: int,
        participants: list[EventParticipant] | None = None,
        location_ids: list[str] | None = None,
        cause_ids: list[str] | None = None,
        outcome_ids: list[str] | None = None,
        world_position: str | None = None,
        idempotency_key: str = "",
    ) -> tuple[CommitResult, Event]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        participants = participants or []
        for participant in participants:
            if not participant.role:
                raise ValidationError("Event participants require a role.")
            await self._require_entity(workspace_id, project_id, participant.entity_id)
        for location_id in location_ids or []:
            await self._require_entity(workspace_id, project_id, location_id)
        event_id = format_canon_id("event", str(uuid7()))
        operation = CreateEventOp(
            id=event_id,
            kind=kind,
            name=name,
            participants=participants,
            location_ids=location_ids or [],
            cause_ids=cause_ids or [],
            outcome_ids=outcome_ids or [],
            world_position=world_position,
        )
        result = await self._commit(
            scope, workspace_id, project_id, [operation], expected_revision, idempotency_key
        )
        event = await self._reader.get_event(
            workspace_id=workspace_id, project_id=project_id, event_id=event_id
        )
        assert event is not None
        return result, event

    async def amend_event(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        event_id: str,
        *,
        expected_revision: int,
        name: str | None = None,
        world_position: str | None = None,
        idempotency_key: str = "",
    ) -> tuple[CommitResult, Event]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        existing = await self._reader.get_event(
            workspace_id=workspace_id, project_id=project_id, event_id=event_id
        )
        if existing is None:
            raise NotFoundError("Event not found.")
        operation = AmendEventOp(id=event_id, name=name, world_position=world_position)
        result = await self._commit(
            scope, workspace_id, project_id, [operation], expected_revision, idempotency_key
        )
        event = await self._reader.get_event(
            workspace_id=workspace_id, project_id=project_id, event_id=event_id
        )
        assert event is not None
        return result, event

    async def retire_event(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        event_id: str,
        *,
        expected_revision: int,
        idempotency_key: str = "",
    ) -> tuple[CommitResult, Event]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_WRITE)
        existing = await self._reader.get_event(
            workspace_id=workspace_id, project_id=project_id, event_id=event_id
        )
        if existing is None:
            raise NotFoundError("Event not found.")
        operation = RetireEventOp(id=event_id)
        result = await self._commit(
            scope, workspace_id, project_id, [operation], expected_revision, idempotency_key
        )
        event = await self._reader.get_event(
            workspace_id=workspace_id, project_id=project_id, event_id=event_id
        )
        assert event is not None
        return result, event

    async def get_event(
        self, scope: AccessScope, workspace_id: str, project_id: str, event_id: str
    ) -> Event:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        event = await self._reader.get_event(
            workspace_id=workspace_id, project_id=project_id, event_id=event_id
        )
        if event is None:
            raise NotFoundError("Event not found.")
        return event

    async def list_events(
        self,
        scope: AccessScope,
        workspace_id: str,
        project_id: str,
        *,
        include_retired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Event]:
        await self._authorize(scope, workspace_id, project_id, Permission.PROJECT_READ)
        return await self._reader.list_events(
            workspace_id=workspace_id,
            project_id=project_id,
            include_retired=include_retired,
            limit=limit,
            offset=offset,
        )


__all__ = ["CanonAssertionService"]
