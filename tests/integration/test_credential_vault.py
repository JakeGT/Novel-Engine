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


async def _store(vault: PostgresCredentialVault, workspace: str, key: str):
    return await vault.store(
        workspace_id=workspace,
        owner_user_id="u1",
        provider=CredentialProvider.OPENROUTER,
        label="mine",
        plaintext_key=key,
    )


@pytest.mark.asyncio
async def test_rekey_moves_every_active_key_so_the_old_key_can_be_retired(
    session_factory,
) -> None:
    """OPS-004: one command re-locks stored keys; afterwards the old key is not needed."""
    factory, database = session_factory
    old_kek, new_kek = generate_kek(), generate_kek()
    old_vault = PostgresCredentialVault(factory, kek=old_kek, key_version=1)
    first = await _store(old_vault, "ws1", "sk-or-v1-first")
    second = await _store(old_vault, "ws2", "sk-or-v1-second")
    revoked = await _store(old_vault, "ws3", "sk-or-v1-gone")
    await old_vault.revoke(workspace_id="ws3", credential_id=revoked.id)
    with psycopg.connect(database.psycopg_dsn) as connection:
        before = dict(connection.execute("SELECT id, ciphertext FROM credentials").fetchall())

    rotating = PostgresCredentialVault(
        factory, kek=new_kek, key_version=2, previous_keks={1: old_kek}
    )
    check = await rotating.verify()
    assert check.ok and check.old_active == 2 and check.retirable_versions == []

    dry = await rotating.rekey(dry_run=True)
    assert dry.ok and len(dry.rekeyed) == 2 and dry.skipped_revoked == 1
    assert {item.key_version for item in await rotating.key_versions()} == {1}

    applied = await rotating.rekey(dry_run=False)
    assert applied.ok
    assert {item.credential_id for item in applied.rekeyed} == {first.id, second.id}
    assert applied.skipped_revoked == 1

    with psycopg.connect(database.psycopg_dsn) as connection:
        after = dict(
            connection.execute(
                "SELECT id, key_version FROM credentials WHERE status = 'active'"
            ).fetchall()
        )
        ciphertexts = dict(connection.execute("SELECT id, ciphertext FROM credentials").fetchall())
    assert after == {first.id: 2, second.id: 2}
    # Only the data key is wrapped again; the stored secret itself is unchanged.
    assert ciphertexts == before

    # With the old key gone, both keys still open.
    retired = PostgresCredentialVault(factory, kek=new_kek, key_version=2)
    for credential, key in ((first, "sk-or-v1-first"), (second, "sk-or-v1-second")):
        assert (
            await retired.resolve_plaintext(
                workspace_id=credential.workspace_id,
                credential_id=credential.id,
                actor_user_id="u1",
            )
            == key
        )
    final = await rotating.verify()
    assert final.ok and final.old_active == 0 and final.retirable_versions == [1]

    # A second run moves nothing.
    again = await rotating.rekey(dry_run=False)
    assert again.rekeyed == [] and again.already_current == 2


@pytest.mark.asyncio
async def test_rekey_never_writes_a_key_it_cannot_open(session_factory) -> None:
    factory, database = session_factory
    real_old = generate_kek()
    stored = await _store(
        PostgresCredentialVault(factory, kek=real_old, key_version=1), "ws1", "sk-or-v1-x"
    )
    orphan = await _store(
        PostgresCredentialVault(factory, kek=generate_kek(), key_version=3), "ws2", "sk-or-v1-y"
    )

    wrong = PostgresCredentialVault(
        factory, kek=generate_kek(), key_version=4, previous_keks={1: generate_kek()}
    )
    report = await wrong.rekey(dry_run=False)

    assert not report.ok
    assert report.unopenable == [stored.id]
    assert report.missing_version == {3: [orphan.id]}
    assert report.rekeyed == []
    check = await wrong.verify()
    assert not check.ok and check.unopenable == [stored.id]
    with psycopg.connect(database.psycopg_dsn) as connection:
        versions = dict(connection.execute("SELECT id, key_version FROM credentials").fetchall())
    assert versions == {stored.id: 1, orphan.id: 3}
    # The real key still opens it: nothing was damaged.
    assert (
        await PostgresCredentialVault(factory, kek=real_old, key_version=1).resolve_plaintext(
            workspace_id="ws1", credential_id=stored.id, actor_user_id="u1"
        )
        == "sk-or-v1-x"
    )


