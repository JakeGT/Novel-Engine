import pytest

pytestmark = pytest.mark.contract


def test_projection_status_reports_watermark(auth_client, fake_container) -> None:
    project_id = fake_container.settings.AUTH_LOCAL_PROJECT_ID

    response = auth_client.get(f"/api/v1/projects/{project_id}/projection")

    assert response.status_code == 200
    body = response.json()
    assert body["projection"] == "graph"
    assert body["head_revision"] == 0
    assert body["applied_revision"] == 0
    assert body["lag"] == 0
    assert body["current"] is True


def test_projection_status_requires_session(api_client, fake_container) -> None:
    project_id = fake_container.settings.AUTH_LOCAL_PROJECT_ID

    response = api_client.get(f"/api/v1/projects/{project_id}/projection")

    assert response.status_code == 401
