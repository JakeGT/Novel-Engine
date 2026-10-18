"""Against PostgreSQL: a blocking rule refuses a real commit, and dismissed findings are
kept per line of the story and copied with a what-if (CON-010, CON-015, CON-016)."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.journal import PostgresCanonJournal
from backend.adapters.rule_store import PostgresRuleStore
from backend.adapters.studio_store import PostgresStudioStore
from backend.core.database import create_engine, create_session_factory, session_scope
from backend.domain.dismissals import DismissReason, FindingDismissal, FindingKind
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import CreateEntityOp
from backend.domain.rules import Check, Pattern, RuleBody, Selector
from backend.domain.validation import (
    AuthoritativeValidator,
    CanonValidationError,
    RuleEnforcement,
)
from backend.services.mutation_service import MutationService
from backend.services.rule_service import RuleService

pytestmark = pytest.mark.integration

WS, PR = "ws_findings_it", "pr_findings_it"
SCOPE = AccessScope(
    actor_user_id="usr_author", auth_mode=AuthMode.LOCAL, workspace_id=WS, project_id=PR
)
BODY = RuleBody(
    applies_to=Selector(type="Faction"),
    checks=[
        Check(kind="at_least", count=1, pattern=Pattern(predicate="MEMBER_OF", direction="in"))
    ],
)


@pytest_asyncio.fixture
async def stack(disposable_database):
    engine = create_engine(disposable_database.settings())
    factory = create_session_factory(engine)
    repository = PostgresCanonRepository(factory)
    rules = RuleService(PostgresRuleStore(factory), reader=repository, local_mode=True)
    service = MutationService(
        PostgresCanonJournal(factory),
        repository,
        local_mode=True,
        validator=AuthoritativeValidator(evaluators=[rules]),
    )
    try:
        yield service, rules, repository, factory
    finally:
        await engine.dispose()


async def _commit(service, operations, head):
    draft = await service.create_draft(
        SCOPE, workspace_id=WS, project_id=PR, operations=operations, expected_head=head
    )
    return await service.commit(SCOPE, draft.id)


@pytest.mark.asyncio
async def test_a_blocking_rule_refuses_a_real_commit_and_the_head_stays(stack) -> None:
    service, rules, repository, _ = stack
    assert (
        await _commit(service, [CreateEntityOp(id="tmp:m", type="Character", name="Mara")], 0)
    ).revision == 1
    added = await rules.add(SCOPE, WS, PR, title="Guilds", sentence="", body=BODY, examples=[])
    assert added.rule.enforcement is RuleEnforcement.WARNING
    changed = await rules.change(SCOPE, WS, PR, added.rule.id, enforcement=RuleEnforcement.BLOCKING)
    assert changed.rule.enforcement is RuleEnforcement.BLOCKING

    empty_guild = [CreateEntityOp(id="tmp:g", type="Faction", name="The Empty Guild")]
    with pytest.raises(CanonValidationError) as refused:
        await _commit(service, empty_guild, 1)
    (finding,) = refused.value.report.blocking
    assert finding.code == f"rule:{added.rule.id}" and finding.rule_version == 1
    names = [e.name for e in await repository.list_entities(workspace_id=WS, project_id=PR)]
    assert names == ["Mara"]

    await rules.change(SCOPE, WS, PR, added.rule.id, enforcement=RuleEnforcement.WARNING)
    assert (await _commit(service, empty_guild, 1)).revision == 2


@pytest.mark.asyncio
async def test_rules_about_belief_and_story_time_read_them_inside_the_commit(stack) -> None:
    """Beliefs and event times come from the commit's own reader, so these rules can block."""
    from backend.domain.canon.propositions import EntityRefObject, Proposition
    from backend.domain.operations import (
        AmendEventOp,
        AssertBeliefOp,
        AssertFactOp,
        CloseBeliefOp,
        CreateEventOp,
    )
    from backend.domain.worldtime import At, Relative

    service, rules, repository, _ = stack

    def at(fact_id: str, who: str, place: str, **times: object) -> AssertFactOp:
        return AssertFactOp(
            id=fact_id,
            proposition=Proposition(
                subject_id=who,
                predicate="LOCATED_AT",
                object=EntityRefObject(entity_id=place, project_id=PR),
            ),
            **times,  # type: ignore[arg-type]
        )

    first = await _commit(
        service,
        [
            CreateEntityOp(id="tmp:m", type="Character", name="Mara"),
            CreateEntityOp(id="tmp:h", type="Location", name="Harbor"),
            CreateEntityOp(id="tmp:s", type="Location", name="Saltmere"),
            CreateEventOp(id="tmp:war", kind="battle", name="The war", when=At(at=20)),
            at("tmp:f1", "tmp:m", "tmp:h", ends=At(at=12)),
            AssertBeliefOp(
                id="tmp:b1",
                holder_entity_id="tmp:m",
                proposition=Proposition(
                    subject_id="tmp:m",
                    predicate="LOCATED_AT",
                    object=EntityRefObject(entity_id="tmp:h", project_id=PR),
                ),
                source_ref="test",
            ),
        ],
        0,
    )
    entities = {
        e.name: e.id for e in await repository.list_entities(workspace_id=WS, project_id=PR)
    }
    (war,) = await repository.list_events(workspace_id=WS, project_id=PR)
    (belief,) = await repository.list_beliefs(workspace_id=WS, project_id=PR)
    # She goes to Saltmere when the war comes, long after leaving the harbour.
    second = await _commit(
        service,
        [
            at(
                "tmp:f2",
                entities["Mara"],
                entities["Saltmere"],
                starts=Relative(anchor_event_id=war.id),
            )
        ],
        first.revision,
    )

    one_at_a_time = RuleBody(
        applies_to=Selector(type="Character"),
        checks=[Check(kind="at_most", count=1, pattern=Pattern(predicate="LOCATED_AT"))],
        time="every_moment",
    )
    believes = RuleBody(
        applies_to=Selector(type="Character"),
        checks=[
            Check(
                kind="believes",
                pattern=Pattern(predicate="LOCATED_AT", other_id=entities["Harbor"]),
            )
        ],
    )
    for title, body in (("One place at a time", one_at_a_time), ("Knows home", believes)):
        added = await rules.add(SCOPE, WS, PR, title=title, sentence="", body=body, examples=[])
        assert added.broken_by == []
        await rules.change(SCOPE, WS, PR, added.rule.id, enforcement=RuleEnforcement.BLOCKING)

    # Moving the war to year 5 puts her in Saltmere while still at the harbour; the commit
    # reads the events itself, so the answer is complete and it blocks.
    with pytest.raises(CanonValidationError) as moved:
        await _commit(service, [AmendEventOp(id=war.id, when=At(at=5))], second.revision)
    assert "One place at a time" in moved.value.report.blocking[0].message

    # Ending the belief breaks the other blocking rule: refused too.
    with pytest.raises(CanonValidationError) as doubted:
        await _commit(service, [CloseBeliefOp(id=belief.id)], second.revision)
    assert "Knows home" in doubted.value.report.blocking[0].message

    # A war later still keeps both rules: the change goes through.
    later = await _commit(service, [AmendEventOp(id=war.id, when=At(at=30))], second.revision)
    assert later.revision == second.revision + 1


