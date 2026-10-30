"""Evidence provenance, history authorization, and redaction (2H)."""

import pytest

from backend.domain.canon.propositions import Proposition, StringObject
from backend.domain.errors import ForbiddenError, NotFoundError
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import AssertBeliefOp, AssertFactOp, CreateEntityOp, EvidenceRef
from backend.domain.tenancy import Role
from backend.domain.validation import CanonValidationError
from backend.services.canon_history_service import CanonHistoryService
from backend.services.mutation_service import MutationService
from backend.services.tenancy_service import TenancyService
from tests.doubles import (
    InMemoryCanonJournal,
    InMemoryCanonRepository,
    InMemoryMediaStore,
    InMemoryTenancyStore,
)

pytestmark = pytest.mark.application

SECRET_NOTE = "confirmed against the author's private outline"


async def _hosted_stack():
    store = InMemoryTenancyStore()
    tenancy = TenancyService(store)
    workspace = await store.create_workspace(slug="studio", name="Studio")
    project = await store.create_project(workspace_id=workspace.id, slug="saga", name="Saga")
    other = await store.create_project(workspace_id=workspace.id, slug="other", name="Other")
    for user, role in (
        ("u_owner", Role.OWNER),
        ("u_editor", Role.EDITOR),
        ("u_viewer", Role.VIEWER),
    ):
        await store.add_membership(workspace_id=workspace.id, user_id=user, role=role)
    journal = InMemoryCanonJournal()
    media = InMemoryMediaStore()
    repository = InMemoryCanonRepository(journal=journal, media=media)
    mutation = MutationService(journal, repository, tenancy=tenancy, media=media)
    history = CanonHistoryService(
        canon=repository, journal=journal, tenancy=tenancy, mutation=mutation
    )

    def scope(user: str, project_id: str = project.id) -> AccessScope:
        return AccessScope(
            actor_user_id=user,
            auth_mode=AuthMode.HOSTED,
            workspace_id=workspace.id,
            project_id=project_id,
        )

    return mutation, history, repository, media, scope, workspace.id, project.id, other.id


def _fact(subject: str, value: str, evidence: list[EvidenceRef] | None = None) -> AssertFactOp:
    return AssertFactOp(
        id="fct_name",
        proposition=Proposition(
            subject_id=subject,
            predicate="HAS_NAME",
            object=StringObject(kind="string", value=value),
        ),
        evidence=evidence or [],
    )


async def _seed(mutation, scope, ws, pr):
    """A model proposal with evidence, reviewed and approved by the editor."""
    draft = await mutation.create_draft(
        scope("u_editor"),
        workspace_id=ws,
        project_id=pr,
        operations=[
            CreateEntityOp(
                id="ent_mara",
                type="Character",
                name="Mara",
                evidence=[
                    EvidenceRef(source_artefact_id="scene:ch01-s02", location="chars:120-164")
                ],
            ),
            _fact("ent_mara", "Mara Venn", [EvidenceRef(source_artefact_id="scene:ch01-s02")]),
        ],
        expected_head=0,
        origin="model",
    )
    await mutation.approve(scope("u_editor"), draft.id, note=SECRET_NOTE)
    return await mutation.commit(scope("u_editor"), draft.id)


@pytest.mark.asyncio
async def test_provenance_explains_origin_review_and_evidence() -> None:
    mutation, history, _repo, _media, scope, ws, pr, _other = await _hosted_stack()
    result = await _seed(mutation, scope, ws, pr)

    entries = await history.record_provenance(
        scope("u_editor"), ws, pr, record_type="entity", record_id="ent_mara"
    )

    assert len(entries) == 1
    entry = entries[0]
    assert entry.commit_id == result.commit_id and entry.revision == 1
    assert entry.operation_type == "create_entity"
    assert entry.origin == "model" and entry.intent == "edit"
    assert entry.actor_user_id == "u_editor"
    assert [(d.decision, d.actor_user_id, d.note) for d in entry.decisions] == [
        ("approved", "u_editor", SECRET_NOTE)
    ]
    assert [(e.source_artefact_id, e.location) for e in entry.evidence] == [
        ("scene:ch01-s02", "chars:120-164")
    ]
    assert entry.redacted is False

    fact_entries = await history.record_provenance(
        scope("u_editor"), ws, pr, record_type="fact", record_id="fct_name"
    )
    # Evidence is attributed to the operation that cited it, not the whole commit.
    assert [(e.source_artefact_id, e.location) for e in fact_entries[0].evidence] == [
        ("scene:ch01-s02", None)
    ]


@pytest.mark.asyncio
async def test_viewers_see_why_but_not_who_or_review_notes() -> None:
    mutation, history, _repo, _media, scope, ws, pr, _other = await _hosted_stack()
    await _seed(mutation, scope, ws, pr)

    entry = (
        await history.record_provenance(
            scope("u_viewer"), ws, pr, record_type="entity", record_id="ent_mara"
        )
    )[0]
    assert entry.origin == "model"
    assert entry.evidence and entry.decisions[0].decision == "approved"
    assert entry.actor_user_id is None
    assert entry.decisions[0].actor_user_id is None and entry.decisions[0].note is None
    assert entry.redacted is True
    assert SECRET_NOTE not in entry.model_dump_json()

    revisions = await history.list_revisions(scope("u_viewer"), ws, pr)
    assert revisions[0].origin == "model" and revisions[0].actor_user_id is None
    assert revisions[0].decision_count == 1 and revisions[0].evidence_count == 2
    editor_view = await history.list_revisions(scope("u_editor"), ws, pr)
    assert editor_view[0].actor_user_id == "u_editor"


