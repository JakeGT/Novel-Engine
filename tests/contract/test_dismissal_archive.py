"""A project archive carries the review screen's dismissals and those beyond one scene
(CON-016): each comes back in the restored story, withdrawn ones too, a review dismissal
on the proposal its saved change was restored as and a wider one in its own line."""

from __future__ import annotations

import asyncio

import pytest

from backend.services.canon_archive import ArchiveError, _checksum, _dedup_branch_id
from tests.contract.test_context import _head
from tests.contract.test_scoped_dismissals import PROSE, _dismiss, _looks, _two_scenes
from tests.contract.test_studio import _save

pytestmark = pytest.mark.contract


def _ids(container) -> tuple[str, str]:
    return container.settings.AUTH_LOCAL_WORKSPACE_ID, container.settings.AUTH_LOCAL_PROJECT_ID


def _duplicate(client, w) -> tuple[str, str]:
    """A proposal from the assistant adding a second Lantern Harbor: a possible duplicate."""
    base = f"{w['base']}/mutations"
    made = client.post(
        base,
        json={
            "operations": [
                {"op": "create_entity", "id": "tmp:a", "type": "Faction", "name": "lantern harbor"}
            ],
            "expected_revision": _head(client, w),
            "origin": "model",
        },
    )
    assert made.status_code == 201, made.text
    return base, made.json()["id"]


def test_review_and_wider_dismissals_travel_with_the_archive(auth_client, fake_container):
    w, scene, _second = _two_scenes(auth_client, fake_container)
    base, mutation_id = _duplicate(auth_client, w)
    (finding,) = auth_client.get(f"{base}/{mutation_id}/findings").json()["items"]
    first = auth_client.post(
        f"{base}/{mutation_id}/findings/dismissals",
        json={"key": finding["key"], "reason": "not_a_problem"},
    ).json()["items"][0]["dismissal"]
    # Withdrawn, then dismissed again: both are carried, as the record is.
    auth_client.delete(f"{base}/{mutation_id}/findings/dismissals/{first['id']}")
    auth_client.post(
        f"{base}/{mutation_id}/findings/dismissals",
        json={"key": finding["key"], "reason": "intended"},
    )
    assert auth_client.post(f"{base}/{mutation_id}/approve", json={}).status_code == 200
    assert auth_client.post(f"{base}/{mutation_id}/commit").status_code == 200

    looks = _looks(_save(auth_client, w, scene, PROSE, None).json())
    done = _dismiss(auth_client, w, scene, looks["key"], applies="character", entity_id=w["mara"])
    assert done.status_code == 200, done.text
    # And one in a what-if, which stays in the what-if.
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Mara stays"}).json()["id"]
    in_what_if = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/dismissals",
        json={
            "key": looks["key"],
            "reason": "intended",
            "applies": "range",
            "from_plan_id": scene["id"],
            "to_plan_id": scene["id"],
        },
        headers={"X-Story-Branch": branch},
    )
    assert in_what_if.status_code == 200, in_what_if.text

    workspace, project = _ids(fake_container)
    service = fake_container.archive_service
    archive = asyncio.run(service.export_project(workspace, project))
    review = archive["review_dismissals"]
    assert [item["reason"] for item in review] == ["not_a_problem", "intended"]
    assert review[0]["withdrawn_at"] is not None and review[1]["withdrawn_at"] is None
    assert all(item["commit_id"] for item in review)
    # Digests only, never the words of the change.
    assert "lantern" not in str(review).lower()
    assert {item["line"] for item in archive["scoped_dismissals"]} == {"main", branch}

    target = "pr_dismissals_restored"
    result = asyncio.run(service.import_project(workspace, target, archive))
    assert result["review_dismissals"] == 2
    assert result["scoped_dismissals"] == len(archive["scoped_dismissals"])

    store = fake_container.review_dismissals._store  # noqa: SLF001 - the rows as restored
    restored = [item for item in store.dismissals if item.project_id == target]
    assert {item.id for item in restored} == {item["id"] for item in review}
    (restored_mutation,) = {item.mutation_id for item in restored}
    assert restored_mutation != mutation_id

    scoped = fake_container.scoped_dismissals._store  # noqa: SLF001
    mine = [item for item in scoped.items if item.project_id == target]
    what_if = _dedup_branch_id(archive["checksum"], branch, workspace, target)
    assert what_if in {item.branch_id for item in mine}
    assert all(item.entity_id == w["mara"] for item in mine if item.scope.value == "character")

    # Restoring again leaves what is there as it is.
    again = asyncio.run(
        service.dismissal_archive.restore_scoped(
            workspace,
            target,
            archive["scoped_dismissals"],
            {"main": mine[0].branch_id, branch: what_if},
        )
    )
    assert again == 0


