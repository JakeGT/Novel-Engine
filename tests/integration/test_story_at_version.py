"""The story rebuilt at a saved version, against PostgreSQL (QRY-002)."""

import pytest
import pytest_asyncio

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.journal import PostgresCanonJournal
from backend.core.database import create_engine, create_session_factory
from backend.domain.branching import working_in
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import (
    AssertFactOp,
    CloseFactOp,
    CreateEntityOp,
    UpdateEntityOp,
)
from backend.services import story_at_version
from backend.services.mutation_service import MutationService
from backend.services.story_at_version import story_at
from backend.services.what_if_service import WhatIfService

pytestmark = pytest.mark.integration

WS, PR = "ws_storyat_it", "pr_storyat_it"
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
        yield mutation, what_ifs, repository
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


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [1, 2, 2000])
async def test_the_story_at_a_version_is_what_was_saved_then(stack, monkeypatch, page) -> None:
    mutation, what_ifs, repository = stack
    # Pages smaller than one version make the rebuild read a version on its own.
    monkeypatch.setattr(story_at_version, "_PAGE", page)
    await _commit(
        mutation,
        [
            CreateEntityOp(id="tmp:m", type="Character", name="Mara"),
            CreateEntityOp(id="tmp:h", type="Location", name="Lantern Harbor"),
            CreateEntityOp(id="tmp:s", type="Location", name="Saltmere"),
        ],
        0,
    )
    entities = await repository.list_entities(workspace_id=WS, project_id=PR, limit=10)
    ids = {entity.name: entity.id for entity in entities}
    harbor = await _commit(
        mutation, [AssertFactOp(id="tmp:f", proposition=_at(ids["Mara"], ids["Lantern Harbor"]))], 1
    )
    facts = await repository.list_facts(workspace_id=WS, project_id=PR, limit=10)
    await _commit(mutation, [UpdateEntityOp(id=ids["Mara"], name="Mara Venn")], harbor.revision)
    await _commit(
        mutation,
        [
            CloseFactOp(id=facts[0].id),
            AssertFactOp(id="tmp:g", proposition=_at(ids["Mara"], ids["Saltmere"])),
        ],
        3,
    )

    then = await story_at(repository, WS, PR, harbor.revision)
    assert then.story.entities[ids["Mara"]].name == "Mara"
    assert [f.proposition.object.entity_id for f in then.story.facts] == [ids["Lantern Harbor"]]

    now = await story_at(repository, WS, PR, 4)
    assert now.story.entities[ids["Mara"]].name == "Mara Venn"
    assert [f.proposition.object.entity_id for f in now.story.facts] == [ids["Saltmere"]]

    # A what-if carries the versions it started from, and its own after that.
    what_if = await what_ifs.start(SCOPE, WS, PR, name="Stay at the harbor")
    with working_in(what_if.id):
        shared = await story_at(repository, WS, PR, harbor.revision)
    assert shared.story.entities[ids["Mara"]].name == "Mara"
    assert [f.id for f in shared.story.facts] == [f.id for f in then.story.facts]
