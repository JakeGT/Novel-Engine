"""A model on the author's own computer, through the connector (SCN-002A, ADR-025).

Registration follows the device authorization grant (RFC 8628); the connector then
authenticates with a device credential, polls for requests and answers them. A fake
device here plays the connector's part over HTTP.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from backend.adapters.ai import OpenAIGateway
from backend.core.security import hash_token
from backend.domain import connector as c
from backend.domain.identity import utcnow
from backend.main import create_app
from backend.ports import AIDeviceUnavailableError
from backend.services.canon_character_workflow import CharacterQuestion, CharacterQuestionSheet
from tests.contract.test_workspace_scope import (
    _create_project,
    _create_workspace,
    _hosted_login,
    _publish_schema,
)

pytestmark = pytest.mark.contract

LOCAL = "local-workspace"
CAPABILITIES = {
    "server": "ollama",
    "models": [
        {"name": "llama3.2:3b", "context_tokens": 8192},
        {"name": "qwen3:8b", "context_tokens": None},
    ],
    "structured_output": True,
}


def _unthrottle(container) -> None:
    for code in container.connector_store.codes.values():
        code.last_polled_at = None


@pytest.fixture
def device_http(fake_container):
    """The connector's side: no session, no cookies."""
    with TestClient(create_app(container=fake_container)) as client:
        yield client


def _start(client, name: str = "Jake's laptop", version: str = "1.0.0"):
    return client.post("/api/v1/connector/codes", json={"name": name, "version": version})


def _token(client, device_code: str):
    return client.post("/api/v1/connector/token", json={"device_code": device_code})


def _connect(app_client, device_client, container, headers=None, name="Jake's laptop"):
    """Register a computer the whole way; returns (device id, credential)."""
    started = _start(device_client, name)
    assert started.status_code == 201, started.text
    code = started.json()
    confirmed = app_client.post(
        "/api/v1/ai/connector/codes/decide",
        json={"code": code["user_code"], "approve": True},
        headers=headers or {},
    )
    assert confirmed.status_code == 200, confirmed.text
    _unthrottle(container)
    issued = _token(device_client, code["device_code"])
    assert issued.status_code == 200, issued.text
    body = issued.json()
    return body["device_id"], body["credential"]


def _auth(credential: str, version: str = "1.0.0") -> dict[str, str]:
    return {"Authorization": f"Bearer {credential}", "X-Connector-Version": version}


def _poll(client, credential, *, capabilities=None, version="1.0.0", take=True):
    body: dict[str, Any] = {"wait": 0.2, "take": take}
    if capabilities is not None:
        body["capabilities"] = capabilities
    return client.post("/api/v1/connector/poll", json=body, headers=_auth(credential, version))


# --- registration ------------------------------------------------------------------------


def test_the_site_says_which_connector_versions_it_accepts(device_http):
    response = device_http.get("/api/v1/connector/version")
    assert response.status_code == 200
    assert response.json() == {
        "minimum": c.MINIMUM_CONNECTOR_VERSION,
        "latest": c.LATEST_CONNECTOR_VERSION,
        "protocol": c.PROTOCOL_VERSION,
    }


def test_the_connector_package_is_the_version_the_site_was_written_for():
    import connector

    assert connector.__version__ == c.LATEST_CONNECTOR_VERSION


