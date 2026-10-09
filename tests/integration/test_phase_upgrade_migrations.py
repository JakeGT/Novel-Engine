"""Upgrading real data across the Phase 1 → Phase 2 migration chain (2L).

Empty-database migration is covered elsewhere; these tests put representative
rows in place at an older revision and prove the upgrade keeps them, repairs
what the new constraints require, and leaves the project fully usable.
"""

import json

import psycopg
import pytest
from alembic import command

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.journal import PostgresCanonJournal
from backend.core.database import create_engine, create_session_factory
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.journal import MutationStatus
from backend.domain.operations import CreateEntityOp
from backend.domain.validation import CanonValidationError
from backend.migrate import alembic_config, postgres_head_revision
from backend.seed.tenancy import apply_tenancy, counts
from backend.services.canon_equivalence import CanonEquivalenceService
from backend.services.mutation_service import MutationService

pytestmark = pytest.mark.integration

PHASE_1_HEAD = "0011_active_credential_unique"
PRE_REVIEW_HEAD = "0018_commit_integrity"
WS, PR = "ws_seed_harbor", "prj_seed_harbor"


def _config(database):
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", database.sqlalchemy_dsn)
    return config


def _scalar(dsn: str, sql: str, *params):
    with psycopg.connect(dsn) as connection:
        return connection.execute(sql, params).fetchone()[0]


def _scope() -> AccessScope:
    return AccessScope(
        actor_user_id="usr_seed_owner", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR
    )


@pytest.mark.asyncio
async def test_phase_1_data_survives_the_upgrade_and_the_project_becomes_usable(
    disposable_database,
) -> None:
    config, dsn = _config(disposable_database), disposable_database.psycopg_dsn
    # Head → Phase 1 also exercises every Phase 2 downgrade.
    command.downgrade(config, PHASE_1_HEAD)
    assert _scalar(dsn, "SELECT to_regclass('public.branches')") is None
    assert _scalar(dsn, "SELECT to_regclass('public.canon_entities')") is None

    apply_tenancy(dsn)
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO audit_events (id, workspace_id, actor_user_id, action, target_type, "
            "target_id, detail, correlation_id, created_at) VALUES "
            "('aud_1', %s, 'usr_seed_owner', 'project.created', 'project', %s, '{}'::jsonb, '', now())",
            (WS, PR),
        )

    command.upgrade(config, "head")

    assert _scalar(dsn, "SELECT version_num FROM alembic_version") == postgres_head_revision()
    expected = counts()
    for table, key in (
        ("users", "users"),
        ("workspaces", "workspaces"),
        ("projects", "projects"),
        ("memberships", "memberships"),
        ("invitations", "invitations"),
    ):
        assert _scalar(dsn, f"SELECT count(*) FROM {table}") == expected[key], table
    assert _scalar(dsn, "SELECT count(*) FROM audit_events WHERE id = 'aud_1'") == 1

    # A Phase 1 project has no canon yet: it gets a root branch at genesis and
    # accepts its first commit through the Phase 2 path.
    engine = create_engine(disposable_database.settings())
    try:
        factory = create_session_factory(engine)
        journal, repository = PostgresCanonJournal(factory), PostgresCanonRepository(factory)
        branch = await journal.ensure_root_branch(workspace_id=WS, project_id=PR)
        assert branch.is_root and branch.head_revision == 0
        service = MutationService(journal, repository, local_mode=True)
        draft = await service.create_draft(
            _scope(),
            workspace_id=WS,
            project_id=PR,
            operations=[CreateEntityOp(id="ent_first", type="Character", name="Mara")],
            expected_head=0,
        )
        assert (await service.commit(_scope(), draft.id)).revision == 1
    finally:
        await engine.dispose()


