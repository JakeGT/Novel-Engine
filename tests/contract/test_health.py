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
    assert response.json()["status"] == "degraded"


def test_health_ready_degrades_when_store_down(fake_container) -> None:
    from tests.doubles import FakeDatabase

    app = create_app(container=fake_container)

    with TestClient(app) as client:
        fake_container.database = FakeDatabase(ready=False)
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["postgres"]["ok"] is False


def test_health_capabilities(fake_container) -> None:
    app = create_app(container=fake_container)

    with TestClient(app) as client:
        response = client.get("/health/capabilities")

    assert response.status_code == 200
    capabilities = response.json()["capabilities"]
    assert capabilities["embeddings"]["available"] is True
    assert capabilities["ai"]["available"] is True


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
