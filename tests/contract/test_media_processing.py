"""Picture preparation over HTTP: smaller copies, status, retry, room, health, archives
and who may see them (AST-003, AST-008, OPS-007)."""

from __future__ import annotations

import asyncio
import io

import pytest
from PIL import Image

from backend.adapters.image_processor import PROCESSOR_VERSION
from backend.domain.identity import IdentityClaims
from backend.services.media_service import MEDIA_PROCESS_KIND
from tests.contract.test_workspace_scope import (
    _create_canon_entity,
    _create_project,
    _create_workspace,
    _hosted_login,
)
from tests.contract.test_workspace_scope import _publish_schema as _hosted_schema

pytestmark = pytest.mark.contract

_EMPTY_DEFINITION = {
    "label": "core",
    "description": "",
    "entity_extensions": [],
    "predicate_extensions": [],
}


def _png(color: str = "teal") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(out, format="PNG")
    return out.getvalue()


def _run(container) -> list:
    return asyncio.run(container.job_runner.run_pending())


def _setup(client, container) -> tuple[str, str, dict]:
    project_id = container.settings.AUTH_LOCAL_PROJECT_ID
    base = f"/api/v1/projects/{project_id}"
    draft = client.post(f"{base}/schemas/drafts", json={"definition": _EMPTY_DEFINITION})
    client.post(f"{base}/schemas/{draft.json()['version']['id']}/publish")
    entity = client.post(
        f"{base}/entities", json={"type": "Character", "name": "Mara", "expected_revision": 0}
    ).json()["id"]
    upload = client.post(
        f"{base}/entities/{entity}/pictures", params={"expected_revision": 1}, content=_png()
    )
    assert upload.status_code == 201, upload.text
    return base, project_id, upload.json()


def test_a_thumbnail_is_served_once_made_and_the_original_until_then(
    auth_client, fake_container
) -> None:
    base, _project, body = _setup(auth_client, fake_container)
    asset_id, version_id = body["link"]["asset_id"], body["version"]["id"]
    content = f"{base}/assets/{asset_id}/versions/{version_id}/content"

    early = auth_client.get(content, params={"size": "thumbnail"})
    assert early.status_code == 200 and early.headers["X-Picture-Size"] == "original"
    assert early.headers["content-type"] == "image/png"
    # A fallback is never kept by the browser as if it were the copy.
    assert "no-cache" in early.headers["Cache-Control"]
    status = auth_client.get(f"{base}/assets/{asset_id}/versions/{version_id}/processing").json()
    assert status["status"] == "queued" and status["copies"] == []

    [job] = _run(fake_container)
    assert job.kind == MEDIA_PROCESS_KIND and job.status.value == "succeeded"

    thumbnail = auth_client.get(content, params={"size": "thumbnail"})
    assert thumbnail.headers["X-Picture-Size"] == "thumbnail"
    assert thumbnail.headers["content-type"] == "image/webp"
    # A copy is made again when the processor changes, at the same address, so it is
    # never cached as unchanging: it carries a tag naming the processor and its bytes.
    assert "immutable" not in thumbnail.headers["Cache-Control"]
    assert PROCESSOR_VERSION in thumbnail.headers["ETag"]
    again = auth_client.get(
        content, params={"size": "thumbnail"}, headers={"If-None-Match": thumbnail.headers["ETag"]}
    )
    assert again.status_code == 304 and again.content == b""
    stale = auth_client.get(
        content, params={"size": "thumbnail"}, headers={"If-None-Match": '"thumbnail-old-0"'}
    )
    assert stale.status_code == 200 and stale.content == thumbnail.content
    assert thumbnail.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in thumbnail.headers["Content-Security-Policy"]
    web = auth_client.get(content, params={"size": "web"})
    assert web.headers["X-Picture-Size"] == "web"
    original = auth_client.get(content)
    assert original.headers["X-Picture-Size"] == "original" and original.content == _png()
    assert "immutable" in original.headers["Cache-Control"]  # a version never changes
    assert auth_client.get(content, params={"size": "huge"}).status_code == 422

    ready = auth_client.get(f"{base}/assets/{asset_id}/versions/{version_id}/processing").json()
    assert ready["status"] == "ready" and ready["copies"] == ["thumbnail", "web"]
    assert ready["current"] is True and ready["retryable"] is False

    library = auth_client.get(f"{base}/pictures").json()
    [picture] = library["items"]
    assert picture["processing"] == "ready" and picture["has_thumbnail"] is True
    assert library["storage"]["used_bytes"] > 0
    assert library["storage"]["quota_bytes"] == fake_container.settings.MEDIA_WORKSPACE_QUOTA_BYTES

    segments = auth_client.get(f"{base}/assets/{asset_id}/versions/{version_id}/segments").json()
    assert [segment["kind"] for segment in segments["items"]] == ["whole"]
    region = auth_client.post(
        f"{base}/assets/{asset_id}/versions/{version_id}/segments",
        json={"x": 1, "y": 2, "width": 10, "height": 10, "label": "Face"},
    )
    assert region.status_code == 201, region.text
    outside = auth_client.post(
        f"{base}/assets/{asset_id}/versions/{version_id}/segments",
        json={"x": 630, "y": 0, "width": 20, "height": 10},
    )
    assert outside.status_code == 400 and "edge" in outside.json()["detail"]
    retired = auth_client.post(
        f"{base}/assets/{asset_id}/versions/{version_id}/segments/{region.json()['id']}/retire"
    )
    assert retired.status_code == 200


