from datetime import UTC, datetime, timedelta

import psycopg
import pytest
import pytest_asyncio

from backend.adapters.credentials import PostgresCredentialVault
from backend.adapters.usage import PostgresProviderUsageStore
from backend.core.crypto import generate_kek
from backend.core.database import create_engine, create_session_factory
from backend.domain.credentials import CredentialProvider, CredentialStatus
from backend.domain.usage import UsageRecord

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def session_factory(disposable_database):
    engine = create_engine(disposable_database.settings())
    try:
        yield create_session_factory(engine), disposable_database
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_vault_encrypts_at_rest_and_resolves(session_factory) -> None:
    factory, database = session_factory
    vault = PostgresCredentialVault(factory, kek=generate_kek(), key_version=2)

    credential = await vault.store(
        workspace_id="ws1",
        owner_user_id="u1",
        provider=CredentialProvider.OPENROUTER,
        label="mine",
        plaintext_key="sk-or-v1-supersecret",
    )

    with psycopg.connect(database.psycopg_dsn) as connection:
        row = connection.execute(
            "SELECT ciphertext, wrapped_dek, key_version FROM credentials WHERE id = %s",
            (credential.id,),
        ).fetchone()
    assert "supersecret" not in row[0]
    assert "supersecret" not in row[1]
    assert row[2] == 2

    assert await vault.get(workspace_id="ws2", credential_id=credential.id) is None
    assert (
        await vault.resolve_plaintext(
            workspace_id="ws1", credential_id=credential.id, actor_user_id="u1"
        )
        == "sk-or-v1-supersecret"
    )

    rotated = await vault.rotate(
        workspace_id="ws1", credential_id=credential.id, plaintext_key="sk-or-v1-rotated"
    )
    assert rotated.key_last4 == "ated"
    assert (
        await vault.resolve_plaintext(
            workspace_id="ws1", credential_id=credential.id, actor_user_id="u1"
        )
        == "sk-or-v1-rotated"
    )

    revoked = await vault.revoke(workspace_id="ws1", credential_id=credential.id)
    assert revoked.status == CredentialStatus.REVOKED
    assert await vault.delete(workspace_id="ws1", credential_id=credential.id) is True


@pytest.mark.asyncio
async def test_usage_store_totals_and_idempotency(session_factory) -> None:
    factory, _database = session_factory
    store = PostgresProviderUsageStore(factory)
    now = datetime.now(UTC)

    for index, (prompt, completion, cost) in enumerate([(10, 5, 0.01), (20, 0, 0.02)]):
        await store.record(
            UsageRecord(
                id=f"u{index}",
                workspace_id="w",
                actor_user_id="a",
                provider="openrouter",
                model="m",
                requested_route="chat",
                prompt_tokens=prompt,
                completion_tokens=completion,
                provider_cost_usd=cost,
                idempotency_key=f"j{index}",
                created_at=now,
            )
        )

    totals = await store.totals(
        workspace_id="w", actor_user_id="a", since=now - timedelta(seconds=60)
    )
    assert totals.request_count == 2
    assert totals.total_tokens == 35
    assert await store.has_idempotency_key(idempotency_key="j0") is True
    assert await store.has_idempotency_key(idempotency_key="missing") is False
    job = await store.totals_for_job(idempotency_key="j0")
    assert job.total_tokens == 15