def test_a_computer_connects_with_a_short_code_an_owner_confirms(
    auth_client, device_http, fake_container
):
    started = _start(device_http)
    assert started.status_code == 201
    code = started.json()
    # RFC 8628 section 6.1: eight consonants, shown in two halves.
    assert len(code["user_code"]) == 9 and code["user_code"][4] == "-"
    assert set(code["user_code"].replace("-", "")) <= set(c.USER_CODE_ALPHABET)
    assert code["interval"] == 5 and code["expires_in"] == 600
    # Only the digest of the device code is kept.
    stored = list(fake_container.connector_store.codes.values())[0]
    assert stored.device_code_hash == hash_token(code["device_code"])

    pending = _token(device_http, code["device_code"])
    assert pending.status_code == 400
    assert pending.json()["error"] == "authorization_pending"

    # The owner sees what is asking before saying yes (RFC 8628 section 5.4); dashes,
    # spaces and small letters are forgiven.
    typed = code["user_code"].lower().replace("-", " ")
    seen = auth_client.post("/api/v1/ai/connector/codes/check", json={"code": typed})
    assert seen.status_code == 200, seen.text
    assert seen.json()["name"] == "Jake's laptop"
    assert seen.json()["connector_version"] == "1.0.0"

    decided = auth_client.post(
        "/api/v1/ai/connector/codes/decide", json={"code": typed, "approve": True}
    )
    assert decided.status_code == 200
    _unthrottle(fake_container)
    issued = _token(device_http, code["device_code"])
    assert issued.status_code == 200
    assert issued.headers["cache-control"] == "no-store"
    credential = issued.json()["credential"]
    assert c.credential_well_formed(credential)

    # Used once: the same device code never gives a second credential.
    _unthrottle(fake_container)
    assert _token(device_http, code["device_code"]).json()["error"] == "invalid_grant"

    devices = auth_client.get("/api/v1/ai/connector/devices").json()
    assert [d["name"] for d in devices] == ["Jake's laptop"]
    assert devices[0]["status"] == "connected" and devices[0]["online"] is True
    assert devices[0]["up_to_date"] is True and devices[0]["in_use"] is False
    # The credential itself is never stored, only its digest.
    stored_device = fake_container.connector_store.devices_by_id[devices[0]["id"]]
    assert stored_device.credential_hash == hash_token(credential)
    assert credential not in json.dumps(devices)


def test_polling_for_the_credential_too_quickly_is_told_to_slow_down(device_http):
    code = _start(device_http).json()
    assert _token(device_http, code["device_code"]).json()["error"] == "authorization_pending"
    slowed = _token(device_http, code["device_code"])
    assert slowed.json() == {"error": "slow_down", "interval": 10}


def test_a_code_turned_down_or_run_out_connects_nothing(auth_client, device_http, fake_container):
    turned_down = _start(device_http).json()
    auth_client.post(
        "/api/v1/ai/connector/codes/decide",
        json={"code": turned_down["user_code"], "approve": False},
    )
    _unthrottle(fake_container)
    assert _token(device_http, turned_down["device_code"]).json()["error"] == "access_denied"

    late = _start(device_http, "Old laptop").json()
    for code in fake_container.connector_store.codes.values():
        if code.device_name == "Old laptop":
            code.expires_at = utcnow() - timedelta(seconds=1)
    refused = auth_client.post(
        "/api/v1/ai/connector/codes/decide", json={"code": late["user_code"], "approve": True}
    )
    assert refused.status_code == 404
    assert "ran out" in refused.json()["detail"]
    assert _token(device_http, late["device_code"]).json()["error"] == "expired_token"
    assert auth_client.get("/api/v1/ai/connector/devices").json() == []


def test_wrong_codes_are_limited(auth_client):
    for _ in range(c.WRONG_CODES_PER_WINDOW):
        wrong = auth_client.post("/api/v1/ai/connector/codes/check", json={"code": "BBBB-BBBB"})
        assert wrong.status_code == 404
    limited = auth_client.post("/api/v1/ai/connector/codes/check", json={"code": "BBBB-BBBB"})
    assert limited.status_code == 429


def test_once_wrong_codes_run_out_even_the_right_code_waits(auth_client, device_http):
    started = _start(device_http).json()
    for _ in range(c.WRONG_CODES_PER_WINDOW):
        auth_client.post("/api/v1/ai/connector/codes/check", json={"code": "BBBB-BBBB"})
    # A guess that happens to be right is refused like the rest until the window passes.
    for path in ("check", "decide"):
        right = auth_client.post(
            f"/api/v1/ai/connector/codes/{path}",
            json={"code": started["user_code"], "approve": True}
            if path == "decide"
            else {"code": started["user_code"]},
        )
        assert right.status_code == 429


def test_once_failed_sign_ins_run_out_even_a_good_credential_waits(
    auth_client, device_http, fake_container
):
    from backend.api.v1.connector import FAILED_PER_MINUTE

    _, credential = _connect(auth_client, device_http, fake_container)
    for _ in range(FAILED_PER_MINUTE):
        assert _poll(device_http, "nvldev_" + "a" * 49).status_code == 401
    assert _poll(device_http, credential).status_code == 429


