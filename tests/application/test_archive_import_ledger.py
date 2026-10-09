"""Archive import ledger, owner authorization, audit, removal, and legacy upgrade (2K)."""

import pytest
import pytest_asyncio

from backend.domain.archive import ArchiveImportError, ImportStatus
from backend.domain.errors import ForbiddenError
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import CreateEntityOp
from backend.domain.schema_defs import SchemaDefinition
from backend.domain.tenancy import Role
from backend.seed.apply import SEED_PROJECT_ID, SEED_WORKSPACE_ID
from backend.seed.canon import PRIMARY_HEAD, SEED_PROJECTS, apply_canon_seed
from backend.services.canon_archive import ArchiveError, CanonArchiveService
from backend.services.canon_equivalence import CanonEquivalenceService
from backend.services.graph_archive_upgrade import (
    LEGACY_NAMESPACE,
    GraphArchiveError,
    GraphArchiveUpgradeService,
    plan_upgrade,
)
from backend.services.mutation_service import MutationService, ReviewRequiredError
from backend.services.project_transfer import _checksum as legacy_checksum
from backend.services.projection_service import GraphProjectionService
from backend.services.schema_compiler import compute_content_hash
from backend.services.tenancy_service import TenancyService
from tests.doubles import (
    InMemoryCanonJournal,
    InMemoryCanonRepository,
    InMemoryCanonWriter,
    InMemoryMediaStore,
    InMemoryObjectStore,
    InMemorySchemaVersionStore,
    InMemoryTenancyStore,
)

pytestmark = pytest.mark.application


class _Env:
    """One deployment: canon stores, projection, tenancy, and the archive service."""

    def __init__(self) -> None:
        self.journal = InMemoryCanonJournal()
        self.media = InMemoryMediaStore()
        self.schemas = InMemorySchemaVersionStore()
        self.objects = InMemoryObjectStore()
        self.repo = InMemoryCanonRepository(
            journal=self.journal, media=self.media, schemas=self.schemas
        )
        self.writer = InMemoryCanonWriter()
        self.tenancy_store = InMemoryTenancyStore()
        self.tenancy = TenancyService(self.tenancy_store)
        self.projector = GraphProjectionService(canon=self.repo, writer=self.writer)
        self.equivalence = CanonEquivalenceService(
            canon=self.repo, journal=self.journal, writer=self.writer
        )
        self.archive = CanonArchiveService(
            canon=self.repo,
            journal=self.journal,
            schemas=self.schemas,
            media=self.media,
            objects=self.objects,
            projector=self.projector,
            equivalence=self.equivalence,
            tenancy=self.tenancy,
            audit=self.tenancy_store,
            canon_writer=self.writer,
        )

    async def provision(self, slug: str) -> tuple[str, str]:
        workspace = await self.tenancy_store.create_workspace(slug=slug, name=slug)
        project = await self.tenancy_store.create_project(
            workspace_id=workspace.id, slug="restored", name="Restored"
        )
        for user, role in (("u_owner", Role.OWNER), ("u_editor", Role.EDITOR)):
            await self.tenancy_store.add_membership(
                workspace_id=workspace.id, user_id=user, role=role
            )
        return workspace.id, project.id

    def scope(self, user: str, workspace_id: str, project_id: str) -> AccessScope:
        return AccessScope(
            actor_user_id=user,
            auth_mode=AuthMode.HOSTED,
            correlation_id="corr-archive",
            workspace_id=workspace_id,
            project_id=project_id,
        )


@pytest_asyncio.fixture
async def source_archive() -> dict:
    """An archive of the seeded reference world, exported from its own deployment."""
    env = _Env()
    definition = SchemaDefinition(label="Seed core schema").canonical()
    for project in SEED_PROJECTS:
        draft = await env.schemas.create_draft(
            workspace_id=project.workspace_id,
            project_id=project.project_id,
            definition=definition,
            content_hash=compute_content_hash(definition),
            compatibility="additive",
            created_by_user_id=None,
        )
        await env.schemas.publish(
            workspace_id=project.workspace_id, project_id=project.project_id, version_id=draft.id
        )
    report = await apply_canon_seed(
        journal=env.journal,
        repository=env.repo,
        schemas=env.schemas,
        media=env.media,
        objects=env.objects,
    )
    assert report.ok, report.problems
    return await env.archive.export_project(SEED_WORKSPACE_ID, SEED_PROJECT_ID)