@pytest.mark.asyncio
async def test_history_and_provenance_are_denied_outside_the_project() -> None:
    mutation, history, _repo, _media, scope, ws, pr, other = await _hosted_stack()
    await _seed(mutation, scope, ws, pr)
    outsider = scope("u_stranger")

    for call in (
        history.list_revisions(outsider, ws, pr),
        history.record_history(outsider, ws, pr, record_type="entity", record_id="ent_mara"),
        history.record_provenance(outsider, ws, pr, record_type="entity", record_id="ent_mara"),
        history.entity_at_revision(outsider, ws, pr, "ent_mara", 1),
        history.diff_revisions(outsider, ws, pr, from_revision=0, to_revision=1),
        history.propose_revert(outsider, ws, pr, from_revision=0),
    ):
        with pytest.raises(ForbiddenError):
            await call

    # A member of the workspace cannot read one project's records through another.
    with pytest.raises(NotFoundError):
        await history.record_provenance(
            scope("u_editor"), ws, other, record_type="entity", record_id="ent_mara"
        )
    assert await history.list_revisions(scope("u_editor"), ws, other) == []
    # A project that does not exist in this workspace is not found, never leaked.
    with pytest.raises(NotFoundError):
        await history.list_revisions(scope("u_editor"), ws, "prj_missing")


@pytest.mark.asyncio
async def test_viewers_cannot_tag_revert_or_review() -> None:
    mutation, history, _repo, _media, scope, ws, pr, _other = await _hosted_stack()
    await _seed(mutation, scope, ws, pr)
    viewer = scope("u_viewer")

    with pytest.raises(ForbiddenError):
        await history.set_tag(viewer, ws, pr, name="v1")
    with pytest.raises(ForbiddenError):
        await history.commit_revert(
            viewer, ws, pr, from_revision=0, expected_head=1, acknowledge_high_impact=True
        )
    draft = await mutation.create_draft(
        scope("u_editor"),
        workspace_id=ws,
        project_id=pr,
        operations=[CreateEntityOp(id="ent_x", type="Character", name="X")],
        expected_head=1,
        origin="model",
    )
    for call in (
        mutation.approve(viewer, draft.id),
        mutation.reject(viewer, draft.id),
        mutation.decide_operation(viewer, draft.id, 0, "accepted"),
        mutation.commit(viewer, draft.id),
    ):
        with pytest.raises(ForbiddenError):
            await call
    # Reading the proposal is allowed for any project member.
    assert (await mutation.review(viewer, draft.id)).mutation.id == draft.id


@pytest.mark.asyncio
async def test_evidence_must_be_a_bounded_pointer_to_known_artefacts() -> None:
    from pydantic import ValidationError

    mutation, _history, _repo, media, scope, ws, pr, _other = await _hosted_stack()
    # Copied prose is structurally impossible: evidence is an identifier, not text.
    with pytest.raises(ValidationError):
        EvidenceRef(source_artefact_id="She walked into the harbor at dawn, alone.")
    with pytest.raises(ValidationError):
        EvidenceRef(source_artefact_id="scene:1", location="the part where she lies")
    with pytest.raises(ValidationError):
        CreateEntityOp(
            id="ent_1",
            type="Character",
            name="A",
            evidence=[EvidenceRef(source_artefact_id=f"s:{i}") for i in range(11)],
        )

    asset = await media.create_asset(workspace_id=ws, project_id=pr, name="Map")
    good = await mutation.create_draft(
        scope("u_editor"),
        workspace_id=ws,
        project_id=pr,
        operations=[
            CreateEntityOp(
                id="ent_1",
                type="Location",
                name="Harbor",
                evidence=[EvidenceRef(source_artefact_id=asset.id, location="region:10,20,80,60")],
            )
        ],
        expected_head=0,
    )
    await mutation.commit(scope("u_editor"), good.id)

    bad = await mutation.create_draft(
        scope("u_editor"),
        workspace_id=ws,
        project_id=pr,
        operations=[
            CreateEntityOp(
                id="ent_2",
                type="Location",
                name="Keep",
                evidence=[EvidenceRef(source_artefact_id="ast_does_not_exist")],
            )
        ],
        expected_head=1,
    )
    with pytest.raises(CanonValidationError, match="unknown_evidence"):
        await mutation.commit(scope("u_editor"), bad.id)


@pytest.mark.asyncio
async def test_belief_provenance_keeps_its_acquisition_source() -> None:
    mutation, history, repository, _media, scope, ws, pr, _other = await _hosted_stack()
    draft = await mutation.create_draft(
        scope("u_editor"),
        workspace_id=ws,
        project_id=pr,
        operations=[
            CreateEntityOp(id="ent_mara", type="Character", name="Mara"),
            AssertBeliefOp(
                id="blf_1",
                holder_entity_id="ent_mara",
                proposition=Proposition(
                    subject_id="ent_mara",
                    predicate="HAS_NAME",
                    object=StringObject(kind="string", value="Mara the Lost"),
                ),
                source_ref="scene:ch03-s01",
                evidence=[EvidenceRef(source_artefact_id="scene:ch03-s01", location="line:44")],
            ),
        ],
        expected_head=0,
    )
    await mutation.commit(scope("u_editor"), draft.id)
    entry = (
        await history.record_provenance(
            scope("u_owner"), ws, pr, record_type="belief", record_id="blf_1"
        )
    )[0]
    assert entry.origin == "author"
    assert [d.note for d in entry.decisions] == ["author-direct"]
    assert entry.evidence[0].location == "line:44"
    assert repository.beliefs[(ws, pr, "blf_1")].source_ref == "scene:ch03-s01"
