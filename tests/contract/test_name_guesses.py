"""A model's name guesses never undo the author's answers, and stay in the what-if they were
made in. Context building reads only each text's newest words when a name answer is limited.
"""

import asyncio

import pytest

from backend.domain.sources import Verdict
from backend.services.intake_workflow import PassageDraft, ReadReference
from tests.contract.test_scoped_names import _decide, _in, _story, _write
from tests.contract.test_texts_assist import _text

pytestmark = pytest.mark.contract

PASSAGE = "The old pilot tied up at Saltmere before the tide turned.\n"


def _guess(container, entity_id: str) -> None:
    container.ai.queue(
        PassageDraft(references=[ReadReference(phrase="The old pilot", ref=entity_id)])
    )


def _answers(client, w, branch: str | None = None) -> list[dict]:
    got = client.get(f"{w['base']}/names", headers=_in(branch))
    assert got.status_code == 200, got.text
    return got.json()["items"]


def _guessed(client, w, text_id: str, branch: str | None = None) -> bool:
    got = client.get(f"{w['base']}/texts/{text_id}", headers=_in(branch))
    assert got.status_code == 200, got.text
    return any(q["phrase"] == "The old pilot" and q["guessed"] for q in got.json()["questions"])


def test_a_guess_never_replaces_the_authors_answer_or_its_examples(auth_client, fake_container):
    from tests.contract.test_god_hand import _world

    w = _world(auth_client, fake_container)
    made = _text(auth_client, w, PASSAGE)
    said = _decide(
        auth_client,
        w,
        phrase="The old pilot",
        verdict="is",
        entity_id=w["mara"],
        source_id=made["id"],
        example={"source_id": made["id"], "start": 0, "end": len("The old pilot"), "means": True},
    )
    assert said.status_code == 201, said.text
    ignored = _decide(
        auth_client, w, phrase="Saltmere tide", verdict="ignore", source_id=made["id"]
    )
    assert ignored.status_code == 201, ignored.text

    # A long reading began before the author answered, so its guesses arrive afterwards:
    # the same entry for one phrase, someone else entirely for the other.
    workspace_id, project_id, _ = next(iter(fake_container.sources.sources))
    for phrase, entity_id in (("The old pilot", w["mara"]), ("Saltmere tide", w["tobin"])):
        asyncio.run(
            fake_container.source_service.remember(
                workspace_id,
                project_id,
                phrase=phrase,
                verdict=Verdict.SUGGESTED,
                entity_id=entity_id,
                source_id=made["id"],
                actor_user_id=None,
            )
        )

    answers = {item["phrase"]: item for item in _answers(auth_client, w)}
    assert answers["The old pilot"]["id"] == said.json()["id"]
    assert answers["The old pilot"]["verdict"] == "is"
    assert len(answers["The old pilot"]["examples"]) == 1
    assert answers["Saltmere tide"]["verdict"] == "ignore"


def test_a_guess_made_reading_a_passage_in_a_what_if_stays_in_it(auth_client, fake_container):
    from tests.contract.test_god_hand import _world

    w = _world(auth_client, fake_container)
    made = _text(auth_client, w, PASSAGE)
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Mara stays"}).json()["id"]
    head = auth_client.get(f"{w['base']}/projection", headers=_in(branch)).json()
    _guess(fake_container, w["mara"])
    read = auth_client.post(
        f"{w['base']}/assist/texts/{made['id']}/read",
        json={"expected_revision": int(head["head_revision"])},
        headers=_in(branch),
    )
    assert read.status_code == 200, read.text
    assert read.json()["guesses"] == 1

    assert _guessed(auth_client, w, made["id"], branch) is True
    assert _guessed(auth_client, w, made["id"]) is False
    # A guess resolves nothing, so it never makes every text be read name by name.
    workspace_id, project_id, _ = next(iter(fake_container.sources.sources))
    assert asyncio.run(fake_container.source_service.line_names(workspace_id, project_id)) is None


def test_a_guess_made_reading_a_scene_back_in_a_what_if_stays_in_it(auth_client, fake_container):
    w, (dawn, _noon, _dusk) = _story(auth_client, fake_container)
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Roe stays"}).json()["id"]
    text = _write(auth_client, w, dawn, PASSAGE, branch)["text_id"]
    _guess(fake_container, w["mara"])
    started = auth_client.post(
        f"{w['base']}/plan/{dawn['id']}/studio/readings", json={}, headers=_in(branch)
    )
    assert started.status_code == 202, started.text
    asyncio.run(fake_container.job_runner.run_pending())

    guesses = [
        item
        for item in fake_container.sources.bindings.values()
        if item.verdict.value == "suggested"
    ]
    assert [(item.source_id, item.branch_id) for item in guesses] == [(text, branch)]


def test_limited_names_read_only_each_texts_newest_words(auth_client, fake_container):
    w, (dawn, _noon, _dusk) = _story(auth_client, fake_container)
    for words in ("The captain mended nets.", "Nobody mended nets.", "The stranger mended nets."):
        text = _write(auth_client, w, dawn, words)["text_id"]
    branch = auth_client.post(f"{w['base']}/what-ifs", json={"name": "Roe stays"}).json()["id"]
    said = _decide(auth_client, w, branch, phrase="the stranger", verdict="is", entity_id=w["roe"])
    assert said.status_code == 201, said.text
    latest = auth_client.get(f"{w['base']}/texts/{text}").json()["version"]["id"]

    store = fake_container.sources
    listed: list[str] = []
    fetched: list[str] = []
    list_versions, get_version = store.list_versions, store.get_version

    async def counting_list(**kwargs):
        listed.append(kwargs["source_id"])
        return await list_versions(**kwargs)

    async def counting_get(**kwargs):
        fetched.append(kwargs["version_id"])
        return await get_version(**kwargs)

    store.list_versions, store.get_version = counting_list, counting_get
    workspace_id, project_id, _ = next(iter(store.sources))
    found = asyncio.run(
        fake_container.context_service._with_limited_phrases(  # noqa: SLF001
            workspace_id, project_id, [], ["the stranger"], {text}
        )
    )

    assert listed == []
    assert fetched == [latest]
    assert found and all(passage.version_id == latest for passage in found)