@pytest.mark.asyncio
async def test_import_is_ledgered_verified_and_audited(source_archive) -> None:
    env = _Env()
    ws, pr = await env.provision("clean")
    owner = env.scope("u_owner", ws, pr)

    result = await env.archive.import_project_as(owner, ws, pr, source_archive)

    assert result["commits"] == PRIMARY_HEAD
    assert result["projection_rebuilt"] is True and result["verified"] is True
    [record] = await env.archive.list_imports_as(owner, ws, pr)
    assert record.id == result["import_id"]
    assert record.status is ImportStatus.COMPLETED and record.stage == "verified"
    assert record.commits_applied == record.commits_total == PRIMARY_HEAD
    assert record.objects_imported == 1 and record.error is None
    assert record.actor_user_id == "u_owner" and record.completed_at is not None

    # Restored canon answers the same questions, and the projection matches it.
    report = await env.equivalence.verify(ws, pr)
    assert report.ok, report.problems
    retired = await env.repo.get_entity(
        workspace_id=ws, project_id=pr, entity_id="ent_seed_lighthouse"
    )
    assert retired is not None and not retired.is_active
    fact = await env.repo.get_fact(workspace_id=ws, project_id=pr, fact_id="fct_seed_mara_at")
    assert fact.proposition.object.project_id == pr

    audit = [event for event in env.tenancy_store.audit if event.action.startswith("archive.")]
    assert [event.action for event in audit] == ["archive.imported"]
    assert audit[0].actor_user_id == "u_owner" and audit[0].target_id == pr
    assert audit[0].detail["commits"] == PRIMARY_HEAD and audit[0].detail["verified"] is True
    # The audit record never contains story content.
    assert "Mara" not in str(audit[0].detail) and "Lantern" not in str(audit[0].detail)

    # Re-importing the same archive is a no-op on a completed import.
    again = await env.archive.import_project_as(owner, ws, pr, source_archive)
    assert again["import_id"] == result["import_id"]
    assert len(await env.archive.list_imports_as(owner, ws, pr)) == 1
    assert len(env.repo.envelopes) == PRIMARY_HEAD


@pytest.mark.asyncio
async def test_full_history_export_and_import_require_an_owner(source_archive) -> None:
    env = _Env()
    ws, pr = await env.provision("authz")
    editor, stranger = env.scope("u_editor", ws, pr), env.scope("u_nobody", ws, pr)

    for scope in (editor, stranger):
        with pytest.raises(ForbiddenError):
            await env.archive.import_project_as(scope, ws, pr, source_archive)
        with pytest.raises(ForbiddenError):
            await env.archive.export_project_as(scope, ws, pr)
        with pytest.raises(ForbiddenError):
            await env.archive.remove_import_as(scope, ws, pr, "imp_x")
    assert env.repo.envelopes == {} and env.repo.imports == {}
    assert [e for e in env.tenancy_store.audit if e.action.startswith("archive.")] == []
    # An editor may see import status, since it explains what they are looking at.
    assert await env.archive.list_imports_as(editor, ws, pr) == []
    with pytest.raises(ForbiddenError):
        await env.archive.list_imports_as(stranger, ws, pr)

    owner = env.scope("u_owner", ws, pr)
    await env.archive.import_project_as(owner, ws, pr, source_archive)
    exported = await env.archive.export_project_as(owner, ws, pr)
    actions = [e.action for e in env.tenancy_store.audit if e.action.startswith("archive.")]
    assert actions == ["archive.imported", "archive.exported"]
    assert env.tenancy_store.audit[-1].detail["archive_checksum"] == exported["checksum"]


