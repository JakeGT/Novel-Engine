"""The canonical reference world and its labelled proposals (2G/2I/2L)."""

import pytest
import pytest_asyncio

from backend.domain.identity import AccessScope, AuthMode
from backend.domain.schema_defs import SchemaDefinition
from backend.seed.apply import SEED_PROJECT_ID, SEED_WORKSPACE_ID
from backend.seed.canon import (
    ALT_PROJECT_ID,
    CHART_ASSET,
    INVALID_PROPOSALS,
    IVEN,
    LIGHTHOUSE,
    MARA,
    PRIMARY_HEAD,
    SEED_PROJECTS,
    VALID_PROPOSALS,
    apply_canon_seed,
    canon_seed_checksum,
)
from backend.seed.tenancy import WS_ALT
from backend.services.canon_equivalence import CanonEquivalenceService
from backend.services.mutation_service import MutationService
from backend.services.schema_compiler import compute_content_hash
from tests.doubles import (
    InMemoryCanonJournal,
    InMemoryCanonRepository,
    InMemoryMediaStore,
    InMemoryObjectStore,
    InMemorySchemaVersionStore,
)

pytestmark = pytest.mark.application


class _World:
    def __init__(self) -> None:
        self.journal = InMemoryCanonJournal()
        self.media = InMemoryMediaStore()
        self.schemas = InMemorySchemaVersionStore()
        self.objects = InMemoryObjectStore()
        self.repo = InMemoryCanonRepository(
            journal=self.journal, media=self.media, schemas=self.schemas
        )

    async def publish_schemas(self) -> None:
        definition = SchemaDefinition(label="Seed core schema").canonical()
        for project in SEED_PROJECTS:
            draft = await self.schemas.create_draft(
                workspace_id=project.workspace_id,
                project_id=project.project_id,
                definition=definition,
                content_hash=compute_content_hash(definition),
                compatibility="additive",
                created_by_user_id=None,
            )
            await self.schemas.publish(
                workspace_id=project.workspace_id,
                project_id=project.project_id,
                version_id=draft.id,
            )

    async def seed(self):
        return await apply_canon_seed(
            journal=self.journal,
            repository=self.repo,
            schemas=self.schemas,
            media=self.media,
            objects=self.objects,
        )


@pytest_asyncio.fixture
async def world() -> _World:
    built = _World()
    await built.publish_schemas()
    return built


@pytest.mark.asyncio
async def test_seed_commits_the_reference_world_through_the_mutation_path(world) -> None:
    report = await world.seed()

    assert report.ok, report.problems
    assert report.heads == {
        f"{SEED_WORKSPACE_ID}/{SEED_PROJECT_ID}": PRIMARY_HEAD,
        f"{WS_ALT}/{ALT_PROJECT_ID}": 2,
    }
    assert report.committed == PRIMARY_HEAD + 2
    assert report.checksum == canon_seed_checksum()
    # Every seeded revision has an envelope, a mutation set, and review evidence.
    assert len(world.repo.envelopes) == PRIMARY_HEAD + 2
    assert all(m.status.value == "committed" for m in world.journal.mutations.values())
    retire = next(
        d for d in world.journal.decisions if d.note and "acknowledged high-impact" in d.note
    )
    assert retire.decision == "approved"
    # The journal replays to exactly the normalized state the seed produced.
    for project in SEED_PROJECTS:
        equivalence = await CanonEquivalenceService(canon=world.repo, journal=world.journal).verify(
            project.workspace_id, project.project_id
        )
        assert equivalence.ok, equivalence.problems


@pytest.mark.asyncio
async def test_seed_is_idempotent(world) -> None:
    first = await world.seed()
    envelopes = dict(world.repo.envelopes)
    objects = len(
        world.objects.list_objects(workspace_id=SEED_WORKSPACE_ID, project_id=SEED_PROJECT_ID)
    )

    second = await world.seed()

    assert second.ok and second.heads == first.heads
    assert world.repo.envelopes == envelopes
    assert len(world.journal.mutations) == PRIMARY_HEAD + 2
    assert (
        len(world.objects.list_objects(workspace_id=SEED_WORKSPACE_ID, project_id=SEED_PROJECT_ID))
        == objects
        == 1
    )
    events = await world.media.list_lifecycle_events(
        workspace_id=SEED_WORKSPACE_ID, project_id=SEED_PROJECT_ID, asset_id=CHART_ASSET
    )
    assert [event.action.value for event in events] == ["asset_imported", "version_imported"]


@pytest.mark.asyncio
async def test_seed_reports_divergence_instead_of_overwriting(world) -> None:
    from backend.domain.operations import CreateEntityOp

    service = MutationService(
        world.journal, world.repo, local_mode=True, schema_store=world.schemas
    )
    scope = AccessScope(
        actor_user_id="someone",
        auth_mode=AuthMode.LOCAL,
        workspace_id=SEED_WORKSPACE_ID,
        project_id=SEED_PROJECT_ID,
    )
    published = await world.schemas.get_published(
        workspace_id=SEED_WORKSPACE_ID, project_id=SEED_PROJECT_ID
    )
    draft = await service.create_draft(
        scope,
        workspace_id=SEED_WORKSPACE_ID,
        project_id=SEED_PROJECT_ID,
        operations=[CreateEntityOp(id="ent_author_own", type="Character", name="Mine")],
        expected_head=0,
        schema_version_id=published.id,
    )
    await service.commit(scope, draft.id)

    report = await world.seed()

    assert not report.ok
    assert any("diverged from the seed" in problem for problem in report.problems)
    assert (SEED_WORKSPACE_ID, SEED_PROJECT_ID, "ent_author_own") in world.repo.entities
    assert (SEED_WORKSPACE_ID, SEED_PROJECT_ID, MARA) not in world.repo.entities


