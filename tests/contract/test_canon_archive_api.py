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


@pytest.mark.asyncio
async def test_the_authors_texts_name_answers_and_rules_travel_with_the_story(
    auth_client, fake_container
) -> None:
    from backend.services.canon_archive import ArchiveError, _checksum
    from tests.contract.test_god_hand import _world
    from tests.contract.test_rules import EXAMPLES, NEEDS_MEMBER

    w = _world(auth_client, fake_container)
    base = w["base"]
    text = auth_client.post(
        f"{base}/texts", json={"title": "Chapter one", "content": "The old pilot tied up."}
    ).json()
    auth_client.patch(
        f"{base}/texts/{text['id']}", json={"content": "The old pilot tied up at dawn."}
    )
    for body in (
        {"phrase": "the old pilot", "verdict": "is", "entity_id": w["mara"]},
        {
            "phrase": "the pilot",
            "verdict": "is_not",
            "entity_id": w["tobin"],
            "source_id": text["id"],
        },
    ):
        assert auth_client.post(f"{base}/names", json=body).status_code == 201
    rule = auth_client.post(
        f"{base}/rules",
        json={
            "title": "Guilds have members",
            "sentence": "Every guild needs a member.",
            "body": NEEDS_MEMBER,
            "examples": EXAMPLES,
        },
    ).json()
    stricter = {**NEEDS_MEMBER, "checks": [{**NEEDS_MEMBER["checks"][0], "count": 2}]}
    changed = auth_client.patch(
        f"{base}/rules/{rule['id']}",
        json={"body": stricter, "examples": [EXAMPLES[0]], "status": "paused"},
    )
    assert changed.status_code == 200, changed.text

    archive = auth_client.post(f"{base}/archive/export").json()
    assert [t["title"] for t in archive["writing"]["texts"]] == ["Chapter one"]
    assert [v["number"] for v in archive["writing"]["texts"][0]["versions"]] == [1, 2]
    assert len(archive["writing"]["names"]) == 2
    assert [len(r["versions"]) for r in archive["story_rules"]] == [2]

    service = fake_container.archive_service
    workspace_id = archive["project"]["workspace_id"]
    result = await service.import_project(workspace_id, "restored", archive)
    assert result["writing"] == {"texts": 1, "names": 2, "rules": 1}

    # The same ids, beside the original, with every version of the words.
    sources, rules = fake_container.sources, fake_container.rules
    restored = await sources.list_versions(
        workspace_id=workspace_id, project_id="restored", source_id=text["id"]
    )
    assert [v.content for v in restored] == [
        "The old pilot tied up.",
        "The old pilot tied up at dawn.",
    ]
    names = await sources.list_bindings(workspace_id=workspace_id, project_id="restored")
    assert sorted((n.phrase, n.verdict.value, n.source_id) for n in names) == [
        ("the old pilot", "is", None),
        ("the pilot", "is_not", text["id"]),
    ]
    (back,) = await rules.list_rules(workspace_id=workspace_id, project_id="restored")
    assert (back.id, back.version, back.status.value) == (rule["id"], 2, "paused")
    assert back.body.checks[0].count == 2 and back.sentence == "Every guild needs a member."
    # The original is untouched, and running the last step again adds nothing.
    assert (
        len(
            await sources.list_sources(
                workspace_id=workspace_id, project_id=w["base"].rsplit("/", 1)[1]
            )
        )
        == 1
    )
    again = await service._restore_writing(workspace_id, "restored", archive, actor_user_id=None)
    assert again == {"texts": 0, "names": 0, "rules": 0}

    # Words that do not match their own checksum are refused before anything is written.
    forged = {**archive, "writing": {**archive["writing"]}}
    forged["writing"]["texts"] = [
        {
            **archive["writing"]["texts"][0],
            "versions": [{**archive["writing"]["texts"][0]["versions"][0], "content": "Forged."}],
        }
    ]
    forged["checksum"] = _checksum(forged)
    with pytest.raises(ArchiveError, match="does not match its checksum"):
        await service.import_project(workspace_id, "forged", forged)
    assert await sources.list_sources(workspace_id=workspace_id, project_id="forged") == []


def test_an_archive_without_writing_or_rules_has_no_such_sections(
    auth_client, fake_container
) -> None:
    project_id = _project_id(fake_container)
    _publish_schema(auth_client, project_id)
    archive = auth_client.post(f"/api/v1/projects/{project_id}/archive/export").json()
    assert "writing" not in archive and "story_rules" not in archive
