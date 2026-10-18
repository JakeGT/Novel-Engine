"""Keep the safe ones for me against PostgreSQL (AST-EXP-015; migration 0042)."""

import pytest
import pytest_asyncio

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.delegation_store import PostgresDelegationStore
from backend.adapters.journal import PostgresCanonJournal
from backend.core.database import create_engine, create_session_factory
from backend.domain.delegation import DelegationSettings
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.journal import MutationStatus
from backend.domain.operations import CreateEntityOp
from backend.domain.validation import CanonValidationError
from backend.services.delegation_service import DelegationService
from backend.services.mutation_service import MutationService

pytestmark = pytest.mark.integration

WS, PR = "ws_keep_it", "pr_keep_it"


def _scope() -> AccessScope:
    return AccessScope(
        actor_user_id="usr_author", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR
    )


@pytest_asyncio.fixture
async def stack(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    journal = PostgresCanonJournal(factory)
    repository = PostgresCanonRepository(factory)
    mutation = MutationService(journal, repository, local_mode=True)
    store = PostgresDelegationStore(factory)
    delegation = DelegationService(store, mutation=mutation, reader=repository, local_mode=True)
    mutation.delegation = delegation
    try:
        yield mutation, delegation, store, repository, journal
    finally:
        await engine.dispose()


async def _suggest(mutation, repository, name: str):
    branch = await mutation._journal.working_branch(workspace_id=WS, project_id=PR)
    head = await repository.get_head(workspace_id=WS, project_id=PR, branch_id=branch.id)
    draft = await mutation.create_draft(
        _scope(),
        workspace_id=WS,
        project_id=PR,
        operations=[CreateEntityOp(id="tmp:a", type="Character", name=name)],
        expected_head=head,
        origin="model",
    )
    return await mutation.submit(_scope(), draft.id, "test")


@pytest.mark.asyncio
async def test_rules_are_earned_kept_and_checked_inside_the_commit(stack) -> None:
    mutation, delegation, store, repository, journal = stack
    assert await store.get(workspace_id=WS, project_id=PR) == DelegationSettings()

    for name in ("Ines", "Oskar", "Pell"):
        waiting = await _suggest(mutation, repository, name)
        await mutation.approve(_scope(), waiting.id)
        await mutation.commit(_scope(), waiting.id)
    assert await store.hand_counts(workspace_id=WS, project_id=PR) == {"new_entry": 3}

    view = await delegation.set_story(_scope(), WS, PR, kinds=["new_entry"], allow_clients=False)
    assert [rule.on for rule in view.rules] == [True, False, False]
    stored = await store.get(workspace_id=WS, project_id=PR)
    assert list(stored.rules) == ["new_entry"] and stored.rules["new_entry"].by == "usr_author"

    kept = await delegation.consider(_scope(), (await _suggest(mutation, repository, "Ruth")).id)
    assert kept.kept and kept.mutation.status is MutationStatus.COMMITTED
    record = await store.get_record(workspace_id=WS, project_id=PR, mutation_id=kept.mutation.id)
    assert record is not None and (record.how, record.state) == ("kept", "kept")
    assert record.revision == 4 and record.kinds == ("new_entry",)
    notes = [item.note for item in await journal.list_decisions(mutation_id=kept.mutation.id)]
    assert "Kept for you under your rule “New entries”." in notes
    assert await store.count_kept_since(workspace_id=WS, project_id=PR, since=None) == 1
    listed = await delegation.kept(_scope(), WS, PR)
    assert [item.record.mutation_id for item in listed] == [kept.mutation.id]
    await delegation.seen(_scope(), WS, PR)
    seen = await store.get_seen(workspace_id=WS, project_id=PR, user_id="usr_author")
    assert await store.count_kept_since(workspace_id=WS, project_id=PR, since=seen) == 0

    # One approved under the rule, then the rule is switched off behind its back: the
    # commit transaction itself refuses it.
    waiting = await _suggest(mutation, repository, "Sela")
    await store.put_record(
        workspace_id=WS,
        project_id=PR,
        mutation_id=waiting.id,
        how="kept",
        state="pending",
        kinds=["new_entry"],
        branch_id=waiting.branch_id,
        actor_user_id="usr_author",
    )
    await mutation.approve(_scope(), waiting.id)
    await store.set(
        workspace_id=WS, project_id=PR, settings=DelegationSettings(), actor_user_id=None
    )
    with pytest.raises(CanonValidationError) as refused:
        await mutation.commit(_scope(), waiting.id)
    assert refused.value.report.findings[0].code == "kept_rule_off"

    # Switched off through the service, it goes back to the review queue.
    await store.set(
        workspace_id=WS,
        project_id=PR,
        settings=stored,
        actor_user_id=None,
    )
    await delegation.set_story(_scope(), WS, PR, kinds=[], allow_clients=False)
    back = await journal.get_mutation(workspace_id=WS, project_id=PR, mutation_id=waiting.id)
    assert back is not None and back.status is MutationStatus.NEEDS_REVIEW
    record = await store.get_record(workspace_id=WS, project_id=PR, mutation_id=waiting.id)
    assert record is not None and (record.how, record.reason_code) == ("waited", "kept_rule_off")


@pytest.mark.asyncio
async def test_a_workspace_owner_forbids_it_for_every_story(stack) -> None:
    _mutation, delegation, store, _repository, _journal = stack
    await delegation.set_workspace_forbidden(_scope(), WS, forbidden=True)
    assert await delegation.workspace_forbidden(WS) is True
    workspace = await store.get(workspace_id=WS)
    assert workspace.forbidden is True
    # The workspace's own row is not a story's.
    assert (await store.get(workspace_id=WS, project_id=PR)).forbidden is False


async def _earned_and_on(mutation, delegation, repository) -> None:
    for name in ("Ines", "Oskar", "Pell"):
        waiting = await _suggest(mutation, repository, name)
        await mutation.approve(_scope(), waiting.id)
        await mutation.commit(_scope(), waiting.id)
    await delegation.set_story(_scope(), WS, PR, kinds=["new_entry"], allow_clients=False)


async def _approved_under_the_rule(mutation, repository, store, name: str):
    waiting = await _suggest(mutation, repository, name)
    await store.put_record(
        workspace_id=WS,
        project_id=PR,
        mutation_id=waiting.id,
        how="kept",
        state="pending",
        kinds=["new_entry"],
        branch_id=waiting.branch_id,
        actor_user_id="usr_author",
    )
    await mutation.approve(_scope(), waiting.id)
    return waiting


@pytest.mark.asyncio
async def test_the_commit_marks_it_kept_and_a_late_switch_off_leaves_it_kept(stack) -> None:
    """Race A: switching off read the suggestion as pending; its commit landed before the
    switch-off sent it back."""
    mutation, delegation, store, repository, journal = stack
    await _earned_and_on(mutation, delegation, repository)
    waiting = await _approved_under_the_rule(mutation, repository, store, "Sela")
    stale = await store.get_record(workspace_id=WS, project_id=PR, mutation_id=waiting.id)
    assert stale is not None and stale.state == "pending"

    # Only the commit transaction runs: the service never hears of the commit.
    mutation.delegation = None
    await mutation.commit(_scope(), waiting.id)
    mutation.delegation = delegation
    record = await store.get_record(workspace_id=WS, project_id=PR, mutation_id=waiting.id)
    assert record is not None and (record.how, record.state) == ("kept", "kept")
    head = await repository.get_head(workspace_id=WS, project_id=PR, branch_id=waiting.branch_id)
    assert record.revision == head

    from backend.domain.delegation import RULE_OFF_CODE, RULE_OFF_MESSAGE, Refusal

    await delegation._back_to_review(_scope(), stale, Refusal(RULE_OFF_CODE, RULE_OFF_MESSAGE))
    await delegation.set_story(_scope(), WS, PR, kinds=[], allow_clients=False)
    record = await store.get_record(workspace_id=WS, project_id=PR, mutation_id=waiting.id)
    assert record is not None and (record.how, record.state) == ("kept", "kept")
    after = await journal.get_mutation(workspace_id=WS, project_id=PR, mutation_id=waiting.id)
    assert after is not None and after.status is MutationStatus.COMMITTED
    assert await store.hand_counts(workspace_id=WS, project_id=PR) == {"new_entry": 3}


@pytest.mark.asyncio
async def test_a_switch_off_during_the_commit_waits_for_it_and_finds_it_kept(stack) -> None:
    """Race B: the commit holds the settings rows first; switching off waits for it."""
    import asyncio

    from backend.adapters import canon_repository

    mutation, delegation, store, repository, journal = stack
    await _earned_and_on(mutation, delegation, repository)
    waiting = await _approved_under_the_rule(mutation, repository, store, "Tova")
    switching: list[asyncio.Task] = []

    class SwitchOffMidCommit:
        """Runs inside the commit transaction, after the rule was checked."""

        def __init__(self, inner) -> None:
            self._inner = inner

        async def validate(self, operations, *, context):
            switching.append(
                asyncio.create_task(
                    delegation.set_story(_scope(), WS, PR, kinds=[], allow_clients=False)
                )
            )
            await asyncio.sleep(0.5)
            assert not switching[0].done(), "switching off did not wait for the commit"
            return await self._inner.validate(operations, context=context)

    apply_commit = repository.apply_commit

    async def apply(**kwargs):
        kwargs["validator"] = SwitchOffMidCommit(
            kwargs.get("validator") or canon_repository._DEFAULT_VALIDATOR
        )
        return await apply_commit(**kwargs)

    repository.apply_commit = apply
    mutation.delegation = None
    try:
        await mutation.commit(_scope(), waiting.id)
    finally:
        repository.apply_commit = apply_commit
        mutation.delegation = delegation
    view = await switching[0]
    assert not any(rule.on for rule in view.rules)
    record = await store.get_record(workspace_id=WS, project_id=PR, mutation_id=waiting.id)
    assert record is not None and (record.how, record.state) == ("kept", "kept")
    assert record.revision is not None
    after = await journal.get_mutation(workspace_id=WS, project_id=PR, mutation_id=waiting.id)
    assert after is not None and after.status is MutationStatus.COMMITTED


@pytest.mark.asyncio
async def test_records_are_read_page_by_page(stack) -> None:
    _mutation, _delegation, store, _repository, _journal = stack
    for n in range(5):
        await store.put_record(
            workspace_id=WS,
            project_id=PR,
            mutation_id=f"mut_page_{n}",
            how="kept",
            state="pending",
            kinds=["new_entry"],
            branch_id="brn",
            actor_user_id=None,
        )
    first = await store.list_records(
        workspace_id=WS, project_id=PR, how="kept", states=["pending"], limit=3
    )
    rest = await store.list_records(
        workspace_id=WS, project_id=PR, how="kept", states=["pending"], limit=3, offset=3
    )
    assert len(first) == 3 and len(rest) == 2
    assert {r.mutation_id for r in first} | {r.mutation_id for r in rest} == {
        f"mut_page_{n}" for n in range(5)
    }