def test_a_failed_picture_says_why_in_plain_words_and_can_be_tried_again(
    auth_client, fake_container
) -> None:
    fake_container.image_processor.failures = ["damaged"]
    base, _project, body = _setup(auth_client, fake_container)
    asset_id, version_id = body["link"]["asset_id"], body["version"]["id"]
    _run(fake_container)

    failed = auth_client.get(f"{base}/assets/{asset_id}/versions/{version_id}/processing").json()
    assert failed["status"] == "failed" and failed["failure_code"] == "damaged"
    assert failed["message"].startswith("This picture could not be read")
    # Offered, but said not to help unchanged.
    assert failed["retryable"] is True and failed["helps"] is False
    library = auth_client.get(f"{base}/pictures").json()["items"][0]
    assert library["processing"] == "failed" and library["processing_message"]
    assert library["has_thumbnail"] is False

    retry = auth_client.post(f"{base}/assets/{asset_id}/versions/{version_id}/processing/retry")
    assert retry.status_code == 202 and retry.json()["status"] == "queued"
    _run(fake_container)
    ready = auth_client.get(f"{base}/assets/{asset_id}/versions/{version_id}/processing").json()
    assert ready["status"] == "ready" and ready["attempts"] == 2


def test_an_upload_over_the_room_left_is_refused(auth_client, fake_container) -> None:
    base, _project, body = _setup(auth_client, fake_container)
    fake_container.media_service._quota_bytes = body["version"]["byte_length"] + 10
    entity = body["link"]["target_record_id"]
    refused = auth_client.post(
        f"{base}/entities/{entity}/pictures", params={"expected_revision": 2}, content=_png("red")
    )
    assert refused.status_code == 413
    problem = refused.json()
    assert problem["code"] == "storage_full" and problem["title"] == "No room left"
    assert "no room left for this picture" in problem["detail"]


def test_health_reports_picture_preparation_and_its_capability(auth_client, fake_container) -> None:
    _setup(auth_client, fake_container)
    ready = auth_client.get("/health/ready").json()
    pictures = ready["checks"]["media_processing"]
    assert pictures["optional"] is True and pictures["ok"] is True
    assert pictures["waiting"] == 1 and pictures["failed"] == 0
    _run(fake_container)
    assert auth_client.get("/health/ready").json()["checks"]["media_processing"]["waiting"] == 0

    capability = auth_client.get("/health/capabilities").json()["capabilities"]["pictures"]
    assert capability["available"] is True and capability["isolated"] is True
    assert capability["formats"] == {"png": True, "jpeg": True, "webp": True}
    assert capability["pillow_version"]


