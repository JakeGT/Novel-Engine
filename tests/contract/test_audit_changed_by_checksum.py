"""Which scenes a what-if wrote differently is read from the texts' stored checksums in
one query, not by loading every scene's words one after another (CON-011)."""

from __future__ import annotations

import pytest

from backend.services.story_audit import StoryAudit
from tests.contract.test_studio import _save, _studio
from tests.contract.test_voice_and_audits import FIRST, _book, _prose

pytestmark = pytest.mark.contract


def test_the_changed_scenes_are_found_from_checksums_without_reading_any_text(
    auth_client, fake_container, monkeypatch
):
    w, _beat, scenes = _book(auth_client, fake_container, drifting=False)
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Mara stays away"}).json()[
        "id"
    ]
    url = f"{w['base']}/what-ifs/{branch}/audit/changed/estimate"
    # Two scenes written in the what-if: one differently, one changed and changed back.
    current = _studio(auth_client, w, scenes[1], branch)["version_id"]
    assert _save(auth_client, w, scenes[1], _prose(FIRST), current, branch).status_code == 200
    before = _studio(auth_client, w, scenes[2], branch)
    saved = _save(auth_client, w, scenes[2], _prose(FIRST), before["version_id"], branch)
    assert saved.status_code == 200, saved.text
    again = _studio(auth_client, w, scenes[2], branch)["version_id"]
    back = _save(auth_client, w, scenes[2], before["content"], again, branch)
    assert back.status_code == 200, back.text

    store = fake_container.studio_service._sources._store  # noqa: SLF001
    read_one_by_one: list[str] = []
    original = StoryAudit._differs  # noqa: SLF001

    async def counted(self, workspace_id, project_id, main, what_if):
        read_one_by_one.append(what_if)
        return await original(self, workspace_id, project_id, main, what_if)

    monkeypatch.setattr(StoryAudit, "_differs", counted)
    store.hash_reads = 0
    found = auth_client.get(url, params={"passes": ["clarity"]})
    assert found.status_code == 200, found.text
    assert [s["title"] for s in found.json()["scenes"]] == ["Two"]
    # One query of checksums, and no scene's text loaded to compare it.
    assert store.hash_reads == 1
    assert read_one_by_one == []
