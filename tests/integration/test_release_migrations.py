"""This release's migrations (0032–0035) on real data: upgraded in place, rolled back, again.

Story time and what-ifs (0032), scene context (0033), the scene studio (0034) and scene
readings (0035). Rows written at the Phase 2.7 head must survive the upgrade unchanged; a
rollback must never delete the author's words.
"""

import psycopg
import pytest
from alembic import command

from backend.migrate import alembic_config, postgres_head_revision

pytestmark = pytest.mark.integration

BEFORE = "0031_story_rules"
WS, PR = "ws_release_it", "pr_release_it"
NEW_TABLES = (
    "story_clocks",
    "text_passages",
    "context_manifests",
    "scene_manuscripts",
    "jobs",
    "scene_drafts",
    "draft_findings",
    "story_voices",
    "scene_readings",
)


def _config(database):
    config = alembic_config()
    config.set_main_option("sqlalchemy.url", database.sqlalchemy_dsn)
    return config


def _run(dsn: str, sql: str, *params):
    with psycopg.connect(dsn, autocommit=True) as connection:
        cursor = connection.execute(sql, params)
        return cursor.fetchall() if cursor.description else None


def _exists(dsn: str, table: str) -> bool:
    return _run(dsn, "SELECT to_regclass(%s)", f"public.{table}")[0][0] is not None


def test_this_releases_migrations_roll_back_and_forward(disposable_database) -> None:
    config, dsn = _config(disposable_database), disposable_database.psycopg_dsn
    for _ in range(2):
        command.downgrade(config, BEFORE)
        assert not any(_exists(dsn, table) for table in NEW_TABLES)
        command.upgrade(config, "head")
        assert (
            _run(dsn, "SELECT version_num FROM alembic_version")[0][0] == postgres_head_revision()
        )
        assert all(_exists(dsn, table) for table in NEW_TABLES)


def test_writing_from_before_survives_and_a_rollback_keeps_a_scenes_words(
    disposable_database,
) -> None:
    config, dsn = _config(disposable_database), disposable_database.psycopg_dsn
    command.downgrade(config, BEFORE)
    _run(
        dsn,
        "INSERT INTO source_texts (workspace_id, project_id, id, title, kind) "
        "VALUES (%s, %s, 'src_old', 'Chapter one', 'paste')",
        WS,
        PR,
    )
    _run(
        dsn,
        "INSERT INTO source_versions (workspace_id, project_id, id, source_id, number, content, "
        "content_hash) VALUES (%s, %s, 'ver_old', 'src_old', 1, 'The old pilot tied up.', 'h1')",
        WS,
        PR,
    )
    command.upgrade(config, "head")
    # Brought-in writing is the author's, with no lineage invented for it.
    assert _run(
        dsn,
        "SELECT content, origin, parent_version_id, draft_id FROM source_versions WHERE id = 'ver_old'",
    ) == [("The old pilot tied up.", "author", None, None)]

    # A scene written at the new head...
    _run(
        dsn,
        "INSERT INTO source_texts (workspace_id, project_id, id, title, kind) "
        "VALUES (%s, %s, 'src_scene', 'The Docks', 'scene')",
        WS,
        PR,
    )
    _run(
        dsn,
        "INSERT INTO source_versions (workspace_id, project_id, id, source_id, number, content, "
        "content_hash, origin) VALUES (%s, %s, 'ver_scene', 'src_scene', 1, "
        "'Tobin waited at the harbor.', 'h2', 'assistant')",
        WS,
        PR,
    )
    # ...survives a rollback as an ordinary text, words intact, and the upgrade after it.
    command.downgrade(config, "0033_context")
    assert _run(dsn, "SELECT kind FROM source_texts WHERE id = 'src_scene'") == [("paste",)]
    assert _run(dsn, "SELECT content FROM source_versions WHERE id = 'ver_scene'") == [
        ("Tobin waited at the harbor.",)
    ]
    command.upgrade(config, "head")
    assert _run(dsn, "SELECT origin FROM source_versions WHERE id = 'ver_scene'") == [("author",)]
