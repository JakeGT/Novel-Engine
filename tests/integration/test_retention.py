import hashlib

import psycopg
import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.adapters.identity_store import PostgresIdentityStore, PostgresSessionStore
from backend.core.database import create_engine, create_session_factory
from backend.devtools.retention import RULES, purge_expired
from backend.domain.identity import IdentityClaims

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def factory(disposable_database):
    engine = create_engine(disposable_database.settings())
    try:
        yield create_session_factory(engine), disposable_database
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_purge_expired_sessions(factory) -> None:
    session_factory, database = factory
    user = await PostgresIdentityStore(session_factory).get_or_create_user(
        IdentityClaims(issuer="local", subject="u1")
    )
    store = PostgresSessionStore(session_factory)
    session = await store.create(user_id=user.id, ttl_seconds=3600, csrf_token="csrf")

    async with session_factory() as db_session:
        await db_session.execute(
            text("UPDATE sessions SET expires_at = now() - interval '1 day' WHERE id_hash = :h"),
            {"h": hashlib.sha256(session.id.encode()).hexdigest()},
        )
        await db_session.commit()

    counts = purge_expired(database.psycopg_dsn)
    assert counts["sessions"] == 1
    assert await store.get(session.id) is None
    assert counts["login_attempts"] == 0
    assert counts["invitations"] == 0


def test_purge_tolerates_unmigrated_database(disposable_database) -> None:
    unmigrated = disposable_database.settings(POSTGRES_DB="postgres")
    counts = purge_expired(unmigrated.postgres_dsn_psycopg)
    assert set(counts) == {rule.name for rule in RULES}
    assert all(value == 0 for value in counts.values())
    assert purge_expired(unmigrated.postgres_dsn_psycopg, dry_run=True) == counts


# ------------------------------------------------------------- assistant leftovers (OPS-005)

W, P = "ws_keep", "pr_keep"


def _story(dsn: str) -> None:
    """A scene's text with versions, drafts, notes, jobs and context records of every age."""
    days = "now() - make_interval(days => %s)"
    with psycopg.connect(dsn, autocommit=True) as db:

        def run(sql: str, *args) -> None:
            db.execute(sql, args)

        for source in ("src_1", "src_2"):
            run(
                "INSERT INTO source_texts (workspace_id, project_id, id, title, kind) "
                "VALUES (%s, %s, %s, 'Scene', 'scene')",
                W,
                P,
                source,
            )
        # src_1: version 1 replaced 45 days ago by version 2, which the author kept from a
        # draft. src_2: version 3 replaced only 5 days ago.
        for version, source, number, age, manifest in (
            ("ver_1", "src_1", 1, 60, None),
            ("ver_2", "src_1", 2, 45, "ctx_kept"),
            ("ver_3", "src_2", 1, 60, None),
            ("ver_4", "src_2", 2, 5, None),
        ):
            run(
                "INSERT INTO source_versions (workspace_id, project_id, id, source_id, number, "
                f"content, content_hash, manifest_id, created_at) VALUES (%s, %s, %s, %s, %s, "
                f"'Tobin waited at Lantern Harbor.', 'hash', %s, {days})",
                W,
                P,
                version,
                source,
                number,
                manifest,
                age,
            )
        for manifest, age in (
            ("ctx_kept", 50),
            ("ctx_waiting", 40),
            ("ctx_unused_old", 40),
            ("ctx_unused_new", 2),
        ):
            run(
                "INSERT INTO context_manifests (workspace_id, project_id, id, purpose, "
                "policy_version, checksum, words, budget, manifest, created_at) VALUES "
                f"(%s, %s, %s, 'scene_draft', 'v1', 'c', 10, 100, '{{}}'::jsonb, {days})",
                W,
                P,
                manifest,
                age,
            )
        for job, status, finished_age, updated_age in (
            ("job_done_old", "succeeded", 40, 40),
            ("job_done_new", "succeeded", 1, 1),
            ("job_failed_old_waiting", "failed", 40, 40),
            ("job_cancelled_old", "cancelled", None, 40),
            ("job_queued_old", "queued", None, 40),
        ):
            finished = "NULL" if finished_age is None else days
            run(
                "INSERT INTO jobs (workspace_id, project_id, id, kind, status, payload, "
                "actor_user_id, auth_mode, policy_version, finished_at, updated_at) VALUES "
                f"(%s, %s, %s, 'scene_draft', %s, "
                "'{\"direction\": \"Mara refuses the ship\"}'::jsonb, 'usr', 'local', 'v1', "
                f"{finished}, {days})",
                *(
                    (W, P, job, status, finished_age, updated_age)
                    if finished_age is not None
                    else (W, P, job, status, updated_age)
                ),
            )
        run(
            "INSERT INTO scene_drafts (workspace_id, project_id, id, plan_id, job_id, "
            "manifest_id, number, content, status, created_at) VALUES "
            f"(%s, %s, 'drf_waiting', 'plan_1', 'job_failed_old_waiting', 'ctx_waiting', 1, "
            f"'Tobin did not wait.', 'proposed', {days})",
            W,
            P,
            40,
        )
        run(
            "INSERT INTO scene_drafts (workspace_id, project_id, id, plan_id, job_id, "
            "manifest_id, number, content, status, kept_version_id, created_at) VALUES "
            f"(%s, %s, 'drf_kept', 'plan_1', 'job_done_old', 'ctx_kept', 2, "
            f"'Words left over.', 'kept', 'ver_2', {days})",
            W,
            P,
            50,
        )
        for finding, kind, target, age in (
            ("fnd_waiting", "draft", "drf_waiting", 40),
            ("fnd_kept_draft", "draft", "drf_kept", 1),
            ("fnd_discarded", "draft", "drf_gone", 1),
            ("fnd_replaced", "version", "ver_1", 50),
            ("fnd_current", "version", "ver_2", 40),
            ("fnd_lately_replaced", "version", "ver_3", 40),
            ("fnd_replaced_recent_note", "version", "ver_1", 5),
            ("fnd_text_gone", "version", "ver_missing", 40),
        ):
            run(
                "INSERT INTO draft_findings (workspace_id, project_id, id, target_kind, "
                "target_id, category, message, created_at) VALUES "
                f"(%s, %s, %s, %s, %s, 'continuity', 'Mara was at sea.', {days})",
                W,
                P,
                finding,
                kind,
                target,
                age,
            )


