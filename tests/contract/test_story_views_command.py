"""``make read-models`` and ``make read-models-verify`` print counts, never names (QRY-001)."""

import asyncio
import json

import pytest

from backend.devtools import read_models as command
from tests.contract.test_story_views import _story

pytestmark = pytest.mark.contract


def test_the_command_builds_verifies_and_forgets(auth_client, fake_container, monkeypatch, capsys):
    w, _ = _story(auth_client, fake_container)
    closed = []

    async def aclose() -> None:
        closed.append(True)

    monkeypatch.setattr(fake_container, "aclose", aclose)
    monkeypatch.setattr(command, "build_container", lambda _settings: fake_container)

    assert command.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["lines_waiting"] == 1
    assert command.main(["build"]) == 0
    assert json.loads(capsys.readouterr().out) == {"lines_built": 1}
    assert command.main(["verify"]) == 0
    out = capsys.readouterr().out
    report = json.loads(out)
    assert report["checked"] == report["matched"] == 6 and report["differed"] == 0
    assert "Mara" not in out and "Harbor" not in out

    store = fake_container.read_model_store
    key = next(key for key in store.views if key[3] == "timeline")
    store.views[key] = store.views[key].__class__(**{**store.views[key].__dict__, "checksum": "x"})
    assert command.main(["verify", "--repair"]) == 1
    assert json.loads(capsys.readouterr().out)["differed"] == 1
    assert store.views == {}

    command.main(["build"])
    capsys.readouterr()
    project = fake_container.settings.AUTH_LOCAL_PROJECT_ID
    assert command.main(["forget", "--project", project]) == 0
    assert json.loads(capsys.readouterr().out) == {"views_removed": 6}
    assert len(closed) == 6
    assert asyncio.run(fake_container.read_model_consumer.run_once()) == 1
