"""Building, switching and rolling back search generations, and gated picture search
(CTX-011, CTX-012). In-memory store; the deterministic stand-in embedder."""

from __future__ import annotations

import io

import pytest

from backend.adapters.embedding_fake import DeterministicEmbedding
from backend.adapters.vector import PgVectorStore
from backend.domain.embedding import (
    CaptionSource,
    EmbeddingError,
    GenerationState,
    MixedSpaceError,
    Modality,
    SearchGate,
    TextInput,
    VectorRow,
)
from backend.services.embedding_index import (
    BuildCancelled,
    EmbeddingIndexService,
    NotCaughtUp,
    caption_row_id,
    picture_row_id,
    wire_media,
    wire_observations,
)
from backend.services.media_search import MediaSearchService
from tests.embedding_doubles import FakeObjects, InMemoryEmbeddingIndexStore, MediaWorld, Picture

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

W, P = "ws_a", "prj_a"


def _png(seed: int) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (seed % 256, (seed * 7) % 256, (seed * 13) % 256)).save(
        buffer, format="PNG"
    )
    return buffer.getvalue()


class Rig:
    def __init__(self, embedder: DeterministicEmbedding | None = None) -> None:
        self.world = MediaWorld()
        self.index = InMemoryEmbeddingIndexStore(self.world)
        self.objects = FakeObjects(self.world)
        self.embedder = embedder or DeterministicEmbedding(dimension=256)
        self.vectors = PgVectorStore("unused", self.embedder, index=self.index)
        self.service = EmbeddingIndexService(
            index=self.index, embedder=self.embedder, objects=self.objects
        )
        self.search = MediaSearchService(index=self.index, embedder=self.embedder)

    def use(self, embedder: DeterministicEmbedding) -> None:
        """Change the configured model (as a restart with new settings would)."""
        self.embedder = embedder
        self.vectors = PgVectorStore("unused", embedder, index=self.index)
        self.service = EmbeddingIndexService(
            index=self.index, embedder=embedder, objects=self.objects
        )
        self.search = MediaSearchService(index=self.index, embedder=embedder)

    def picture(self, asset: str, words: str, **flags) -> Picture:  # type: ignore[no-untyped-def]
        data = _png(len(self.world.pictures.get((W, P), [])) + 1)
        picture = self.world.add_picture(
            W, P, Picture(asset_id=asset, version_id=f"ver_{asset}", data=data, **flags)
        )
        self.embedder.describe(data, words)
        return picture

    def passage(self, key: str, words: str) -> None:
        self.vectors.add_documents(
            [words], [{"kind": "passage"}], [key], workspace_id=W, project_id=P
        )


async def _built(rig: Rig) -> str:
    generation = await rig.service.start_build()
    await rig.service.build_all(generation.id)
    await rig.service.activate(generation.id)
    return generation.id


# --- building, switching, rolling back -------------------------------------------------


async def test_a_new_model_is_built_beside_the_old_and_switched_atomically() -> None:
    rig = Rig(DeterministicEmbedding(dimension=256, modalities=(Modality.TEXT,), model="text"))
    rig.passage("passage:s1:1", "the windswept basalt coast")
    old = rig.index.active()
    assert old is not None

    rig.use(DeterministicEmbedding(dimension=512, model="multi"))
    # Until the new index is switched to, search by meaning is off rather than mixed.
    assert rig.vectors.query("basalt", workspace_id=W, project_id=P) == []
    status = await rig.service.status()
    assert status.searchable is False and "different model" in (status.reason or "")

    new = await rig.service.start_build()
    assert new.previous_generation_id == old.id
    with pytest.raises(NotCaughtUp):
        await rig.service.activate(new.id)
    report = await rig.service.build_all(new.id)
    assert report.documents == 1
    activated = await rig.service.activate(new.id)
    assert activated.state is GenerationState.ACTIVE
    assert rig.index.get(old.id).state is GenerationState.RETIRED  # type: ignore[union-attr]
    assert (
        rig.vectors.query("basalt coast", workspace_id=W, project_id=P)[0]["id"] == "passage:s1:1"
    )

    restored = await rig.service.rollback()
    assert restored.id == old.id
    # The configured model is still the new one, so the old index is not searched with it.
    assert rig.vectors.query("basalt", workspace_id=W, project_id=P) == []


