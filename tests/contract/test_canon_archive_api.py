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


@pytest.mark.asyncio
async def test_story_time_and_secrets_survive_a_restore_and_the_main_story_stays_itself(
    auth_client, fake_container
) -> None:
    from tests.contract.test_god_hand import _world

    w = _world(auth_client, fake_container)
    base, head = w["base"], int(w["head"])
    auth_client.put(f"{base}/timeline/unit", json={"unit": "season"})
    for fact, body in (
        ("in_guild", {"visibility": "secret"}),
        ("at_harbor", {"ends": {"kind": "at", "at": 9}}),
    ):
        amended = auth_client.post(
            f"{base}/facts/{w[fact]}/amend", json={"expected_revision": head, **body}
        )
        assert amended.status_code == 200, amended.text
        head += 1
    head -= 1
    war = auth_client.post(
        f"{base}/events",
        json={
            "kind": "battle",
            "name": "The War",
            "expected_revision": head + 1,
            "when": {"kind": "at", "at": 100, "label": "the long winter"},
        },
    ).json()
    what_if = auth_client.post(f"{base}/what-ifs", json={"name": "No war"}).json()
    auth_client.post(
        f"{base}/entities",
        json={"type": "Character", "name": "Only in the what-if", "expected_revision": head + 2},
        headers={"X-Story-Branch": what_if["id"]},
    )

    # Asked for from inside the what-if, the main story is still the main story; the
    # what-if travels beside it, in its own section.
    archive = auth_client.post(
        f"{base}/archive/export", headers={"X-Story-Branch": what_if["id"]}
    ).json()
    assert archive["story_time"] == {"unit": "season"}
    assert "Only in the what-if" not in str({**archive, "what_ifs": None})
    assert "Only in the what-if" in str(archive["what_ifs"])

    service = fake_container.archive_service
    workspace_id = archive["project"]["workspace_id"]
    await service.import_project(workspace_id, "restored", archive)
    canon = fake_container.canon_repository
    secret = await canon.get_fact(
        workspace_id=workspace_id, project_id="restored", fact_id=w["in_guild"]
    )
    bounded = await canon.get_fact(
        workspace_id=workspace_id, project_id="restored", fact_id=w["at_harbor"]
    )
    assert secret.visibility.value == "secret" and bounded.ends.at == 9
    event = await canon.get_event(
        workspace_id=workspace_id, project_id="restored", event_id=war["id"]
    )
    assert event.when.label == "the long winter"
    assert (
        await fake_container.story_clocks.get_unit(workspace_id=workspace_id, project_id="restored")
        == "season"
    )


# ------------------------------------------------------------ the complete archive


async def _commit_mutations(container, workspace_id: str, project_id: str, branch_id: str):
    """The proposal behind each saved change in one line of the story, oldest first."""
    canon = container.canon_repository
    found = []
    for revision in await canon.list_commits(
        workspace_id=workspace_id, project_id=project_id, branch_id=branch_id, limit=1000
    ):
        envelope = await canon.get_envelope(revision.commit_id)
        found.append(envelope.mutation_id)
    return found


async def _names(container, workspace_id: str, project_id: str, branch_id: str | None) -> set:
    from backend.domain.branching import working_in

    with working_in(branch_id):
        entities = await container.canon_repository.list_entities(
            workspace_id=workspace_id, project_id=project_id, include_retired=True, limit=1000
        )
    return {entity.name for entity in entities}


