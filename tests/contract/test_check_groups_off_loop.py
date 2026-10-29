"""Working out how a long scene is sent in groups or parts reads the whole scene, so it
runs off the event loop, for the estimate shown before a check as for the check itself
(CON-013, the PERFORMANCE RULE)."""

from __future__ import annotations

import asyncio

import pytest

import backend.domain.review_bundles as review_bundles
from backend.services import scene_checks
from tests.contract.test_check_groups import PARAGRAPHS
from tests.contract.test_studio import _save, _scene_world

pytestmark = pytest.mark.contract


@pytest.mark.parametrize(("check", "budget"), [("continuity", 2_500), ("clarity", 1_100)])
def test_the_estimate_cuts_a_long_scene_into_groups_off_the_event_loop(
    auth_client, fake_container, monkeypatch, check, budget
):
    monkeypatch.setattr(scene_checks, "GROUP_ABOVE_TOKENS", 200)
    monkeypatch.setattr(scene_checks, "GROUP_BUDGET", budget)
    real = review_bundles.review_units
    on_loop: list[bool] = []

    def watched(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(*args, **kwargs)

    monkeypatch.setattr(review_bundles, "review_units", watched)
    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, "\n\n".join(PARAGRAPHS * 6), None)
    on_loop.clear()
    estimate = auth_client.get(
        f"{w['base']}/plan/{scene['id']}/studio/check-estimate", params={"passes": check}
    )
    assert estimate.status_code == 200, estimate.text
    assert on_loop and not any(on_loop), on_loop