async def test_rebuild_is_idempotent_and_equivalent() -> None:
    rig = Rig()
    rig.passage("passage:s1:1", "lantern on the harbour wall")
    rig.picture("ast_coast", "windswept basalt coast")
    first = await _built(rig)
    again = await rig.service.build_project(first, W, P)
    assert (again.documents, again.pictures, again.captions) == (0, 0, 0)
    assert again.unchanged >= 1

    second = await rig.service.start_build()
    await rig.service.build_all(second.id)
    one = {(r.id, r.content_hash, r.values) for r in rig.index.rows(first)}
    two = {(r.id, r.content_hash, r.values) for r in rig.index.rows(second.id)}
    assert one == two  # same model, same inputs: the same index


async def test_a_build_can_be_stopped_and_what_it_made_is_removed() -> None:
    rig = Rig()
    for n in range(40):
        rig.passage(f"passage:s:{n}", f"words number {n}")
    await _built(rig)
    building = await rig.service.start_build()
    steps = 0

    async def step(stage: str, pct: int) -> None:
        nonlocal steps
        steps += 1
        if steps == 2:
            await rig.service.cancel()

    with pytest.raises(BuildCancelled):
        await rig.service.build_all(building.id, step=step)
    assert rig.index.get(building.id) is None
    assert rig.index.building() is None


async def test_rows_from_another_space_are_refused() -> None:
    rig = Rig()
    generation_id = await _built(rig)
    wrong = VectorRow(
        id="x",
        kind="passage",
        modality=Modality.TEXT,
        task="document",  # type: ignore[arg-type]
        content_hash="h",
        values=(1.0,) * 128,
    )
    with pytest.raises(MixedSpaceError):
        rig.index.upsert(generation_id, [wrong], workspace_id=W, project_id=P)
    with pytest.raises(MixedSpaceError):
        rig.index.search(
            generation_id, (1.0,) * 128, workspace_id=W, project_id=P, gate=SearchGate(), limit=1
        )


async def test_only_one_build_at_a_time_and_retired_takes_no_rows() -> None:
    rig = Rig()
    old = await _built(rig)
    await rig.service.start_build()
    with pytest.raises(EmbeddingError):
        await rig.service.start_build()
    building = rig.index.building()
    assert building is not None
    await rig.service.activate(building.id, force=True)
    with pytest.raises(EmbeddingError):
        rig.index.upsert(
            old,
            [
                VectorRow(
                    id="x",
                    kind="p",
                    modality=Modality.TEXT,
                    task="document",  # type: ignore[arg-type]
                    content_hash="h",
                    values=(1.0,) * 256,
                )
            ],
            workspace_id=W,
            project_id=P,
        )


# --- pictures and captions ------------------------------------------------------------


async def test_pictures_are_embedded_only_by_a_space_that_takes_images() -> None:
    rig = Rig(DeterministicEmbedding(dimension=256, modalities=(Modality.TEXT,), model="text"))
    rig.picture("ast_coast", "windswept basalt coast")
    rig.world.add_caption(
        W,
        P,
        CaptionSource(
            observation_id="obs_1",
            asset_id="ast_coast",
            asset_version_id="ver_ast_coast",
            text="A dark coast",
        ),
    )
    text_only = await _built(rig)
    kinds = {row.kind for row in rig.index.rows(text_only)}
    assert kinds == {"caption"}  # never pretends to have seen the picture

    rig.use(DeterministicEmbedding(dimension=256, model="multi"))
    rig.embedder.describe(rig.world.pictures[(W, P)][0].data, "windswept basalt coast")
    multi = await _built(rig)
    rows = {row.id: row for row in rig.index.rows(multi)}
    assert rows[picture_row_id("ver_ast_coast")].modality is Modality.IMAGE
    assert rows[picture_row_id("ver_ast_coast")].segment_id == "seg_ver_ast_coast"
    assert caption_row_id("obs_1") in rows