@pytest.mark.asyncio
async def test_what_ifs_their_scene_texts_readings_and_conversations_travel_and_restore_once(
    auth_client, fake_container
) -> None:
    from datetime import UTC, datetime

    from backend.domain.assist import AssistKind, AssistSession, AssistStatus
    from backend.domain.dismissals import DismissReason, FindingDismissal, FindingKind
    from backend.domain.studio import SceneReading
    from tests.contract.test_studio import PROSE, _save, _scene_world

    w, scene = _scene_world(auth_client, fake_container)
    base = w["base"]
    project_id = base.rsplit("/", 1)[1]
    workspace_id = fake_container.settings.AUTH_LOCAL_WORKSPACE_ID
    journal, studio = fake_container.journal, fake_container.studio_store
    main_text = _save(auth_client, w, scene, PROSE, None).json()

    what_if = auth_client.post(
        f"{base}/what-ifs", json={"name": "Mara comes", "description": "She does not refuse."}
    ).json()
    branch = what_if["id"]
    inside = {"X-Story-Branch": branch}
    added = auth_client.post(
        f"{base}/entities",
        json={
            "type": "Character",
            "name": "Only in the what-if",
            "expected_revision": what_if["version"],
        },
        headers=inside,
    )
    assert added.status_code == 201, added.text
    # Changing the scene's words in the what-if copies its text there.
    copied = _save(
        auth_client, w, scene, PROSE + " Mara came.", main_text["version_id"], branch
    ).json()
    assert copied["text_id"] != main_text["text_id"]
    # The main story moves on after the what-if started.
    later = auth_client.post(
        f"{base}/entities",
        json={
            "type": "Character",
            "name": "Added after the what-if",
            "expected_revision": what_if["version"],
        },
    )
    assert later.status_code == 201, later.text
    auth_client.post(f"{base}/what-ifs/{branch}/set-aside", json={"aside": True})

    # What was read from the what-if's scene, and the proposal it made.
    (what_if_mutation,) = await _commit_mutations(fake_container, workspace_id, project_id, branch)
    read_at = datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
    await studio.save_reading(
        SceneReading(
            workspace_id=workspace_id,
            project_id=project_id,
            branch_id=branch,
            id="rdg_in_what_if",
            plan_id=scene["id"],
            source_id=copied["text_id"],
            version_id=copied["version_id"],
            job_id="job_gone",
            passages=2,
            proposals=(what_if_mutation, "mut_never_saved"),
            discarded=1,
            created_by=None,
            created_at=read_at,
            covered=((0, 40),),
        )
    )
    # A finding the author dismissed in the what-if, and one they took back.
    for dismissal_id, withdrawn, mark in (("dsm_kept", None, "1"), ("dsm_undone", read_at, "2")):
        await studio.restore_dismissal(
            FindingDismissal(
                workspace_id=workspace_id,
                project_id=project_id,
                branch_id=branch,
                id=dismissal_id,
                plan_id=scene["id"],
                fingerprint="a" * 63 + mark,
                kind=FindingKind.CHECK,
                code="unknown_name",
                reason=DismissReason.INTENDED,
                note="Tobin calls her that on purpose.",
                expires_at=None,
                created_by=None,
                created_at=read_at,
                withdrawn_at=withdrawn,
            )
        )
    # A conversation with the assistant, and the change it led to in the main story.
    root = await journal.ensure_root_branch(workspace_id=workspace_id, project_id=project_id)
    main_mutations = await _commit_mutations(fake_container, workspace_id, project_id, root.id)
    now = datetime.now(UTC)
    await fake_container.assist_sessions.save(
        AssistSession(
            id="asx_pilot",
            workspace_id=workspace_id,
            project_id=project_id,
            actor_user_id="usr_author",
            kind=AssistKind.CREATE,
            status=AssistStatus.PROPOSED,
            description="A harbour pilot who keeps her own counsel.",
            mutation_id=main_mutations[-1],
            created_at=now,
            updated_at=now,
        )
    )

    archive = auth_client.post(f"{base}/archive/export").json()
    assert archive["format_version"] == "4"
    assert any("Drafts waiting" in line for line in archive["left_out"])
    (exported,) = archive["what_ifs"]
    assert exported["name"] == "Mara comes" and exported["status"] == "set_aside"
    assert exported["fork_revision"] == what_if["started_at_version"]
    assert [c["revision"] for c in exported["commits"]] == [what_if["started_at_version"] + 1]
    assert exported["scenes"] == [{"plan_id": scene["id"], "text_id": copied["text_id"]}]
    (reading,) = exported["readings"]
    # A proposal never saved is not in the archive, so it is not pointed at.
    assert len(reading["proposal_commits"]) == 1
    assert {t["id"] for t in archive["writing"]["texts"]} >= {
        main_text["text_id"],
        copied["text_id"],
    }
    (conversation,) = archive["conversations"]
    assert conversation["commit_id"] == archive["commits"][-1]["commit_id"]
    assert "mutation_id" not in conversation and "workspace_id" not in conversation

    service = fake_container.archive_service
    result = await service.import_project(workspace_id, "restored", archive)
    assert result["what_ifs"] == 1 and result["what_if_commits"] == 1
    assert result["conversations"] == 1 and result["readings"] == 1 and result["verified"]
    assert result["dismissals"] == 2

    # The what-if is back, started where it first started, with its own change only.
    (back,) = [
        b
        for b in await journal.list_branches(workspace_id=workspace_id, project_id="restored")
        if not b.is_root
    ]
    assert (back.name, back.description, back.status) == (
        "Mara comes",
        "She does not refuse.",
        "set_aside",
    )
    assert back.fork_revision == what_if["started_at_version"]
    in_what_if = await _names(fake_container, workspace_id, "restored", back.id)
    in_main = await _names(fake_container, workspace_id, "restored", None)
    assert "Only in the what-if" in in_what_if and "Added after the what-if" not in in_what_if
    assert "Added after the what-if" in in_main and "Only in the what-if" not in in_main

    # Each line keeps its own words for the scene.
    restored_root = await journal.ensure_root_branch(
        workspace_id=workspace_id, project_id="restored"
    )
    for branch_id, text_id in (
        (restored_root.id, main_text["text_id"]),
        (back.id, copied["text_id"]),
    ):
        assert (
            await studio.manuscript(
                workspace_id=workspace_id,
                project_id="restored",
                branch_id=branch_id,
                plan_id=scene["id"],
            )
            == text_id
        )
    versions = await fake_container.sources.list_versions(
        workspace_id=workspace_id, project_id="restored", source_id=copied["text_id"]
    )
    assert versions[-1].content.endswith("Mara came.")

    # The reading keeps its time and points at the restored proposal.
    (restored_reading,) = await studio.readings_in(
        workspace_id=workspace_id, project_id="restored", branch_id=back.id
    )
    (restored_mutation,) = await _commit_mutations(
        fake_container, workspace_id, "restored", back.id
    )
    assert restored_reading.created_at == read_at and restored_reading.job_id is None
    assert restored_reading.proposals == (restored_mutation,)
    assert restored_reading.covered == ((0, 40),)

    # The conversation is back beside its original, pointing at the restored change.
    restored_main = await _commit_mutations(
        fake_container, workspace_id, "restored", restored_root.id
    )
    (talk,) = await fake_container.assist_sessions.list_for_project(
        workspace_id=workspace_id, project_id="restored", limit=10
    )
    assert talk.description == "A harbour pilot who keeps her own counsel."
    assert talk.mutation_id == restored_main[-1]
    assert talk.id.startswith("asx_pilot")

    # Restoring again changes nothing, and the import is recorded as complete.
    again = await service.import_project(workspace_id, "restored", archive)
    assert again["conversations"] == 0 and again["readings"] == 0 and again["dismissals"] == 0
    dismissed = await studio.dismissals_in(
        workspace_id=workspace_id, project_id="restored", branch_id=back.id
    )
    assert [(d.id, d.withdrawn_at) for d in dismissed] == [
        ("dsm_kept", None),
        ("dsm_undone", read_at),
    ]
    assert dismissed[0].note == "Tobin calls her that on purpose."
    assert len(await journal.list_branches(workspace_id=workspace_id, project_id="restored")) == 2
    assert (
        len(
            await fake_container.assist_sessions.list_for_project(
                workspace_id=workspace_id, project_id="restored", limit=10
            )
        )
        == 1
    )
    (record,) = await fake_container.canon_repository.list_imports(
        workspace_id=workspace_id, project_id="restored"
    )
    assert record.status.value == "completed"
    assert record.commits_total == len(archive["commits"]) + 1
    assert record.created_records["assist_sessions"] == [talk.id]


