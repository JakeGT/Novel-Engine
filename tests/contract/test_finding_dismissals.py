"""Dismissing a check or a note in the scene studio, with a reason (CON-010, CON-016)."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tests.contract.test_studio import _in, _save, _scene_world, _studio

pytestmark = pytest.mark.contract

PROSE = "Tobin waited at Lantern Harbor. The salt air was palpable. Mara did not come."


def _dismiss(client, w, scene, key: str, branch: str | None = None, **body):
    return client.post(
        f"{w['base']}/plan/{scene['id']}/studio/dismissals",
        json={"key": key, "reason": "intended", **body},
        headers=_in(branch),
    )


def _undo(client, w, scene, dismissal_id: str, branch: str | None = None):
    return client.delete(
        f"{w['base']}/plan/{scene['id']}/studio/dismissals/{dismissal_id}", headers=_in(branch)
    )


def _stock(state: dict) -> dict:
    return next(c for c in state["checks"] if c["code"] == "stock_phrase")


def test_a_dismissed_check_stays_dismissed_until_its_words_change(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    saved = _save(auth_client, w, scene, PROSE, None).json()
    check = _stock(saved)
    assert check["dismissal"] is None and len(check["key"]) == 64

    done = _dismiss(auth_client, w, scene, check["key"], reason="not_a_problem")
    assert done.status_code == 200, done.text
    dismissed = _stock(done.json())
    assert dismissed["dismissal"]["reason"] == "not_a_problem"
    assert dismissed["dismissal"]["expires_at"] is None
    # The text itself is untouched.
    assert done.json()["content"] == PROSE and done.json()["version_id"] == saved["version_id"]

    # Another sentence changes: still dismissed.
    other = _save(
        auth_client, w, scene, PROSE.replace("did not come", "came late"), saved["version_id"]
    ).json()
    assert _stock(other)["dismissal"] is not None
    # Its own sentence changes: it comes back.
    own = _save(
        auth_client,
        w,
        scene,
        other["content"].replace("The salt air", "The cold air"),
        other["version_id"],
    ).json()
    assert _stock(own)["dismissal"] is None
    # Back to the words it was dismissed on: dismissed again, without asking twice.
    back = _save(
        auth_client,
        w,
        scene,
        own["content"].replace("The cold air", "The salt air"),
        own["version_id"],
    ).json()
    assert _stock(back)["dismissal"] is not None


def test_undo_brings_a_finding_back_and_keeps_the_record(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    check = _stock(_save(auth_client, w, scene, PROSE, None).json())
    dismissal = _stock(_dismiss(auth_client, w, scene, check["key"]).json())["dismissal"]

    undone = _undo(auth_client, w, scene, dismissal["id"])
    assert undone.status_code == 200, undone.text
    assert _stock(undone.json())["dismissal"] is None
    assert _undo(auth_client, w, scene, dismissal["id"]).status_code == 404
    # Withdrawn, not deleted; and no words of the scene are in the record.
    (record,) = fake_container.studio_store.saved_dismissals
    assert record.withdrawn_at is not None
    assert "palpable" not in repr(record) and "salt" not in repr(record)


def test_a_reason_in_the_authors_words_and_how_long_it_lasts(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    check = _stock(_save(auth_client, w, scene, PROSE, None).json())

    empty = _dismiss(auth_client, w, scene, check["key"], reason="other", note="   ")
    assert empty.status_code == 400, empty.text
    assert "why" in empty.json()["detail"]
    assert _dismiss(auth_client, w, scene, check["key"], days=3).status_code == 422
    assert _dismiss(auth_client, w, scene, "0" * 64).status_code == 404

    made = _dismiss(
        auth_client, w, scene, check["key"], reason="other", note="Tobin talks like this.", days=7
    ).json()
    dismissal = _stock(made)["dismissal"]
    assert dismissal["reason"] == "other" and dismissal["note"] == "Tobin talks like this."
    assert dismissal["expires_at"] is not None

    # Once it runs out, the finding is back.
    store = fake_container.studio_store
    store.saved_dismissals = [
        replace(item, expires_at=datetime.now(UTC) - timedelta(minutes=1))
        for item in store.saved_dismissals
    ]
    assert _stock(_studio(auth_client, w, scene))["dismissal"] is None
    # And it can be dismissed again.
    again = _dismiss(auth_client, w, scene, check["key"])
    assert again.status_code == 200, again.text
    assert _stock(again.json())["dismissal"]["reason"] == "intended"


def test_a_note_stays_dismissed_when_the_assistant_words_it_differently(
    auth_client, fake_container
):
    from backend.services.scene_checks import CheckNote, CheckNotes

    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)

    def check_with(problem: str) -> dict:
        fake_container.ai.queue(
            CheckNotes(notes=[CheckNote(quote="Mara did not come", problem=problem)])
        )
        started = auth_client.post(
            f"{w['base']}/plan/{scene['id']}/studio/reviews", json={"passes": ["plan"]}
        )
        assert started.status_code == 202, started.text
        asyncio.run(fake_container.job_runner.run_pending())
        notes = _studio(auth_client, w, scene)["assistant"]
        return next(item for item in notes if item["message"] == problem)

    note = check_with("The plan says Mara arrives.")
    made = _dismiss(auth_client, w, scene, note["key"], reason="intended")
    assert made.status_code == 200, made.text
    assert made.json()["assistant"][0]["dismissal"]["reason"] == "intended"
    again = check_with("Mara should arrive here, by the plan.")
    assert again["dismissal"] is not None


def test_a_what_if_starts_with_the_same_dismissals_and_keeps_its_own(auth_client, fake_container):
    w, scene = _scene_world(auth_client, fake_container)
    check = _stock(_save(auth_client, w, scene, PROSE, None).json())
    dismissal = _stock(_dismiss(auth_client, w, scene, check["key"]).json())["dismissal"]
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Mara comes"}).json()["id"]

    inside = _stock(_studio(auth_client, w, scene, branch))
    assert inside["dismissal"] is not None
    assert _undo(auth_client, w, scene, dismissal["id"], branch).status_code == 200
    assert _stock(_studio(auth_client, w, scene, branch))["dismissal"] is None
    # The main story keeps its decision.
    assert _stock(_studio(auth_client, w, scene))["dismissal"] is not None


def _review(auth_client, fake_container, w, scene, notes, check: str = "voice") -> list[dict]:
    from backend.services.scene_checks import CheckNotes

    fake_container.ai.queue(CheckNotes(notes=notes))
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews", json={"passes": [check]}
    )
    assert started.status_code == 202, started.text
    asyncio.run(fake_container.job_runner.run_pending())
    return _studio(auth_client, w, scene)["assistant"]


def test_two_notes_on_one_sentence_are_dismissed_one_at_a_time(auth_client, fake_container):
    from backend.services.scene_checks import CheckNote

    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    notes = _review(
        auth_client,
        fake_container,
        w,
        scene,
        [
            CheckNote(quote="Tobin waited", problem="Too flat an opening."),
            CheckNote(quote="Lantern Harbor", problem="Named twice nearby."),
        ],
    )
    first, second = notes
    assert first["key"] != second["key"]

    made = _dismiss(auth_client, w, scene, first["key"]).json()["assistant"]
    by_message = {item["message"]: item for item in made}
    assert by_message["Too flat an opening."]["dismissal"] is not None
    assert by_message["Named twice nearby."]["dismissal"] is None


def test_two_notes_on_the_same_words_are_told_apart(auth_client, fake_container):
    from backend.services.scene_checks import CheckNote

    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    first, second = _review(
        auth_client,
        fake_container,
        w,
        scene,
        [
            CheckNote(quote="Tobin waited", problem="Too flat an opening."),
            CheckNote(quote="Tobin waited", problem="Tense slips here."),
        ],
    )
    assert first["key"] != second["key"]
    made = _dismiss(auth_client, w, scene, second["key"]).json()["assistant"]
    assert [item["dismissal"] is not None for item in made] == [False, True]


def test_a_note_dismissed_before_quotes_were_fingerprinted_stays_dismissed(
    auth_client, fake_container
):
    from backend.domain.studio import AssistantFinding
    from backend.services.scene_checks import CheckNote
    from backend.services.studio_service import _old_note_key

    w, scene = _scene_world(auth_client, fake_container)
    _save(auth_client, w, scene, PROSE, None)
    (note,) = _review(
        auth_client,
        fake_container,
        w,
        scene,
        [CheckNote(quote="Mara did not come", problem="The plan differs.")],
        check="plan",
    )
    start = PROSE.index("Mara did not come")
    old = _old_note_key(
        PROSE,
        AssistantFinding(
            id="x",
            target_kind="version",
            target_id="v",
            job_id=None,
            category="plan",
            message="The plan differs.",
            suggestion="",
            start=start,
            end=start + len("Mara did not come"),
        ),
    )
    assert old != note["key"]
    # Kept as it would have been before: under the older fingerprint.
    _dismiss(auth_client, w, scene, note["key"])
    store = fake_container.studio_store
    store.saved_dismissals = [replace(item, fingerprint=old) for item in store.saved_dismissals]

    (again,) = _studio(auth_client, w, scene)["assistant"]
    assert again["dismissal"] is not None
