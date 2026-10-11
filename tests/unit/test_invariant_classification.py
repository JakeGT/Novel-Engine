"""Enforcement classes: integrity always blocks, author rules warn by default (ADR-013)."""

import pytest

from backend.domain.canon.ids import ID_PREFIXES, RESERVED_ID_KINDS
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import CreateEntityOp
from backend.domain.validation import (
    AuthoritativeValidator,
    CanonValidationError,
    RuleEnforcement,
    RuleEvaluation,
    ValidationSeverity,
    classify_rule_evaluation,
)
from backend.services.mutation_service import MutationService
from tests.doubles import InMemoryCanonJournal, InMemoryCanonRepository

pytestmark = pytest.mark.unit

SCOPE = AccessScope(actor_user_id="u", auth_mode=AuthMode.LOCAL, workspace_id="ws", project_id="pr")


class _Rule:
    def __init__(self, *evaluations: RuleEvaluation, error: Exception | None = None) -> None:
        self._evaluations = evaluations
        self._error = error
        self.calls = 0

    async def evaluate(self, operations, *, context):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._evaluations


def _service(*rules) -> tuple[MutationService, InMemoryCanonRepository]:
    journal = InMemoryCanonJournal()
    repo = InMemoryCanonRepository(journal=journal)
    validator = AuthoritativeValidator(evaluators=rules)
    return MutationService(journal, repo, local_mode=True, validator=validator), repo


async def _draft(service, *operations):
    return await service.create_draft(
        SCOPE, workspace_id="ws", project_id="pr", operations=list(operations), expected_head=0
    )


@pytest.mark.parametrize(
    ("evaluation", "expected"),
    [
        (RuleEvaluation("r", "m"), ValidationSeverity.ADVISORY),
        (
            RuleEvaluation("r", "m", enforcement=RuleEnforcement.BLOCKING),
            ValidationSeverity.BLOCKING_RULE,
        ),
        (
            RuleEvaluation("r", "m", enforcement=RuleEnforcement.BLOCKING, complete=False),
            ValidationSeverity.ADVISORY,
        ),
        (
            RuleEvaluation("r", "m", enforcement=RuleEnforcement.BLOCKING, deterministic=False),
            ValidationSeverity.ADVISORY,
        ),
        (
            RuleEvaluation("r", "m", enforcement=RuleEnforcement.WARNING, deterministic=False),
            ValidationSeverity.ADVISORY,
        ),
    ],
)
def test_rule_classification(evaluation, expected) -> None:
    assert classify_rule_evaluation(evaluation) is expected


def test_an_author_rule_defaults_to_a_warning() -> None:
    assert RuleEvaluation("r", "m").enforcement is RuleEnforcement.WARNING
    # No author-rule result can ever be classified as a built-in integrity failure.
    for enforcement in RuleEnforcement:
        for deterministic in (True, False):
            for complete in (True, False):
                result = classify_rule_evaluation(
                    RuleEvaluation(
                        "r",
                        "m",
                        enforcement=enforcement,
                        deterministic=deterministic,
                        complete=complete,
                    )
                )
                assert result is not ValidationSeverity.INTEGRITY


@pytest.mark.asyncio
async def test_warning_rule_informs_but_does_not_block_a_commit() -> None:
    rule = _Rule(RuleEvaluation("no-rain-in-act-one", "it should not rain yet", 0))
    service, repo = _service(rule)
    draft = await _draft(service, CreateEntityOp(id="ent_1", type="Character", name="Mara"))

    report = await service.validate(SCOPE, draft.id)
    assert report.ok
    assert [(i.code, i.severity, i.ordinal) for i in report.issues] == [
        ("rule:no-rain-in-act-one", "advisory", 0)
    ]
    assert (await service.commit(SCOPE, draft.id)).revision == 1
    assert rule.calls == 2  # preview, then again inside the commit transaction


