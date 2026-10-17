"""The scene studio's records in PostgreSQL (ADR-022): which text holds a scene's prose in
each line of the story, drafts waiting for the author, findings, the author's voice, and
where each version of the words came from."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.journal import PostgresCanonJournal
from backend.adapters.source_store import PostgresSourceStore
from backend.adapters.studio_store import PostgresStudioStore
from backend.core.database import create_engine, create_session_factory, session_scope
from backend.domain.branching import working_in
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import CreateEntityOp
from backend.domain.sources import SourceKind, VersionOrigin, content_hash
from backend.domain.studio import AssistantFinding, SceneDraft
from backend.services.mutation_service import MutationService
from backend.services.what_if_service import WhatIfService

pytestmark = pytest.mark.integration

WS, PR = "ws_studio_it", "pr_studio_it"
S = {"workspace_id": WS, "project_id": PR}
SCOPE = AccessScope(
    actor_user_id="usr_author", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR
)


@pytest_asyncio.fixture
async def stack(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    try:
        yield PostgresStudioStore(factory), PostgresSourceStore(factory), factory
    finally:
        await engine.dispose()


async def _text(sources, source_id: str, content: str = "Tobin waited.") -> None:
    await sources.add_source(
        **S, source_id=source_id, title="The Docks", kind=SourceKind.SCENE, actor_user_id="u1"
    )
    await sources.add_version(
        **S,
        version_id=f"{source_id}_v1",
        source_id=source_id,
        content=content,
        content_hash=content_hash(content),
        actor_user_id="u1",
    )


def _draft(draft_id: str, *, branch_id: str | None = None, number: int = 1) -> SceneDraft:
    return SceneDraft(
        **S,
        id=draft_id,
        branch_id=branch_id,
        plan_id="plan_1",
        job_id="job_1",
        manifest_id="ctx_1",
        number=number,
        content=f"Draft {draft_id}: Tobin watched the water.",
        model="model-a",
        status="proposed",
        kept_version_id=None,
        created_by="usr_author",
        created_at=datetime.now(UTC),
    )


def _finding(finding_id: str, kind: str, target: str, start: int | None) -> AssistantFinding:
    return AssistantFinding(
        id=finding_id,
        target_kind=kind,
        target_id=target,
        job_id="job_2",
        category="continuity",
        message="The tide went out in the last scene.",
        suggestion="Let it go out.",
        start=start,
        end=None if start is None else start + 4,
    )


@pytest.mark.asyncio
async def test_each_line_of_the_story_links_its_own_text_to_a_scene(stack) -> None:
    studio, sources, _ = stack
    await _text(sources, "src_main")
    await _text(sources, "src_copy")
    assert await studio.manuscript(**S, branch_id="b_main", plan_id="plan_1") is None

    await studio.link(**S, branch_id="b_main", plan_id="plan_1", source_id="src_main")
    await studio.link(**S, branch_id="b_main", plan_id="plan_2", source_id="src_main")
    await studio.link(**S, branch_id="b_whatif", plan_id="plan_1", source_id="src_main")
    assert await studio.manuscript(**S, branch_id="b_main", plan_id="plan_1") == "src_main"
    assert await studio.manuscripts_for(**S, branch_id="b_main") == [
        ("plan_1", "src_main"),
        ("plan_2", "src_main"),
    ]
    # Shared with the what-if, so the what-if must copy before changing it.
    assert await studio.linked_elsewhere(**S, branch_id="b_whatif", source_id="src_main")
    # Two scenes in the same line sharing a text is not "elsewhere".
    assert not await studio.linked_elsewhere(**S, branch_id="b_other", source_id="src_copy")

    # The what-if's first change links its own copy; the main story keeps its text.
    await studio.link(**S, branch_id="b_whatif", plan_id="plan_1", source_id="src_copy")
    assert await studio.manuscript(**S, branch_id="b_whatif", plan_id="plan_1") == "src_copy"
    assert await studio.manuscript(**S, branch_id="b_main", plan_id="plan_1") == "src_main"
    assert not await studio.linked_elsewhere(**S, branch_id="b_whatif", source_id="src_copy")
    assert await studio.manuscripts_for(**S, branch_id="b_whatif") == [("plan_1", "src_copy")]
    # Another project sees none of it.
    assert (
        await studio.manuscripts_for(workspace_id=WS, project_id="pr_other", branch_id="b_main")
        == []
    )


@pytest.mark.asyncio
async def test_drafts_wait_in_their_own_line_until_kept_or_discarded(stack) -> None:
    studio, _, _ = stack
    await studio.add_draft(_draft("drf_main_2", number=2))
    await studio.add_draft(_draft("drf_main_1", number=1))
    await studio.add_draft(_draft("drf_whatif", branch_id="b_whatif"))

    main = await studio.drafts(**S, branch_id=None, plan_id="plan_1")
    assert [d.id for d in main] == ["drf_main_2", "drf_main_1"]
    assert main[0].branch_id is None and main[0].manifest_id == "ctx_1"
    assert main[0].created_by == "usr_author" and main[0].model == "model-a"
    assert [d.id for d in await studio.drafts(**S, branch_id="b_whatif", plan_id="plan_1")] == [
        "drf_whatif"
    ]
    assert await studio.drafts(**S, branch_id=None, plan_id="plan_2") == []

    await studio.mark_kept(**S, draft_id="drf_main_1", version_id="srv_kept")
    kept = await studio.get_draft(**S, draft_id="drf_main_1")
    # Once kept, its words live in the version; the draft only says where they went.
    assert (kept.status, kept.kept_version_id, kept.content) == ("kept", "srv_kept", "")
    assert [d.id for d in await studio.drafts(**S, branch_id=None, plan_id="plan_1")] == [
        "drf_main_2"
    ]

    await studio.add_findings(**S, findings=[_finding("fnd_d", "draft", "drf_main_2", 3)])
    await studio.discard(**S, draft_id="drf_main_2")
    assert await studio.get_draft(**S, draft_id="drf_main_2") is None
    assert await studio.findings(**S, target_kind="draft", target_id="drf_main_2") == []
    assert (
        await studio.get_draft(workspace_id=WS, project_id="pr_other", draft_id="drf_whatif")
        is None
    )


@pytest.mark.asyncio
async def test_findings_are_kept_against_what_was_checked(stack) -> None:
    studio, _, factory = stack
    await studio.add_findings(
        **S,
        findings=[
            _finding("fnd_late", "version", "srv_1", 40),
            _finding("fnd_none", "version", "srv_1", None),
            _finding("fnd_early", "version", "srv_1", 2),
            _finding("fnd_other", "version", "srv_2", 0),
            _finding("fnd_draft", "draft", "srv_1", 0),
        ],
    )
    found = await studio.findings(**S, target_kind="version", target_id="srv_1")
    assert [f.id for f in found] == ["fnd_early", "fnd_late", "fnd_none"]
    first = found[0]
    assert (first.start, first.end, first.job_id, first.category) == (2, 6, "job_2", "continuity")
    assert first.message.startswith("The tide") and first.suggestion == "Let it go out."
    assert first.created_at is not None
    assert [f.id for f in await studio.findings(**S, target_kind="draft", target_id="srv_1")] == [
        "fnd_draft"
    ]
    await studio.add_findings(**S, findings=[])
    async with session_scope(factory) as db:
        assert (await db.execute(text("SELECT count(*) FROM draft_findings"))).scalar_one() == 5


@pytest.mark.asyncio
async def test_the_authors_voice_is_one_note_per_story_replaced_in_place(stack) -> None:
    studio, _, factory = stack
    assert await studio.voice(**S) == ""
    await studio.set_voice(**S, notes="Short sentences.")
    await studio.set_voice(**S, notes="Short sentences. Present tense.")
    assert await studio.voice(**S) == "Short sentences. Present tense."
    assert await studio.voice(workspace_id=WS, project_id="pr_other") == ""
    async with session_scope(factory) as db:
        assert (await db.execute(text("SELECT count(*) FROM story_voices"))).scalar_one() == 1


@pytest.mark.asyncio
async def test_each_version_remembers_where_its_words_came_from(stack) -> None:
    _, sources, _ = stack
    await _text(sources, "src_1", "Tobin waited.")
    (first,) = await sources.list_versions(**S, source_id="src_1")
    assert first.origin is VersionOrigin.AUTHOR and first.parent_version_id is None
    assert first.draft_id is None and first.manifest_id is None
    kept = await sources.add_version(
        **S,
        version_id="src_1_v2",
        source_id="src_1",
        content="Tobin watched the water.",
        content_hash=content_hash("Tobin watched the water."),
        actor_user_id="u1",
        origin=VersionOrigin.ASSISTANT,
        parent_version_id=first.id,
        draft_id="drf_1",
        manifest_id="ctx_1",
    )
    assert kept.number == 2 and kept.origin is VersionOrigin.ASSISTANT
    _, again = await sources.list_versions(**S, source_id="src_1")
    assert (again.origin, again.parent_version_id, again.draft_id, again.manifest_id) == (
        VersionOrigin.ASSISTANT,
        first.id,
        "drf_1",
        "ctx_1",
    )
    # The text's kind for the studio is accepted by the database.
    assert (await sources.get_source(**S, source_id="src_1")).kind is SourceKind.SCENE


@pytest.mark.asyncio
async def test_starting_a_what_if_copies_each_scenes_link_to_its_text(stack) -> None:
    studio, sources, factory = stack
    repository = PostgresCanonRepository(factory)
    journal = PostgresCanonJournal(factory)
    mutation = MutationService(journal, repository, local_mode=True)
    what_ifs = WhatIfService(journal=journal, canon=repository, mutation=mutation, local_mode=True)
    with working_in(None):
        draft = await mutation.create_draft(
            SCOPE,
            workspace_id=WS,
            project_id=PR,
            operations=[CreateEntityOp(id="tmp:m", type="Character", name="Mara")],
            expected_head=0,
        )
        await mutation.commit(SCOPE, draft.id)
    main = await journal.ensure_root_branch(**S)
    await _text(sources, "src_main")
    await studio.link(**S, branch_id=main.id, plan_id="plan_1", source_id="src_main")

    what_if = await what_ifs.start(SCOPE, WS, PR, name="What if Mara came?")
    assert await studio.manuscripts_for(**S, branch_id=what_if.id) == [("plan_1", "src_main")]
    # The text itself is shared until the what-if changes it.
    assert await studio.linked_elsewhere(**S, branch_id=what_if.id, source_id="src_main")
    assert await studio.manuscripts_for(**S, branch_id=main.id) == [("plan_1", "src_main")]
