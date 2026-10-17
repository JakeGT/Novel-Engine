"""What-ifs against PostgreSQL: a fork copies the story, and the two lines never mix (ADR-020)."""

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.journal import PostgresCanonJournal
from backend.core.database import create_engine, create_session_factory, session_scope
from backend.domain.branching import working_in
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import (
    AmendEventOp,
    AssertFactOp,
    CreateEntityOp,
    CreateEventOp,
    UpdateEntityOp,
)
from backend.domain.worldtime import At, Direction, Relative
from backend.services.mutation_service import MutationService
from backend.services.what_if_service import Keep, WhatIfService

pytestmark = pytest.mark.integration

WS, PR = "ws_whatif_it", "pr_whatif_it"
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
    try:
        yield mutation, what_ifs, repository, factory
    finally:
        await engine.dispose()


async def _commit(service, operations, head, branch_id=None):
    with working_in(branch_id):
        draft = await service.create_draft(
            SCOPE, workspace_id=WS, project_id=PR, operations=operations, expected_head=head
        )
        return await service.commit(SCOPE, draft.id)


async def _names(repository, branch_id=None) -> set[str]:
    with working_in(branch_id):
        entities = await repository.list_entities(workspace_id=WS, project_id=PR, limit=100)
    return {entity.name for entity in entities}


@pytest.mark.asyncio
async def test_a_fork_copies_the_story_and_each_line_keeps_its_own_changes(stack) -> None:
    mutation, what_ifs, repository, factory = stack
    made = await _commit(
        mutation,
        [
            CreateEntityOp(id="tmp:m", type="Character", name="Mara", slug="mara"),
            CreateEntityOp(id="tmp:h", type="Location", name="Lantern Harbor"),
            AssertFactOp(
                id="tmp:f",
                proposition=Proposition(
                    subject_id="tmp:m",
                    predicate="LOCATED_AT",
                    object=EntityRefObject(entity_id="tmp:h", project_id=PR),
                ),
            ),
            CreateEventOp(id="tmp:war", kind="battle", name="The War", when=At(at=100)),
        ],
        0,
    )
    assert made.revision == 1
    what_if = await what_ifs.start(SCOPE, WS, PR, name="What if Mara refused?")
    assert what_if.fork_revision == 1 and what_if.parent_branch_id is not None

    # Every story table now holds the same rows twice, once per line.
    async with session_scope(factory) as session:
        for table in ("canon_entities", "canon_facts", "canon_events", "canon_record_versions"):
            counts = (
                await session.execute(
                    text(
                        f"SELECT branch_id, count(*) FROM {table} "  # noqa: S608
                        "WHERE workspace_id = :w AND project_id = :p GROUP BY branch_id"
                    ),
                    {"w": WS, "p": PR},
                )
            ).all()
            assert len(counts) == 2 and counts[0][1] == counts[1][1], table

    with working_in(what_if.id):
        mara = (await repository.list_entities(workspace_id=WS, project_id=PR, limit=10))[1]
    assert mara.name == "Mara"
    # The same slug is free to use in each line: it is unique per line, not per project.
    renamed = await _commit(mutation, [UpdateEntityOp(id=mara.id, name="Mara Venn")], 1, what_if.id)
    assert renamed.revision == 2
    await _commit(mutation, [CreateEntityOp(id="tmp:o", type="Character", name="Oren")], 1)

    assert await _names(repository, what_if.id) == {"Mara Venn", "Lantern Harbor"}
    assert await _names(repository) == {"Mara", "Lantern Harbor", "Oren"}

    compared = await what_ifs.compare(SCOPE, WS, PR, what_if.id)
    assert [(c.label, c.kind.value) for c in compared.in_what_if] == [("Mara Venn", "changed")]
    assert [(c.label, c.kind.value) for c in compared.in_main] == [("Oren", "added")]
    assert compared.conflicts == []

    brought = await what_ifs.bring_back(SCOPE, WS, PR, what_if.id, keep={})
    committed = await mutation.commit(SCOPE, brought.id)
    assert committed.revision == 3
    assert await _names(repository) == {"Mara Venn", "Lantern Harbor", "Oren"}
    # Bringing it back changed nothing in the what-if itself.
    assert await _names(repository, what_if.id) == {"Mara Venn", "Lantern Harbor"}


@pytest.mark.asyncio
async def test_a_conflict_kept_as_main_drops_the_what_ifs_version(stack) -> None:
    mutation, what_ifs, repository, _ = stack
    await _commit(mutation, [CreateEventOp(id="tmp:e", kind="battle", name="The War")], 0)
    what_if = await what_ifs.start(SCOPE, WS, PR, name="Earlier war")
    with working_in(None):
        (war,) = await repository.list_events(workspace_id=WS, project_id=PR)
    await _commit(mutation, [AmendEventOp(id=war.id, when=At(at=90))], 1, what_if.id)
    await _commit(mutation, [AmendEventOp(id=war.id, when=At(at=100))], 1)
    await _commit(
        mutation,
        [
            CreateEventOp(
                id="tmp:x",
                kind="exile",
                name="The Exile",
                when=Relative(anchor_event_id=war.id, offset=3, direction=Direction.BEFORE),
            )
        ],
        2,
        what_if.id,
    )
    compared = await what_ifs.compare(SCOPE, WS, PR, what_if.id)
    assert [c.label for c in compared.conflicts] == ["The War"]
    brought = await what_ifs.bring_back(
        SCOPE, WS, PR, what_if.id, keep={f"event:{war.id}": Keep.MAIN}
    )
    await mutation.commit(SCOPE, brought.id)
    with working_in(None):
        events = {e.name: e for e in await repository.list_events(workspace_id=WS, project_id=PR)}
    assert events["The War"].when == At(at=100)
    assert isinstance(events["The Exile"].when, Relative)