@pytest.mark.asyncio
async def test_explicitly_blocking_deterministic_rule_rejects_the_commit() -> None:
    rule = _Rule(
        RuleEvaluation("one-heir", "only one heir", 0, enforcement=RuleEnforcement.BLOCKING)
    )
    service, repo = _service(rule)
    draft = await _draft(service, CreateEntityOp(id="ent_1", type="Character", name="Mara"))

    with pytest.raises(CanonValidationError, match="rule:one-heir"):
        await service.commit(SCOPE, draft.id)
    assert repo.entities == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uncertain",
    [
        RuleEvaluation("r", "m", enforcement=RuleEnforcement.BLOCKING, complete=False),
        RuleEvaluation("r", "m", enforcement=RuleEnforcement.BLOCKING, deterministic=False),
    ],
)
async def test_incomplete_or_extraction_dependent_evaluation_cannot_block(uncertain) -> None:
    service, repo = _service(_Rule(uncertain))
    draft = await _draft(service, CreateEntityOp(id="ent_1", type="Character", name="Mara"))
    assert (await service.commit(SCOPE, draft.id)).revision == 1
    assert len(repo.entities) == 1


@pytest.mark.asyncio
async def test_a_rule_that_fails_to_evaluate_is_advisory_and_never_crashes_the_commit() -> None:
    service, repo = _service(_Rule(error=RuntimeError("evaluator unavailable")))
    draft = await _draft(service, CreateEntityOp(id="ent_1", type="Character", name="Mara"))

    report = await service.validate(SCOPE, draft.id)
    assert report.ok
    assert [(i.code, i.severity) for i in report.issues] == [("rule_evaluation_failed", "advisory")]
    assert "evaluator unavailable" not in report.issues[0].message
    assert (await service.commit(SCOPE, draft.id)).revision == 1


@pytest.mark.asyncio
async def test_no_evaluator_can_suppress_or_weaken_a_built_in_integrity_finding() -> None:
    """An evaluator only adds findings; the integrity failure still blocks."""
    permissive = _Rule()  # reports nothing at all
    noisy = _Rule(RuleEvaluation("all-good", "everything is fine", 0))
    service, repo = _service(permissive, noisy)
    draft = await _draft(service, CreateEntityOp(id="ent_1", type="Spaceship", name="Nope"))

    report = await service.validate(SCOPE, draft.id)
    assert not report.ok
    assert ("unknown_type", "blocking") in {(i.code, i.severity) for i in report.issues}
    with pytest.raises(CanonValidationError) as raised:
        await service.commit(SCOPE, draft.id)
    codes = {finding.code: finding.severity for finding in raised.value.report.findings}
    assert codes["unknown_type"] is ValidationSeverity.INTEGRITY
    assert repo.entities == {}
    assert permissive.calls == noisy.calls == 2


@pytest.mark.asyncio
async def test_direct_repository_write_also_runs_rules_and_integrity() -> None:
    """The repository, not the service, is the enforcement boundary."""
    from datetime import UTC, datetime

    from backend.domain.journal import CommitEnvelope
    from backend.domain.operations import canonicalize_operations, operations_checksum

    repo = InMemoryCanonRepository()
    validator = AuthoritativeValidator(
        evaluators=[_Rule(RuleEvaluation("one-heir", "x", 0, enforcement=RuleEnforcement.BLOCKING))]
    )
    operations = [CreateEntityOp(id="ent_1", type="Character", name="Mara")]
    envelope = CommitEnvelope(
        commit_id="cmt_direct",
        mutation_id="mut_direct",
        workspace_id="ws",
        project_id="pr",
        branch_id="br",
        revision=1,
        expected_head=0,
        canonical=canonicalize_operations(operations),
        checksum=operations_checksum(operations),
        created_at=datetime.now(UTC),
    )
    with pytest.raises(CanonValidationError, match="rule:one-heir"):
        await repo.apply_commit(envelope=envelope, operations=operations, validator=validator)
    assert repo.entities == {}


def test_reserved_id_prefixes_are_distinct_and_unimplemented() -> None:
    assert len(set(ID_PREFIXES.values())) == len(ID_PREFIXES)
    assert RESERVED_ID_KINDS <= set(ID_PREFIXES)
    for kind in (
        "story_invariant",
        "scene_contract",
        "narrated_assertion",
        "disclosure",
        "finding_suppression",
        "dependency_edge",
        "read_model_watermark",
    ):
        assert kind in RESERVED_ID_KINDS