def test_a_computers_name_cannot_hide_characters(auth_client, device_http):
    started = _start(device_http, name="Jake\u2019s laptop\u202e\u200b\n").json()
    seen = auth_client.post("/api/v1/ai/connector/codes/check", json={"code": started["user_code"]})
    assert seen.json()["name"] == "Jake\u2019s laptop"


def test_a_connector_older_than_the_site_accepts_is_refused(
    auth_client, device_http, fake_container
):
    assert _start(device_http, version="0.9.0").status_code == 426
    _, credential = _connect(auth_client, device_http, fake_container)
    old = _poll(device_http, credential, version="0.9.0")
    assert old.status_code == 426
    assert "too old" in old.json()["detail"]
    assert _poll(device_http, credential).status_code == 200


def test_a_made_up_or_missing_credential_is_refused(device_http):
    assert _poll(device_http, "nvldev_" + "a" * 49).status_code == 401
    assert device_http.post("/api/v1/connector/poll", json={}).status_code == 401


# --- the channel ---------------------------------------------------------------------------


class Sheet(BaseModel):
    title: str
    count: int


def _relay_in_thread(container, call):
    """Run a gateway call on its own event loop, as a request on the site would."""
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["value"] = asyncio.run(call())
        except BaseException as error:  # noqa: BLE001 - handed back to the test
            outcome["error"] = error

    thread = threading.Thread(target=run)
    thread.start()
    return thread, outcome


def _take(device_http, credential, *, attempts: int = 100):
    for _ in range(attempts):
        polled = _poll(device_http, credential)
        assert polled.status_code == 200, polled.text
        request = polled.json()["request"]
        if request is not None:
            return request
        time.sleep(0.02)
    raise AssertionError("no request reached the device")


def _completion(content: str, model: str = "llama3.2:3b") -> dict:
    return {
        "id": "chatcmpl-local",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 40, "completion_tokens": 9, "cost": 3.5},
    }


def test_a_structured_request_goes_to_the_computer_and_its_answer_comes_back(
    auth_client, device_http, fake_container
):
    device_id, credential = _connect(auth_client, device_http, fake_container)
    _poll(device_http, credential, capabilities=CAPABILITIES)
    gateway = OpenAIGateway(api_key="", devices=fake_container.connector_service)
    address = c.device_address(LOCAL, device_id)
    capabilities = gateway.capabilities(base_url=address)
    assert capabilities.provider == c.MY_COMPUTER
    assert capabilities.structured_output is True
    assert not capabilities.sees_images

    thread, outcome = _relay_in_thread(
        fake_container,
        lambda: gateway.generate_structured(
            messages=[{"role": "user", "content": "Name the harbour."}],
            response_format=Sheet,
            model="llama3.2:3b",
            base_url=address,
        ),
    )
    request = _take(device_http, credential)
    assert request["method"] == "POST" and request["path"] == "/chat/completions"
    assert request["body"]["model"] == "llama3.2:3b"
    # The schema goes with it, named for the answer's shape.
    assert request["body"]["response_format"]["json_schema"]["name"] == "Sheet"
    answered = device_http.post(
        f"/api/v1/connector/requests/{request['id']}/answer",
        json={"status": 200, "body": _completion('{"title": "Gull Point", "count": 2}')},
        headers=_auth(credential),
    )
    assert answered.status_code == 204
    thread.join(10)
    assert "error" not in outcome, outcome
    result = outcome["value"]
    assert result.parsed == Sheet(title="Gull Point", count=2)
    assert result.usage.prompt_tokens == 40
    # A computer costs nothing per call: a cost it reports is not kept.
    assert result.usage.cost_usd is None

    # The words are cleared once the answer was read; counts stay.
    stored = fake_container.connector_store.requests[request["id"]]
    assert stored.payload is None and stored.result is None
    assert stored.status is c.RequestStatus.DONE
    assert (stored.prompt_tokens, stored.completion_tokens) == (40, 9)
    # Answering twice is refused.
    again = device_http.post(
        f"/api/v1/connector/requests/{request['id']}/answer",
        json={"status": 200, "body": _completion("{}")},
        headers=_auth(credential),
    )
    assert again.status_code == 404