@pytest.mark.asyncio
async def test_an_older_version_3_archive_still_restores(auth_client, fake_container) -> None:
    from backend.services.canon_archive import ArchiveError, _checksum
    from tests.contract.test_god_hand import _world

    w = _world(auth_client, fake_container)
    archive = auth_client.post(f"{w['base']}/archive/export").json()
    # What this project's archive looked like before version 4: the same sections,
    # without the list of what is left out and without what-ifs or conversations.
    older = {k: v for k, v in archive.items() if k not in {"left_out", "checksum"}}
    older["format_version"] = "3"
    older["checksum"] = _checksum(older)

    service = fake_container.archive_service
    workspace_id = archive["project"]["workspace_id"]
    result = await service.import_project(workspace_id, "from_v3", older)
    assert result["verified"] and result["commits"] == len(archive["commits"])
    assert result["what_ifs"] == 0 and result["conversations"] == 0
    canon = fake_container.canon_repository
    assert await canon.get_entity(
        workspace_id=workspace_id, project_id="from_v3", entity_id=w["mara"]
    )

    # A version this code does not know is refused before anything is written.
    newer = {**older, "format_version": "5"}
    newer["checksum"] = _checksum(newer)
    with pytest.raises(ArchiveError, match="unsupported archive format version"):
        await service.import_project(workspace_id, "from_v5", newer)
    assert await canon.list_imports(workspace_id=workspace_id, project_id="from_v5") == []


