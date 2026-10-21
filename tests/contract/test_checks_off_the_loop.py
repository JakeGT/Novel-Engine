"""The checks that need no model are plain Python over the whole text: the studio and the
incremental checks run them apart from the event loop, so a long scene never holds up
every other request."""

import asyncio

import pytest

import backend.services.incremental_checks as incremental_checks
import backend.services.studio_service as studio_service
from backend.domain.drafting import check_draft
from tests.contract.test_incremental_checks import PROSE, _record
from tests.contract.test_studio import _save, _scene_world, _studio

pytestmark = pytest.mark.contract


def test_the_checks_run_off_the_event_loop(auth_client, fake_container, monkeypatch):
    on_loop: list[bool] = []

    def watched(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return check_draft(*args, **kwargs)

    monkeypatch.setattr(studio_service, "check_draft", watched)
    monkeypatch.setattr(incremental_checks, "check_draft", watched)
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    _studio(auth_client, w, scene)
    _record(auth_client, w, scene)
    assert on_loop and not any(on_loop)