def test_a_computer_whose_server_ignores_schemas_is_asked_in_words(
    auth_client, device_http, fake_container
):
    device_id, credential = _connect(auth_client, device_http, fake_container)
    _poll(device_http, credential, capabilities={**CAPABILITIES, "structured_output": False})
    gateway = OpenAIGateway(api_key="", devices=fake_container.connector_service)
    thread, outcome = _relay_in_thread(
        fake_container,
        lambda: gateway.generate_structured(
            messages=[{"role": "user", "content": "Name it."}],
            response_format=Sheet,
            base_url=c.device_address(LOCAL, device_id),
        ),
    )
    request = _take(device_http, credential)
    assert "response_format" not in request["body"]
    device_http.post(
        f"/api/v1/connector/requests/{request['id']}/answer",
        json={"status": 200, "body": _completion('Here: {"title": "Quay", "count": 1}')},
        headers=_auth(credential),
    )
    thread.join(10)
    assert outcome["value"].parsed == Sheet(title="Quay", count=1)


def test_a_failure_on_the_computer_is_said_in_plain_words(auth_client, device_http, fake_container):
    device_id, credential = _connect(auth_client, device_http, fake_container)
    _poll(device_http, credential, capabilities=CAPABILITIES)
    gateway = OpenAIGateway(api_key="", devices=fake_container.connector_service)
    thread, outcome = _relay_in_thread(
        fake_container,
        lambda: gateway.generate_text(
            messages=[{"role": "user", "content": "Write the scene."}],
            max_tokens=100,
            base_url=c.device_address(LOCAL, device_id),
        ),
    )
    request = _take(device_http, credential)
    device_http.post(
        f"/api/v1/connector/requests/{request['id']}/answer",
        # An unknown code is treated as the server's failure, never shown as given.
        json={"failure": "local_server_unreachable"},
        headers=_auth(credential),
    )
    thread.join(10)
    error = outcome["error"]
    assert isinstance(error, AIDeviceUnavailableError)
    assert "could not reach the model server" in str(error)


def test_a_computer_that_is_offline_or_does_not_pick_up_fails_at_once_and_plainly(
    auth_client, device_http, fake_container
):
    device_id, credential = _connect(auth_client, device_http, fake_container)
    service = fake_container.connector_service
    address = c.device_address(LOCAL, device_id)
    gateway = OpenAIGateway(api_key="", devices=service)

    device = fake_container.connector_store.devices_by_id[device_id]
    device.last_seen_at = utcnow() - c.OFFLINE_AFTER - timedelta(seconds=1)
    with pytest.raises(AIDeviceUnavailableError, match="not connected right now"):
        asyncio.run(
            gateway.generate_text(
                messages=[{"role": "user", "content": "x"}], max_tokens=10, base_url=address
            )
        )
    assert fake_container.connector_store.requests == {}

    device.last_seen_at = utcnow()
    service._pickup = 0.05
    with pytest.raises(AIDeviceUnavailableError, match="did not pick up"):
        asyncio.run(
            gateway.generate_text(
                messages=[{"role": "user", "content": "x"}], max_tokens=10, base_url=address
            )
        )
    (left,) = fake_container.connector_store.requests.values()
    assert left.status is c.RequestStatus.EXPIRED and left.payload is None


def test_only_the_two_relayed_calls_reach_a_computer(fake_container):
    with pytest.raises(Exception, match="cannot be sent"):
        asyncio.run(
            fake_container.connector_service.relay(
                LOCAL, "dev_x", method="POST", path="/embeddings", body={}
            )
        )


def test_one_computer_cannot_take_or_answer_another_computers_request(
    auth_client, device_http, fake_container
):
    first, first_credential = _connect(auth_client, device_http, fake_container, name="One")
    _, second_credential = _connect(auth_client, device_http, fake_container, name="Two")
    _poll(device_http, first_credential, capabilities=CAPABILITIES)
    gateway = OpenAIGateway(api_key="", devices=fake_container.connector_service)
    thread, outcome = _relay_in_thread(
        fake_container,
        lambda: gateway.generate_text(
            messages=[{"role": "user", "content": "x"}],
            max_tokens=10,
            base_url=c.device_address(LOCAL, first),
        ),
    )
    time.sleep(0.1)
    assert _poll(device_http, second_credential).json()["request"] is None
    request = _take(device_http, first_credential)
    stolen = device_http.post(
        f"/api/v1/connector/requests/{request['id']}/answer",
        json={"status": 200, "body": _completion("Nope.")},
        headers=_auth(second_credential),
    )
    assert stolen.status_code == 404
    device_http.post(
        f"/api/v1/connector/requests/{request['id']}/answer",
        json={"status": 200, "body": _completion("The tide turned.")},
        headers=_auth(first_credential),
    )
    thread.join(10)
    assert outcome["value"].text == "The tide turned."