async def test_picture_ready_hook_embeds_and_removal_hook_forgets() -> None:
    rig = Rig()
    generation = await _built(rig)
    rig.picture("ast_new", "a red door")
    status = await rig.service.status()
    assert status.lag["pictures"] == 1

    class Media:
        def __init__(self) -> None:
            self.removed = []

        def on_versions_removed(self, callback):  # type: ignore[no-untyped-def]
            self.removed.append(callback)

        async def process_version(self, workspace_id, project_id, version_id, **kwargs):  # type: ignore[no-untyped-def]
            return {"outcome": "ready"}

    media = Media()
    wire_media(media, rig.service)
    await media.process_version(W, P, "ver_ast_new")
    assert picture_row_id("ver_ast_new") in {row.id for row in rig.index.rows(generation)}
    assert (await rig.service.status()).lag["pictures"] == 0

    await media.removed[0](W, P, ["ver_ast_new"])
    assert picture_row_id("ver_ast_new") not in {row.id for row in rig.index.rows(generation)}


def _caption(observation_id: str, text: str) -> CaptionSource:
    return CaptionSource(
        observation_id=observation_id,
        asset_id="ast_coast",
        asset_version_id="ver_ast_coast",
        text=text,
    )


async def _active_and_retired(rig: Rig) -> tuple[str, str]:
    """Two builds with the same model: the first is kept, retired, for a rollback."""
    first = await _built(rig)
    second = await _built(rig)
    assert rig.index.get(first).state is GenerationState.RETIRED  # type: ignore[union-attr]
    return second, first


async def test_reviewed_captions_reach_the_index_and_rejected_ones_leave_every_generation() -> None:
    from types import SimpleNamespace

    rig = Rig()
    rig.picture("ast_coast", "windswept basalt coast")
    rig.world.add_caption(W, P, _caption("obs_old", "a dark coast"))
    active, retired = await _active_and_retired(rig)
    assert caption_row_id("obs_old") in {row.id for row in rig.index.rows(retired)}

    class Observations:
        """Stands in for the observation service: review changes what is accepted."""

        async def review(self, scope, workspace_id, project_id, observation_id, decision, **kw):  # type: ignore[no-untyped-def]
            captions = rig.world.captions[(workspace_id, project_id)]
            captions[:] = [c for c in captions if c.observation_id != observation_id]
            if decision == "accept":
                captions.append(_caption(observation_id, "the basalt coast at dusk"))
            return SimpleNamespace(kind="caption")

        async def restore_notes(self, workspace_id, project_id, raw):  # type: ignore[no-untyped-def]
            rig.world.add_caption(workspace_id, project_id, _caption("obs_back", "a quay"))
            return 1

    observations = Observations()
    wire_observations(observations, rig.service)

    # Accepted: in the index at once, not at the next build.
    await observations.review(None, W, P, "obs_new", "accept")
    assert caption_row_id("obs_new") in {row.id for row in rig.index.rows(active)}
    # Rejected: gone from the index in use and from the one kept for a rollback.
    await observations.review(None, W, P, "obs_old", "reject")
    assert caption_row_id("obs_old") not in {row.id for row in rig.index.rows(active)}
    assert caption_row_id("obs_old") not in {row.id for row in rig.index.rows(retired)}
    # Restored notes from an archive reach the index too.
    await observations.restore_notes(W, P, {})
    assert caption_row_id("obs_back") in {row.id for row in rig.index.rows(active)}


