import pytest

pytestmark = pytest.mark.contract


def test_credentials_require_session(api_client) -> None:
    assert api_client.get("/api/v1/credentials").status_code == 401


def test_credential_flow_never_returns_key(auth_client) -> None:
    created = auth_client.post(
        "/api/v1/credentials",
        json={"provider": "openrouter", "label": "mine", "key": "sk-or-v1-supersecret-value"},
    )
    assert created.status_code == 201
    body = created.json()
    assert "key" not in body
    assert "supersecret" not in created.text
    assert body["key_last4"] == "alue"

    listed = auth_client.get("/api/v1/credentials")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [body["id"]]

    revoked = auth_client.post(f"/api/v1/credentials/{body['id']}/revoke")
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"

    deleted = auth_client.delete(f"/api/v1/credentials/{body['id']}")
    assert deleted.status_code == 204
