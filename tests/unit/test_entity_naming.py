"""Entity naming policy: names are handles, never identity (2F)."""

import pytest

from backend.domain.canon.naming import (
    MAX_ALIASES,
    aliases_problem,
    external_ids_problem,
    name_problem,
    normalize_name,
    slug_problem,
    slugify,
)
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import (
    CreateEntityOp,
    RetireEntityOp,
    UpdateEntityOp,
    canonicalize_operations,
)
from backend.domain.validation import CanonValidationError
from backend.services.mutation_service import MutationService
from tests.doubles import InMemoryCanonJournal, InMemoryCanonRepository

pytestmark = pytest.mark.unit

WS, PR = "ws", "pr"
SCOPE = AccessScope(actor_user_id="u1", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR)


def _service():
    journal = InMemoryCanonJournal()
    repository = InMemoryCanonRepository(journal=journal)
    return MutationService(journal, repository, local_mode=True), repository


async def _commit(service, repository, operations, *, acknowledge=False):
    head = await repository.get_head(
        workspace_id=WS,
        project_id=PR,
        branch_id=(await service._journal.ensure_root_branch(workspace_id=WS, project_id=PR)).id,
    )
    draft = await service.create_draft(
        SCOPE, workspace_id=WS, project_id=PR, operations=operations, expected_head=head
    )
    return await service.commit_author_change(SCOPE, draft.id, acknowledge_high_impact=acknowledge)


def test_pure_policy_rules() -> None:
    assert normalize_name("  Mara   VENN ") == normalize_name("mara venn")
    assert name_problem("   ") and name_problem("x" * 301) and name_problem("a\x00b")
    assert name_problem("Mara Venn") is None
    assert slug_problem("mara-venn") is None
    for bad in ("Mara", "mara_venn", "-mara", "mara--venn", "", "m" * 101):
        assert slug_problem(bad), bad
    assert slugify("Mára  Venn, the Bold!") == "mara-venn-the-bold"
    assert slugify("???") == ""
    assert aliases_problem(["The Fox", "the fox"], name="Mara")
    assert aliases_problem(["mara"], name="Mara")
    assert aliases_problem(["a"] * 0 + [f"alias {i}" for i in range(MAX_ALIASES + 1)], name="M")
    assert aliases_problem(["The Fox"], name="Mara") is None
    assert external_ids_problem({"Wiki": "1"}) and external_ids_problem({"wiki": ""})
    assert external_ids_problem({"wiki": " 1"})
    assert external_ids_problem({"wiki": "Q42", "scrivener.uuid": "ab-1"}) is None


def test_new_fields_do_not_change_earlier_envelope_checksums() -> None:
    """Operations without the new fields serialize exactly as before they existed."""
    canonical = canonicalize_operations([CreateEntityOp(id="ent_1", type="Character", name="M")])
    assert canonical == (
        '[{"attributes":{},"id":"ent_1","name":"M","op":"create_entity","slug":null,'
        '"type":"Character"}]'
    )
    extended = canonicalize_operations(
        [CreateEntityOp(id="ent_1", type="Character", name="M", aliases=["Em"])]
    )
    assert '"aliases":["Em"]' in extended


@pytest.mark.asyncio
async def test_duplicate_names_are_allowed_and_only_advised() -> None:
    service, repository = _service()
    await _commit(service, repository, [CreateEntityOp(id="ent_1", type="Character", name="Mara")])
    draft = await service.create_draft(
        SCOPE,
        workspace_id=WS,
        project_id=PR,
        operations=[
            CreateEntityOp(id="ent_2", type="Character", name=" mara "),
            CreateEntityOp(id="ent_3", type="Location", name="Mara"),
        ],
        expected_head=1,
    )
    report = await service.validate(SCOPE, draft.id)
    assert report.ok
    assert [(i.ordinal, i.code, i.severity) for i in report.issues] == [
        (0, "name_collision", "advisory"),
        # The same name on a different kind of entry is worth a look too.
        (1, "possible_duplicate", "advisory"),
    ]
    await service.commit(SCOPE, draft.id)
    assert len(repository.entities) == 3


@pytest.mark.asyncio
async def test_slug_is_unique_among_active_entities_and_freed_by_retirement() -> None:
    service, repository = _service()
    await _commit(
        service, repository, [CreateEntityOp(id="ent_1", type="Character", name="A", slug="fox")]
    )
    with pytest.raises(CanonValidationError, match="slug_conflict"):
        await _commit(
            service,
            repository,
            [CreateEntityOp(id="ent_2", type="Character", name="B", slug="fox")],
        )
    with pytest.raises(CanonValidationError, match="invalid_slug"):
        await _commit(
            service,
            repository,
            [CreateEntityOp(id="ent_2", type="Character", name="B", slug="Not A Slug")],
        )
    # Two creations in one batch may not claim the same slug either.
    with pytest.raises(CanonValidationError, match="slug_conflict"):
        await _commit(
            service,
            repository,
            [
                CreateEntityOp(id="ent_2", type="Character", name="B", slug="owl"),
                CreateEntityOp(id="ent_3", type="Character", name="C", slug="owl"),
            ],
        )
    await _commit(service, repository, [RetireEntityOp(id="ent_1")], acknowledge=True)
    await _commit(
        service, repository, [CreateEntityOp(id="ent_2", type="Character", name="B", slug="fox")]
    )
    holder = await repository.get_entity_by_slug(workspace_id=WS, project_id=PR, slug="fox")
    assert holder is not None and holder.id == "ent_2"