def test_a_disconnected_computer_stops_at_once(auth_client, device_http, fake_container):
    device_id, credential = _connect(auth_client, device_http, fake_container)
    gone = auth_client.post(f"/api/v1/ai/connector/devices/{device_id}/disconnect")
    assert gone.status_code == 200
    assert gone.json()["status"] == "disconnected"
    assert gone.json()["disconnected_because"] == "owner"
    assert _poll(device_http, credential).status_code == 401
    assert (
        auth_client.post(f"/api/v1/ai/connector/devices/{device_id}/disconnect").status_code == 404
    )
    with pytest.raises(AIDeviceUnavailableError, match="no longer connected"):
        asyncio.run(
            fake_container.connector_service.relay(
                LOCAL, device_id, method="GET", path="/models", body=None
            )
        )


def test_the_credential_is_rotated_daily_and_the_old_one_stops_after_a_short_grace(
    auth_client, device_http, fake_container
):
    device_id, credential = _connect(auth_client, device_http, fake_container)
    assert _poll(device_http, credential).json()["rotate"] is False
    device = fake_container.connector_store.devices_by_id[device_id]
    device.credential_issued_at = utcnow() - c.ROTATE_AFTER - timedelta(minutes=1)
    assert _poll(device_http, credential).json()["rotate"] is True

    rotated = device_http.post("/api/v1/connector/rotate", headers=_auth(credential))
    assert rotated.status_code == 200
    fresh = rotated.json()["credential"]
    assert fresh != credential and c.credential_well_formed(fresh)
    assert _poll(device_http, fresh).json()["rotate"] is False
    # The old one still works for a few minutes, in case the answer was lost, and asks
    # for another rotation...
    assert _poll(device_http, credential).json()["rotate"] is True
    # ... and not after the grace window.
    device.previous_valid_until = utcnow() - timedelta(seconds=1)
    assert _poll(device_http, credential).status_code == 401
    assert _poll(device_http, fresh).status_code == 200

    # A credential not rotated for 30 days stops working, and the computer is disconnected.
    device.credential_issued_at = utcnow() - c.CREDENTIAL_MAX_AGE - timedelta(minutes=1)
    assert _poll(device_http, fresh).status_code == 401
    listed = auth_client.get("/api/v1/ai/connector/devices").json()
    assert listed[0]["disconnected_because"] == "expired"


def test_a_copied_old_credential_cannot_take_the_computer_over(
    auth_client, device_http, fake_container
):
    device_id, first = _connect(auth_client, device_http, fake_container)
    second = device_http.post("/api/v1/connector/rotate", headers=_auth(first)).json()["credential"]
    assert _poll(device_http, second).status_code == 200
    # Someone with a copy of the replaced credential rotates inside the grace window: both
    # copies stop, and the owner sees why.
    stolen = device_http.post("/api/v1/connector/rotate", headers=_auth(first))
    assert stolen.status_code == 401
    assert _poll(device_http, second).status_code == 401
    (listed,) = auth_client.get("/api/v1/ai/connector/devices").json()
    assert listed["id"] == device_id and listed["disconnected_because"] == "reused"


def test_an_answer_larger_than_the_site_keeps_is_refused_and_ends_the_request(
    auth_client, device_http, fake_container, monkeypatch
):
    device_id, credential = _connect(auth_client, device_http, fake_container)
    asyncio.run(
        fake_container.connector_store.enqueue(
            c.ChannelRequest(
                id="creq_big",
                workspace_id=LOCAL,
                device_id=device_id,
                actor_user_id="u",
                kind="GET /models",
                status=c.RequestStatus.WAITING,
                created_at=utcnow(),
                payload={"method": "GET", "path": "/models", "body": None},
            )
        )
    )
    assert _take(device_http, credential)["id"] == "creq_big"
    monkeypatch.setattr(c, "MAX_ANSWER_BYTES", 2000)
    refused = device_http.post(
        "/api/v1/connector/requests/creq_big/answer",
        json={"status": 200, "body": {"data": [{"id": "m" * 3000}]}},
        headers=_auth(credential),
    )
    assert refused.status_code == 413
    stored = fake_container.connector_store.requests["creq_big"]
    assert stored.failure == "too_large" and stored.result is None


