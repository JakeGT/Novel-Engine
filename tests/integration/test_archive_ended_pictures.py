"""A story whose picture was shown and then retired, or had its file deleted (AST-008),
restores from its archive into a clean environment on PostgreSQL: the change that first
showed the picture is checked against the picture as it stood then, and the restored
picture keeps its later state (retired, or deleted with its file still gone)."""

import uuid

import pytest

from backend.domain.media import AssetLifecycle
from backend.graph.manager import GraphManager
from backend.seed.apply import SEED_PROJECT_ID, SEED_WORKSPACE_ID
from backend.seed.canon import (
    CHART_ASSET,
    CHART_BYTES,
    CHART_LINK,
    CHART_VERSION,
    PRIMARY_HEAD,
    SEED_PROJECTS,
    apply_canon_seed,
)
from backend.seed.schema import apply_schema
from backend.seed.tenancy import apply_tenancy
from tests.integration.test_archive_clean_environment import _Environment, second_database

pytestmark = pytest.mark.integration

__all__ = ["second_database"]


async def _seeded(source: _Environment, dsn: str) -> None:
    apply_tenancy(dsn)
    for project in SEED_PROJECTS:
        apply_schema(dsn, workspace_id=project.workspace_id, project_id=project.project_id)
    seeded = await apply_canon_seed(
        journal=source.journal,
        repository=source.repo,
        schemas=source.schemas,
        media=source.media,
        objects=source.objects,
    )
    assert seeded.ok, seeded.problems


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["retired", "deleted"])
async def test_a_picture_shown_then_ended_restores_with_its_history_and_state(
    disposable_database, second_database, neo4j_settings, neo4j_driver, ending
) -> None:
    manager = GraphManager(neo4j_settings)
    source = _Environment(disposable_database, manager)
    target = _Environment(second_database, manager)
    suffix = uuid.uuid4().hex[:8]
    source_scope = {"workspace_id": SEED_WORKSPACE_ID, "project_id": SEED_PROJECT_ID}
    target_scope = {"workspace_id": f"ws_ended_{suffix}", "project_id": f"prj_ended_{suffix}"}
    try:
        await _seeded(source, disposable_database.psycopg_dsn)
        await source.writer.purge(**source_scope)
        # The chart is shown in the seeded history; it is retired after that.
        await source.media.retire_asset(
            asset_id=CHART_ASSET, retired_revision=PRIMARY_HEAD, **source_scope
        )
        if ending == "deleted":
            version = await source.media.get_version(version_id=CHART_VERSION, **source_scope)
            assert source.objects.delete(version.object_key, **source_scope)
            await source.media.delete_original(
                version_id=CHART_VERSION, retired_revision=PRIMARY_HEAD, **source_scope
            )
            await source.media.delete_asset(
                asset_id=CHART_ASSET, retired_revision=PRIMARY_HEAD, **source_scope
            )
        archive = await source.archive.export_project(SEED_WORKSPACE_ID, SEED_PROJECT_ID)

        result = await target.archive.import_project(
            target_scope["workspace_id"], target_scope["project_id"], archive
        )

        assert result["commits"] == PRIMARY_HEAD and result["verified"] is True
        target_branch = await target.journal.ensure_root_branch(**target_scope)
        commits = await target.repo.list_commits(branch_id=target_branch.id, **target_scope)
        assert [c.revision for c in commits] == list(range(1, PRIMARY_HEAD + 1))
        link = await target.repo.get_link(link_id=CHART_LINK, **target_scope)
        source_link = await source.repo.get_link(link_id=CHART_LINK, **source_scope)
        assert link is not None and source_link is not None
        assert (link.created_revision, link.retired_revision, link.asset_version_id) == (
            source_link.created_revision,
            source_link.retired_revision,
            source_link.asset_version_id,
        )
        asset = await target.media.get_asset(asset_id=CHART_ASSET, **target_scope)
        restored = await target.media.get_version(version_id=CHART_VERSION, **target_scope)
        before = await source.media.get_version(version_id=CHART_VERSION, **source_scope)
        assert asset is not None and restored is not None and before is not None
        assert asset.retired_revision == PRIMARY_HEAD
        assert restored.retired_revision == before.retired_revision
        assert restored.deleted_at == before.deleted_at
        stored = [
            target.objects.open(stat.key, **target_scope)
            for stat in target.objects.list_objects(**target_scope)
        ]
        if ending == "retired":
            assert asset.lifecycle is AssetLifecycle.RETIRED
            assert target.objects.open(restored.object_key, **target_scope) == CHART_BYTES
        else:
            assert asset.lifecycle is AssetLifecycle.DELETED
            assert restored.deleted_at is not None
            # No file is restored for a deleted original.
            assert CHART_BYTES not in stored
    finally:
        await source.writer.purge(**source_scope)
        await target.writer.purge(**target_scope)
        await source.engine.dispose()
        await target.engine.dispose()
        manager.close()
