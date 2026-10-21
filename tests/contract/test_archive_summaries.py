"""A restore puts back each kept scene summary in the line it belongs to: a what-if's
summary goes into the restored what-if, never dropped and never made the main story's."""

from __future__ import annotations

import asyncio

import pytest

from backend.services.canon_archive import _dedup_branch_id
from tests.contract.test_context_layers import _three
from tests.contract.test_god_hand import _world

pytestmark = pytest.mark.contract


def test_a_restore_keeps_a_what_ifs_scene_summary_in_the_restored_what_if(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    first, _second, _third = _three(auth_client, w)
    base = f"{w['base']}/plan/{first['id']}/summary"
    assert auth_client.put(base, json={"body": "Mara is home at last."}).status_code == 200
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Mara stays away"}).json()[
        "id"
    ]
    kept = auth_client.put(base, json={"body": "Mara dreams."}, headers={"X-Story-Branch": branch})
    assert kept.status_code == 200, kept.text
    workspace = fake_container.settings.AUTH_LOCAL_WORKSPACE_ID
    project = fake_container.settings.AUTH_LOCAL_PROJECT_ID
    service = fake_container.archive_service
    archive = asyncio.run(service.export_project(workspace, project))
    assert len(archive["scene_summaries"]) == 2

    target = "pr_summaries_restored"
    result = asyncio.run(service.import_project(workspace, target, archive))

    assert result["scene_summaries"] == 2
    store = fake_container.scene_summaries._store  # noqa: SLF001 - the rows as restored
    restored = {
        (row.body, row.branch_id)
        for (w_id, p_id, _), row in store.rows.items()
        if (w_id, p_id) == (workspace, target)
    }
    what_if = _dedup_branch_id(archive["checksum"], branch, workspace, target)
    assert restored == {("Mara is home at last.", None), ("Mara dreams.", what_if)}
