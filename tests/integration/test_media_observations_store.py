"""What a model saw in pictures, each picture's context choice, and secret, timed picture
links, against PostgreSQL (AST-004, AST-005, AST-007; migration 0048)."""

import pytest
import pytest_asyncio

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.journal import PostgresCanonJournal
from backend.adapters.media_store import PostgresMediaStore
from backend.adapters.observation_store import PostgresObservationStore
from backend.core.database import create_engine, create_session_factory
from backend.domain.context_parts import ContextChoice
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.media_observations import (
    MediaObservation,
    ObservationKind,
    ObservationState,
    Region,
)
from backend.domain.operations import CreateEntityOp, LinkAssetOp
from backend.domain.validation import CanonValidationError
from backend.services.media_service import MediaService
from backend.services.mutation_service import MutationService
from tests.doubles import InMemoryObjectStore

pytestmark = pytest.mark.integration

WS, PR = "ws_seen_it", "pr_seen_it"


def _scope() -> AccessScope:
    return AccessScope(
        actor_user_id="usr_seen", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR
    )


@pytest_asyncio.fixture
async def stack(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    journal = PostgresCanonJournal(factory)
    repository = PostgresCanonRepository(factory)
    mutation = MutationService(journal, repository, local_mode=True)
    objects = InMemoryObjectStore()
    media = MediaService(
        media=PostgresMediaStore(factory),
        objects=objects,
        mutation=mutation,
        reader=repository,
        journal=journal,
        local_mode=True,
    )
    try:
        yield media, mutation, repository, objects, PostgresObservationStore(factory)
    finally:
        await engine.dispose()


async def _picture(media, objects):
    stat = objects.put(b"portrait", workspace_id=WS, project_id=PR, content_type="image/png")
    asset = await media.register_asset(_scope(), WS, PR, name="Portrait")
    version = await media.register_version(_scope(), WS, PR, asset.id, object_key=stat.key)
    return asset, version


def _seen(asset_id: str, version_id: str, index: int, **fields) -> MediaObservation:
    return MediaObservation(
        workspace_id=WS,
        project_id=PR,
        id=f"obs_{index}",
        asset_id=asset_id,
        asset_version_id=version_id,
        kind=fields.pop("kind", ObservationKind.APPEARANCE),
        text=fields.pop("text", "A grey coat."),
        model="vision",
        prompt_name="describe-picture",
        prompt_version="describe-picture/1",
        job_id="job_1",
        **fields,
    )


@pytest.mark.asyncio
async def test_observations_round_trip_and_a_retry_writes_nothing_twice(stack) -> None:
    media, _mutation, _repository, objects, store = stack
    asset, version = await _picture(media, objects)
    first = _seen(
        asset.id,
        version.id,
        1,
        region=Region(x=0.1, y=0.1, width=0.5, height=0.5),
        confidence=0.75,
        subject_record_type="entity",
        subject_record_id="ent_1",
    )
    caption = _seen(asset.id, version.id, 2, kind=ObservationKind.CAPTION, text="A quay.")
    await store.add([first, caption])
    # A job taken over after a lost lease writes the same ids again: nothing changes.
    await store.add([first.model_copy(update={"text": "changed"})])
    found = await store.for_assets(workspace_id=WS, project_id=PR, asset_ids=[asset.id])
    assert [item.id for item in found] == ["obs_1", "obs_2"]
    assert found[0].text == "A grey coat." and found[0].region == first.region
    assert found[0].confidence == 0.75 and found[0].prompt_version == "describe-picture/1"

    decided = found[0].model_copy(
        update={
            "state": ObservationState.CORRECTED,
            "corrected_text": "A green coat.",
            "reviewed_by": "usr_seen",
            "proposal_mutation_set_id": "mut_1",
        }
    )
    await store.save(decided)
    again = await store.get(workspace_id=WS, project_id=PR, observation_id="obs_1")
    assert again is not None and again.words == "A green coat."
    assert again.state is ObservationState.CORRECTED and again.proposal_mutation_set_id == "mut_1"
    assert await store.get(workspace_id=WS, project_id="other", observation_id="obs_1") is None
    assert len(await store.all(workspace_id=WS, project_id=PR)) == 2


@pytest.mark.asyncio
async def test_each_picture_keeps_its_context_choice(stack) -> None:
    media, _mutation, _repository, objects, store = stack
    asset, _version = await _picture(media, objects)
    assert await store.get_choices(workspace_id=WS, project_id=PR, asset_ids=[asset.id]) == {
        asset.id: ContextChoice.DESCRIBE
    }
    assert await store.set_choice(
        workspace_id=WS, project_id=PR, asset_id=asset.id, choice=ContextChoice.SEND
    )
    assert (await store.get_choices(workspace_id=WS, project_id=PR, asset_ids=[asset.id]))[
        asset.id
    ] is ContextChoice.SEND
    assert not await store.set_choice(
        workspace_id=WS, project_id=PR, asset_id="ast_missing", choice=ContextChoice.SEND
    )


@pytest.mark.asyncio
async def test_a_secret_timed_link_is_kept_and_checked_in_the_commit(stack) -> None:
    media, mutation, repository, objects, _store = stack
    draft = await mutation.create_draft(
        _scope(),
        workspace_id=WS,
        project_id=PR,
        operations=[
            CreateEntityOp(id="ent_mara", type="Character", name="Mara"),
            CreateEntityOp(id="ent_tobin", type="Character", name="Tobin"),
        ],
        expected_head=0,
    )
    await mutation.commit(_scope(), draft.id)
    _asset, version = await _picture(media, objects)
    link = LinkAssetOp(
        id="lnk_secret",
        asset_id=version.asset_id,
        asset_version_id=version.id,
        target_id="ent_mara",
        role="DEPICTS",
        visibility="secret",
        known_by=["ent_tobin"],
        applies_from={"kind": "at", "at": 10},
    )
    draft = await mutation.create_draft(
        _scope(), workspace_id=WS, project_id=PR, operations=[link], expected_head=1
    )
    await mutation.commit(_scope(), draft.id)
    stored = await repository.get_link(workspace_id=WS, project_id=PR, link_id="lnk_secret")
    assert stored is not None and stored.is_secret
    assert stored.known_by == ["ent_tobin"] and stored.applies_from == {"kind": "at", "at": 10}

    # Only a secret link names who knows, and they must be in the story.
    for bad in (
        link.model_copy(update={"id": "lnk_b", "visibility": None}),
        link.model_copy(update={"id": "lnk_c", "known_by": ["ent_nobody"]}),
        link.model_copy(update={"id": "lnk_d", "applies_from": {"kind": "nonsense"}}),
    ):
        draft = await mutation.create_draft(
            _scope(), workspace_id=WS, project_id=PR, operations=[bad], expected_head=2
        )
        with pytest.raises(CanonValidationError):
            await mutation.commit(_scope(), draft.id)
