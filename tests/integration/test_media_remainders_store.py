"""Batch 9 C against PostgreSQL (migration 0058): picture links for a window of story time
that show one version (AST-001), two-step uploads (AST-002), removing what a model said about
a deleted picture (AST-008), thread steps kept with a reading (EXT-007), and changing an
entry's type and what a claim says (AST-EXP-017)."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.journal import PostgresCanonJournal
from backend.adapters.media_store import PostgresMediaProcessingStore, PostgresMediaStore
from backend.adapters.observation_store import PostgresObservationStore
from backend.adapters.source_store import PostgresSourceStore
from backend.adapters.studio_store import PostgresStudioStore
from backend.core.database import create_engine, create_session_factory, session_scope
from backend.domain.canon.events import CLAIM_KIND, EventClaim, EventParticipant
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.media import MediaLifecycleAction, UploadState, UploadTarget
from backend.domain.media_observations import MediaObservation, ObservationKind
from backend.domain.operations import (
    AmendEventOp,
    AssertFactOp,
    CloseFactOp,
    CreateEntityOp,
    CreateEventOp,
    LinkAssetOp,
    UpdateEntityOp,
)
from backend.domain.sources import SourceKind, content_hash
from backend.domain.studio import SceneReading
from backend.domain.validation import CanonValidationError
from backend.services.media_service import MediaService
from backend.services.mutation_service import MutationService
from tests.contract.test_media_processing import _png
from tests.doubles import InMemoryObjectStore

pytestmark = pytest.mark.integration

WS, PR = "ws_c9_it", "pr_c9_it"
SCOPE = AccessScope(
    actor_user_id="usr_c9", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR
)


@pytest_asyncio.fixture
async def stack(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    journal = PostgresCanonJournal(factory)
    repository = PostgresCanonRepository(factory)
    mutation = MutationService(journal, repository, local_mode=True)
    store = PostgresMediaStore(factory)
    objects = InMemoryObjectStore()
    media = MediaService(
        media=store,
        objects=objects,
        mutation=mutation,
        reader=repository,
        journal=journal,
        local_mode=True,
        processing=PostgresMediaProcessingStore(factory),
    )
    media.observation_store = PostgresObservationStore(factory)
    try:
        yield {
            "media": media,
            "store": store,
            "objects": objects,
            "mutation": mutation,
            "repository": repository,
            "factory": factory,
        }
    finally:
        await engine.dispose()


async def _commit(stack, *operations, head: int) -> int:
    draft = await stack["mutation"].create_draft(
        SCOPE, workspace_id=WS, project_id=PR, operations=list(operations), expected_head=head
    )
    result = await stack["mutation"].commit(SCOPE, draft.id)
    return int(result.revision)


async def _picture(stack):
    stat = stack["objects"].put(
        _png("teal"), workspace_id=WS, project_id=PR, content_type="image/png"
    )
    asset = await stack["media"].register_asset(SCOPE, WS, PR, name="Portrait")
    version = await stack["media"].register_version(SCOPE, WS, PR, asset.id, object_key=stat.key)
    return asset, version


async def _chunks(data: bytes):
    yield data


@pytest.mark.asyncio
async def test_a_link_keeps_its_window_and_version_and_refuses_one_ending_first(stack) -> None:
    head = await _commit(
        stack, CreateEntityOp(id="ent_mara", type="Character", name="Mara"), head=0
    )
    asset, version = await _picture(stack)
    link = LinkAssetOp(
        id="lnk_window",
        asset_id=asset.id,
        asset_version_id=version.id,
        target_id="ent_mara",
        role="DEPICTS",
        applies_from={"kind": "at", "at": 10},
        applies_until={"kind": "at", "at": 20},
        shows_version=True,
    )
    head = await _commit(stack, link, head=head)
    stored = await stack["repository"].get_link(workspace_id=WS, project_id=PR, link_id=link.id)
    assert stored is not None and stored.shows_version is True
    assert stored.applies_until == {"kind": "at", "at": 20}
    backwards = link.model_copy(
        update={"id": "lnk_back", "applies_until": {"kind": "at", "at": 5}, "shows_version": None}
    )
    draft = await stack["mutation"].create_draft(
        SCOPE, workspace_id=WS, project_id=PR, operations=[backwards], expected_head=head
    )
    with pytest.raises(CanonValidationError):
        await stack["mutation"].commit(SCOPE, draft.id)


@pytest.mark.asyncio
async def test_a_two_step_upload_is_checked_staged_finished_once_and_expires(stack) -> None:
    media, store, objects = stack["media"], stack["store"], stack["objects"]
    asset, _ = await _picture(stack)
    data = _png("navy")
    digest = hashlib.sha256(data).hexdigest()
    upload = await media.start_upload(
        SCOPE,
        WS,
        PR,
        target_type=UploadTarget.ASSET,
        target_id=asset.id,
        byte_length=len(data),
        sha256=digest,
    )
    assert (await store.get_upload(workspace_id=WS, project_id=PR, upload_id=upload.id)).state is (
        UploadState.WAITING
    )
    assert await store.open_uploads(workspace_id=WS) == 1
    received, _ = await media.receive_upload(SCOPE, WS, PR, upload.id, _chunks(data))
    assert received.state is UploadState.RECEIVED and received.staged_key
    # Only one move from a state wins.
    assert (
        await store.move_upload(
            workspace_id=WS,
            project_id=PR,
            upload_id=upload.id,
            from_state=UploadState.WAITING,
            to_state=UploadState.RECEIVED,
        )
        is None
    )
    finished, link, version = await media.finish_upload(SCOPE, WS, PR, upload.id)
    assert link is None and version.version == 2 and finished.state is UploadState.FINISHED
    assert finished.version_id == version.id and finished.finished_at is not None
    assert objects.stat(received.staged_key, workspace_id=WS, project_id=PR) is None
    again, _, same = await media.finish_upload(SCOPE, WS, PR, upload.id)
    assert same.id == version.id and again.state is UploadState.FINISHED

    # One never finished is cleared once its hour is up, and its staged file with it.
    late = await media.start_upload(
        SCOPE,
        WS,
        PR,
        target_type=UploadTarget.ASSET,
        target_id=asset.id,
        byte_length=len(data),
        sha256=digest,
    )
    staged, _ = await media.receive_upload(SCOPE, WS, PR, late.id, _chunks(data))
    async with session_scope(stack["factory"]) as session:
        await session.execute(
            text("UPDATE media_uploads SET expires_at = :t WHERE id = :id"),
            {"t": datetime.now(UTC) - timedelta(minutes=1), "id": late.id},
        )
    assert await media.expire_uploads() == 1
    assert (await store.get_upload(workspace_id=WS, project_id=PR, upload_id=late.id)).state is (
        UploadState.EXPIRED
    )
    assert objects.stat(staged.staged_key, workspace_id=WS, project_id=PR) is None
    assert await media.expire_uploads() == 0


@pytest.mark.asyncio
async def test_uploads_on_their_way_are_counted_per_person_at_once(stack) -> None:
    import asyncio

    from backend.domain.media import PictureUpload

    store = stack["store"]
    now = datetime.now(UTC)

    def upload(index: int, actor: str | None) -> PictureUpload:
        return PictureUpload(
            workspace_id=WS,
            project_id=PR,
            id=f"upl_race_{actor}_{index}",
            actor_user_id=actor,
            target_type=UploadTarget.ENTITY,
            target_id="ent_x",
            expected_bytes=10,
            expected_sha256="a" * 64,
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )

    # Many at once for the last two places: exactly two win.
    made = await asyncio.gather(
        *(
            store.create_upload(upload(i, "usr_a"), max_per_person=2, max_per_workspace=50)
            for i in range(8)
        )
    )
    assert sum(1 for one in made if one is not None) == 2
    assert await store.open_uploads(workspace_id=WS, actor_user_id="usr_a") == 2
    # Another person still has their own places, up to the workspace's ceiling.
    other = await store.create_upload(upload(0, "usr_b"), max_per_person=2, max_per_workspace=3)
    assert other is not None
    assert (
        await store.create_upload(upload(1, "usr_b"), max_per_person=2, max_per_workspace=3) is None
    )
    assert await store.open_uploads(workspace_id=WS) == 3

    # One being finished is left past its hour for a while; one long stuck is cleared.
    async with session_scope(stack["factory"]) as session:
        await session.execute(
            text("UPDATE media_uploads SET state='finishing', expires_at=:t WHERE id=:id"),
            {"t": now - timedelta(minutes=1), "id": "upl_race_usr_b_0"},
        )
    assert await store.expire_uploads(finishing_grace_seconds=900) == []
    async with session_scope(stack["factory"]) as session:
        await session.execute(
            text("UPDATE media_uploads SET expires_at=:t WHERE id=:id"),
            {"t": now - timedelta(minutes=20), "id": "upl_race_usr_b_0"},
        )
    assert [one.id for one in await store.expire_uploads(finishing_grace_seconds=900)] == [
        "upl_race_usr_b_0"
    ]


@pytest.mark.asyncio
async def test_deleting_a_picture_removes_its_notes_and_says_how_many(stack) -> None:
    media = stack["media"]
    asset, version = await _picture(stack)
    await media.observation_store.add(
        [
            MediaObservation(
                workspace_id=WS,
                project_id=PR,
                id=f"obs_{index}",
                asset_id=asset.id,
                asset_version_id=version.id,
                kind=ObservationKind.CAPTION,
                text="A woman in a grey coat.",
            )
            for index in range(3)
        ]
    )
    deleted = await media.delete_picture(SCOPE, WS, PR, asset.id)
    assert deleted.notes_removed == 3
    left = await media.observation_store.for_assets(
        workspace_id=WS, project_id=PR, asset_ids=[asset.id]
    )
    assert left == []
    events = await stack["store"].list_lifecycle_events(
        workspace_id=WS, project_id=PR, asset_id=asset.id
    )
    removed = [event for event in events if event.action is MediaLifecycleAction.NOTES_REMOVED]
    assert len(removed) == 1 and removed[0].item_count == 3 and removed[0].actor_user_id == "usr_c9"


@pytest.mark.asyncio
async def test_a_readings_unsuggested_thread_steps_are_kept(stack) -> None:
    factory = stack["factory"]
    sources = PostgresSourceStore(factory)
    studio = PostgresStudioStore(factory)
    words = "He wondered again."
    await sources.add_source(
        workspace_id=WS,
        project_id=PR,
        source_id="src_s",
        title="The Docks",
        kind=SourceKind.SCENE,
        actor_user_id="u1",
    )
    await sources.add_version(
        workspace_id=WS,
        project_id=PR,
        version_id="src_s_v1",
        source_id="src_s",
        content=words,
        content_hash=content_hash(words),
        actor_user_id="u1",
    )
    reading = SceneReading(
        workspace_id=WS,
        project_id=PR,
        branch_id="b_main",
        id="rdg_1",
        plan_id="plan_1",
        source_id="src_s",
        version_id="src_s_v1",
        job_id="job_1",
        passages=1,
        proposals=(),
        discarded=0,
        created_by="u1",
        created_at=datetime.now(UTC),
        status="partial",
        steps=(("pln_guild", "advances", "chars:0-18", "", ""),),
    )
    await studio.save_reading(reading)
    (found,) = await studio.readings(
        workspace_id=WS, project_id=PR, branch_id="b_main", plan_id="plan_1"
    )
    assert found.steps == reading.steps
    await studio.save_reading(found.__class__(**{**found.__dict__, "steps": ()}))
    (cleared,) = await studio.readings(
        workspace_id=WS, project_id=PR, branch_id="b_main", plan_id="plan_1"
    )
    assert cleared.steps == ()


@pytest.mark.asyncio
async def test_an_entry_changes_type_and_a_claim_names_another_entry(stack) -> None:
    repository = stack["repository"]
    head = await _commit(
        stack,
        CreateEntityOp(id="ent_guild", type="Faction", name="Harbor Guild"),
        CreateEntityOp(id="ent_mara", type="Character", name="Mara"),
        CreateEntityOp(id="ent_tobin", type="Character", name="Tobin"),
        head=0,
    )
    member = AssertFactOp(
        id="fct_member",
        proposition=Proposition(
            subject_id="ent_mara",
            predicate="MEMBER_OF",
            object=EntityRefObject(entity_id="ent_guild", project_id=PR),
        ),
    )
    claim = CreateEventOp(
        id="evt_says",
        kind=CLAIM_KIND,
        name="Tobin says Mara knows him",
        participants=[EventParticipant(entity_id="ent_tobin", role="speaker")],
        claim=EventClaim(
            proposition=Proposition(
                subject_id="ent_mara",
                predicate="KNOWS",
                object=EntityRefObject(entity_id="ent_tobin", project_id=PR),
            )
        ),
    )
    head = await _commit(stack, member, claim, head=head)
    # A new type that leaves a fact unfit is refused in the commit.
    draft = await stack["mutation"].create_draft(
        SCOPE,
        workspace_id=WS,
        project_id=PR,
        operations=[UpdateEntityOp(id="ent_guild", type="Character")],
        expected_head=head,
    )
    with pytest.raises(CanonValidationError):
        await stack["mutation"].commit(SCOPE, draft.id)
    head = await _commit(
        stack,
        CloseFactOp(id="fct_member"),
        UpdateEntityOp(id="ent_guild", type="Character"),
        head=head,
    )
    guild = await repository.get_entity(workspace_id=WS, project_id=PR, entity_id="ent_guild")
    assert guild is not None and guild.type == "Character"

    said = claim.claim.model_copy(
        update={
            "proposition": claim.claim.proposition.model_copy(update={"subject_id": "ent_guild"})
        }
    )
    await _commit(stack, AmendEventOp(id="evt_says", claim=said), head=head)
    event = await repository.get_event(workspace_id=WS, project_id=PR, event_id="evt_says")
    assert event is not None and event.claim is not None
    assert event.claim.proposition.subject_id == "ent_guild"