@pytest.mark.asyncio
async def test_a_what_if_that_starts_past_the_main_story_is_refused(
    auth_client, fake_container
) -> None:
    from backend.services.canon_archive import ArchiveError, _checksum
    from tests.contract.test_god_hand import _world

    w = _world(auth_client, fake_container)
    auth_client.post(f"{w['base']}/what-ifs", json={"name": "Mara comes"})
    archive = auth_client.post(f"{w['base']}/archive/export").json()
    forged = {**archive, "what_ifs": [{**archive["what_ifs"][0], "fork_revision": 999}]}
    forged["checksum"] = _checksum(forged)
    service = fake_container.archive_service
    workspace_id = archive["project"]["workspace_id"]
    with pytest.raises(ArchiveError, match="starts from a version"):
        await service.import_project(workspace_id, "forged", forged)

    # A target holding a what-if of its own is not mixed with the archive's.
    canon, journal = fake_container.canon_repository, fake_container.journal
    root = await journal.ensure_root_branch(workspace_id=workspace_id, project_id="clean")
    await canon.fork_branch(
        workspace_id=workspace_id,
        project_id="clean",
        parent_branch_id=root.id,
        branch_id="brn_someone_else",
        name="Mara comes",
        description=None,
        actor_user_id=None,
    )
    with pytest.raises(ArchiveError, match="unrelated what-ifs"):
        await service.import_project(workspace_id, "clean", archive)
    assert await canon.list_imports(workspace_id=workspace_id, project_id="clean") == []


async def _guard_with_blocking_rule(container, workspace_id: str, project_id: str) -> None:
    """The author's own rule in the target, set to block: every guild has a member."""
    from backend.domain.rules import RuleBody
    from backend.domain.validation import RuleEnforcement
    from tests.contract.test_rules import NEEDS_MEMBER

    await container.rules.add_rule(
        workspace_id=workspace_id,
        project_id=project_id,
        rule_id="story_invariant_target",
        title="Guilds have members",
        sentence="",
        body=RuleBody.model_validate(NEEDS_MEMBER),
        examples=[],
        actor_user_id=None,
    )
    await container.rules.change_rule(
        workspace_id=workspace_id,
        project_id=project_id,
        rule_id="story_invariant_target",
        enforcement=RuleEnforcement.BLOCKING,
    )


@pytest.mark.asyncio
async def test_a_rule_set_to_block_never_refuses_restoring_history(
    auth_client, fake_container, monkeypatch
) -> None:
    from backend.domain.validation import AuthoritativeValidator
    from tests import doubles
    from tests.contract.test_god_hand import _world

    w = _world(auth_client, fake_container)
    # The guild is added before anyone joins it: a rule that every guild has a member
    # would refuse that step if it were checked again on the way back in.
    archive = auth_client.post(f"{w['base']}/archive/export").json()
    workspace_id = archive["project"]["workspace_id"]
    await _guard_with_blocking_rule(fake_container, workspace_id, "guarded")
    # Even a store whose own check would apply the author's rules restores it.
    monkeypatch.setattr(
        doubles,
        "_MEMORY_DEFAULT_VALIDATOR",
        AuthoritativeValidator(evaluators=[fake_container.rule_service]),
    )

    result = await fake_container.archive_service.import_project(workspace_id, "guarded", archive)
    assert result["verified"] and result["commits"] == len(archive["commits"])


