"""Assistant settings, conversation counts and reported job costs in PostgreSQL (migration 0038)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.adapters.assist_store import PostgresAssistSessionStore
from backend.adapters.assistant_settings_store import PostgresAssistantSettingsStore
from backend.adapters.job_store import PostgresJobStore
from backend.core.database import create_engine, create_session_factory, session_scope
from backend.domain.assist import AssistKind, AssistSession
from backend.domain.identity import AuthMode
from backend.domain.jobs import AUTHORIZATION_POLICY_VERSION, Job, JobStatus
from backend.domain.usage_controls import AssistantSettings, CheckProfile

pytestmark = pytest.mark.integration

WS, PR = "ws_controls_it", "pr_controls_it"
NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def factory(disposable_database):
    engine = create_engine(disposable_database.settings())
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_story_and_its_workspace_keep_their_own_settings(factory) -> None:
    store = PostgresAssistantSettingsStore(factory)
    assert await store.get(workspace_id=WS, project_id=PR) == AssistantSettings()
    chosen = AssistantSettings(
        check_profile=CheckProfile.THOROUGH,
        scene_tokens=60_000,
        check_minutes=30,
        confirm_above_tokens=25_000,
        conversation_tokens=None,
    )
    await store.set(workspace_id=WS, project_id=PR, settings=chosen, actor_user_id="usr_a")
    await store.set(
        workspace_id=WS,
        settings=AssistantSettings(confirm_above_tokens=None, conversation_tokens=90_000),
        actor_user_id="usr_owner",
    )
    assert await store.get(workspace_id=WS, project_id=PR) == chosen
    assert (await store.get(workspace_id=WS)).conversation_tokens == 90_000
    # Choosing again replaces the row.
    await store.set(
        workspace_id=WS, project_id=PR, settings=AssistantSettings(), actor_user_id="usr_a"
    )
    assert (await store.get(workspace_id=WS, project_id=PR)).check_profile is (
        CheckProfile.ON_REQUEST
    )
    async with session_scope(factory) as db:
        count = (
            await db.execute(
                text("SELECT count(*) FROM assistant_settings WHERE workspace_id = :w"),
                {"w": WS},
            )
        ).scalar_one()
    assert count == 2


@pytest.mark.asyncio
async def test_a_conversation_counts_what_it_used(factory) -> None:
    store = PostgresAssistSessionStore(factory)
    session = AssistSession(
        id="chg_1",
        workspace_id=WS,
        project_id=PR,
        actor_user_id="usr_a",
        kind=AssistKind.CHANGE,
        created_at=NOW,
        updated_at=NOW,
    )
    await store.save(session)
    assert await store.tokens_used(workspace_id=WS, project_id=PR, session_id="chg_1") == 0
    await store.add_tokens(workspace_id=WS, project_id=PR, session_id="chg_1", tokens=1_200)
    await store.add_tokens(workspace_id=WS, project_id=PR, session_id="chg_1", tokens=300)
    # Saving the conversation again keeps its count.
    await store.save(session.model_copy(update={"understood": "Burn it."}))
    assert await store.tokens_used(workspace_id=WS, project_id=PR, session_id="chg_1") == 1_500
    # Another story's conversation of the same id is not touched.
    await store.add_tokens(workspace_id=WS, project_id="other", session_id="chg_1", tokens=9)
    assert await store.tokens_used(workspace_id=WS, project_id="other", session_id="chg_1") == 0


@pytest.mark.asyncio
async def test_a_job_keeps_the_cost_its_provider_reported(factory) -> None:
    store = PostgresJobStore(factory)
    await store.create(
        Job(
            workspace_id=WS,
            project_id=PR,
            id="job_cost",
            kind="scene_draft",
            status=JobStatus.QUEUED,
            actor_user_id="usr_a",
            auth_mode=AuthMode.LOCAL,
            policy_version=AUTHORIZATION_POLICY_VERSION,
            payload={"plan_id": "plan_1"},
        )
    )
    job = await store.claim(owner="worker_a")
    await store.add_usage(job, owner="worker_a", prompt_tokens=10, completion_tokens=1)
    found = await store.get(workspace_id=WS, project_id=PR, job_id="job_cost")
    assert found.provider_cost_usd is None
    await store.add_usage(
        job, owner="worker_a", prompt_tokens=10, completion_tokens=1, cost_usd=0.002
    )
    await store.add_usage(
        job, owner="worker_a", prompt_tokens=10, completion_tokens=1, cost_usd=0.0005
    )
    found = await store.get(workspace_id=WS, project_id=PR, job_id="job_cost")
    assert found.provider_cost_usd == pytest.approx(0.0025)
    assert found.prompt_tokens == 30