def _insert_pre_review_canon(dsn: str) -> None:
    """Rows as they existed at 0018: free-text slugs, no revisions, no ordinals."""
    create = {
        "op": "create_entity",
        "id": "ent_a",
        "type": "Character",
        "name": "A",
        "slug": "fox",
        "attributes": {},
    }
    create_b = {**create, "id": "ent_b", "name": "B"}
    canonical = json.dumps([create, create_b], sort_keys=True, separators=(",", ":"))
    import hashlib

    checksum = hashlib.sha256(canonical.encode()).hexdigest()
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO branches (id, workspace_id, project_id, name, is_root, head_revision) "
            "VALUES ('brn_old', %s, %s, 'main', true, 1)",
            (WS, PR),
        )
        connection.execute(
            "INSERT INTO mutation_sets (id, workspace_id, project_id, branch_id, status, origin, "
            "expected_head) VALUES ('mut_old', %s, %s, 'brn_old', 'committed', 'author', 0)",
            (WS, PR),
        )
        connection.execute(
            "INSERT INTO mutation_sets (id, workspace_id, project_id, branch_id, status, origin, "
            "expected_head) VALUES ('mut_pending', %s, %s, 'brn_old', 'draft', 'model', 1)",
            (WS, PR),
        )
        for mutation_id, operations in (
            ("mut_old", [create, create_b]),
            (
                "mut_pending",
                [
                    {
                        "op": "create_entity",
                        "id": "tmp:c",
                        "type": "Character",
                        "name": "C",
                        "slug": None,
                        "attributes": {},
                    }
                ],
            ),
        ):
            for ordinal, payload in enumerate(operations):
                connection.execute(
                    "INSERT INTO mutation_operations (mutation_id, ordinal, workspace_id, "
                    "project_id, operation_type, payload) VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
                    (mutation_id, ordinal, WS, PR, payload["op"], json.dumps(payload)),
                )
        connection.execute(
            "INSERT INTO review_decisions (id, mutation_id, workspace_id, project_id, decision, "
            "actor_user_id, note) VALUES ('opn_old', 'mut_old', %s, %s, 'approved', "
            "'usr_seed_owner', 'author-direct')",
            (WS, PR),
        )
        connection.execute(
            "INSERT INTO commit_envelopes (commit_id, mutation_id, workspace_id, project_id, "
            "branch_id, revision, expected_head, canonical, checksum, actor_user_id) VALUES "
            "('cmt_old', 'mut_old', %s, %s, 'brn_old', 1, 0, %s, %s, 'usr_seed_owner')",
            (WS, PR, canonical, checksum),
        )
        connection.execute(
            "INSERT INTO canon_revisions (id, revision, workspace_id, project_id, branch_id, "
            "commit_id, expected_head, result_head, envelope_checksum) VALUES "
            "('rev_old', 1, %s, %s, 'brn_old', 'cmt_old', 0, 1, %s)",
            (WS, PR, checksum),
        )
        connection.execute(
            "INSERT INTO canon_provenance (workspace_id, project_id, branch_id, id, origin, "
            "actor_user_id) VALUES (%s, %s, 'brn_old', 'prov_cmt_old', 'author', 'usr_seed_owner')",
            (WS, PR),
        )
        for payload in (create, create_b):
            # Two active entities sharing one slug: legal before 0020, not after.
            connection.execute(
                "INSERT INTO canon_entities (workspace_id, project_id, branch_id, id, type, name, "
                "slug, lifecycle, created_revision, attributes) VALUES "
                "(%s, %s, 'brn_old', %s, 'Character', %s, 'fox', 'active', 1, '{}'::jsonb)",
                (WS, PR, payload["id"], payload["name"]),
            )
            connection.execute(
                "INSERT INTO canon_record_versions (workspace_id, project_id, branch_id, "
                "record_type, record_id, revision, operation_type, commit_id, change) VALUES "
                "(%s, %s, 'brn_old', 'entity', %s, 1, 'create_entity', 'cmt_old', %s::jsonb)",
                (WS, PR, payload["id"], json.dumps(payload)),
            )


@pytest.mark.asyncio
async def test_pre_review_canon_is_upgraded_in_place(disposable_database) -> None:
    config, dsn = _config(disposable_database), disposable_database.psycopg_dsn
    command.downgrade(config, PRE_REVIEW_HEAD)
    apply_tenancy(dsn)
    _insert_pre_review_canon(dsn)

    command.upgrade(config, "head")

    # 0019: every existing operation gained an immutable first revision.
    assert _scalar(dsn, "SELECT count(*) FROM mutation_operation_revisions") == 3
    assert _scalar(dsn, "SELECT min(revision) FROM mutation_operations") == 1
    assert _scalar(dsn, "SELECT intent FROM mutation_sets WHERE id = 'mut_old'") == "edit"
    # 0020: the earliest holder keeps a duplicated active slug; the other is cleared,
    # never deleted, so the unique index could be built.
    assert _scalar(dsn, "SELECT slug FROM canon_entities WHERE id = 'ent_a'") == "fox"
    assert _scalar(dsn, "SELECT slug FROM canon_entities WHERE id = 'ent_b'") is None
    assert _scalar(dsn, "SELECT count(*) FROM canon_entities") == 2
    assert _scalar(dsn, "SELECT aliases::text FROM canon_entities WHERE id = 'ent_a'") == "[]"
    assert _scalar(dsn, "SELECT max(ordinal) FROM canon_record_versions") == 0

    engine = create_engine(disposable_database.settings())
    try:
        factory = create_session_factory(engine)
        journal, repository = PostgresCanonJournal(factory), PostgresCanonRepository(factory)
        service = MutationService(journal, repository, local_mode=True)

        # The pre-existing envelope checksum still verifies and replays to the
        # entities' *original* state; the slug repair is reported, not hidden.
        report = await CanonEquivalenceService(canon=repository, journal=journal).verify(WS, PR)
        assert report.problems == ["normalized canon vs journal: entity ent_b differs in slug"]

        # A pending model proposal from before the review gate still needs approval.
        pending = await journal.get_mutation(
            workspace_id=WS, project_id=PR, mutation_id="mut_pending"
        )
        assert pending.status is MutationStatus.DRAFT
        with pytest.raises(CanonValidationError, match="review_required"):
            await service.commit(_scope(), "mut_pending")
        await service.approve(_scope(), "mut_pending", note="reviewed after upgrade")
        assert (await service.commit(_scope(), "mut_pending")).revision == 2

        # The new slug rule now applies to new writes.
        clash = await service.create_draft(
            _scope(),
            workspace_id=WS,
            project_id=PR,
            operations=[CreateEntityOp(id="ent_d", type="Character", name="D", slug="fox")],
            expected_head=2,
        )
        with pytest.raises(CanonValidationError, match="slug_conflict"):
            await service.commit(_scope(), clash.id)
    finally:
        await engine.dispose()


def test_phase_2_migrations_roll_back_and_forward(disposable_database) -> None:
    config, dsn = _config(disposable_database), disposable_database.psycopg_dsn
    for _ in range(2):
        command.downgrade(config, PRE_REVIEW_HEAD)
        assert _scalar(dsn, "SELECT to_regclass('public.mutation_transitions')") is None
        assert _scalar(dsn, "SELECT to_regclass('public.archive_imports')") is None
        command.upgrade(config, "head")
        assert _scalar(dsn, "SELECT version_num FROM alembic_version") == postgres_head_revision()
        assert _scalar(dsn, "SELECT to_regclass('public.media_lifecycle_events')") is not None
