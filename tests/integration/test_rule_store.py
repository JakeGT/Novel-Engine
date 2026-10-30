"""Story rules against PostgreSQL: versions are kept, and a rule warns inside a real commit."""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from backend.adapters.canon_repository import PostgresCanonRepository
from backend.adapters.journal import PostgresCanonJournal
from backend.adapters.rule_store import PostgresRuleStore
from backend.core.database import create_engine, create_session_factory, session_scope
from backend.domain.canon.propositions import EntityRefObject, Proposition
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import AssertFactOp, CloseFactOp, CreateEntityOp
from backend.domain.rules import Check, Pattern, RuleBody, RuleStatus, Selector
from backend.domain.validation import AuthoritativeValidator
from backend.services.mutation_service import MutationService
from backend.services.rule_service import RuleService

pytestmark = pytest.mark.integration

WS, PR = "ws_rules_it", "pr_rules_it"
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
    store = PostgresRuleStore(factory)
    rules = RuleService(store, reader=repository, local_mode=True)
    service = MutationService(
        PostgresCanonJournal(factory),
        repository,
        local_mode=True,
        validator=AuthoritativeValidator(evaluators=[rules]),
    )
    try:
        yield service, rules, store, factory
    finally:
        await engine.dispose()


async def _commit(service, operations, head):
    draft = await service.create_draft(
        SCOPE, workspace_id=WS, project_id=PR, operations=operations, expected_head=head
    )
    return draft, await service.commit(SCOPE, draft.id)


@pytest.mark.asyncio
async def test_a_rule_warns_inside_a_real_commit_and_never_stops_it(stack) -> None:
    service, rules, store, _ = stack
    _, made = await _commit(
        service,
        [
            CreateEntityOp(id="tmp:g", type="Faction", name="Harbor Guild"),
            CreateEntityOp(id="tmp:m", type="Character", name="Mara"),
            AssertFactOp(
                id="tmp:f",
                proposition=Proposition(
                    subject_id="tmp:m",
                    predicate="MEMBER_OF",
                    object=EntityRefObject(entity_id="tmp:g", project_id=PR),
                ),
            ),
        ],
        0,
    )
    assert made.revision == 1
    added = await rules.add(
        SCOPE, WS, PR, title="Guilds have members", sentence="", body=BODY, examples=[]
    )
    assert added.broken_by == []

    world = await rules.world(WS, PR)
    (fact_id,) = world.facts
    draft = await service.create_draft(
        SCOPE,
        workspace_id=WS,
        project_id=PR,
        operations=[CloseFactOp(id=fact_id)],
        expected_head=1,
    )
    report = await service.validate(SCOPE, draft.id)
    assert report.ok
    assert [(issue.severity, issue.ordinal) for issue in report.issues] == [("advisory", 0)]
    assert "Guilds have members" in report.issues[0].message
    assert (await service.commit(SCOPE, draft.id)).revision == 2
    (reading,) = await rules.rules(SCOPE, WS, PR)
    assert [item.entity_name for item in reading.broken_by] == ["Harbor Guild"]


@pytest.mark.asyncio
async def test_rewording_adds_a_version_and_versions_cannot_be_changed(stack) -> None:
    _, rules, store, factory = stack
    added = await rules.add(SCOPE, WS, PR, title="Guilds", sentence="", body=BODY, examples=[])
    stricter = BODY.model_copy(update={"checks": [BODY.checks[0].model_copy(update={"count": 2})]})
    changed = await rules.change(
        SCOPE, WS, PR, added.rule.id, body=stricter, status=RuleStatus.PAUSED
    )
    assert (changed.rule.version, changed.rule.status) == (2, RuleStatus.PAUSED)
    assert changed.rule.body.checks[0].count == 2

    async with session_scope(factory) as db:
        numbers = (
            (await db.execute(text("SELECT number FROM story_rule_versions ORDER BY number")))
            .scalars()
            .all()
        )
    assert numbers == [1, 2]
    with pytest.raises(DBAPIError, match="immutable"):
        async with session_scope(factory) as db:
            await db.execute(text("UPDATE story_rule_versions SET body = '{}'::jsonb"))

    assert await store.get_rule(workspace_id=WS, project_id="other", rule_id=added.rule.id) is None
    assert await store.delete_project(WS, PR) == 1
    assert await store.list_rules(workspace_id=WS, project_id=PR) == []
