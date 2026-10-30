"""Journal replay, normalized canon, and projection must agree (2G/2K)."""

import pytest

from backend.domain.canon.propositions import EntityRefObject, Proposition, StringObject
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import (
    AmendEventOp,
    AssertBeliefOp,
    AssertFactOp,
    CloseFactOp,
    CreateEntityOp,
    CreateEventOp,
    RetireEntityOp,
    UpdateEntityOp,
)
from backend.services.canon_equivalence import CanonEquivalenceService
from backend.services.mutation_service import MutationService
from backend.services.projection_service import GraphProjectionService
from tests.doubles import InMemoryCanonJournal, InMemoryCanonRepository, InMemoryCanonWriter

pytestmark = pytest.mark.unit

WS, PR = "ws", "pr"
SCOPE = AccessScope(actor_user_id="u1", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR)


async def _world():
    journal = InMemoryCanonJournal()
    repo = InMemoryCanonRepository(journal=journal)
    writer = InMemoryCanonWriter()
    mutation = MutationService(journal, repo, local_mode=True)
    projector = GraphProjectionService(canon=repo, writer=writer)
    equivalence = CanonEquivalenceService(canon=repo, journal=journal, writer=writer)
    head = 0
    batches = [
        [
            CreateEntityOp(id="ent_mara", type="Character", name="Mara", aliases=["Fox"]),
            CreateEntityOp(id="ent_harbor", type="Location", name="Harbor"),
            CreateEntityOp(id="ent_keep", type="Location", name="Keep"),
        ],
        [
            AssertFactOp(
                id="fct_at",
                proposition=Proposition(
                    subject_id="ent_mara",
                    predicate="LOCATED_AT",
                    object=EntityRefObject(
                        kind="entity_ref", entity_id="ent_harbor", project_id=PR
                    ),
                ),
            ),
            AssertBeliefOp(
                id="blf_1",
                holder_entity_id="ent_mara",
                proposition=Proposition(
                    subject_id="ent_keep",
                    predicate="HAS_NAME",
                    object=StringObject(kind="string", value="The Safe Keep"),
                ),
                source_ref="scene:1",
            ),
            CreateEventOp(
                id="evt_1",
                kind="arrival",
                name="Arrival",
                participants=[{"entity_id": "ent_mara", "role": "traveller"}],
                location_ids=["ent_harbor"],
            ),
        ],
        [
            UpdateEntityOp(id="ent_mara", name="Mara Venn"),
            CloseFactOp(id="fct_at"),
            AmendEventOp(id="evt_1", world_position="dawn"),
            RetireEntityOp(id="ent_keep"),
        ],
    ]
    for operations in batches:
        draft = await mutation.create_draft(
            SCOPE, workspace_id=WS, project_id=PR, operations=operations, expected_head=head
        )
        head = (
            await mutation.commit_author_change(SCOPE, draft.id, acknowledge_high_impact=True)
        ).revision
    branch = await journal.ensure_root_branch(workspace_id=WS, project_id=PR)
    return repo, writer, projector, equivalence, branch


@pytest.mark.asyncio
async def test_replay_normalized_and_projection_agree() -> None:
    repo, writer, projector, equivalence, branch = await _world()
    await projector.project_pending(workspace_id=WS, project_id=PR, branch_id=branch.id)

    report = await equivalence.verify(WS, PR)

    assert report.ok, report.problems
    assert report.head_revision == 3 and report.projection_revision == 3
    assert report.journal_checksum == report.canon_checksum == report.projection_checksum
    # The only entity-to-entity fact was closed, so no relationship shortcut remains.
    assert await writer.list_fact_edges(workspace_id=WS, project_id=PR) == []


@pytest.mark.asyncio
async def test_rebuild_reproduces_the_same_projection_checksum() -> None:
    _repo, _writer, projector, equivalence, branch = await _world()
    await projector.project_pending(workspace_id=WS, project_id=PR, branch_id=branch.id)
    before = await equivalence.verify(WS, PR)

    await projector.rebuild(workspace_id=WS, project_id=PR, branch_id=branch.id)
    after = await equivalence.verify(WS, PR)

    assert after.ok and after.projection_checksum == before.projection_checksum
    assert after.canon_checksum == before.canon_checksum


@pytest.mark.asyncio
async def test_lagging_projection_is_reported_not_called_equivalent() -> None:
    _repo, _writer, _projector, equivalence, _branch = await _world()
    report = await equivalence.verify(WS, PR)
    assert report.problems == ["projection is at revision 0, canon head is 3"]
    assert report.projection_checksum is None
    # Journal and normalized canon still agree independently of the projection.
    assert report.journal_checksum == report.canon_checksum


@pytest.mark.asyncio
async def test_divergence_is_detected_by_record_and_field_without_content() -> None:
    repo, writer, projector, equivalence, branch = await _world()
    await projector.project_pending(workspace_id=WS, project_id=PR, branch_id=branch.id)

    # Tamper with normalized canon behind the journal's back.
    key = (WS, PR, "ent_mara")
    repo.entities[key] = repo.entities[key].model_copy(update={"name": "Someone Else"})
    # Corrupt the projection independently.
    writer.entities[(WS, PR, "ent_harbor")]["lifecycle"] = "retired"
    del writer.events[(WS, PR, "evt_1")]

    report = await equivalence.verify(WS, PR)

    assert not report.ok
    assert "normalized canon vs journal: entity ent_mara differs in name" in report.problems
    assert "projection vs canon: entity ent_harbor differs in lifecycle" in report.problems
    assert "projection vs canon: event evt_1 is missing" in report.problems
    assert all("Someone Else" not in problem for problem in report.problems)


@pytest.mark.asyncio
async def test_open_entity_fact_projects_exactly_one_relationship() -> None:
    journal = InMemoryCanonJournal()
    repo = InMemoryCanonRepository(journal=journal)
    writer = InMemoryCanonWriter()
    mutation = MutationService(journal, repo, local_mode=True)
    draft = await mutation.create_draft(
        SCOPE,
        workspace_id=WS,
        project_id=PR,
        operations=[
            CreateEntityOp(id="ent_a", type="Character", name="A"),
            CreateEntityOp(id="ent_b", type="Character", name="B"),
            AssertFactOp(
                id="fct_knows",
                proposition=Proposition(
                    subject_id="ent_a",
                    predicate="KNOWS",
                    object=EntityRefObject(kind="entity_ref", entity_id="ent_b", project_id=PR),
                ),
            ),
        ],
        expected_head=0,
    )
    await mutation.commit(SCOPE, draft.id)
    branch = await journal.ensure_root_branch(workspace_id=WS, project_id=PR)
    await GraphProjectionService(canon=repo, writer=writer).project_pending(
        workspace_id=WS, project_id=PR, branch_id=branch.id
    )
    assert await writer.list_fact_edges(workspace_id=WS, project_id=PR) == [
        ("fct_knows", "ent_a", "KNOWS", "ent_b")
    ]
    report = await CanonEquivalenceService(canon=repo, journal=journal, writer=writer).verify(
        WS, PR
    )
    assert report.ok, report.problems
