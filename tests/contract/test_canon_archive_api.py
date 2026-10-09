"""Archive export and import-status HTTP contract (2K)."""

import pytest

from tests.contract.test_canon_entities import _project_id, _publish_schema

pytestmark = pytest.mark.contract


def test_export_returns_checksummed_history_and_is_audited(auth_client, fake_container) -> None:
    project_id = _project_id(fake_container)
    _publish_schema(auth_client, project_id)
    base = f"/api/v1/projects/{project_id}"
    auth_client.post(
        f"{base}/entities", json={"type": "Character", "name": "Mara", "expected_revision": 0}
    )

    exported = auth_client.post(f"{base}/archive/export")

    assert exported.status_code == 200, exported.text
    archive = exported.json()
    assert archive["format"] == "novel-engine/canon-archive"
    assert len(archive["commits"]) == 1 and archive["checksum"]
    assert "credentials" not in archive and "sessions" not in archive
    audit = [e for e in fake_container.tenancy.audit if e.action == "archive.exported"]
    assert len(audit) == 1 and audit[0].detail["commits"] == 1
    assert "Mara" not in str(audit[0].detail)


def test_import_status_is_visible_on_the_project(auth_client, fake_container) -> None:
    project_id = _project_id(fake_container)
    base = f"/api/v1/projects/{project_id}"

    assert auth_client.get(f"{base}/imports").json() == {"items": []}
    assert auth_client.get(f"{base}/projection").json()["import"] is None
    missing = auth_client.delete(f"{base}/imports/imp_missing")
    assert missing.status_code == 404


def test_archive_routes_require_authentication(api_client, fake_container) -> None:
    base = f"/api/v1/projects/{_project_id(fake_container)}"
    assert api_client.get(f"{base}/imports").status_code == 401
    assert api_client.post(f"{base}/archive/export").status_code in {401, 403}
