"""Source texts in PostgreSQL: versions cannot change, and decisions about names replace cleanly."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.adapters.source_store import PostgresSourceStore
from backend.core.database import create_engine, create_session_factory, session_scope
from backend.domain.sources import AliasBinding, SourceKind, Verdict, content_hash

pytestmark = pytest.mark.integration

SCOPE = {"workspace_id": "ws1", "project_id": "p1"}


@pytest_asyncio.fixture
async def stores(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    try:
        yield PostgresSourceStore(factory), factory
    finally:
        await engine.dispose()


async def _add(store, source_id: str, content: str, **scope) -> None:
    scope = {**SCOPE, **scope}
    await store.add_source(
        **scope, source_id=source_id, title="Chapter", kind=SourceKind.PASTE, actor_user_id="u1"
    )
    await store.add_version(
        **scope,
        version_id=f"srv_{source_id}_1",
        source_id=source_id,
        content=content,
        content_hash=content_hash(content),
        actor_user_id="u1",
    )


def _binding(binding_id: str, verdict: Verdict, entity_id, source_id=None) -> AliasBinding:
    return AliasBinding(
        **SCOPE,
        id=binding_id,
        phrase="the Fox",
        normalized="the fox",
        verdict=verdict,
        entity_id=entity_id,
        source_id=source_id,
        decided_by="u1",
        decided_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_versions_are_numbered_kept_and_cannot_be_changed(stores) -> None:
    store, factory = stores
    await _add(store, "src_a", "First words.")
    second = await store.add_version(
        **SCOPE,
        version_id="srv_a_2",
        source_id="src_a",
        content="Second words, longer.",
        content_hash=content_hash("Second words, longer."),
        actor_user_id="u1",
    )
    assert second.number == 2

    source = await store.get_source(**SCOPE, source_id="src_a")
    assert (source.versions, source.latest_version_id, source.length) == (2, "srv_a_2", 21)
    assert [v.content for v in await store.list_versions(**SCOPE, source_id="src_a")] == [
        "First words.",
        "Second words, longer.",
    ]
    with pytest.raises(DBAPIError, match="immutable"):
        async with session_scope(factory) as db:
            await db.execute(text("UPDATE source_versions SET content = 'forged'"))

    # Another story cannot read it.
    assert await store.get_source(workspace_id="ws1", project_id="p2", source_id="src_a") is None
    assert (
        await store.get_version(workspace_id="ws2", project_id="p1", version_id="srv_a_2") is None
    )


@pytest.mark.asyncio
async def test_removing_a_text_erases_its_words_and_its_own_answers(stores) -> None:
    store, _ = stores
    await _add(store, "src_a", "Words.")
    await store.put_binding(_binding("alb_1", Verdict.IS, "ent_1", source_id="src_a"))
    await store.put_binding(_binding("alb_2", Verdict.IS, "ent_2"))

    await store.remove_source(**SCOPE, source_id="src_a")

    assert await store.list_sources(**SCOPE) == []
    assert await store.list_versions(**SCOPE, source_id="src_a") == []
    # The marker stays so old evidence can still say which text it was.
    assert (await store.get_source(**SCOPE, source_id="src_a")).removed_at is not None
    assert [b.id for b in await store.list_bindings(**SCOPE)] == ["alb_2"]


@pytest.mark.asyncio
async def test_a_later_answer_replaces_an_earlier_one(stores) -> None:
    store, _ = stores
    await store.put_binding(_binding("alb_1", Verdict.SUGGESTED, "ent_1"))
    await store.put_binding(_binding("alb_2", Verdict.IS_NOT, "ent_2"))
    await store.put_binding(_binding("alb_3", Verdict.IS, "ent_1"))
    found = {b.id: b.verdict for b in await store.list_bindings(**SCOPE)}
    assert found == {"alb_2": Verdict.IS_NOT, "alb_3": Verdict.IS}

    await store.put_binding(_binding("alb_4", Verdict.IGNORE, None))
    assert [b.id for b in await store.list_bindings(**SCOPE)] == ["alb_4"]
    assert await store.delete_binding(**SCOPE, binding_id="alb_4") is True
    assert await store.delete_binding(**SCOPE, binding_id="alb_4") is False


@pytest.mark.asyncio
async def test_a_guess_never_takes_the_place_of_the_authors_answer(stores) -> None:
    """A model's guess saved after the author answered keeps the answer and its examples."""
    from dataclasses import replace

    from backend.domain.sources import NameExample

    store, _ = stores
    await _add(store, "src_a", "The Fox waved.")
    example = NameExample(id="nex_1", quote="The Fox waved.", means=True, at=(0, 7))
    await store.put_binding(replace(_binding("alb_1", Verdict.IS, "ent_1"), examples=(example,)))
    await store.put_binding(_binding("alb_2", Verdict.IGNORE, None, source_id="src_a"))
    await store.put_binding(_binding("alb_3", Verdict.SUGGESTED, "ent_1"))
    await store.put_binding(_binding("alb_4", Verdict.SUGGESTED, "ent_2", source_id="src_a"))
    found = {b.id: b for b in await store.list_bindings(**SCOPE)}
    assert found["alb_1"].examples == (example,)
    assert found["alb_2"].verdict is Verdict.IGNORE


