"""The outline reads only the part of the story bible its checks look at."""

from __future__ import annotations

import pytest

from backend.domain.canon.propositions import Proposition
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.knowledge import Reveal, with_reveals
from backend.domain.operations import AssertFactOp, CreateEntityOp, CreatePlanItemOp
from backend.domain.rules import Check, Pattern
from backend.domain.scene_conditions import SceneCondition, with_conditions
from backend.services.mutation_service import MutationService
from backend.services.plan_service import PlanService
from tests.doubles import InMemoryCanonJournal, InMemoryCanonRepository

pytestmark = pytest.mark.application

WS, PR = "ws_outline", "pr_outline"


def _scope() -> AccessScope:
    return AccessScope(
        actor_user_id="usr_1", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR
    )


def _located(subject: str, target: str, fact_id: str) -> AssertFactOp:
    return AssertFactOp(
        id=fact_id,
        proposition=Proposition(
            subject_id=subject,
            predicate="LOCATED_AT",
            object={"kind": "entity_ref", "entity_id": target, "project_id": PR},
        ),
    )


class _Watched:
    """The canon reader, noting every read of the story bible the outline makes."""

    def __init__(self, inner: InMemoryCanonRepository) -> None:
        self._inner = inner
        self.fact_reads: list[dict] = []
        self.whole_reads: list[str] = []

    async def list_facts(self, **kwargs):
        self.fact_reads.append(kwargs)
        return await self._inner.list_facts(**kwargs)

    async def list_entities(self, **kwargs):
        self.whole_reads.append("entities")
        return await self._inner.list_entities(**kwargs)

    async def list_events(self, **kwargs):
        self.whole_reads.append("events")
        return await self._inner.list_events(**kwargs)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


@pytest.mark.asyncio
async def test_the_outline_reads_only_the_facts_its_conditions_and_reveals_name() -> None:
    journal = InMemoryCanonJournal()
    canon = InMemoryCanonRepository(journal=journal)
    mutation = MutationService(journal, canon, local_mode=True)
    watched = _Watched(canon)
    plans = PlanService(mutation=mutation, reader=watched, local_mode=True)  # type: ignore[arg-type]

    need = SceneCondition(
        when="before",
        entity_id="ent_mara",
        check=Check(kind="has", pattern=Pattern(predicate="LOCATED_AT", other_id="ent_harbor")),
    )
    details = with_conditions(with_reveals({}, [Reveal(fact_id="fct_tobin", how="shown")]), [need])
    draft = await mutation.create_draft(
        _scope(),
        workspace_id=WS,
        project_id=PR,
        operations=[
            CreateEntityOp(id="ent_mara", type="Character", name="Mara"),
            CreateEntityOp(id="ent_tobin", type="Character", name="Tobin"),
            CreateEntityOp(id="ent_harbor", type="Location", name="Lantern Harbor"),
            CreateEntityOp(id="ent_castle", type="Location", name="Grey Castle"),
            # People and facts no check looks at.
            *[
                CreateEntityOp(id=f"ent_crowd_{i}", type="Character", name=f"Sailor {i}")
                for i in range(5)
            ],
            *[_located(f"ent_crowd_{i}", "ent_castle", f"fct_crowd_{i}") for i in range(5)],
            _located("ent_tobin", "ent_castle", "fct_tobin"),
            CreatePlanItemOp(id="pln_scene", kind="scene", title="Low tide", details=details),
        ],
        expected_head=0,
    )
    await mutation.commit(_scope(), draft.id)

    reading = await plans.outline(_scope(), WS, PR)

    # The condition is judged and flagged, with the entry's own name.
    (state,) = reading.conditions
    assert state.status == "not_met" and "Lantern Harbor" in state.reading
    assert any(item.code == "condition_not_met" for item in reading.open)
    # Every fact read was about, or pointed at, the one entry the condition names.
    assert watched.fact_reads
    for read in watched.fact_reads:
        assert read.get("subject_id") == "ent_mara" or read.get("object_entity_id") == "ent_mara"
    # No scene has a time, and few entries are needed: nothing read in bulk.
    assert watched.whole_reads == []

    # Once she is there, the same scoped read sees it.
    draft = await mutation.create_draft(
        _scope(),
        workspace_id=WS,
        project_id=PR,
        operations=[_located("ent_mara", "ent_harbor", "fct_mara")],
        expected_head=1,
    )
    await mutation.commit(_scope(), draft.id)
    (state,) = (await plans.outline(_scope(), WS, PR)).conditions
    assert state.status == "met"
