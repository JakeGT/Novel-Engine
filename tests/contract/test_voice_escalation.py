"""The bounded voice critic (CON-018): when the voice measures point strongly away from
the author's usual, a small check with the assistant on just the flagged passages is
offered with its cost; its notes quote the words and are kept with the version read."""

from __future__ import annotations

import pytest

from backend.services.scene_checks import CheckNote, CheckNotes
from tests.contract.test_studio import _studio
from tests.contract.test_voice_and_audits import _book, _run

pytestmark = pytest.mark.contract


def test_strong_drift_offers_a_check_of_the_flagged_passages_only(auth_client, fake_container):
    w, _beat, scenes = _book(auth_client, fake_container)
    url = f"{w['base']}/plan/{scenes[3]['id']}/voice/escalation"
    offered = auth_client.get(url).json()
    assert offered["offered"] is True and offered["total_tokens"] > 0
    assert any("told with “I”" in note for note in offered["notes"])
    assert 0 < len(offered["passages"]) <= 6 and offered["words"] <= 900
    # Nothing is asked to work this out.
    assert fake_container.ai.calls == []

    usual = auth_client.get(f"{w['base']}/plan/{scenes[0]['id']}/voice/escalation").json()
    assert usual["offered"] is False and "far enough" in usual["why_not"]
    refused = auth_client.post(f"{w['base']}/plan/{scenes[0]['id']}/voice/escalation")
    assert refused.status_code == 400

    fake_container.ai.queue(
        CheckNotes(
            notes=[
                CheckNote(
                    quote="because counting is the only thing I can do",
                    problem="This is told with “I” in the present; your scenes use “she”.",
                ),
                CheckNote(quote="words that are not in the scene", problem="Lost."),
            ]
        )
    )
    started = auth_client.post(url)
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done.error_message
    assert done.result == {"findings": 1, "dropped": 1, "passages": len(offered["passages"])}
    call = fake_container.ai.calls[-1]
    assert "the author's voice, in a few passages" in call["messages"][0]["content"]
    sent = call["messages"][1]["content"]
    assert "Why these passages were flagged" in sent and "told with “I”" in sent
    (note,) = [n for n in _studio(auth_client, w, scenes[3])["assistant"] if "“I”" in n["message"]]
    assert note["check_pass"] == "voice"
    first = offered["passages"][0]
    assert first["start"] <= note["start"] < note["end"] <= offered["passages"][-1]["end"]


def test_a_dismissed_note_no_longer_counts_towards_offering_it(auth_client, fake_container):
    w, _beat, scenes = _book(auth_client, fake_container)
    base = f"{w['base']}/plan/{scenes[3]['id']}"
    voice = auth_client.get(f"{base}/voice").json()
    for note in voice["notes"]:
        dismissed = auth_client.post(
            f"{base}/voice/dismissals", json={"key": note["key"], "reason": "intended"}
        )
        assert dismissed.status_code == 200, dismissed.text
    found = auth_client.get(f"{base}/voice/escalation").json()
    assert found["offered"] is False and found["notes"] == []