async def test_a_rejected_caption_leaves_the_index_even_when_the_model_is_unavailable() -> None:
    rig = Rig()
    rig.picture("ast_coast", "windswept basalt coast")
    rig.world.add_caption(W, P, _caption("obs_old", "a dark coast"))
    active = await _built(rig)
    rig.world.captions[(W, P)].clear()
    rig.embedder._available = False  # noqa: SLF001 - the model went away
    await rig.service.captions_changed(W, P)
    assert caption_row_id("obs_old") not in {row.id for row in rig.index.rows(active)}


async def test_a_changed_passage_leaves_the_generation_kept_for_a_rollback() -> None:
    rig = Rig()
    rig.passage("passage:s1:1", "the lantern on the harbour wall")
    active, retired = await _active_and_retired(rig)
    rig.passage("passage:s1:1", "the lantern is gone")
    assert "passage:s1:1" not in {row.id for row in rig.index.rows(retired)}
    assert "passage:s1:1" in {row.id for row in rig.index.rows(active)}


async def test_rollback_is_refused_while_the_earlier_index_is_missing_rows() -> None:
    rig = Rig()
    rig.passage("passage:s1:1", "the lantern on the harbour wall")
    active, retired = await _active_and_retired(rig)
    rig.passage("passage:s1:2", "written after the switch")
    with pytest.raises(NotCaughtUp):
        await rig.service.rollback()
    assert rig.index.active().id == active  # type: ignore[union-attr]
    restored = await rig.service.rollback(force=True)
    assert restored.id == retired


async def test_a_missing_picture_copy_is_counted_not_fatal() -> None:
    rig = Rig()
    picture = rig.picture("ast_gone", "nothing")
    rig.objects.missing.add(picture.object_key)
    generation = await rig.service.start_build()
    report = await rig.service.build_all(generation.id)
    assert report.pictures_unreadable == 1 and report.pictures == 0


# --- gated search: links first, gates before ranking -----------------------------------


async def _search_rig() -> Rig:
    rig = Rig()
    rig.picture("ast_portrait", "mara portrait grey cloak")
    rig.picture("ast_coast", "windswept basalt coast cliffs")
    rig.picture("ast_coast_secret", "windswept basalt coast hidden cave", secret=True)
    rig.picture("ast_coast_old", "windswept basalt coast", current=False)
    rig.picture("ast_coast_rejected", "windswept basalt coast", rejected=True)
    rig.picture(
        "ast_coast_link",
        "windswept basalt coast smugglers",
        # Secretly linked on the main story; only Mara knows what it shows.
        secret_links=[("br_main", ("ent_mara",))],
    )
    await _built(rig)
    return rig


async def test_linked_pictures_come_first_then_similar_ones() -> None:
    rig = await _search_rig()
    result = await rig.search.pictures_for(
        W, P, query="windswept basalt coast", linked_asset_ids=["ast_portrait"]
    )
    assert result.semantic is True
    assert result.pictures[0].asset_id == "ast_portrait"
    assert result.pictures[0].reason == "linked"
    similar = [p.asset_id for p in result.pictures if p.reason == "similar"]
    assert similar[0] in ("ast_coast", "ast_coast_link", "ast_coast_secret")
    assert "ast_portrait" not in similar  # one picture, one place in the list


async def test_gates_apply_before_ranking_for_a_point_of_view() -> None:
    rig = await _search_rig()
    gate = SearchGate(point_of_view=True, point_of_view_id="ent_tobin")
    result = await rig.search.pictures_for(
        W,
        P,
        query="windswept basalt coast",
        gate=gate,
        limit=10,
        linked_asset_ids=["ast_coast_secret"],
    )
    found = {p.asset_id for p in result.pictures}
    assert "ast_coast" in found
    # Secret, secretly linked, retired, rejected: never returned, even when linked.
    assert not found & {"ast_coast_secret", "ast_coast_link", "ast_coast_old", "ast_coast_rejected"}

    knows = SearchGate(point_of_view=True, point_of_view_id="ent_mara")
    found = {
        p.asset_id
        for p in (
            await rig.search.pictures_for(W, P, query="basalt coast", gate=knows, limit=10)
        ).pictures
    }
    assert "ast_coast_link" in found and "ast_coast_secret" not in found

    # With no point-of-view character, nobody knows: the secretly linked one stays out.
    nobody = SearchGate(point_of_view=True)
    found = {
        p.asset_id
        for p in (
            await rig.search.pictures_for(W, P, query="basalt coast", gate=nobody, limit=10)
        ).pictures
    }
    assert "ast_coast_link" not in found