@pytest.mark.asyncio
async def test_colliding_ids_and_names_stay_isolated_between_tenants(world) -> None:
    await world.seed()
    primary = {"workspace_id": SEED_WORKSPACE_ID, "project_id": SEED_PROJECT_ID}
    alt = {"workspace_id": WS_ALT, "project_id": ALT_PROJECT_ID}

    here = await world.repo.get_entity(**primary, entity_id=MARA)
    there = await world.repo.get_entity(**alt, entity_id=MARA)
    assert here.name == there.name == "Mara Venn" and here.slug == there.slug == "mara-venn"
    assert here.aliases == ["The Fox"] and there.aliases == []
    assert here.attributes == {"role": "harbor pilot"} and there.attributes == {}

    # The same slug, external id, and name resolve only inside their own project.
    assert (
        await world.repo.get_entity_by_slug(**alt, slug="mara-venn")
    ).project_id == ALT_PROJECT_ID
    assert await world.repo.get_entity_by_external_id(**alt, namespace="seed", value="mara") is None
    assert [e.id for e in await world.repo.search_entities(**alt, query="iven")] == []
    assert await world.repo.get_entity(**alt, entity_id=IVEN) is None
    assert len(await world.repo.list_facts(**alt, limit=100)) == 2
    assert await world.repo.list_links(**alt) == []
    assert await world.repo.list_evidence(**alt) == []
    assert await world.repo.list_active_references(**alt, entity_id=LIGHTHOUSE) == []


def _scope() -> AccessScope:
    return AccessScope(
        actor_user_id="reviewer",
        auth_mode=AuthMode.LOCAL,
        workspace_id=SEED_WORKSPACE_ID,
        project_id=SEED_PROJECT_ID,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("proposal", VALID_PROPOSALS, ids=lambda p: p.label)
async def test_labelled_valid_proposals_commit(world, proposal) -> None:
    await world.seed()
    service = MutationService(
        world.journal, world.repo, local_mode=True, schema_store=world.schemas, media=world.media
    )
    published = await world.schemas.get_published(
        workspace_id=SEED_WORKSPACE_ID, project_id=SEED_PROJECT_ID
    )
    draft = await service.create_draft(
        _scope(),
        workspace_id=SEED_WORKSPACE_ID,
        project_id=SEED_PROJECT_ID,
        operations=list(proposal.operations),
        expected_head=PRIMARY_HEAD,
        schema_version_id=published.id,
    )
    report = await service.validate(_scope(), draft.id)
    assert report.ok, [(i.code, i.message) for i in report.issues]
    assert (await service.commit(_scope(), draft.id)).revision == PRIMARY_HEAD + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("proposal", INVALID_PROPOSALS, ids=lambda p: p.label)
async def test_labelled_invalid_proposals_are_blocked_with_the_expected_finding(
    world, proposal
) -> None:
    from backend.domain.validation import CanonValidationError

    await world.seed()
    service = MutationService(
        world.journal, world.repo, local_mode=True, schema_store=world.schemas, media=world.media
    )
    published = await world.schemas.get_published(
        workspace_id=SEED_WORKSPACE_ID, project_id=SEED_PROJECT_ID
    )
    draft = await service.create_draft(
        _scope(),
        workspace_id=SEED_WORKSPACE_ID,
        project_id=SEED_PROJECT_ID,
        operations=list(proposal.operations),
        expected_head=PRIMARY_HEAD,
        schema_version_id=published.id,
    )

    preview = await service.validate(_scope(), draft.id)
    blocking = {issue.code for issue in preview.issues if issue.severity == "blocking"}
    assert proposal.expected_code in blocking, (proposal.label, blocking)

    # The preview is advisory; the commit transaction is what actually refuses it.
    with pytest.raises(CanonValidationError) as raised:
        await service.commit(_scope(), draft.id)
    assert proposal.expected_code in {f.code for f in raised.value.report.blocking}
    branch = await world.journal.ensure_root_branch(
        workspace_id=SEED_WORKSPACE_ID, project_id=SEED_PROJECT_ID
    )
    assert (
        await world.repo.get_head(
            workspace_id=SEED_WORKSPACE_ID, project_id=SEED_PROJECT_ID, branch_id=branch.id
        )
        == PRIMARY_HEAD
    )


def test_seed_checksum_is_stable_and_labels_are_unique() -> None:
    assert canon_seed_checksum() == canon_seed_checksum()
    labels = [p.label for p in (*VALID_PROPOSALS, *INVALID_PROPOSALS)]
    assert len(labels) == len(set(labels))
    assert all(p.expected_code for p in INVALID_PROPOSALS)
    assert all(p.expected_code is None for p in VALID_PROPOSALS)