@pytest.mark.asyncio
async def test_dismissals_are_kept_per_line_of_the_story_and_forked(stack) -> None:
    service, _, repository, factory = stack
    await _commit(service, [CreateEntityOp(id="tmp:m", type="Character", name="Mara")], 0)
    async with session_scope(factory) as db:
        root = (
            await db.execute(
                text("SELECT id FROM branches WHERE workspace_id = :w AND project_id = :p"),
                {"w": WS, "p": PR},
            )
        ).scalar_one()
    store = PostgresStudioStore(factory)
    now = datetime.now(UTC)

    def dismissal(ident: str, reason: DismissReason) -> FindingDismissal:
        return FindingDismissal(
            workspace_id=WS,
            project_id=PR,
            branch_id=root,
            id=ident,
            plan_id="pln_scene",
            fingerprint="a" * 64,
            kind=FindingKind.CHECK,
            code="stock_phrase",
            reason=reason,
            note="",
            expires_at=None,
            created_by="usr_author",
            created_at=now,
        )

    await store.add_dismissal(dismissal("d1", DismissReason.INTENDED))
    # The same finding dismissed again: the newer decision stands, the older is kept withdrawn.
    await store.add_dismissal(dismissal("d2", DismissReason.NOT_A_PROBLEM))
    (standing,) = await store.dismissals(
        workspace_id=WS, project_id=PR, branch_id=root, plan_id="pln_scene"
    )
    assert standing.id == "d2" and standing.reason is DismissReason.NOT_A_PROBLEM

    await repository.fork_branch(
        workspace_id=WS,
        project_id=PR,
        parent_branch_id=root,
        branch_id="brn_whatif",
        name="What if",
        description=None,
        actor_user_id="usr_author",
    )
    (copied,) = await store.dismissals(
        workspace_id=WS, project_id=PR, branch_id="brn_whatif", plan_id="pln_scene"
    )
    assert copied.id == "d2"
    assert await store.withdraw_dismissal(
        workspace_id=WS,
        project_id=PR,
        branch_id="brn_whatif",
        plan_id="pln_scene",
        dismissal_id="d2",
        at=now,
    )
    assert (
        await store.dismissals(
            workspace_id=WS, project_id=PR, branch_id="brn_whatif", plan_id="pln_scene"
        )
        == []
    )
    # The main story keeps its decision, and the record keeps every one.
    assert (
        len(
            await store.dismissals(
                workspace_id=WS, project_id=PR, branch_id=root, plan_id="pln_scene"
            )
        )
        == 1
    )
    async with session_scope(factory) as db:
        rows = (await db.execute(text("SELECT count(*) FROM finding_dismissals"))).scalar_one()
    # Two in the main story, and both copied into the what-if, withdrawn ones too.
    assert rows == 4