@pytest.mark.asyncio
async def test_scoped_answers_keep_their_limits_and_examples(stores) -> None:
    """CTX-016: answers with different limits live side by side; examples are kept."""
    from dataclasses import replace

    from backend.domain.sources import NameExample

    store, _ = stores
    everywhere = _binding("alb_1", Verdict.IS, "ent_1")
    example = NameExample(
        id="nex_1", quote="The Fox waved.", means=True, scene_id="pln_2", at=(0, 7), noted_at=None
    )
    scoped = replace(
        _binding("alb_2", Verdict.IS, "ent_2"),
        branch_id="br_1",
        from_scene_id="pln_2",
        until_scene_id="pln_3",
        viewpoint_id="ent_9",
        examples=(example,),
    )
    await store.put_binding(everywhere)
    await store.put_binding(scoped)
    found = {b.id: b for b in await store.list_bindings(**SCOPE)}
    assert set(found) == {"alb_1", "alb_2"}
    back = found["alb_2"]
    assert (back.branch_id, back.from_scene_id, back.until_scene_id, back.viewpoint_id) == (
        "br_1",
        "pln_2",
        "pln_3",
        "ent_9",
    )
    assert back.examples == (example,)

    # The same scope answered again replaces only that answer.
    await store.put_binding(replace(scoped, id="alb_3", entity_id="ent_3", examples=()))
    assert {b.id for b in await store.list_bindings(**SCOPE)} == {"alb_1", "alb_3"}
    assert await store.set_examples(replace(scoped, id="alb_3", examples=(example,))) is True
    assert await store.set_examples(replace(scoped, id="alb_gone")) is False
    kept = {b.id: b for b in await store.list_bindings(**SCOPE)}
    assert kept["alb_3"].examples == (example,)


@pytest.mark.asyncio
async def test_deleting_a_story_or_a_workspace_takes_its_texts(stores) -> None:
    store, _ = stores
    await _add(store, "src_a", "Words.")
    await _add(store, "src_b", "Other words.", project_id="p2")
    await _add(store, "src_c", "Elsewhere.", workspace_id="ws2")
    await store.put_binding(_binding("alb_1", Verdict.IS, "ent_1"))

    assert await store.delete_project("ws1", "p1") == 1
    assert await store.list_bindings(**SCOPE) == []
    assert await store.get_version(**SCOPE, version_id="srv_src_a_1") is None
    assert len(await store.list_sources(workspace_id="ws1", project_id="p2")) == 1
    assert await store.delete_workspace("ws1") == 1
    assert len(await store.list_sources(workspace_id="ws2", project_id="p1")) == 1