@pytest.mark.asyncio
async def test_slug_can_move_within_one_batch() -> None:
    service, repository = _service()
    await _commit(
        service, repository, [CreateEntityOp(id="ent_1", type="Character", name="A", slug="fox")]
    )
    await _commit(
        service,
        repository,
        [
            UpdateEntityOp(id="ent_1", slug="old-fox"),
            CreateEntityOp(id="ent_2", type="Character", name="B", slug="fox"),
        ],
    )
    assert repository.entities[(WS, PR, "ent_1")].slug == "old-fox"
    assert repository.entities[(WS, PR, "ent_2")].slug == "fox"


@pytest.mark.asyncio
async def test_external_identifier_resolves_to_one_entity_even_when_retired() -> None:
    service, repository = _service()
    await _commit(
        service,
        repository,
        [CreateEntityOp(id="ent_1", type="Character", name="A", external_ids={"wiki": "Q42"})],
    )
    with pytest.raises(CanonValidationError, match="external_id_conflict"):
        await _commit(
            service,
            repository,
            [CreateEntityOp(id="ent_2", type="Character", name="B", external_ids={"wiki": "Q42"})],
        )
    with pytest.raises(CanonValidationError, match="invalid_external_id"):
        await _commit(
            service,
            repository,
            [CreateEntityOp(id="ent_2", type="Character", name="B", external_ids={"Bad NS": "1"})],
        )
    await _commit(service, repository, [RetireEntityOp(id="ent_1")], acknowledge=True)
    # A retired entity keeps its external identity so re-imports still de-duplicate.
    with pytest.raises(CanonValidationError, match="external_id_conflict"):
        await _commit(
            service,
            repository,
            [CreateEntityOp(id="ent_2", type="Character", name="B", external_ids={"wiki": "Q42"})],
        )
    found = await repository.get_entity_by_external_id(
        workspace_id=WS, project_id=PR, namespace="wiki", value="Q42"
    )
    assert found is not None and found.id == "ent_1"


@pytest.mark.asyncio
async def test_aliases_are_bounded_searchable_and_never_block() -> None:
    service, repository = _service()
    await _commit(
        service,
        repository,
        [
            CreateEntityOp(id="ent_1", type="Character", name="Mara", aliases=["The Fox"]),
            # Many characters can share an epithet; aliases are not unique.
            CreateEntityOp(id="ent_2", type="Character", name="Iva", aliases=["The Fox"]),
        ],
    )
    hits = await repository.search_entities(workspace_id=WS, project_id=PR, query="fox")
    assert {entity.id for entity in hits} == {"ent_1", "ent_2"}
    with pytest.raises(CanonValidationError, match="invalid_alias"):
        await _commit(service, repository, [UpdateEntityOp(id="ent_1", aliases=["Mara"])])
    await _commit(service, repository, [UpdateEntityOp(id="ent_1", aliases=[])])
    assert repository.entities[(WS, PR, "ent_1")].aliases == []


@pytest.mark.asyncio
async def test_unrelated_edit_does_not_fail_on_untouched_handles() -> None:
    service, repository = _service()
    await _commit(
        service, repository, [CreateEntityOp(id="ent_1", type="Character", name="A", slug="fox")]
    )
    await _commit(service, repository, [UpdateEntityOp(id="ent_1", attributes={"age": 30})])
    assert repository.entities[(WS, PR, "ent_1")].slug == "fox"


@pytest.mark.asyncio
async def test_retiring_a_referenced_entity_is_advised_not_blocked() -> None:
    from backend.domain.canon.propositions import EntityRefObject, Proposition
    from backend.domain.operations import AssertFactOp

    service, repository = _service()
    await _commit(
        service,
        repository,
        [
            CreateEntityOp(id="ent_c", type="Character", name="Mara"),
            CreateEntityOp(id="ent_l", type="Location", name="Harbor"),
            AssertFactOp(
                id="fct_1",
                proposition=Proposition(
                    subject_id="ent_c",
                    predicate="LOCATED_AT",
                    object=EntityRefObject(kind="entity_ref", entity_id="ent_l", project_id=PR),
                ),
            ),
        ],
    )
    draft = await service.create_draft(
        SCOPE,
        workspace_id=WS,
        project_id=PR,
        operations=[RetireEntityOp(id="ent_l")],
        expected_head=1,
    )
    report = await service.validate(SCOPE, draft.id)
    assert report.ok
    assert [(i.code, i.severity) for i in report.issues] == [("referenced_entity", "advisory")]
    assert await repository.list_active_references(
        workspace_id=WS, project_id=PR, entity_id="ent_l"
    ) == [("fact", "fct_1")]