@pytest.mark.asyncio
async def test_the_rekey_command_moves_keys_and_audits_each_move(session_factory) -> None:
    """The operator command end to end on a real database (OPS-004)."""
    from backend.devtools import rekey

    factory, database = session_factory
    old_kek, new_kek = generate_kek(), generate_kek()
    stored = await _store(
        PostgresCredentialVault(factory, kek=old_kek, key_version=1), "ws1", "sk-or-v1-z"
    )
    settings = database.settings(
        CREDENTIAL_KEK=new_kek,
        CREDENTIAL_KEK_VERSION=2,
        CREDENTIAL_PREVIOUS_KEKS=f"1:{old_kek}",
    )

    code, dry = await rekey.run("rekey", settings)
    assert code == 0 and dry["would_move"] == 1
    code, moved = await rekey.run("rekey", settings, apply=True)
    assert code == 0 and moved["moved"] == 1
    code, check = await rekey.run("verify", settings)
    assert code == 0 and check["versions_safe_to_remove"] == [1]
    assert new_kek not in str(check) and old_kek not in str(moved)

    with psycopg.connect(database.psycopg_dsn) as connection:
        audit = connection.execute(
            "SELECT action, target_id, detail FROM audit_events WHERE workspace_id = 'ws1'"
        ).fetchall()
    assert audit == [
        ("credential.rekeyed", stored.id, {"from_version": 1, "to_version": 2}),
    ]
    retired = database.settings(CREDENTIAL_KEK=new_kek, CREDENTIAL_KEK_VERSION=2)
    code, after = await rekey.run("verify", retired)
    assert code == 0 and after["opened"] == 1


@pytest.mark.asyncio
async def test_each_rekeyed_key_is_recorded_with_its_move_even_when_a_run_stops(
    session_factory, monkeypatch
) -> None:
    """OPS-004: a move and its audit event are one transaction, so a run that stops part
    way leaves every moved key recorded and no record of a key it did not move."""
    from backend.adapters import credentials as vault_module

    factory, database = session_factory
    old_kek, new_kek = generate_kek(), generate_kek()
    old_vault = PostgresCredentialVault(factory, kek=old_kek, key_version=1)
    first = await _store(old_vault, "ws1", "sk-or-v1-first")
    second = await _store(old_vault, "ws2", "sk-or-v1-second")
    rotating = PostgresCredentialVault(
        factory, kek=new_kek, key_version=2, previous_keks={1: old_kek}
    )
    real = vault_module.rewrap_envelope
    calls = {"n": 0}

    def stops_on_the_second(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("the server stopped")
        return real(*args, **kwargs)

    monkeypatch.setattr(vault_module, "rewrap_envelope", stops_on_the_second)
    with pytest.raises(RuntimeError):
        await rotating.rekey(dry_run=False, audit_actor="operator:rekey")
    with psycopg.connect(database.psycopg_dsn) as connection:
        versions = dict(connection.execute("SELECT id, key_version FROM credentials").fetchall())
        events = connection.execute(
            "SELECT workspace_id, actor_user_id, action, target_type, target_id, detail "
            "FROM audit_events WHERE action = 'credential.rekeyed'"
        ).fetchall()
    moved = first if versions[first.id] == 2 else second
    assert sorted(versions.values()) == [1, 2]
    assert events == [
        (
            moved.workspace_id,
            "operator:rekey",
            "credential.rekeyed",
            "credential",
            moved.id,
            {"from_version": 1, "to_version": 2},
        )
    ]

    # Run again to the end: the other key is moved and recorded once.
    monkeypatch.setattr(vault_module, "rewrap_envelope", real)
    done = await rotating.rekey(dry_run=False, audit_actor="operator:rekey")
    assert len(done.rekeyed) == 1
    with psycopg.connect(database.psycopg_dsn) as connection:
        targets = sorted(
            row[0]
            for row in connection.execute(
                "SELECT target_id FROM audit_events WHERE action = 'credential.rekeyed'"
            ).fetchall()
        )
    assert targets == sorted([first.id, second.id])
    # A dry run, or one without an actor, records nothing.
    assert (await rotating.rekey(dry_run=True, audit_actor="operator:rekey")).rekeyed == []
