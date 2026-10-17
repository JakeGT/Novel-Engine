"""The passage index and kept context against PostgreSQL (ADR-021)."""

import pytest
import pytest_asyncio

from backend.adapters.passage_store import PostgresManifestStore, PostgresPassageStore
from backend.core.database import create_engine, create_session_factory
from backend.domain.passages import IndexedPassage

pytestmark = pytest.mark.integration

WS, PR = "ws_passages_it", "pr_passages_it"


@pytest_asyncio.fixture
async def stores(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    try:
        yield PostgresPassageStore(factory), PostgresManifestStore(factory)
    finally:
        await engine.dispose()


def _passage(source: str, version: str, number: int, ids: tuple[str, ...]) -> IndexedPassage:
    return IndexedPassage(
        source_id=source,
        version_id=version,
        number=number,
        start=number * 100,
        end=number * 100 + 50,
        section="One",
        entity_ids=ids,
        indexed_revision=7,
    )


@pytest.mark.asyncio
async def test_passages_are_found_by_the_entries_they_name_and_replaced_whole(stores) -> None:
    passages, _ = stores
    await passages.replace_source(
        workspace_id=WS,
        project_id=PR,
        source_id="src_a",
        passages=[_passage("src_a", "srv_1", 1, ("ent_mara",)), _passage("src_a", "srv_1", 2, ())],
    )
    await passages.replace_source(
        workspace_id=WS,
        project_id=PR,
        source_id="src_b",
        passages=[_passage("src_b", "srv_9", 1, ("ent_tobin", "ent_mara"))],
    )
    found = await passages.mentioning(workspace_id=WS, project_id=PR, entity_ids=["ent_mara"])
    assert [(p.source_id, p.number) for p in found] == [("src_a", 1), ("src_b", 1)]
    assert (
        await passages.mentioning(workspace_id="other", project_id=PR, entity_ids=["ent_mara"])
        == []
    )
    assert await passages.indexed_versions(workspace_id=WS, project_id=PR) == {
        "src_a": ("srv_1", 7),
        "src_b": ("srv_9", 7),
    }

    # A new version replaces everything indexed for the text.
    await passages.replace_source(
        workspace_id=WS,
        project_id=PR,
        source_id="src_a",
        passages=[_passage("src_a", "srv_2", 1, ("ent_tobin",))],
    )
    assert [
        p.version_id
        for p in await passages.for_source(workspace_id=WS, project_id=PR, source_id="src_a")
    ] == ["srv_2"]
    await passages.forget_source(workspace_id=WS, project_id=PR, source_id="src_a")
    assert await passages.for_source(workspace_id=WS, project_id=PR, source_id="src_a") == []


@pytest.mark.asyncio
async def test_what_was_given_is_kept_exactly(stores) -> None:
    _, manifests = stores
    await manifests.save(
        workspace_id=WS,
        project_id=PR,
        manifest_id="ctx_1",
        branch_id=None,
        purpose="scene-draft",
        plan_id="plan_1",
        policy_version="scene-draft/1",
        checksum="abc",
        words=120,
        budget=2500,
        manifest={"items": [{"key": "scene:plan_1"}], "left_out": []},
        created_by="usr_1",
    )
    kept = await manifests.get(workspace_id=WS, project_id=PR, manifest_id="ctx_1")
    assert kept is not None and kept.manifest["items"] == [{"key": "scene:plan_1"}]
    assert await manifests.get(workspace_id="other", project_id=PR, manifest_id="ctx_1") is None