def test_an_archive_leaves_the_copies_out_and_a_restore_queues_them(
    auth_client, fake_container
) -> None:
    base, project_id, body = _setup(auth_client, fake_container)
    _run(fake_container)
    workspace_id = fake_container.settings.AUTH_LOCAL_WORKSPACE_ID
    copies = asyncio.run(fake_container.media_service.variant_object_keys(workspace_id, project_id))
    assert len(copies) == 2

    archive = asyncio.run(fake_container.archive_service.export_project(workspace_id, project_id))
    exported = {item["key"] for item in archive["objects"]}
    assert exported == {body["version"]["object_key"]}
    assert archive["derived"]["picture_copies"]["included"] is False
    assert any("smaller copies" in line for line in archive["left_out"])

    result = asyncio.run(
        fake_container.archive_service.import_project(
            "ws_restored", "pr_restored", archive, actor_user_id="usr_restorer"
        )
    )
    assert result["pictures_queued"] == 1
    queued = [
        job
        for job in fake_container.jobs.jobs.values()
        if job.kind == MEDIA_PROCESS_KIND and job.project_id == "pr_restored"
    ]
    assert len(queued) == 1 and queued[0].actor_user_id == "usr_restorer"


def test_pictures_are_served_to_members_of_the_story_only(hosted_client, hosted_container) -> None:
    csrf = _hosted_login(hosted_client, hosted_container)
    workspace_id = _create_workspace(hosted_client, csrf, "pics-ws")
    project_id = _create_project(hosted_client, csrf, workspace_id, "pics-prj")
    _hosted_schema(hosted_client, csrf, workspace_id, project_id)
    entity = _create_canon_entity(hosted_client, csrf, workspace_id, project_id, "Mara")
    headers = {"X-CSRF-Token": csrf, "X-Workspace-Id": workspace_id}
    upload = hosted_client.post(
        f"/api/v1/projects/{project_id}/entities/{entity['id']}/pictures",
        params={"expected_revision": 1},
        content=_png(),
        headers=headers,
    )
    assert upload.status_code == 201, upload.text
    asset_id = upload.json()["link"]["asset_id"]
    version_id = upload.json()["version"]["id"]
    _run(hosted_container)
    path = f"/api/v1/projects/{project_id}/assets/{asset_id}/versions/{version_id}"
    assert (
        hosted_client.get(f"{path}/content", params={"size": "thumbnail"}, headers=headers).headers[
            "X-Picture-Size"
        ]
        == "thumbnail"
    )

    # Named from another workspace of the same person: not found, not served.
    other = _create_workspace(hosted_client, csrf, "pics-other")
    elsewhere = {"X-CSRF-Token": csrf, "X-Workspace-Id": other}
    for suffix in ("/content?size=thumbnail", "/processing", "/segments"):
        response = hosted_client.get(f"{path}{suffix}", headers=elsewhere)
        assert response.status_code == 404, suffix
    retry = hosted_client.post(f"{path}/processing/retry", headers=elsewhere)
    assert retry.status_code == 404

    # Someone who is not a member of the workspace at all is refused before anything.
    hosted_container.identity_provider._claims = IdentityClaims(
        issuer=hosted_container.identity_provider.issuer,
        subject="subject-outsider",
        email="outsider@example.com",
        display_name="Outsider",
    )
    hosted_client.cookies.clear()
    outsider_csrf = _hosted_login(hosted_client, hosted_container)
    outsider = {"X-CSRF-Token": outsider_csrf, "X-Workspace-Id": workspace_id}
    for suffix in ("/content?size=thumbnail", "/content", "/processing"):
        response = hosted_client.get(f"{path}{suffix}", headers=outsider)
        assert response.status_code in (403, 404), suffix
        assert not response.headers.get("content-type", "").startswith("image/")
