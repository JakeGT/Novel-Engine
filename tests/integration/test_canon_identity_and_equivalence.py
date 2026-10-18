"""Naming policy, evidence, references, and three-way equivalence on real stores (2F/2G/2H)."""

import uuid

import pytest
import pytest_asyncio

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.canon_writer import Neo4jCanonWriter
from backend.adapters.journal import PostgresCanonJournal
from backend.core.database import create_engine, create_session_factory
from backend.domain.canon.events import EventClaim
from backend.domain.canon.propositions import EntityRefObject, Proposition, StringObject
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import (
    AmendEventOp,
    AssertBeliefOp,
    AssertFactOp,
    CloseFactOp,
    CreateEntityOp,
    CreateEventOp,
    EvidenceRef,
    RetireEntityOp,
    UpdateEntityOp,
)
from backend.domain.validation import CanonValidationError
from backend.graph.manager import GraphManager
from backend.services.canon_equivalence import CanonEquivalenceService
from backend.services.canon_history_service import CanonHistoryService
from backend.services.mutation_service import MutationService
from backend.services.projection_service import GraphProjectionService

pytestmark = pytest.mark.integration


class _Stack:
    def __init__(self, factory, manager) -> None:
        suffix = uuid.uuid4().hex[:8]
        self.ws, self.pr = f"ws_ident_{suffix}", f"pr_ident_{suffix}"
        self.journal = PostgresCanonJournal(factory)
        self.repo = PostgresCanonRepository(factory)
        self.mutation = MutationService(self.journal, self.repo, local_mode=True)
        self.history = CanonHistoryService(
            canon=self.repo, journal=self.journal, local_mode=True, mutation=self.mutation
        )
        self.writer = Neo4jCanonWriter(manager) if manager is not None else None
        self.head = 0

    @property
    def scope(self) -> AccessScope:
        return AccessScope(
            actor_user_id="usr_ident",
            auth_mode=AuthMode.LOCAL,
            workspace_id=self.ws,
            project_id=self.pr,
        )

    async def commit(self, *operations) -> int:
        draft = await self.mutation.create_draft(
            self.scope,
            workspace_id=self.ws,
            project_id=self.pr,
            operations=list(operations),
            expected_head=self.head,
        )
        result = await self.mutation.commit_author_change(
            self.scope, draft.id, acknowledge_high_impact=True
        )
        self.head = result.revision
        return result.revision


@pytest_asyncio.fixture
async def stack(disposable_database):
    engine = create_engine(disposable_database.settings())
    try:
        yield _Stack(create_session_factory(engine), None)
    finally:
        await engine.dispose()


def _located(fact_id: str, subject: str, place: str, project_id: str) -> AssertFactOp:
    return AssertFactOp(
        id=fact_id,
        proposition=Proposition(
            subject_id=subject,
            predicate="LOCATED_AT",
            object=EntityRefObject(kind="entity_ref", entity_id=place, project_id=project_id),
        ),
    )


@pytest.mark.asyncio
async def test_slug_external_id_and_alias_policy_on_postgres(stack) -> None:
    await stack.commit(
        CreateEntityOp(
            id="ent_1",
            type="Character",
            name="Mara",
            slug="fox",
            aliases=["The Fox", "Venn"],
            external_ids={"wiki": "Q42", "scrivener.uuid": "ab-1"},
        )
    )
    stored = await stack.repo.get_entity(
        workspace_id=stack.ws, project_id=stack.pr, entity_id="ent_1"
    )
    assert stored is not None
    assert stored.aliases == ["The Fox", "Venn"]
    assert stored.external_ids == {"wiki": "Q42", "scrivener.uuid": "ab-1"}

    for operation, code in (
        (CreateEntityOp(id="ent_2", type="Character", name="B", slug="fox"), "slug_conflict"),
        (
            CreateEntityOp(id="ent_2", type="Character", name="B", external_ids={"wiki": "Q42"}),
            "external_id_conflict",
        ),
    ):
        with pytest.raises(CanonValidationError, match=code):
            await stack.commit(operation)

    by_alias = await stack.repo.search_entities(
        workspace_id=stack.ws, project_id=stack.pr, query="venn"
    )
    assert [entity.id for entity in by_alias] == ["ent_1"]
    by_external = await stack.repo.get_entity_by_external_id(
        workspace_id=stack.ws, project_id=stack.pr, namespace="wiki", value="Q42"
    )
    assert by_external is not None and by_external.id == "ent_1"
    assert (
        await stack.repo.get_entity_by_external_id(
            workspace_id=stack.ws, project_id="pr_other", namespace="wiki", value="Q42"
        )
        is None
    )
    twins = await stack.repo.find_entities_by_name(
        workspace_id=stack.ws, project_id=stack.pr, entity_type="Character", name="  MARA "
    )
    assert [entity.id for entity in twins] == ["ent_1"]

    # Replacing the mapping rebinds external ids; retiring frees the slug only.
    await stack.commit(UpdateEntityOp(id="ent_1", external_ids={"wiki": "Q43"}))
    await stack.commit(
        CreateEntityOp(id="ent_2", type="Character", name="B", external_ids={"wiki": "Q42"})
    )
    await stack.commit(RetireEntityOp(id="ent_1"))
    await stack.commit(CreateEntityOp(id="ent_3", type="Character", name="C", slug="fox"))
    with pytest.raises(CanonValidationError, match="external_id_conflict"):
        await stack.commit(
            CreateEntityOp(id="ent_4", type="Character", name="D", external_ids={"wiki": "Q43"})
        )


