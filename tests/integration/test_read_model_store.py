"""The story's ready views against PostgreSQL: found from the outbox, built per line,
verified by replaying the record history, never moved backwards (QRY-001)."""

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.journal import PostgresCanonJournal
from backend.adapters.read_model_store import PostgresReadModelStore
from backend.core.database import create_engine, create_session_factory
from backend.domain.branching import working_in
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import AssertFactOp, CloseFactOp, CreateEntityOp
from backend.domain.read_views import BUILDER_VERSION, VIEWS
from backend.domain.worldtime import At
from backend.services.mutation_service import MutationService
from backend.services.read_models import ReadModelConsumer, ReadModelService
from backend.services.what_if_service import WhatIfService

pytestmark = pytest.mark.integration

WS, PR = "ws_views_it", "pr_views_it"
SCOPE = AccessScope(
    actor_user_id="usr_author", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR
)


@pytest_asyncio.fixture
async def stack(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    repository = PostgresCanonRepository(factory)
    journal = PostgresCanonJournal(factory)
    mutation = MutationService(journal, repository, local_mode=True)
    what_ifs = WhatIfService(journal=journal, canon=repository, mutation=mutation, local_mode=True)
    store = PostgresReadModelStore(factory)
    consumer = ReadModelConsumer(store=store, reader=repository)
    service = ReadModelService(store=store, reader=repository, journal=journal, local_mode=True)
    try:
        yield mutation, what_ifs, repository, store, consumer, service, factory
    finally:
        await engine.dispose()


async def _commit(service, operations, head, branch_id=None):
    with working_in(branch_id):
        draft = await service.create_draft(
            SCOPE, workspace_id=WS, project_id=PR, operations=operations, expected_head=head
        )
        return await service.commit(SCOPE, draft.id)


def _at(subject: str, place: str) -> Proposition:
    return Proposition(
        subject_id=subject,
        predicate="LOCATED_AT",
        object=EntityRefObject(entity_id=place, project_id=PR),
    )


async def _story(mutation, repository) -> dict[str, str]:
    await _commit(
        mutation,
        [
            CreateEntityOp(id="tmp:m", type="Character", name="Mara"),
            CreateEntityOp(id="tmp:h", type="Location", name="Lantern Harbor"),
            CreateEntityOp(id="tmp:s", type="Location", name="Saltmere"),
        ],
        0,
    )
    ids = {e.name: e.id for e in await repository.list_entities(workspace_id=WS, project_id=PR)}
    await _commit(
        mutation,
        [
            AssertFactOp(
                id="tmp:f",
                proposition=_at(ids["Mara"], ids["Lantern Harbor"]),
                ends=At(at=10.5),
            ),
            AssertFactOp(
                id="tmp:g", proposition=_at(ids["Mara"], ids["Saltmere"]), starts=At(at=10.5)
            ),
        ],
        1,
    )
    return ids


@pytest.mark.asyncio
async def test_views_are_built_from_the_outbox_and_match_their_replay(stack) -> None:
    mutation, _what_ifs, repository, store, consumer, service, _factory = stack
    ids = await _story(mutation, repository)
    (pending,) = await store.pending(builder_version=BUILDER_VERSION, views=len(VIEWS))
    assert pending.is_root and pending.latest == 2
    assert await consumer.run_once() == 1
    assert await store.pending(builder_version=BUILDER_VERSION, views=len(VIEWS)) == []

    results = await service.verify(WS, PR)
    assert len(results) == len(VIEWS) and all(r.matched for r in results), results
    answer = await service.where(SCOPE, WS, PR, ids["Mara"], 11)
    assert answer.source == "view" and answer.version == 2
    assert [row["place_id"] for row in answer.rows] == [ids["Saltmere"]]
    # Numbers come back from JSONB as they went in.
    stored = await store.get(
        workspace_id=WS, project_id=PR, branch_id=pending.branch_id, view="location"
    )
    assert stored is not None
    spans = sorted(row["span"]["start"] or row["span"]["end"] for row in stored.rows)
    assert spans == [[10.5, 10.5], [10.5, 10.5]]

    # A new commit: the line is pending again and reads fall back until it is built.
    facts = await repository.list_facts(workspace_id=WS, project_id=PR)
    harbor = next(f for f in facts if f.proposition.object.entity_id == ids["Lantern Harbor"])  # type: ignore[union-attr]
    await _commit(mutation, [CloseFactOp(id=harbor.id)], 2)
    lagging = await service.where(SCOPE, WS, PR, ids["Mara"], 5)
    assert lagging.source == "worked_out" and lagging.rows == []
    assert await consumer.run_once() == 1
    assert (await service.where(SCOPE, WS, PR, ids["Mara"], 5)).source == "view"
    assert all(r.matched for r in await service.verify(WS, PR))


@pytest.mark.asyncio
async def test_an_older_build_never_replaces_a_newer_one(stack) -> None:
    mutation, _what_ifs, repository, store, consumer, _service, _factory = stack
    await _story(mutation, repository)
    await consumer.run_once()
    (line,) = await store.lines()
    rows = {view: [] for view in VIEWS}
    written = await store.save(
        workspace_id=WS,
        project_id=PR,
        branch_id=line[2],
        revision=1,
        builder_version=BUILDER_VERSION,
        views=rows,
        checksums={view: "old" for view in VIEWS},
    )
    assert written == 0
    other_builder = await store.save(
        workspace_id=WS,
        project_id=PR,
        branch_id=line[2],
        revision=1,
        builder_version=BUILDER_VERSION + 1,
        views={"timeline": []},
        checksums={"timeline": "new"},
    )
    assert other_builder == 1
    assert await store.forget(workspace_id=WS, project_id=PR) == len(VIEWS)


@pytest.mark.asyncio
async def test_a_what_if_builds_its_own_views(stack) -> None:
    mutation, what_ifs, repository, store, consumer, service, factory = stack
    ids = await _story(mutation, repository)
    what_if = await what_ifs.start(SCOPE, WS, PR, name="What if she stayed?")
    with working_in(what_if.id):
        facts = await repository.list_facts(workspace_id=WS, project_id=PR)
    saltmere = next(f for f in facts if f.proposition.object.entity_id == ids["Saltmere"])  # type: ignore[union-attr]
    await _commit(mutation, [CloseFactOp(id=saltmere.id)], 2, what_if.id)
    assert await consumer.run_once() == 2
    assert all(r.matched for r in await service.verify(WS, PR))
    with working_in(what_if.id):
        inside = await service.where(SCOPE, WS, PR, ids["Mara"], 11)
    assert inside.source == "view" and inside.rows == []
    main = await service.where(SCOPE, WS, PR, ids["Mara"], 11)
    assert [row["place_id"] for row in main.rows] == [ids["Saltmere"]]
    async with factory() as session:
        count = (await session.execute(text("SELECT count(*) FROM read_models"))).scalar()
    assert count == 2 * len(VIEWS)