def _ids(dsn: str, table: str) -> set[str]:
    with psycopg.connect(dsn) as db:
        return {
            row[0]
            for row in db.execute(
                f"SELECT id FROM {table} WHERE workspace_id = %s AND project_id = %s", (W, P)
            )
        }


def _snapshot(dsn: str) -> dict[str, set[str]]:
    tables = (
        "jobs",
        "context_manifests",
        "draft_findings",
        "scene_drafts",
        "source_versions",
        "source_texts",
    )
    return {table: _ids(dsn, table) for table in tables}


def test_a_dry_run_counts_and_changes_nothing(disposable_database) -> None:
    dsn = disposable_database.psycopg_dsn
    _story(dsn)
    before = _snapshot(dsn)

    counts = purge_expired(dsn, dry_run=True)

    assert _snapshot(dsn) == before
    assert counts["finished_jobs"] == 2
    assert counts["unused_context_records"] == 1
    assert counts["notes_on_finished_drafts"] == 2
    assert counts["words_on_kept_drafts"] == 1
    assert counts["notes_on_replaced_versions"] == 2


def test_assistant_leftovers_go_and_everything_the_author_kept_stays(
    disposable_database,
) -> None:
    dsn = disposable_database.psycopg_dsn
    _story(dsn)
    before = _snapshot(dsn)
    expected = purge_expired(dsn, dry_run=True)

    counts = purge_expired(dsn)

    assert counts == expected
    after = _snapshot(dsn)
    # Finished work goes after 30 days, unless a draft it wrote is still waiting.
    assert after["jobs"] == {"job_done_new", "job_failed_old_waiting", "job_queued_old"}
    # A record of what was given stays while a kept version or a waiting draft uses it.
    assert after["context_manifests"] == {"ctx_kept", "ctx_waiting", "ctx_unused_new"}
    # Notes stay on a waiting draft and on a current or lately replaced version.
    assert after["draft_findings"] == {
        "fnd_waiting",
        "fnd_current",
        "fnd_lately_replaced",
        "fnd_replaced_recent_note",
    }
    # No text, version or draft is ever removed.
    for table in ("scene_drafts", "source_versions", "source_texts"):
        assert after[table] == before[table]
    with psycopg.connect(dsn) as db:
        words = dict(db.execute("SELECT id, content FROM scene_drafts").fetchall())
    assert words == {"drf_waiting": "Tobin did not wait.", "drf_kept": ""}

    # Running again finds nothing more.
    assert all(value == 0 for value in purge_expired(dsn).values())


def test_a_shorter_period_takes_more_and_a_period_below_a_day_is_refused(
    disposable_database,
) -> None:
    dsn = disposable_database.psycopg_dsn
    _story(dsn)
    assert purge_expired(dsn, days=3, dry_run=True)["finished_jobs"] == 2
    # Finished a day ago, so still within even a two-day period.
    assert "job_done_new" in _ids(dsn, "jobs")
    assert purge_expired(dsn, days=1, dry_run=True)["unused_context_records"] == 2
    with pytest.raises(ValueError):
        purge_expired(dsn, days=0)


def test_the_command_reports_in_plain_words(disposable_database, monkeypatch, capsys) -> None:
    from backend.devtools import retention

    dsn = disposable_database.psycopg_dsn
    _story(dsn)
    monkeypatch.setattr(retention, "default_settings", disposable_database.settings(), raising=True)
    assert retention.main(["--dry-run", "--days", "30"]) == 0
    printed = capsys.readouterr().out
    assert "retention: finished background work: would remove 2" in printed
    # Nothing the author wrote is printed.
    assert "Mara" not in printed and "Tobin" not in printed
    assert "job_done_old" in _ids(dsn, "jobs")