@pytest.mark.asyncio
async def test_removing_a_failed_import_removes_its_rules_and_the_conversations_it_restored(
    auth_client, fake_container, monkeypatch
) -> None:
    from datetime import UTC, datetime

    from backend.domain.assist import AssistKind, AssistSession, AssistStatus
    from tests.contract.test_god_hand import _world
    from tests.contract.test_rules import _add

    w = _world(auth_client, fake_container)
    rule = _add(auth_client, w).json()
    project_id = w["base"].rsplit("/", 1)[1]
    workspace_id = fake_container.settings.AUTH_LOCAL_WORKSPACE_ID
    assists = fake_container.assist_sessions
    now = datetime.now(UTC)
    for session_id in ("asx_first", "asx_second"):
        await assists.save(
            AssistSession(
                id=session_id,
                workspace_id=workspace_id,
                project_id=project_id,
                actor_user_id="usr_author",
                kind=AssistKind.CREATE,
                status=AssistStatus.PROPOSED,
                description="A harbour pilot.",
                created_at=now,
                updated_at=now,
            )
        )
    archive = auth_client.post(f"{w['base']}/archive/export").json()
    assert len(archive["conversations"]) == 2

    # The second conversation cannot be written: the import fails part way.
    real_save = assists.save

    async def failing_save(session):
        if session.project_id == "copy" and session.id.startswith("asx_second"):
            raise RuntimeError("the store went away")
        await real_save(session)

    monkeypatch.setattr(assists, "save", failing_save)
    service, canon, rules = (
        fake_container.archive_service,
        fake_container.canon_repository,
        fake_container.rules,
    )
    with pytest.raises(RuntimeError):
        await service.import_project(workspace_id, "copy", archive)
    (record,) = await canon.list_imports(workspace_id=workspace_id, project_id="copy")
    assert record.created_records["story_rules"] == [rule["id"]]
    # Its own id beside the original, which this database already holds.
    (first,) = record.created_records["assist_sessions"]
    assert first.startswith("asx_first")
    assert await rules.get_rule(workspace_id=workspace_id, project_id="copy", rule_id=rule["id"])

    # Removing it removes what it restored, not just the story.
    await service.remove_import(workspace_id, "copy", record.id)
    assert await rules.list_rules(workspace_id=workspace_id, project_id="copy") == []
    assert (
        await assists.list_for_project(workspace_id=workspace_id, project_id="copy", limit=10) == []
    )

    # And it can be imported again, whole.
    monkeypatch.setattr(assists, "save", real_save)
    result = await service.import_project(workspace_id, "copy", archive)
    assert result["writing"]["rules"] == 1 and result["conversations"] == 2


@pytest.mark.asyncio
async def test_a_damaged_archive_is_refused_in_plain_words(auth_client, fake_container) -> None:
    from backend.services.canon_archive import ArchiveError, _checksum
    from tests.contract.test_god_hand import _world

    w = _world(auth_client, fake_container)
    made = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Mara comes"}).json()
    added = auth_client.post(
        f"{w['base']}/entities",
        json={"type": "Character", "name": "Ines", "expected_revision": made["version"]},
        headers={"X-Story-Branch": made["id"]},
    )
    assert added.status_code == 201, added.text
    archive = auth_client.post(f"{w['base']}/archive/export").json()
    (what_if,) = archive["what_ifs"]
    service = fake_container.archive_service
    workspace_id = archive["project"]["workspace_id"]

    missing = {k: v for k, v in what_if["commits"][0].items() if k != "expected_head"}
    bad_tag = {"name": "draft", "revision": "soon", "commit_id": missing.get("commit_id")}
    for damaged_what_if in (
        {**what_if, "commits": [missing]},
        {**what_if, "tags": [bad_tag]},
    ):
        forged = {**archive, "what_ifs": [damaged_what_if]}
        forged["checksum"] = _checksum(forged)
        with pytest.raises(ArchiveError, match="damaged or incomplete"):
            await service.import_project(workspace_id, "damaged", forged)
    assert (
        await fake_container.canon_repository.list_imports(
            workspace_id=workspace_id, project_id="damaged"
        )
        == []
    )
