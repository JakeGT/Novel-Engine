"""A complete project archive on PostgreSQL: what-ifs with their own changes and scene
texts, readings and conversations with the assistant travel and restore exactly once."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from backend.adapters.assist_store import PostgresAssistSessionStore
from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.journal import PostgresCanonJournal
from backend.adapters.media_store import PostgresMediaStore
from backend.adapters.schema_store import PostgresSchemaVersionStore
from backend.adapters.source_store import PostgresSourceStore
from backend.adapters.studio_store import PostgresStudioStore
from backend.core.database import create_engine, create_session_factory
from backend.domain.assist import AssistKind, AssistSession, AssistStatus
from backend.domain.branching import working_in
from backend.domain.dismissals import DismissReason, FindingDismissal, FindingKind
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import CreateEntityOp
from backend.domain.sources import SourceKind, content_hash
from backend.domain.studio import SceneReading
from backend.services.canon_archive import CanonArchiveService
from backend.services.mutation_service import MutationService
from backend.services.what_if_service import WhatIfService
from tests.doubles import InMemoryObjectStore

pytestmark = pytest.mark.integration

WS, PR, RESTORED = "ws_archive_wi", "pr_archive_wi", "pr_archive_wi_restored"
S = {"workspace_id": WS, "project_id": PR}
R = {"workspace_id": WS, "project_id": RESTORED}
SCOPE = AccessScope(
    actor_user_id="usr_author", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR
)


@pytest_asyncio.fixture
async def stack(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _text(sources, source_id: str, content: str) -> str:
    await sources.add_source(
        **S, source_id=source_id, title="The Docks", kind=SourceKind.SCENE, actor_user_id="u1"
    )
    version_id = f"{source_id}_v1"
    await sources.add_version(
        **S,
        version_id=version_id,
        source_id=source_id,
        content=content,
        content_hash=content_hash(content),
        actor_user_id="u1",
    )
    return version_id


async def _save(mutation: MutationService, name: str, head: int) -> str:
    draft = await mutation.create_draft(
        SCOPE,
        workspace_id=WS,
        project_id=PR,
        operations=[CreateEntityOp(id=f"tmp:{name}", type="Character", name=name)],
        expected_head=head,
    )
    await mutation.commit(SCOPE, draft.id)
    return draft.id


async def _names(repository, scope: dict, branch_id: str | None) -> set[str]:
    with working_in(branch_id):
        entities = await repository.list_entities(**scope, include_retired=True, limit=100)
    return {entity.name for entity in entities}


@pytest.mark.asyncio
async def test_a_what_if_its_scene_text_reading_and_a_conversation_restore_on_postgres(
    stack,
) -> None:
    factory = stack
    repository = PostgresCanonRepository(factory)
    journal = PostgresCanonJournal(factory)
    studio, sources = PostgresStudioStore(factory), PostgresSourceStore(factory)
    assists = PostgresAssistSessionStore(factory)
    mutation = MutationService(journal, repository, local_mode=True)
    what_ifs = WhatIfService(journal=journal, canon=repository, mutation=mutation, local_mode=True)
    archive_service = CanonArchiveService(
        canon=repository,
        journal=journal,
        schemas=PostgresSchemaVersionStore(factory),
        media=PostgresMediaStore(factory),
        objects=InMemoryObjectStore(),
        local_mode=True,
        sources=sources,
        studio=studio,
        assists=assists,
    )

    with working_in(None):
        mara = await _save(mutation, "Mara", 0)
    main = await journal.ensure_root_branch(**S)
    await _text(sources, "src_main", "Tobin waited.")
    await studio.link(**S, branch_id=main.id, plan_id="plan_1", source_id="src_main")
    what_if = await what_ifs.start(SCOPE, WS, PR, name="What if Mara came?")
    with working_in(what_if.id):
        only_here = await _save(mutation, "Only in the what-if", 1)
    # The what-if's own copy of the scene's words.
    copy_version = await _text(sources, "src_copy", "Tobin waited. Mara came.")
    await studio.link(**S, branch_id=what_if.id, plan_id="plan_1", source_id="src_copy")
    with working_in(None):
        await _save(mutation, "Added after the what-if", 1)
    await studio.add_reading(
        SceneReading(
            **S,
            branch_id=what_if.id,
            id="rdg_w",
            plan_id="plan_1",
            source_id="src_copy",
            version_id=copy_version,
            job_id="job_gone",
            passages=2,
            proposals=(only_here,),
            discarded=0,
            created_by="usr_author",
            created_at=datetime.now(UTC),
            covered=((0, 24),),
        )
    )
    now = datetime.now(UTC)
    # A dismissal in the main story, taken back, and a standing one in the what-if.
    for branch_id, dismissal_id, withdrawn in (
        (main.id, "dsm_main", now),
        (what_if.id, "dsm_what_if", None),
    ):
        await studio.restore_dismissal(
            FindingDismissal(
                **S,
                branch_id=branch_id,
                id=dismissal_id,
                plan_id="plan_1",
                fingerprint="b" * 64,
                kind=FindingKind.NOTE,
                code="continuity",
                reason=DismissReason.NOT_A_PROBLEM,
                note="",
                expires_at=now,
                created_by="usr_author",
                created_at=now,
                withdrawn_at=withdrawn,
            )
        )
    await assists.save(
        AssistSession(
            id="asx_archive_wi",
            **S,
            actor_user_id="usr_author",
            kind=AssistKind.CREATE,
            status=AssistStatus.PROPOSED,
            description="A pilot who keeps her own counsel.",
            mutation_id=mara,
            created_at=now,
            updated_at=now,
        )
    )

    (source_reading,) = await studio.readings_in(**S, branch_id=what_if.id)
    archive = await archive_service.export_project(WS, PR)
    (exported,) = archive["what_ifs"]
    assert exported["fork_revision"] == 1 and len(exported["commits"]) == 1
    assert len(archive["conversations"]) == 1

    result = await archive_service.import_project(WS, RESTORED, archive)
    assert result["what_ifs"] == 1 and result["readings"] == 1 and result["conversations"] == 1
    assert result["dismissals"] == 2

    (back,) = [b for b in await journal.list_branches(**R) if not b.is_root]
    assert back.name == "What if Mara came?" and back.fork_revision == 1
    assert await repository.get_head(**R, branch_id=back.id) == 2
    assert await _names(repository, R, back.id) == {"Mara", "Only in the what-if"}
    assert await _names(repository, R, None) == {"Mara", "Added after the what-if"}
    restored_main = await journal.ensure_root_branch(**R)
    assert await studio.manuscripts_for(**R, branch_id=restored_main.id) == [("plan_1", "src_main")]
    assert await studio.manuscripts_for(**R, branch_id=back.id) == [("plan_1", "src_copy")]

    (reading,) = await studio.readings_in(**R, branch_id=back.id)
    (what_if_commit,) = await repository.list_commits(**R, branch_id=back.id, after_revision=1)
    envelope = await repository.get_envelope(what_if_commit.commit_id)
    # Read when the original was read, not when it was restored.
    assert reading.created_at == source_reading.created_at
    assert reading.proposals == (envelope.mutation_id,)
    assert reading.job_id is None and reading.covered == ((0, 24),)

    # The original conversation keeps its id; the copy beside it gets its own.
    (talk,) = await assists.list_for_project(**R, limit=10)
    first = (await repository.list_commits(**R, branch_id=restored_main.id))[0]
    assert talk.id != "asx_archive_wi" and talk.id.startswith("asx_archive_wi-")
    assert talk.mutation_id == (await repository.get_envelope(first.commit_id)).mutation_id

    # Again: nothing new.
    again = await archive_service.import_project(WS, RESTORED, archive)
    assert again["readings"] == 0 and again["conversations"] == 0 and again["dismissals"] == 0
    (kept,) = await studio.dismissals_in(**R, branch_id=back.id)
    assert (kept.id, kept.withdrawn_at, kept.expires_at) == ("dsm_what_if", None, now)
    (undone,) = await studio.dismissals_in(**R, branch_id=restored_main.id)
    assert undone.withdrawn_at == now
    assert len(await journal.list_branches(**R)) == 2
    assert len(await studio.readings_in(**R, branch_id=back.id)) == 1
    assert len(await assists.list_for_project(**R, limit=10)) == 1