def test_a_malformed_dismissal_in_an_archive_is_refused_before_anything_is_written(
    auth_client, fake_container
):
    w, _scene, _second = _two_scenes(auth_client, fake_container)
    base, mutation_id = _duplicate(auth_client, w)
    (finding,) = auth_client.get(f"{base}/{mutation_id}/findings").json()["items"]
    auth_client.post(
        f"{base}/{mutation_id}/findings/dismissals",
        json={"key": finding["key"], "reason": "not_a_problem"},
    )
    auth_client.post(f"{base}/{mutation_id}/approve", json={})
    auth_client.post(f"{base}/{mutation_id}/commit")
    workspace, project = _ids(fake_container)
    service = fake_container.archive_service
    archive = asyncio.run(service.export_project(workspace, project))
    archive["review_dismissals"][0]["fingerprint"] = "not a digest"
    archive["checksum"] = _checksum(archive)
    with pytest.raises(ArchiveError, match="review screen"):
        asyncio.run(service.import_project(workspace, "pr_bad_dismissals", archive))
    archive["review_dismissals"] = []
    archive["scoped_dismissals"] = [{"id": "x", "line": "elsewhere"}]
    archive["checksum"] = _checksum(archive)
    with pytest.raises(ArchiveError, match="beyond one scene"):
        asyncio.run(service.import_project(workspace, "pr_bad_dismissals", archive))


def test_restored_dismissals_are_the_restoring_authors_whoever_the_archive_names(
    auth_client, fake_container
):
    w, scene, _second = _two_scenes(auth_client, fake_container)
    base, mutation_id = _duplicate(auth_client, w)
    (finding,) = auth_client.get(f"{base}/{mutation_id}/findings").json()["items"]
    auth_client.post(
        f"{base}/{mutation_id}/findings/dismissals",
        json={"key": finding["key"], "reason": "not_a_problem"},
    )
    assert auth_client.post(f"{base}/{mutation_id}/approve", json={}).status_code == 200
    assert auth_client.post(f"{base}/{mutation_id}/commit").status_code == 200
    looks = _looks(_save(auth_client, w, scene, PROSE, None).json())
    done = _dismiss(auth_client, w, scene, looks["key"], applies="character", entity_id=w["mara"])
    assert done.status_code == 200, done.text

    workspace, project = _ids(fake_container)
    service = fake_container.archive_service
    archive = asyncio.run(service.export_project(workspace, project))
    # An archive that says someone else made them.
    for item in [*archive["review_dismissals"], *archive["scoped_dismissals"]]:
        item["created_by"] = "usr_someone_else"
    archive["checksum"] = _checksum(archive)
    target = "pr_dismissals_mine"
    asyncio.run(service.import_project(workspace, target, archive, actor_user_id="usr_restorer"))

    review = fake_container.review_dismissals._store  # noqa: SLF001
    scoped = fake_container.scoped_dismissals._store  # noqa: SLF001
    assert {item.created_by for item in review.dismissals if item.project_id == target} == {
        "usr_restorer"
    }
    assert {item.created_by for item in scoped.items if item.project_id == target} == {
        "usr_restorer"
    }
