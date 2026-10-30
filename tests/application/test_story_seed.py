"""The reference world exercises every layer (QLT-001).

Canon: story time (a start, a span, an event at a point), a secret fact and one everyone
knows, a thread opened, moved along and resolved by three timed scenes, an arc placed in
them, and a claim. Beside canon: a scene's prose with a kept draft and the context it was
written from, a name answer limited to part of the story, and a what-if with its own
changes. Seeding twice changes nothing; a database seeded before the later steps existed
takes them instead of reporting that it diverged.
"""

import pytest
import pytest_asyncio

from backend.domain.branching import working_in
from backend.domain.canon.assertions import FactVisibility
from backend.domain.sources import NamePlace, applicable
from backend.seed import canon as canon_seed
from backend.seed.apply import SEED_PROJECT_ID, SEED_WORKSPACE_ID
from backend.seed.canon import (
    ARC,
    CLAIM_EVENT,
    PRIMARY_HEAD,
    SCENE_DUSK,
    SCENE_INQUIRY,
    SCENE_SALTKEEP,
    SEED_PROJECTS,
    SPAN_FACT,
    THREAD,
    SeedProject,
    apply_canon_seed,
)
from backend.seed.story import (
    DRAFT,
    MANIFEST,
    NAME_ANSWER,
    PROSE_TEXT,
    PROSE_V2,
    WHAT_IF,
    apply_story_seed,
)
from tests.application.test_canon_seed import _World
from tests.doubles import InMemoryManifestStore, InMemorySourceStore, InMemoryStudioStore

pytestmark = pytest.mark.application

P = {"workspace_id": SEED_WORKSPACE_ID, "project_id": SEED_PROJECT_ID}


class _Story(_World):
    def __init__(self) -> None:
        super().__init__()
        self.sources = InMemorySourceStore()
        self.studio = InMemoryStudioStore()
        self.manifests = InMemoryManifestStore()
        # The double copies scene links into a what-if, as _FORKED_TABLES does.
        self.repo.studio = self.studio

    async def seed_story(self):
        return await apply_story_seed(
            journal=self.journal,
            repository=self.repo,
            schemas=self.schemas,
            sources=self.sources,
            studio=self.studio,
            manifests=self.manifests,
        )


@pytest_asyncio.fixture
async def world() -> _Story:
    built = _Story()
    await built.publish_schemas()
    return built


@pytest.mark.asyncio
async def test_the_reference_world_has_every_layer(world) -> None:
    canon = await world.seed()
    assert canon.ok, canon.problems
    story = await world.seed_story()
    assert story.ok, story.problems

    # Story time and who may know.
    span = await world.repo.get_fact(**P, fact_id=SPAN_FACT)
    assert (span.starts.at, span.ends.at) == (2, 5)
    secret = await world.repo.get_fact(**P, fact_id="fct_seed_iven_ledger")
    assert secret.visibility is FactVisibility.SECRET
    # The plan: three scenes in story order under an arc, and the thread through them.
    items = {item.id: item for item in await world.repo.list_plan_items(**P)}
    assert items[THREAD].kind.value == "thread" and items[ARC].kind.value == "arc"
    assert THREAD in items[SCENE_DUSK].sets_up and THREAD in items[SCENE_INQUIRY].pays_off
    # A claim, said in a scene and heard by Mara; never a fact.
    claim = await world.repo.get_event(**P, event_id=CLAIM_EVENT)
    assert claim.kind == "claim" and claim.claim.scene_id == SCENE_INQUIRY
    assert {p.role for p in claim.participants} == {"speaker", "perceiver"}

    # The scene's prose, and the draft the author kept, with its context.
    versions = await world.sources.list_versions(**P, source_id=PROSE_TEXT)
    assert [v.origin.value for v in versions] == ["author", "assistant"]
    assert versions[1].draft_id == DRAFT and versions[1].manifest_id == MANIFEST
    draft = await world.studio.get_draft(**P, draft_id=DRAFT)
    assert draft.status == "kept" and draft.kept_version_id == PROSE_V2
    saved = await world.manifests.get(**P, manifest_id=MANIFEST)
    assert saved.policy_version == "scene-draft/3" and saved.plan_id == SCENE_DUSK

    # "The pilot" means Mara only from the Saltkeep scene on.
    (answer,) = [b for b in await world.sources.list_bindings(**P) if b.id == NAME_ANSWER]
    assert answer.from_scene_id == SCENE_SALTKEEP
    order = {SCENE_DUSK: 0, SCENE_SALTKEEP: 1, SCENE_INQUIRY: 2}
    before = NamePlace(scene_id=SCENE_DUSK, order=order)
    after = NamePlace(scene_id=SCENE_INQUIRY, order=order)
    assert applicable([answer], before) == [] and applicable([answer], after) == [answer]

    # The what-if has its own changes; the main story keeps its secret.
    with working_in(WHAT_IF):
        there = await world.repo.get_fact(**P, fact_id="fct_seed_iven_ledger")
    assert there.visibility is FactVisibility.COMMON
    branch = await world.journal.get_branch(**P, branch_id=WHAT_IF)
    assert await world.repo.get_head(**P, branch_id=WHAT_IF) == (branch.fork_revision or 0) + 1


