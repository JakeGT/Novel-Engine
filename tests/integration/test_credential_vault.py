import asyncio
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

    for index, (prompt, completion, cost, cached) in enumerate(
        [(10, 5, 0.01, 8), (20, 0, 0.02, 0)]
    ):
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
                cached_prompt_tokens=cached,
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
    # Cached prompt tokens are part of the prompt, never added to the total (CTX-015).
    assert totals.cached_prompt_tokens == 8
    assert await store.has_idempotency_key(idempotency_key="j0") is True
    assert await store.has_idempotency_key(idempotency_key="missing") is False
    job = await store.totals_for_job(idempotency_key="j0")
    assert job.total_tokens == 15


@pytest.mark.asyncio
async def test_kek_rotation_still_decrypts_old_credentials(session_factory) -> None:
    factory, _database = session_factory
    old_kek = generate_kek()
    new_kek = generate_kek()

    stored = await PostgresCredentialVault(factory, kek=old_kek, key_version=1).store(
        workspace_id="ws1",
        owner_user_id="u1",
        provider=CredentialProvider.OPENROUTER,
        label="old",
        plaintext_key="sk-or-v1-kept",
    )

    rotated_vault = PostgresCredentialVault(
        factory,
        kek=new_kek,
        key_version=2,
        previous_keks={1: old_kek},
    )
    assert (
        await rotated_vault.resolve_plaintext(
            workspace_id="ws1", credential_id=stored.id, actor_user_id="u1"
        )
        == "sk-or-v1-kept"
    )


@pytest.mark.asyncio
async def test_concurrent_store_keeps_one_active_credential_per_provider(session_factory) -> None:
    factory, database = session_factory
    vault = PostgresCredentialVault(factory, kek=generate_kek())

    await asyncio.gather(
        vault.store(
            workspace_id="ws1",
            owner_user_id="u1",
            provider=CredentialProvider.OPENROUTER,
            label="first",
            plaintext_key="sk-or-v1-first",
        ),
        vault.store(
            workspace_id="ws1",
            owner_user_id="u1",
            provider=CredentialProvider.OPENROUTER,
            label="second",
            plaintext_key="sk-or-v1-second",
        ),
    )

    credentials = await vault.list_for_workspace("ws1")
    assert sum(item.status == CredentialStatus.ACTIVE for item in credentials) == 1
    with psycopg.connect(database.psycopg_dsn) as connection:
        active_count = connection.execute(
            "SELECT count(*) FROM credentials "
            "WHERE workspace_id = %s AND provider = %s AND status = 'active'",
            ("ws1", CredentialProvider.OPENROUTER.value),
        ).fetchone()[0]
    assert active_count == 1


@pytest.mark.asyncio
async def test_rotating_revoked_credential_makes_it_the_only_active_one(session_factory) -> None:
    factory, _database = session_factory
    vault = PostgresCredentialVault(factory, kek=generate_kek())
    first = await vault.store(
        workspace_id="ws1",
        owner_user_id="u1",
        provider=CredentialProvider.OPENROUTER,
        label="first",
        plaintext_key="sk-or-v1-first",
    )
    second = await vault.store(
        workspace_id="ws1",
        owner_user_id="u1",
        provider=CredentialProvider.OPENROUTER,
        label="second",
        plaintext_key="sk-or-v1-second",
    )

    restored = await vault.rotate(
        workspace_id="ws1", credential_id=first.id, plaintext_key="sk-or-v1-restored"
    )
    credentials = {item.id: item for item in await vault.list_for_workspace("ws1")}

    assert restored.status == CredentialStatus.ACTIVE
    assert credentials[first.id].status == CredentialStatus.ACTIVE
    assert credentials[second.id].status == CredentialStatus.REVOKED