def test_a_computer_reports_what_it_can_do_and_bad_parts_are_dropped(
    auth_client, device_http, fake_container
):
    _, credential = _connect(auth_client, device_http, fake_container)
    reported = {
        "server": "something-else",
        "models": [
            {"name": "llama3.2:3b", "context_tokens": 8192},
            {"name": "llama3.2:3b", "context_tokens": 1},
            {"name": "", "context_tokens": 5},
            {"name": "phi4", "context_tokens": -4},
        ],
        "structured_output": True,
    }
    assert _poll(device_http, credential, capabilities=reported).status_code == 200
    (device,) = auth_client.get("/api/v1/ai/connector/devices").json()
    assert device["server"] == "A model server"
    assert device["models"] == [
        {"name": "llama3.2:3b", "context_tokens": 8192},
        {"name": "phi4", "context_tokens": None},
    ]


def test_a_busy_computer_stays_online_without_taking_more_work(
    auth_client, device_http, fake_container
):
    device_id, credential = _connect(auth_client, device_http, fake_container)
    device = fake_container.connector_store.devices_by_id[device_id]
    device.last_seen_at = utcnow() - timedelta(seconds=60)
    asyncio.run(
        fake_container.connector_store.enqueue(
            c.ChannelRequest(
                id="creq_waiting",
                workspace_id=LOCAL,
                device_id=device_id,
                actor_user_id="u",
                kind="GET /models",
                status=c.RequestStatus.WAITING,
                created_at=utcnow(),
                payload={"method": "GET", "path": "/models", "body": None},
            )
        )
    )
    beat = _poll(device_http, credential, take=False)
    assert beat.json()["request"] is None
    assert device.online(utcnow())
    assert fake_container.connector_store.requests["creq_waiting"].status is c.RequestStatus.WAITING


# --- the assistant's route (hosted) --------------------------------------------------------


SHEET = CharacterQuestionSheet(
    questions=[CharacterQuestion(id="q1", question="Where?", category="place")]
)


def _studio(client, container, slug: str):
    csrf = _hosted_login(client, container)
    workspace_id = _create_workspace(client, csrf, slug)
    project_id = _create_project(client, csrf, workspace_id, "novel")
    _publish_schema(client, csrf, workspace_id, project_id)
    return {"X-CSRF-Token": csrf, "X-Workspace-Id": workspace_id}, workspace_id, project_id


def _ask(client, headers, project_id: str):
    return client.post(
        f"/api/v1/projects/{project_id}/assist/questions",
        json={"description": "A harbor pilot."},
        headers=headers,
    )


@pytest.fixture
def hosted_device_http(hosted_container):
    with TestClient(
        create_app(container=hosted_container), base_url="https://testserver"
    ) as client:
        yield client