@pytest.mark.asyncio
async def test_failed_import_is_visibly_unsuccessful_then_resumes(source_archive) -> None:
    env = _Env()
    ws, pr = await env.provision("crash")
    owner = env.scope("u_owner", ws, pr)
    real_apply = env.repo.apply_commit
    calls = 0

    async def crash_on_fourth(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 4:
            raise RuntimeError("simulated crash holding secret: Mara betrays the guild")
        return await real_apply(**kwargs)

    env.repo.apply_commit = crash_on_fourth
    with pytest.raises(RuntimeError):
        await env.archive.import_project_as(owner, ws, pr, source_archive)

    [record] = await env.archive.list_imports_as(owner, ws, pr)
    assert record.status is ImportStatus.FAILED and not record.is_successful
    assert (record.stage, record.commits_applied, record.commits_total) == (
        "commits",
        3,
        PRIMARY_HEAD,
    )
    # Only the error class is kept: the message could have carried content.
    assert record.error == "RuntimeError"
    assert env.tenancy_store.audit[-1].action == "archive.import_failed"
    assert "betrays" not in str(env.tenancy_store.audit[-1].detail)

    # Retrying resumes: already-applied commits are not duplicated.
    env.repo.apply_commit = real_apply
    result = await env.archive.import_project_as(owner, ws, pr, source_archive)
    assert result["verified"] is True
    [record] = await env.archive.list_imports_as(owner, ws, pr)
    assert record.status is ImportStatus.COMPLETED and record.commits_applied == PRIMARY_HEAD
    assert len(env.repo.envelopes) == PRIMARY_HEAD
    assert len(env.objects.list_objects(workspace_id=ws, project_id=pr)) == 1


@pytest.mark.asyncio
async def test_failed_import_can_be_removed_but_authored_canon_cannot(source_archive) -> None:
    env = _Env()
    ws, pr = await env.provision("remove")
    owner = env.scope("u_owner", ws, pr)
    real_apply = env.repo.apply_commit
    calls = 0

    async def crash_on_third(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("boom")
        return await real_apply(**kwargs)

    env.repo.apply_commit = crash_on_third
    with pytest.raises(RuntimeError):
        await env.archive.import_project_as(owner, ws, pr, source_archive)
    env.repo.apply_commit = real_apply
    [failed] = await env.archive.list_imports_as(owner, ws, pr)
    assert failed.object_keys and len(env.repo.entities) > 0

    removed = await env.archive.remove_import_as(owner, ws, pr, failed.id)

    assert removed.status is ImportStatus.REMOVED
    assert env.repo.entities == {} and env.repo.envelopes == {} and env.repo.heads == {}
    assert env.objects.list_objects(workspace_id=ws, project_id=pr) == []
    assert env.tenancy_store.audit[-1].action == "archive.import_removed"
    with pytest.raises(ArchiveImportError):
        await env.archive.remove_import_as(owner, ws, pr, failed.id)

    # A completed import, or a project with authored canon, is never removed.
    done = await env.archive.import_project_as(owner, ws, pr, source_archive)
    with pytest.raises(ArchiveImportError, match="completed"):
        await env.archive.remove_import_as(owner, ws, pr, done["import_id"])


@pytest.mark.asyncio
async def test_import_refuses_a_project_with_unrelated_canon_before_writing(
    source_archive,
) -> None:
    env = _Env()
    ws, pr = await env.provision("occupied")
    owner = env.scope("u_owner", ws, pr)
    service = MutationService(env.journal, env.repo, local_mode=True)
    draft = await service.create_draft(
        owner,
        workspace_id=ws,
        project_id=pr,
        operations=[CreateEntityOp(id="ent_mine", type="Character", name="Mine")],
        expected_head=0,
    )
    await service.commit(owner, draft.id)

    with pytest.raises(ArchiveError, match="unrelated canon"):
        await env.archive.import_project_as(owner, ws, pr, source_archive)

    # Preflight failed before the first durable write: no ledger row, no objects.
    assert env.repo.imports == {}
    assert env.objects.list_objects(workspace_id=ws, project_id=pr) == []
    assert list(env.repo.entities) == [(ws, pr, "ent_mine")]


@pytest.mark.asyncio
async def test_tampered_archive_never_reaches_the_ledger(source_archive) -> None:
    env = _Env()
    ws, pr = await env.provision("tamper")
    tampered = {**source_archive, "tags": [{"name": "x", "revision": 1, "commit_id": "c"}]}
    with pytest.raises(ArchiveError, match="checksum"):
        await env.archive.import_project_as(env.scope("u_owner", ws, pr), ws, pr, tampered)
    assert env.repo.imports == {} and env.repo.envelopes == {}


def _legacy_archive(nodes: list[dict], edges: list[dict]) -> dict:
    body = {
        "archive_version": "2",
        "schema_version": "1",
        "project": {"workspace_id": "old_ws", "project_id": "old_pr"},
        "nodes": nodes,
        "edges": edges,
        "objects": [],
        "derived": {},
        "sections": {},
    }
    body["checksum"] = legacy_checksum(body)
    return body


LEGACY = _legacy_archive(
    nodes=[
        {
            "id": "char_mara",
            "type": "Character",
            "properties": {"name": "Mara", "age": 30, "embedding": [0.1, 0.2]},
        },
        {"id": "loc_harbor", "type": "Location", "properties": {"name": "Harbor"}},
        {"id": "loc_keep", "type": "Location", "properties": {"name": "Keep"}},
        {
            "id": "belief_1",
            "type": "Belief",
            "properties": {"claim": "The king lies", "is_true": True},
        },
        {"id": "app_char_mara", "type": "Appearance", "properties": {"hair": "black"}},
        {"id": "bad id!", "type": "Character", "properties": {"name": "Odd"}},
    ],
    edges=[
        {"source": "char_mara", "target": "loc_harbor", "type": "LOCATED_AT", "properties": {}},
        {"source": "char_mara", "target": "loc_keep", "type": "LOCATED_AT", "properties": {}},
        {"source": "char_mara", "target": "belief_1", "type": "BELIEVES", "properties": {}},
        {"source": "loc_harbor", "target": "char_mara", "type": "OWNS", "properties": {}},
        {
            "source": "char_mara",
            "target": "app_char_mara",
            "type": "HAS_APPEARANCE",
            "properties": {},
        },
    ],
)


def test_legacy_graph_is_mapped_conservatively_and_reported() -> None:
    from backend.domain.canon.registry import CORE_ENTITY_TYPES, CORE_PREDICATES

    plan = plan_upgrade(
        LEGACY,
        project_id="pr",
        entity_types=set(CORE_ENTITY_TYPES),
        predicates=dict(CORE_PREDICATES),
        existing={},
        source_artefact_id="legacy-archive:abc",
    )

    assert plan.summary == {"entities": 3, "facts": 1, "already_present": 0, "skipped": 7}
    mara = plan.operations[0]
    assert mara.name == "Mara" and mara.external_ids == {LEGACY_NAMESPACE: "char_mara"}
    # Scalars are kept; nested values such as embeddings are dropped.
    assert mara.attributes == {"age": 30}
    reasons = {(item.reference, item.reason) for item in plan.skipped}
    assert ("belief_1", "type 'Belief' is not a canon entity type") in reasons
    assert ("bad id!", "identifier is not portable") in reasons
    assert ("char_mara-[LOCATED_AT]->loc_keep", "predicate allows one value; first kept") in reasons
    assert ("loc_harbor-[OWNS]->char_mara", "endpoint types do not fit the predicate") in reasons
    # A legacy belief's stored truth flag is never smuggled into canon.
    assert all("is_true" not in str(op.model_dump()) for op in plan.operations)
    assert all("The king lies" not in item.reason for item in plan.skipped)


@pytest.mark.asyncio
async def test_legacy_upgrade_is_only_ever_a_proposal_that_needs_approval() -> None:
    journal = InMemoryCanonJournal()
    repo = InMemoryCanonRepository(journal=journal)
    mutation = MutationService(journal, repo, local_mode=True)
    upgrader = GraphArchiveUpgradeService(mutation=mutation, reader=repo)
    scope = AccessScope(
        actor_user_id="u1", auth_mode=AuthMode.LOCAL, workspace_id="ws", project_id="pr"
    )

    result = await upgrader.propose(scope, "ws", "pr", LEGACY, expected_revision=0)

    assert result.mutation.origin == "import" and result.mutation.status.value == "draft"
    assert repo.entities == {}
    with pytest.raises(ReviewRequiredError) as parked:
        await mutation.commit(scope, result.mutation.id)
    assert "protected_origin" in parked.value.reasons
    assert repo.entities == {}

    await mutation.approve(scope, result.mutation.id, note="reviewed the migration report")
    await mutation.commit(scope, result.mutation.id)
    names = sorted(entity.name for entity in repo.entities.values())
    assert names == ["Harbor", "Keep", "Mara"]
    assert len(repo.facts) == 1

    # Upgrading the same archive again proposes nothing new for what already exists.
    with pytest.raises(GraphArchiveError, match="Nothing in this archive"):
        await upgrader.propose(scope, "ws", "pr", LEGACY, expected_revision=1)


def test_legacy_upgrade_rejects_tampered_wrong_or_oversized_archives() -> None:
    from backend.services.graph_archive_upgrade import MAX_NODES, validate_graph_archive

    with pytest.raises(GraphArchiveError, match="checksum"):
        validate_graph_archive({**LEGACY, "nodes": []})
    with pytest.raises(GraphArchiveError, match="not a supported"):
        validate_graph_archive({"format": "novel-engine/canon-archive", "format_version": "3"})
    with pytest.raises(GraphArchiveError, match="JSON object"):
        validate_graph_archive([])
    huge = _legacy_archive(
        [{"id": f"n{i}", "type": "Character", "properties": {}} for i in range(MAX_NODES + 1)], []
    )
    with pytest.raises(GraphArchiveError, match="exceeds the upgrade bound"):
        validate_graph_archive(huge)
