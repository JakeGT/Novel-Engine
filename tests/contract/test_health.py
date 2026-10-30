import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from tests.doubles import FakeMigrationVerifier

pytestmark = pytest.mark.contract


def test_health_live(fake_container) -> None:
    app = create_app(container=fake_container)

    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_health_ready_with_fake_stores(fake_container) -> None:
    app = create_app(container=fake_container)

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["neo4j_migrations"]["ok"] is True


def test_health_ready_degrades_when_migration_behind(fake_container) -> None:
    app = create_app(container=fake_container)

    with TestClient(app) as client:
        fake_container.neo4j_migrations = FakeMigrationVerifier(current=False, detail="pending=x")
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"


def test_health_ready_degrades_when_store_down(fake_container) -> None:
    from tests.doubles import FakeDatabase

    app = create_app(container=fake_container)

    with TestClient(app) as client:
        fake_container.database = FakeDatabase(ready=False)
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["postgres"]["ok"] is False


def test_health_ready_degrades_when_neo4j_unreachable(fake_container, monkeypatch) -> None:
    app = create_app(container=fake_container)

    with TestClient(app) as client:
        monkeypatch.setattr(fake_container.graph, "ping", lambda: False)
        response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["neo4j"]["ok"] is False


def test_health_ready_stays_ready_when_optional_providers_down(fake_container) -> None:
    from tests.doubles import DisabledEmbeddingProvider, ScriptedAIGateway

    app = create_app(container=fake_container)

    with TestClient(app) as client:
        fake_container.embeddings = DisabledEmbeddingProvider()
        fake_container.ai = ScriptedAIGateway(available=False)
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_health_capabilities_report_degradation_reasons(fake_container, monkeypatch) -> None:
    from tests.doubles import DisabledEmbeddingProvider, ScriptedAIGateway

    app = create_app(container=fake_container)

    with TestClient(app) as client:
        fake_container.embeddings = DisabledEmbeddingProvider()
        fake_container.ai = ScriptedAIGateway(available=False)
        monkeypatch.setattr(fake_container.vectors, "health", lambda: False)
        response = client.get("/health/capabilities")

    assert response.status_code == 200
    capabilities = response.json()["capabilities"]
    assert capabilities["embeddings"]["available"] is False
    assert capabilities["embeddings"]["reason"]
    assert capabilities["ai"]["available"] is False
    assert capabilities["ai"]["reason"]
    assert capabilities["vector"]["available"] is False
    assert capabilities["vector"]["reason"]


def test_health_ready_returns_503_when_migration_check_raises(fake_container, monkeypatch) -> None:
    app = create_app(container=fake_container)

    with TestClient(app) as client:

        def boom():
            raise RuntimeError("neo4j unreachable")

        monkeypatch.setattr(fake_container.neo4j_migrations, "status", boom)
        response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["neo4j_migrations"]["ok"] is False
    assert "RuntimeError" in body["checks"]["neo4j_migrations"]["detail"]


def test_health_ready_returns_503_when_graph_ping_raises(fake_container, monkeypatch) -> None:
    app = create_app(container=fake_container)

    with TestClient(app) as client:

        def boom():
            raise RuntimeError("driver down")

        monkeypatch.setattr(fake_container.graph, "ping", boom)
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["neo4j"]["ok"] is False


def test_health_ready_returns_503_when_postgres_checks_raise(fake_container, monkeypatch) -> None:
    app = create_app(container=fake_container)

    with TestClient(app) as client:

        async def boom():
            raise RuntimeError("pg down")

        monkeypatch.setattr(fake_container.database, "ready", boom)
        monkeypatch.setattr(fake_container.database, "migration_status", boom)
        response = client.get("/health/ready")

    assert response.status_code == 503
    checks = response.json()["checks"]
    assert checks["postgres"]["ok"] is False
    assert checks["postgres_migrations"]["ok"] is False


def test_health_capabilities(fake_container) -> None:
    app = create_app(container=fake_container)

    with TestClient(app) as client:
        response = client.get("/health/capabilities")

    assert response.status_code == 200
    capabilities = response.json()["capabilities"]
    assert capabilities["embeddings"]["available"] is True
    assert capabilities["ai"]["available"] is True
    assert capabilities["telemetry"]["sentry_backend"] is False


def test_startup_fails_when_migrations_pending(fake_container) -> None:
    fake_container.neo4j_migrations = FakeMigrationVerifier(current=False, detail="pending")
    app = create_app(container=fake_container)

    with pytest.raises(RuntimeError, match="not migrated"):
        with TestClient(app):
            pass


def test_startup_fails_when_postgres_migrations_pending(fake_container) -> None:
    from tests.doubles import FakeDatabase

    fake_container.database = FakeDatabase(
        ready=True, current=False, detail="current=None head=0001"
    )
    app = create_app(container=fake_container)

    with pytest.raises(RuntimeError, match="PostgreSQL"):
        with TestClient(app):
            pass

    assert fake_container.database.closed is True
    assert fake_container.graph.close_calls == 1


# ------------------------------------------------- optional parts: worker and search store


def test_ready_reports_the_job_worker_and_the_search_store(fake_container) -> None:
    app = create_app(container=fake_container)

    with TestClient(app) as client:
        body = client.get("/health/ready").json()

    worker = body["checks"]["job_worker"]
    assert worker["optional"] is True and worker["ok"] is True
    assert worker["waiting"] == 0 and worker["oldest_waiting_seconds"] is None
    assert body["checks"]["vector_store"] == {"ok": True, "optional": True}
    assert body["checks"]["postgres"]["optional"] is False
    assert body["reasons"] == []


