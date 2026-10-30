"""Initial performance budgets for the Phase 2 canon paths (2L).

Correctness is the release gate; these budgets exist to catch an accidental
order-of-magnitude regression (an N+1 query, a missing index, a full scan) on a
realistically small project (300 entities, 100 facts, 16+ revisions). Each
budget is roughly ten times the median measured on a developer workstation, so
the test stays stable on a slower CI runner while still failing on a real
regression. Measured medians are printed (``-s``) and recorded in
``plan/phase-2-evidence.md``.
"""

import statistics
import time

import pytest
import pytest_asyncio

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.journal import PostgresCanonJournal
from backend.adapters.media_store import PostgresMediaStore
from backend.adapters.schema_store import PostgresSchemaVersionStore
from backend.core.database import create_engine, create_session_factory
from backend.domain.canon.propositions import EntityRefObject, Proposition, StringObject
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import AssertFactOp, CreateEntityOp, UpdateEntityOp
from backend.services.canon_archive import CanonArchiveService
from backend.services.canon_history_service import CanonHistoryService
from backend.services.mutation_service import MutationService
from tests.doubles import InMemoryObjectStore

pytestmark = pytest.mark.integration

WS, PR = "ws_perf", "pr_perf"
ENTITIES = 300
BATCH = 25  # at the bulk-change threshold, so seeding needs no review

# Budgets in seconds (median of several runs).
BUDGETS = {
    "entity_page_read": 0.05,
    "entity_search": 0.05,
    "validate_25_operations": 0.60,
    "small_commit": 0.30,
    "record_history": 0.05,
    "revision_list": 0.60,
    "export_project": 1.0,
}


def _scope() -> AccessScope:
    return AccessScope(
        actor_user_id="usr_perf", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR
    )


async def _median(label: str, action, *, runs: int = 7) -> float:
    samples = []
    for _ in range(runs):
        started = time.perf_counter()
        await action()
        samples.append(time.perf_counter() - started)
    median = statistics.median(samples)
    print(f"perf {label}: median {median * 1000:.1f} ms (budget {BUDGETS[label] * 1000:.0f} ms)")
    return median


@pytest_asyncio.fixture
async def project(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    journal, repo = PostgresCanonJournal(factory), PostgresCanonRepository(factory)
    service = MutationService(journal, repo, local_mode=True)
    head = 0
    for start in range(0, ENTITIES, BATCH):
        operations = [
            CreateEntityOp(
                id=f"ent_perf_{index:04d}",
                type="Character" if index % 3 else "Location",
                name=f"Entity {index:04d}",
                aliases=[f"Alias {index:04d}"],
                attributes={"index": index},
            )
            for index in range(start, start + BATCH)
        ]
        draft = await service.create_draft(
            _scope(), workspace_id=WS, project_id=PR, operations=operations, expected_head=head
        )
        head = (await service.commit(_scope(), draft.id)).revision
    for start in range(0, 100, BATCH):
        facts = [
            AssertFactOp(
                id=f"fct_perf_{index:04d}",
                proposition=Proposition(
                    subject_id=f"ent_perf_{index * 3 + 1:04d}",
                    predicate="HAS_NAME",
                    object=StringObject(value=f"Name {index}"),
                ),
            )
            for index in range(start, start + BATCH)
        ]
        draft = await service.create_draft(
            _scope(), workspace_id=WS, project_id=PR, operations=facts, expected_head=head
        )
        head = (await service.commit(_scope(), draft.id)).revision
    try:
        yield service, journal, repo, factory, head
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_canon_paths_stay_within_their_budgets(project) -> None:
    service, journal, repo, factory, head = project
    history = CanonHistoryService(canon=repo, journal=journal, local_mode=True, mutation=service)
    archive = CanonArchiveService(
        canon=repo,
        journal=journal,
        schemas=PostgresSchemaVersionStore(factory),
        media=PostgresMediaStore(factory),
        objects=InMemoryObjectStore(),
        local_mode=True,
    )
    results: dict[str, float] = {}

    async def page() -> None:
        items = await repo.list_entities(workspace_id=WS, project_id=PR, limit=50, offset=150)
        assert len(items) == 50

    async def search() -> None:
        hits = await repo.search_entities(workspace_id=WS, project_id=PR, query="alias 02")
        assert hits

    results["entity_page_read"] = await _median("entity_page_read", page)
    results["entity_search"] = await _median("entity_search", search)

    preview = await service.create_draft(
        _scope(),
        workspace_id=WS,
        project_id=PR,
        operations=[
            AssertFactOp(
                id=f"tmp:f{index}",
                proposition=Proposition(
                    subject_id=f"ent_perf_{index * 3 + 1:04d}",
                    predicate="KNOWS",
                    object=EntityRefObject(
                        entity_id=f"ent_perf_{index * 3 + 2:04d}", project_id=PR
                    ),
                ),
            )
            for index in range(25)
        ],
        expected_head=head,
    )

    async def validate() -> None:
        assert (await service.validate(_scope(), preview.id)).ok

    results["validate_25_operations"] = await _median("validate_25_operations", validate)

    counter = 0

    async def small_commit() -> None:
        nonlocal head, counter
        counter += 1
        draft = await service.create_draft(
            _scope(),
            workspace_id=WS,
            project_id=PR,
            operations=[UpdateEntityOp(id="ent_perf_0001", attributes={"touched": counter})],
            expected_head=head,
        )
        head = (await service.commit(_scope(), draft.id)).revision

    results["small_commit"] = await _median("small_commit", small_commit)

    async def record_history() -> None:
        entries = await history.record_history(
            _scope(), WS, PR, record_type="entity", record_id="ent_perf_0001"
        )
        assert len(entries) >= 2

    async def revision_list() -> None:
        assert len(await history.list_revisions(_scope(), WS, PR, limit=20)) == 20

    async def export() -> None:
        exported = await archive.export_project(WS, PR)
        assert len(exported["canon"]["entities"]) == ENTITIES

    results["record_history"] = await _median("record_history", record_history)
    results["revision_list"] = await _median("revision_list", revision_list)
    results["export_project"] = await _median("export_project", export, runs=3)

    over = {name: value for name, value in results.items() if value > BUDGETS[name]}
    assert not over, f"over budget: {over}"
