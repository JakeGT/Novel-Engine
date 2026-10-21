"""The connector program, end to end, against the local stand-in model (SCN-002A).

The site runs on this computer (uvicorn on 127.0.0.1, in-memory stores), the stand-in
model (``scripts/e2e/fake_model.py``) answers like an OpenAI-compatible server, and
``python -m connector`` runs as a separate process between them: it gets a code, the
owner confirms it, it reports the stand-in's models, takes one request and answers it.
Nothing leaves this computer.
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from pydantic import BaseModel

from backend.adapters.ai import OpenAIGateway
from backend.domain import connector as c
from backend.domain.identity import AccessScope, AuthMode
from backend.main import create_app
from backend.services.provider_route import resolve_provider

pytestmark = pytest.mark.timeout(120)

ROOT = Path(__file__).resolve().parents[2]


class SceneSummaryDraft(BaseModel):
    summary: str


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def site(fake_container):
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(container=fake_container),
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="off",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(10)


@pytest.fixture
def stand_in():
    from scripts.e2e.fake_model import start

    server, base_url = start("127.0.0.1")
    yield base_url
    server.shutdown()


def _wait_for(predicate, seconds: float = 20.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = predicate()
        if found:
            return found
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_the_connector_connects_with_a_code_and_answers_through_the_stand_in(
    auth_client, fake_container, site, stand_in, tmp_path
):
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "connector",
            "--site",
            site,
            "--model-server",
            stand_in,
            "--name",
            "Test laptop",
            "--state-dir",
            str(tmp_path / "state"),
            "--once",
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "NO_PROXY": "*"},
    )
    lines: list[str] = []
    try:
        assert process.stdout is not None
        reader = threading.Thread(
            target=lambda: lines.extend(iter(process.stdout.readline, "")), daemon=True
        )
        reader.start()
        code = _wait_for(
            lambda: next(
                (m[1] for line in lines if (m := re.search(r"Code: ([A-Z]{4}-[A-Z]{4})", line))),
                None,
            )
        )
        confirmed = auth_client.post(
            "/api/v1/ai/connector/codes/decide", json={"code": code, "approve": True}
        )
        assert confirmed.status_code == 200, confirmed.text

        # Connected (after its next poll for the credential), and reporting the stand-in.
        device = _wait_for(
            lambda: next(
                (d for d in auth_client.get("/api/v1/ai/connector/devices").json() if d["models"]),
                None,
            ),
            seconds=30,
        )
        assert device["name"] == "Test laptop" and device["online"]
        assert [m["name"] for m in device["models"]] == ["e2e-model"]
        credential_file = next((tmp_path / "state").glob("*.json"))
        assert oct(credential_file.stat().st_mode & 0o777) == "0o600"

        chosen = auth_client.post(
            f"/api/v1/ai/connector/devices/{device['id']}/use", json={"model": "e2e-model"}
        )
        assert chosen.status_code == 200

        async def ask() -> object:
            scope = AccessScope(
                actor_user_id="local-user", auth_mode=AuthMode.LOCAL, workspace_id="local-workspace"
            )
            _, api_key, base_url, _sink = await resolve_provider(
                settings=fake_container.settings,
                credentials=fake_container.credential_service,
                usage=fake_container.usage_service,
                scope=scope,
            )
            assert base_url == c.device_address("local-workspace", device["id"])
            gateway = OpenAIGateway(api_key="", devices=fake_container.connector_service)
            return await gateway.generate_structured(
                messages=[{"role": "user", "content": "Summarise: the secret harbour scene."}],
                response_format=SceneSummaryDraft,
                api_key=api_key,
                base_url=base_url,
            )

        result = asyncio.run(ask())
        assert result.parsed == SceneSummaryDraft(  # type: ignore[attr-defined]
            summary="Someone arrives at the harbour and is not welcomed."
        )
        assert process.wait(30) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    output = "".join(lines)
    assert "POST /chat/completions: answered 200" in output
    # The connector says what it did, never the words it carried.
    assert "secret harbour" not in output
    assert "nvldev_" not in output
