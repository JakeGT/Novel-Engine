"""Who says what, and dismissing a note on how a scene sounds (CON-018): dialogue tied to
its speaker by a tag or by turns in an exchange, each character's speech against their
own speech elsewhere, and a voice note dismissed with a reason and brought back."""

from __future__ import annotations

import pytest

from tests.contract.test_god_hand import _world
from tests.contract.test_story_audit import _plan
from tests.contract.test_studio import _save
from tests.contract.test_voice_and_audits import THIRD, _prose

pytestmark = pytest.mark.contract

# Tobin talks in short, clipped lines with short forms; Mara answers.
USUAL = (
    '"You\'re late," said Tobin Ashe.\n'
    '"The tide turned," said Mara.\n'
    "\"Don't lie. It's been turning all night.\"\n"
    '"Then it\'s been slow."\n'
    "\"I'm cold, and I won't wait again.\"\n"
    '"You will."\n'
    "\"Can't. Won't. I'm done.\"\n"
    '"You\'re never done."\n'
)
# Here he makes speeches, with no short forms.
ODD = (
    '"You are late, and I would have you know that I have been standing upon these stones '
    'since the first bell, considering the many ways in which a person might be delayed," '
    "said Tobin Ashe.\n"
    '"The tide turned," said Mara.\n'
    '"I do not believe that the tide is responsible, for I have watched it all night, and it '
    'has done nothing that it does not do upon every other night of the year."\n'
    '"Then it was slow."\n'
    '"I am cold, and I will not wait upon you again, whatever reasons you may bring me '
    'tomorrow, or the day after, or upon any day that follows it."\n'
    '"You will."\n'
    '"I shall not, and you may tell the harbour-master that I said so, in those words and in '
    'no others, when next you see him at the wall."\n'
    '"You are never done."\n'
)


def _book(client, container):
    w = _world(client, container)
    beat = _plan(client, w, "beat", "The wait")
    refs = [
        {"entity_id": w["mara"], "role": "point_of_view"},
        {"entity_id": w["tobin"], "role": "participant"},
    ]
    scenes = [
        _plan(client, w, "scene", title, beat["id"], refs=refs)
        for title in ("One", "Two", "Three", "Four")
    ]
    for index, scene in enumerate(scenes):
        talk = ODD if index == 3 else USUAL
        text = _prose(THIRD, 400) + "\n\n" + "\n".join([talk] * 5)
        assert _save(client, w, scene, text, None).status_code == 200
    return w, scenes


def test_each_speakers_dialogue_is_compared_with_their_own_elsewhere(auth_client, fake_container):
    w, scenes = _book(auth_client, fake_container)
    usual = auth_client.get(f"{w['base']}/plan/{scenes[0]['id']}/voice").json()
    speakers = {row["name"]: row for row in usual["speakers"]}
    assert set(speakers) == {"Tobin Ashe", "Mara"}
    # Tagged lines and the turns between them are tied to their speaker; "he said" in the
    # narration's own lines is not, since a pronoun is never read.
    assert 0.75 < usual["attributed"] < 1.0
    assert speakers["Tobin Ashe"]["lines"] == 20 and speakers["Tobin Ashe"]["short_forms"] > 10
    assert not any(note["code"].startswith("voice_speaker") for note in usual["notes"])

    odd = auth_client.get(f"{w['base']}/plan/{scenes[3]['id']}/voice").json()
    tobin = next(row for row in odd["speakers"] if row["name"] == "Tobin Ashe")
    assert tobin["compared"] is True and tobin["elsewhere"] == 3, (
        tobin["elsewhere"],
        odd["attributed"],
    )
    notes = {note["code"]: note for note in odd["notes"]}
    assert notes["voice_speaker_short_forms"]["message"].startswith(
        "Tobin Ashe uses short forms such as “don't” and “I'm” far less here"
    )
    assert "Tobin Ashe's sentences run about" in notes["voice_speaker_length"]["message"]
    # Mara says too little to compare.
    assert not any("Mara" in note["message"] for note in odd["notes"])


def test_a_voice_note_is_dismissed_with_a_reason_and_brought_back(auth_client, fake_container):
    w, scenes = _book(auth_client, fake_container)
    url = f"{w['base']}/plan/{scenes[3]['id']}/voice"
    odd = auth_client.get(url).json()
    note = next(n for n in odd["notes"] if n["code"] == "voice_speaker_short_forms")
    assert note["dismissal"] is None and len(note["key"]) == 64
    refused = auth_client.post(
        f"{url}/dismissals", json={"key": note["key"], "reason": "other", "note": ""}
    )
    assert refused.status_code == 400, refused.text
    done = auth_client.post(
        f"{url}/dismissals",
        json={"key": note["key"], "reason": "intended", "note": "He is putting on airs here."},
    )
    assert done.status_code == 200, done.text
    kept = next(n for n in done.json()["notes"] if n["key"] == note["key"])
    assert kept["dismissal"]["reason"] == "intended"
    assert kept["dismissal"]["note"] == "He is putting on airs here."
    # Read again: still dismissed, while the note says the same thing.
    again = next(n for n in auth_client.get(url).json()["notes"] if n["key"] == note["key"])
    assert again["dismissal"] is not None
    gone = auth_client.post(f"{url}/dismissals", json={"key": "f" * 64, "reason": "intended"})
    assert gone.status_code == 404, gone.text
    undone = auth_client.delete(f"{url}/dismissals/{kept['dismissal']['id']}")
    assert undone.status_code == 200, undone.text
    back = next(n for n in undone.json()["notes"] if n["key"] == note["key"])
    assert back["dismissal"] is None