@pytest.mark.asyncio
async def test_the_same_finding_dismissed_twice_at_once_keeps_one_standing(stack) -> None:
    import asyncio

    service, _, _, factory = stack
    await _commit(service, [CreateEntityOp(id="tmp:m", type="Character", name="Mara")], 0)
    async with session_scope(factory) as db:
        root = (
            await db.execute(
                text("SELECT id FROM branches WHERE workspace_id = :w AND project_id = :p"),
                {"w": WS, "p": PR},
            )
        ).scalar_one()
    store = PostgresStudioStore(factory)
    now = datetime.now(UTC)

    def dismissal(ident: str) -> FindingDismissal:
        return FindingDismissal(
            workspace_id=WS,
            project_id=PR,
            branch_id=root,
            id=ident,
            plan_id="pln_scene",
            fingerprint="b" * 64,
            kind=FindingKind.NOTE,
            code="voice",
            reason=DismissReason.INTENDED,
            note="",
            expires_at=None,
            created_by="usr_author",
            created_at=now,
        )

    # Several rounds, with and without one already standing.
    for round_ in range(5):
        await asyncio.gather(*(store.add_dismissal(dismissal(f"d{round_}_{n}")) for n in range(4)))
        standing = await store.dismissals(
            workspace_id=WS, project_id=PR, branch_id=root, plan_id="pln_scene"
        )
        assert len(standing) == 1
    async with session_scope(factory) as db:
        rows = (await db.execute(text("SELECT count(*) FROM finding_dismissals"))).scalar_one()
    assert rows == 20
