"""Operational metrics: counts and timings only, never ids or content (2L)."""

import pytest

from backend.core.metrics import MetricLabelError, MetricsRegistry, metrics, span
from backend.domain.identity import AccessScope, AuthMode
from backend.domain.operations import CreateEntityOp, RetireEntityOp
from backend.services.mutation_service import MutationService, ReviewRequiredError
from backend.services.outbox_consumer import OutboxConsumer
from backend.services.projection_service import GraphProjectionService
from tests.doubles import InMemoryCanonJournal, InMemoryCanonRepository, InMemoryCanonWriter

pytestmark = pytest.mark.unit

SCOPE = AccessScope(actor_user_id="u", auth_mode=AuthMode.LOCAL, workspace_id="ws", project_id="pr")


@pytest.fixture(autouse=True)
def _clean_registry():
    metrics.reset()
    yield
    metrics.reset()


def test_registry_renders_prometheus_text() -> None:
    registry = MetricsRegistry()
    registry.increment("jobs_total", "Jobs.", outcome="ok")
    registry.increment("jobs_total", "Jobs.", amount=2, outcome="ok")
    registry.set_gauge("queue_depth", "Depth.", 7)
    registry.observe("job_duration_seconds", "Duration.", 0.02, outcome="ok")
    registry.observe("job_duration_seconds", "Duration.", 3.0, outcome="ok")

    text = registry.render()

    assert "# TYPE jobs_total counter" in text and 'jobs_total{outcome="ok"} 3' in text
    assert "# TYPE queue_depth gauge" in text and "queue_depth 7" in text
    assert 'job_duration_seconds_bucket{outcome="ok",le="0.025"} 1' in text
    assert 'job_duration_seconds_bucket{outcome="ok",le="+Inf"} 2' in text
    assert 'job_duration_seconds_count{outcome="ok"} 2' in text
    assert registry.value("jobs_total", outcome="ok") == 3
    assert registry.count("job_duration_seconds", outcome="ok") == 2


@pytest.mark.parametrize(
    "value",
    ["ent_018f3a2c-7b1e-7c3a", "Mara Venn", "ws_seed_harbor/prj", "", "UPPER", "a" * 41, "x y"],
)
def test_labels_cannot_carry_identifiers_or_content(value) -> None:
    registry = MetricsRegistry()
    with pytest.raises(MetricLabelError):
        registry.increment("jobs_total", "Jobs.", target=value)
    with pytest.raises(MetricLabelError):
        with span("canon.commit", target=value):
            pass
    assert registry.render() == "\n"


def test_a_metric_cannot_change_kind() -> None:
    registry = MetricsRegistry()
    registry.increment("jobs_total", "Jobs.")
    with pytest.raises(ValueError):
        registry.set_gauge("jobs_total", "Jobs.", 1)


def _service():
    journal = InMemoryCanonJournal()
    repo = InMemoryCanonRepository(journal=journal)
    return MutationService(journal, repo, local_mode=True), journal, repo


async def _draft(service, operations, *, origin="author", expected_head=0):
    return await service.create_draft(
        SCOPE,
        workspace_id="ws",
        project_id="pr",
        operations=operations,
        expected_head=expected_head,
        origin=origin,
    )


@pytest.mark.asyncio
async def test_commit_outcomes_are_counted_by_origin() -> None:
    from backend.domain.journal import HeadConflict
    from backend.domain.validation import CanonValidationError

    service, _journal, _repo = _service()
    ok = await _draft(service, [CreateEntityOp(id="ent_1", type="Character", name="Mara Venn")])
    await service.commit(SCOPE, ok.id)
    invalid = await _draft(
        service, [CreateEntityOp(id="ent_2", type="Spaceship", name="X")], expected_head=1
    )
    with pytest.raises(CanonValidationError):
        await service.commit(SCOPE, invalid.id)
    stale = await _draft(service, [CreateEntityOp(id="ent_3", type="Character", name="Y")])
    with pytest.raises(HeadConflict):
        await service.commit(SCOPE, stale.id)
    model = await _draft(
        service,
        [CreateEntityOp(id="ent_4", type="Character", name="Z")],
        origin="model",
        expected_head=1,
    )
    with pytest.raises(ReviewRequiredError):
        await service.commit(SCOPE, model.id)
    await service.approve(SCOPE, model.id)
    await service.commit(SCOPE, model.id)
    retire = await _draft(service, [RetireEntityOp(id="ent_1")], expected_head=2)
    await service.reject(SCOPE, retire.id)

    def commits(origin: str, outcome: str) -> float:
        return metrics.value("canon_commits_total", origin=origin, outcome=outcome)

    assert commits("author", "committed") == 1
    assert commits("author", "validation_failed") == 1
    assert commits("author", "head_conflict") == 1
    assert commits("model", "review_required") == 1
    assert commits("model", "committed") == 1
    assert metrics.value("canon_commit_operations_total", origin="author") == 1
    assert metrics.count("canon_commit_duration_seconds", outcome="committed") == 2
    assert metrics.value("mutation_transitions_total", to_status="approved", origin="model") == 1
    assert metrics.value("mutation_transitions_total", to_status="rejected", origin="author") == 1

    rendered = metrics.render()
    # Nothing identifying a record, project, or story reaches the exposition.
    for secret in ("Mara", "Venn", "ent_1", "ws", 'pr"', SCOPE.actor_user_id + '"'):
        assert secret not in rendered.replace("project", "").replace("workspace", ""), secret


@pytest.mark.asyncio
async def test_projection_and_outbox_outcomes_are_counted() -> None:
    service, journal, repo = _service()
    writer = InMemoryCanonWriter()
    projector = GraphProjectionService(canon=repo, writer=writer)
    consumer = OutboxConsumer(journal=journal, projector=projector)
    draft = await _draft(service, [CreateEntityOp(id="ent_1", type="Character", name="Mara")])
    await service.commit(SCOPE, draft.id)
    branch = await journal.ensure_root_branch(workspace_id="ws", project_id="pr")
    target = {"workspace_id": "ws", "project_id": "pr", "branch_id": branch.id}

    writer.fail_after_ops = 0
    failed = await consumer.drain(**target)
    assert failed.failed == 1
    assert metrics.value("projection_commits_total", outcome="error") == 1
    assert metrics.value("outbox_deliveries_total", outcome="failed") == 1
    assert metrics.value("projection_lag_revisions_last_drain") == 1

    writer.fail_after_ops = None
    await consumer.drain(**target)
    assert metrics.value("projection_commits_total", outcome="applied") == 1
    assert metrics.value("outbox_deliveries_total", outcome="published") == 1
    assert metrics.value("projection_lag_revisions_last_drain") == 0

    await projector.rebuild(**target)
    assert metrics.value("projection_rebuilds_total") == 1