@pytest.mark.asyncio
async def test_evidence_provenance_and_references_on_postgres(stack) -> None:
    await stack.commit(
        CreateEntityOp(
            id="ent_mara",
            type="Character",
            name="Mara",
            evidence=[
                EvidenceRef(source_artefact_id="scene:ch01", location="chars:10-40"),
                EvidenceRef(source_artefact_id="scene:ch02"),
            ],
        ),
        CreateEntityOp(id="ent_harbor", type="Location", name="Harbor"),
        _located("fct_at", "ent_mara", "ent_harbor", stack.pr),
        AssertBeliefOp(
            id="blf_1",
            holder_entity_id="ent_mara",
            proposition=Proposition(
                subject_id="ent_harbor",
                predicate="HAS_NAME",
                object=StringObject(kind="string", value="Safe Harbor"),
            ),
            source_ref="scene:ch01",
        ),
        CreateEventOp(
            id="evt_1",
            kind="arrival",
            name="Arrival",
            participants=[{"entity_id": "ent_mara", "role": "traveller"}],
            location_ids=["ent_harbor"],
        ),
    )
    entries = await stack.history.record_provenance(
        stack.scope, stack.ws, stack.pr, record_type="entity", record_id="ent_mara"
    )
    assert [(e.source_artefact_id, e.location) for e in entries[0].evidence] == [
        ("scene:ch01", "chars:10-40"),
        ("scene:ch02", None),
    ]
    assert entries[0].origin == "author"
    assert [d.note for d in entries[0].decisions] == ["author-direct"]

    assert await stack.repo.list_active_references(
        workspace_id=stack.ws, project_id=stack.pr, entity_id="ent_harbor"
    ) == [("belief", "blf_1"), ("event", "evt_1"), ("fact", "fct_at")]
    assert await stack.repo.list_active_references(
        workspace_id=stack.ws, project_id=stack.pr, entity_id="ent_mara"
    ) == [("belief", "blf_1"), ("event", "evt_1"), ("fact", "fct_at")]

    # Listing by referenced entity is answered by the database, not by scanning pages.
    at_harbor = await stack.repo.list_facts(
        workspace_id=stack.ws, project_id=stack.pr, object_entity_id="ent_harbor"
    )
    assert [fact.id for fact in at_harbor] == ["fct_at"]
    assert (
        await stack.repo.list_facts(
            workspace_id=stack.ws, project_id=stack.pr, object_entity_id="ent_mara"
        )
        == []
    )
    for involved in ("ent_mara", "ent_harbor"):
        events = await stack.repo.list_events(
            workspace_id=stack.ws, project_id=stack.pr, entity_id=involved
        )
        assert [event.id for event in events] == ["evt_1"]

    # History inside one commit keeps operation order.
    diff = await stack.history.diff_revisions(
        stack.scope, stack.ws, stack.pr, from_revision=0, to_revision=1
    )
    assert [entry.record_id for entry in diff] == [
        "ent_mara",
        "ent_harbor",
        "fct_at",
        "blf_1",
        "evt_1",
    ]

    # A revert of the whole range removes dependants before the entities.
    proposal = await stack.history.propose_revert(stack.scope, stack.ws, stack.pr, from_revision=0)
    assert proposal.conflicts == []
    assert [op["op"] for op in proposal.operations] == [
        "retire_event",
        "close_belief",
        "close_fact",
        "retire_entity",
        "retire_entity",
    ]