def test_choosing_a_computer_sends_the_assistant_to_it_and_never_to_a_key(
    hosted_client, hosted_container, hosted_device_http
):
    headers, workspace_id, project_id = _studio(hosted_client, hosted_container, "mine")
    hosted_client.post(
        "/api/v1/credentials",
        json={"provider": "anthropic", "label": "anthropic", "key": "sk-ant-secret-key-000001"},
        headers=headers,
    )
    device_id, credential = _connect(
        hosted_client, hosted_device_http, hosted_container, headers=headers
    )
    _poll(hosted_device_http, credential, capabilities=CAPABILITIES)

    unlisted = hosted_client.post(
        f"/api/v1/ai/connector/devices/{device_id}/use",
        json={"model": "gpt-9"},
        headers=headers,
    )
    assert unlisted.status_code == 400
    chosen = hosted_client.post(
        f"/api/v1/ai/connector/devices/{device_id}/use",
        json={"model": "llama3.2:3b"},
        headers=headers,
    )
    assert chosen.status_code == 200 and chosen.json()["in_use"] is True
    assert hosted_client.get("/api/v1/ai/settings", headers=headers).json() == {
        "provider": "my_computer",
        "model": "llama3.2:3b",
        "chosen": True,
    }

    hosted_container.ai.queue(SHEET)
    assert _ask(hosted_client, headers, project_id).status_code == 200
    call = hosted_container.ai.calls[-1]
    assert call["base_url"] == c.device_address(workspace_id, device_id)
    assert call["api_key"] == "connector"
    assert call["route_model"] == "llama3.2:3b"
    # Spending controls still apply, and the use is recorded as the computer's.
    records = [r for r in hosted_container.usage.records if r.workspace_id == workspace_id]
    assert records and records[-1].provider == c.MY_COMPUTER
    assert records[-1].provider_cost_usd is None

    # Disconnected: the assistant stops and says so, and does not move to the key.
    hosted_client.post(f"/api/v1/ai/connector/devices/{device_id}/disconnect", headers=headers)
    refused = _ask(hosted_client, headers, project_id)
    assert refused.status_code == 503
    assert refused.json()["code"] == "device_unavailable"
    assert "no longer connected" in refused.json()["detail"]

    # Stopping its use goes back to the key.
    assert hosted_client.delete("/api/v1/ai/connector/use", headers=headers).status_code == 204
    assert hosted_client.get("/api/v1/ai/settings", headers=headers).json()["provider"] == (
        "anthropic"
    )


def test_a_computer_belongs_to_the_workspace_it_was_confirmed_in(
    hosted_client, hosted_container, hosted_device_http
):
    headers_a, workspace_a, _ = _studio(hosted_client, hosted_container, "first")
    workspace_b = _create_workspace(hosted_client, headers_a["X-CSRF-Token"], "second")
    headers_b = {**headers_a, "X-Workspace-Id": workspace_b}
    device_id, credential = _connect(
        hosted_client, hosted_device_http, hosted_container, headers=headers_a
    )
    _poll(hosted_device_http, credential, capabilities=CAPABILITIES)

    assert hosted_client.get("/api/v1/ai/connector/devices", headers=headers_b).json() == []
    assert (
        hosted_client.post(
            f"/api/v1/ai/connector/devices/{device_id}/use",
            json={"model": "llama3.2:3b"},
            headers=headers_b,
        ).status_code
        == 404
    )
    assert (
        hosted_client.post(
            f"/api/v1/ai/connector/devices/{device_id}/disconnect", headers=headers_b
        ).status_code
        == 404
    )
    # A request for another workspace's address is refused, whatever the address says.
    with pytest.raises(AIDeviceUnavailableError):
        asyncio.run(
            hosted_container.connector_service.relay(
                workspace_b, device_id, method="GET", path="/models", body=None
            )
        )
    # Not a member: no code can be confirmed there.
    started = _start(hosted_device_http).json()
    stranger = hosted_client.post(
        "/api/v1/ai/connector/codes/decide",
        json={"code": started["user_code"], "approve": True},
        headers={**headers_a, "X-Workspace-Id": "ws-not-mine"},
    )
    assert stranger.status_code in (403, 404)
    assert workspace_a != workspace_b


def test_deleting_a_workspace_removes_its_computers(auth_client, device_http, fake_container):
    _connect(auth_client, device_http, fake_container)
    assert fake_container.connector_store.devices_by_id
    removed = asyncio.run(fake_container.connector_store.delete_workspace(LOCAL))
    assert removed >= 1
    assert not fake_container.connector_store.devices_by_id
    # The workspace purge is wired to it.
    assert fake_container.tenancy_service._purger._connector is fake_container.connector_store


def test_removing_a_member_disconnects_the_computers_they_connected(
    auth_client, device_http, fake_container
):
    device_id, credential = _connect(auth_client, device_http, fake_container)
    owner = fake_container.connector_store.devices_by_id[device_id].registered_by
    service = fake_container.connector_service
    assert fake_container.tenancy_service._member_devices is service
    from backend.domain.identity import AccessScope, AuthMode

    stopped = asyncio.run(
        service.withdraw_for_member(
            AccessScope(actor_user_id="someone", auth_mode=AuthMode.LOCAL, workspace_id=LOCAL),
            LOCAL,
            owner,
        )
    )
    assert stopped == [device_id]
    assert _poll(device_http, credential).status_code == 401