@pytest.mark.asyncio
async def test_seeding_the_layers_again_changes_nothing(world) -> None:
    await world.seed()
    first = await world.seed_story()
    assert first.ok and first.written
    mutations = len(world.journal.mutations)
    versions = len(world.sources.versions)

    again = await world.seed_story()

    assert again.ok, again.problems
    assert again.written == []
    assert len(world.journal.mutations) == mutations and len(world.sources.versions) == versions


@pytest.mark.asyncio
async def test_the_layers_need_the_canon_seed_first(world) -> None:
    report = await world.seed_story()
    assert not report.ok and "seed canon first" in report.problems[0]


@pytest.mark.asyncio
async def test_a_world_seeded_before_the_later_steps_takes_them(world, monkeypatch) -> None:
    """Steps are appended and keep their first keys, so an older seed is brought up to date."""
    primary = SEED_PROJECTS[0]
    earlier = SeedProject(
        primary.workspace_id, primary.project_id, primary.actor_user_id, primary.steps[:7]
    )
    monkeypatch.setattr(canon_seed, "SEED_PROJECTS", (earlier, *SEED_PROJECTS[1:]))
    await world.seed()
    monkeypatch.setattr(canon_seed, "SEED_PROJECTS", SEED_PROJECTS)

    report = await apply_canon_seed(
        journal=world.journal,
        repository=world.repo,
        schemas=world.schemas,
        media=world.media,
        objects=world.objects,
    )

    assert report.ok, report.problems
    assert report.heads[f"{SEED_WORKSPACE_ID}/{SEED_PROJECT_ID}"] == PRIMARY_HEAD


@pytest.mark.asyncio
async def test_the_seeded_context_is_kept_in_the_main_story_as_the_product_keeps_it(world) -> None:
    await world.seed()
    saved: list[dict] = []
    keep = world.manifests.save

    async def spy(**fields):
        saved.append(fields)
        await keep(**fields)

    world.manifests.save = spy
    story = await world.seed_story()
    assert story.ok, story.problems
    (manifest,) = saved
    assert manifest["manifest_id"] == MANIFEST and manifest["branch_id"] is None


@pytest.mark.asyncio
async def test_a_seeded_world_that_has_been_written_in_still_checks_out(world) -> None:
    from backend.domain.sources import content_hash

    await world.seed()
    assert (await world.seed_story()).ok
    main = await world.journal.ensure_root_branch(**P)
    # The author writes on in the scene's text, then moves the scene to other words.
    await world.sources.add_version(
        **P,
        version_id="ver_author_more",
        source_id=PROSE_TEXT,
        content="More words.",
        content_hash=content_hash("More words."),
        actor_user_id=None,
        parent_version_id=PROSE_V2,
    )
    await world.studio.link(
        **P, branch_id=main.id, plan_id=SCENE_DUSK, source_id="src_author_other"
    )

    again = await world.seed_story()
    assert again.ok, again.problems