@pytest.mark.asyncio
async def test_journal_normalized_canon_and_neo4j_projection_agree(
    disposable_database, neo4j_settings, neo4j_driver
) -> None:
    engine = create_engine(disposable_database.settings())
    stack = _Stack(create_session_factory(engine), GraphManager(neo4j_settings))
    branch = await stack.journal.ensure_root_branch(workspace_id=stack.ws, project_id=stack.pr)
    projector = GraphProjectionService(canon=stack.repo, writer=stack.writer)
    equivalence = CanonEquivalenceService(
        canon=stack.repo, journal=stack.journal, writer=stack.writer
    )
    scope = {"workspace_id": stack.ws, "project_id": stack.pr}
    try:
        await stack.commit(
            CreateEntityOp(
                id="ent_mara",
                type="Character",
                name="Mara",
                aliases=["Fox"],
                external_ids={"wiki": "Q1"},
                attributes={"role": "scout"},
            ),
            CreateEntityOp(id="ent_iva", type="Character", name="Iva"),
            CreateEntityOp(id="ent_harbor", type="Location", name="Harbor"),
            CreateEntityOp(id="ent_keep", type="Location", name="Keep"),
        )
        await stack.commit(
            _located("fct_at", "ent_mara", "ent_harbor", stack.pr),
            AssertFactOp(
                id="fct_knows",
                proposition=Proposition(
                    subject_id="ent_mara",
                    predicate="KNOWS",
                    object=EntityRefObject(
                        kind="entity_ref", entity_id="ent_iva", project_id=stack.pr
                    ),
                ),
            ),
            AssertFactOp(
                id="fct_name",
                proposition=Proposition(
                    subject_id="ent_mara",
                    predicate="HAS_NAME",
                    object=StringObject(kind="string", value="Mara Venn"),
                ),
            ),
            AssertBeliefOp(
                id="blf_1",
                holder_entity_id="ent_iva",
                proposition=Proposition(
                    subject_id="ent_mara",
                    predicate="LOCATED_AT",
                    object=EntityRefObject(
                        kind="entity_ref", entity_id="ent_keep", project_id=stack.pr
                    ),
                ),
                source_ref="scene:ch02",
            ),
            CreateEventOp(
                id="evt_1",
                kind="arrival",
                name="Arrival",
                participants=[{"entity_id": "ent_mara", "role": "traveller"}],
                location_ids=["ent_harbor"],
            ),
            # Something said, not true (KNW-005): kept on the event, in every store.
            CreateEventOp(
                id="evt_claim",
                kind="claim",
                name="Iva says Mara is at the Keep",
                participants=[{"entity_id": "ent_iva", "role": "speaker"}],
                claim=EventClaim(
                    proposition=Proposition(
                        subject_id="ent_mara",
                        predicate="LOCATED_AT",
                        object=EntityRefObject(
                            kind="entity_ref", entity_id="ent_keep", project_id=stack.pr
                        ),
                    ),
                    modality="suggests",
                ),
            ),
        )
        said = await stack.repo.get_event(event_id="evt_claim", **scope)
        assert said is not None and said.claim is not None
        assert said.claim.modality == "suggests" and said.claim.proposition.object.entity_id == (
            "ent_keep"
        )
        report = await projector.project_pending(branch_id=branch.id, **scope)
        assert report.error is None and report.applied == 2
        assert await stack.writer.list_fact_edges(**scope) == [
            ("fct_at", "ent_mara", "LOCATED_AT", "ent_harbor"),
            ("fct_knows", "ent_mara", "KNOWS", "ent_iva"),
        ]
        first = await equivalence.verify(stack.ws, stack.pr)
        assert first.ok, first.problems
        assert first.journal_checksum == first.canon_checksum == first.projection_checksum

        # Close-and-reassert, edit, amend, and retire, then prove equivalence again.
        await stack.commit(
            CloseFactOp(id="fct_at"),
            _located("fct_at_2", "ent_mara", "ent_keep", stack.pr),
            UpdateEntityOp(id="ent_mara", name="Mara Venn", aliases=["Fox", "Venn"]),
            AmendEventOp(id="evt_1", world_position="dawn, year 12"),
            RetireEntityOp(id="ent_iva"),
        )
        lagging = await equivalence.verify(stack.ws, stack.pr)
        assert lagging.problems == ["projection is at revision 2, canon head is 3"]

        await projector.project_pending(branch_id=branch.id, **scope)
        assert await stack.writer.list_fact_edges(**scope) == [
            ("fct_at_2", "ent_mara", "LOCATED_AT", "ent_keep"),
            ("fct_knows", "ent_mara", "KNOWS", "ent_iva"),
        ]
        second = await equivalence.verify(stack.ws, stack.pr)
        assert second.ok, second.problems

        # Close/reassert history is complete on the reified records.
        closed = await stack.history.record_history(
            stack.scope, stack.ws, stack.pr, record_type="fact", record_id="fct_at"
        )
        assert [entry.operation_type for entry in closed] == ["close_fact", "assert_fact"]
        assert (
            await stack.history.fact_at_revision(stack.scope, stack.ws, stack.pr, "fct_at", 2)
        ).status.value == "asserted"

        # Delete the projection and rebuild it from the journal alone.
        await projector.rebuild(branch_id=branch.id, **scope)
        rebuilt = await equivalence.verify(stack.ws, stack.pr)
        assert rebuilt.ok, rebuilt.problems
        assert rebuilt.projection_checksum == second.projection_checksum
        assert rebuilt.canon_checksum == second.canon_checksum

        # A tampered projection is detected by record and field.
        with neo4j_driver.session() as session:
            session.run(
                "MATCH (n:CanonEntity {workspace_id: $w, project_id: $p, id: 'ent_harbor'}) "
                "SET n.name = 'Tampered'",
                w=stack.ws,
                p=stack.pr,
            )
        tampered = await equivalence.verify(stack.ws, stack.pr)
        assert tampered.problems == ["projection vs canon: entity ent_harbor differs in name"]
    finally:
        await stack.writer.discard(branch_id=branch.id, **scope)
        await engine.dispose()
