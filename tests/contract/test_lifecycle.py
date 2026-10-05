import pytest
from fastapi.testclient import TestClient

from backend.main import create_app

pytestmark = pytest.mark.contract


def test_resources_open_and_close_once(fake_container) -> None:
    app = create_app(container=fake_container)

    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert fake_container.graph.close_calls == 1
    assert fake_container.database.closed is True
    assert fake_container.ai.closed is True
    assert fake_container.neo4j_migrations.calls >= 1


def test_startup_failure_closes_opened_resources(fake_container) -> None:
    from tests.doubles import FakeMigrationVerifier

    fake_container.neo4j_migrations = FakeMigrationVerifier(current=False, detail="pending")
    app = create_app(container=fake_container)

    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass

    assert fake_container.graph.close_calls == 1
    assert fake_container.database.closed is True