async def test_a_secret_link_is_judged_in_the_working_line_of_the_story() -> None:
    from backend.domain.branching import working_in

    rig = Rig()
    rig.picture("ast_coast", "windswept basalt coast")
    rig.picture(
        "ast_cave", "windswept basalt coast cave", secret_links=[("br_whatif", ("ent_mara",))]
    )
    await _built(rig)
    gate = SearchGate(point_of_view=True, point_of_view_id="ent_tobin")

    async def found() -> set[str]:
        result = await rig.search.pictures_for(W, P, query="basalt coast", gate=gate, limit=10)
        return {p.asset_id for p in result.pictures}

    # On the main story the link does not exist; in the what-if it hides the picture.
    assert "ast_cave" in await found()
    with working_in("br_whatif"):
        assert "ast_cave" not in await found()
        hits = await rig.search.search(
            W, P, TextInput(text="basalt coast cave"), gate=gate, limit=10
        )
        assert "ast_cave" not in {hit.asset_id for hit in hits}


async def test_other_projects_never_leak() -> None:
    rig = await _search_rig()
    result = await rig.search.pictures_for("ws_b", P, query="windswept basalt coast", limit=10)
    assert result.pictures == []


async def test_a_picture_and_its_caption_are_one_piece_of_evidence() -> None:
    rig = Rig()
    rig.picture("ast_coast", "windswept basalt coast")
    rig.world.add_caption(
        W,
        P,
        CaptionSource(
            observation_id="obs_c",
            asset_id="ast_coast",
            asset_version_id="ver_ast_coast",
            text="windswept basalt coast",
        ),
    )
    await _built(rig)
    result = await rig.search.pictures_for(W, P, query="windswept basalt coast", limit=5)
    assert [p.asset_id for p in result.pictures] == ["ast_coast"]


async def test_without_a_usable_index_links_still_work_and_say_why() -> None:
    rig = Rig()
    rig.picture("ast_portrait", "mara")
    result = await rig.search.pictures_for(W, P, query="mara", linked_asset_ids=["ast_portrait"])
    assert [p.asset_id for p in result.pictures] == ["ast_portrait"]
    assert result.semantic is False and result.reason


async def test_the_reindex_job_builds_one_story_and_stops_when_asked() -> None:
    from types import SimpleNamespace

    from backend.domain.jobs import JobCancelled
    from backend.services.embedding_index import EMBEDDING_REINDEX_KIND

    rig = Rig()
    rig.picture("ast_coast", "windswept basalt coast")
    registered = {}
    runner = SimpleNamespace(
        register=lambda kind, handler, **kw: registered.__setitem__(kind, handler)
    )
    service = EmbeddingIndexService(
        index=rig.index, embedder=rig.embedder, objects=rig.objects, runner=runner
    )
    service.register_jobs()
    handler = registered[EMBEDDING_REINDEX_KIND]
    building = await service.start_build()
    stages: list[str] = []

    async def step(stage: str, pct: int) -> None:
        stages.append(stage)

    ctx = SimpleNamespace(
        job=SimpleNamespace(workspace_id=W, project_id=P, payload={"generation_id": building.id}),
        step=step,
        partial={},
    )
    result = await handler(ctx)
    assert result["pictures"] == 1 and stages[-1] == "done"

    async def stop(stage: str, pct: int) -> None:
        raise JobCancelled(stage)

    ctx.step = stop
    with pytest.raises(JobCancelled):
        await handler(ctx)