def test_a_stopped_worker_degrades_but_does_not_fail(fake_container) -> None:
    app = create_app(container=fake_container)

    with TestClient(app) as client:
        # The worker should be running on this server, and is not.
        fake_container.job_runner.autostart = True
        response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["job_worker"]["ok"] is False
    assert body["checks"]["job_worker"]["running"] is False
    assert any("background worker is not running" in reason for reason in body["reasons"])


def test_work_left_waiting_too_long_degrades(fake_container, monkeypatch) -> None:
    import asyncio
    from datetime import timedelta

    from backend.domain.identity import AuthMode, utcnow
    from backend.domain.jobs import AUTHORIZATION_POLICY_VERSION, Job, JobStatus
    from backend.services.job_runner import JobRunner

    asyncio.run(
        fake_container.jobs.create(
            Job(
                workspace_id="ws",
                project_id="pr",
                id="job_old",
                kind="scene_draft",
                status=JobStatus.QUEUED,
                actor_user_id="usr",
                auth_mode=AuthMode.LOCAL,
                policy_version=AUTHORIZATION_POLICY_VERSION,
                payload={"direction": "Mara refuses the ship."},
                created_at=utcnow() - timedelta(minutes=12),
            )
        )
    )
    monkeypatch.setattr(JobRunner, "running", property(lambda self: True))
    app = create_app(container=fake_container)

    with TestClient(app) as client:
        fake_container.job_runner.autostart = True
        response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    worker = body["checks"]["job_worker"]
    assert body["status"] == "degraded" and worker["ok"] is False
    assert worker["waiting"] == 1 and worker["oldest_waiting_seconds"] >= 12 * 60
    assert "12 minutes" in worker["detail"]
    # Counts only: nothing a job is about.
    assert "Mara" not in response.text


def test_a_search_store_that_is_down_degrades_with_a_plain_reason(
    fake_container, monkeypatch
) -> None:
    app = create_app(container=fake_container)

    def boom() -> bool:
        raise RuntimeError("connection to postgresql://novel:hunter2@db:5432 refused")

    with TestClient(app) as client:
        monkeypatch.setattr(fake_container.vectors, "health", boom)
        response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["vector_store"]["ok"] is False
    assert "Search by name still works" in body["checks"]["vector_store"]["detail"]
    # The store's address and password never appear.
    assert "hunter2" not in response.text and "postgresql" not in response.text


def test_a_mismatched_embedding_size_degrades(fake_container, monkeypatch) -> None:
    app = create_app(container=fake_container)

    with TestClient(app) as client:
        monkeypatch.setattr(
            fake_container.vectors, "dimension_supported", lambda: False, raising=False
        )
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert "embedding size" in response.json()["checks"]["vector_store"]["detail"]


def test_a_required_store_down_is_unavailable_even_with_optional_parts_down(
    fake_container, monkeypatch
) -> None:
    from tests.doubles import FakeDatabase

    app = create_app(container=fake_container)

    with TestClient(app) as client:
        fake_container.database = FakeDatabase(ready=False)
        monkeypatch.setattr(fake_container.vectors, "health", lambda: False)
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"


def test_the_worker_started_with_the_app_is_reported_running(fake_container) -> None:
    fake_container.job_runner.autostart = True
    app = create_app(container=fake_container)

    with TestClient(app) as client:
        body = client.get("/health/ready").json()

    assert body["status"] == "ready"
    assert body["checks"]["job_worker"]["running"] is True
    assert "detail" not in body["checks"]["job_worker"]


# ------------------------------------------- what a hosted server shows without the token


def _old_job(container) -> None:
    import asyncio
    from datetime import timedelta

    from backend.domain.identity import AuthMode, utcnow
    from backend.domain.jobs import AUTHORIZATION_POLICY_VERSION, Job, JobStatus

    asyncio.run(
        container.jobs.create(
            Job(
                workspace_id="ws",
                project_id="pr",
                id="job_old",
                kind="scene_draft",
                status=JobStatus.QUEUED,
                actor_user_id="usr",
                auth_mode=AuthMode.LOCAL,
                policy_version=AUTHORIZATION_POLICY_VERSION,
                payload={},
                created_at=utcnow() - timedelta(minutes=12),
            )
        )
    )


def test_a_hosted_server_shows_no_counts_without_the_metrics_token(
    hosted_container, monkeypatch
) -> None:
    from backend.services.job_runner import JobRunner

    _old_job(hosted_container)
    monkeypatch.setattr(JobRunner, "running", property(lambda self: True))
    hosted_container.settings.METRICS_TOKEN = "metrics-secret"
    app = create_app(container=hosted_container)

    with TestClient(app) as client:
        hosted_container.job_runner.autostart = True
        anonymous = client.get("/health/ready")
        wrong = client.get("/health/ready", headers={"Authorization": "Bearer nope"})
        operator = client.get("/health/ready", headers={"Authorization": "Bearer metrics-secret"})

    for response in (anonymous, wrong):
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"
        assert body["checks"]["job_worker"] == {"ok": False, "optional": True}
        assert "waiting" not in response.text and "minutes" not in response.text
        assert not any(character.isdigit() for character in " ".join(body["reasons"]))
        assert any("waited too long" in reason for reason in body["reasons"])
    worker = operator.json()["checks"]["job_worker"]
    assert worker["waiting"] == 1 and worker["oldest_waiting_seconds"] >= 12 * 60
    assert "12 minutes" in worker["detail"]


def test_a_hosted_server_without_a_metrics_token_never_shows_counts(hosted_container) -> None:
    app = create_app(container=hosted_container)

    with TestClient(app) as client:
        body = client.get("/health/ready", headers={"Authorization": "Bearer "}).json()

    assert body["status"] == "ready"
    assert body["checks"]["job_worker"] == {"ok": True, "optional": True}
    assert body["reasons"] == []
