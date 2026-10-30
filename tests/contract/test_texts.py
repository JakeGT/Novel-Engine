"""Texts an author brings in: kept as given, names found without a model, answers remembered."""

import pytest

from tests.contract.test_plan import Plan

pytestmark = pytest.mark.contract

STORY = (
    "# The shoals\n\n"
    "Ines Varga rowed out past Kestrel Point before dawn. The Fox was already there, "
    "and @Tobin with her. Nobody had any hope of sleep.\n\n"
    "## The chart\n\n"
    'Ines unrolled the chart. "Whose hand is this?" she asked [[the Harbourmaster]]. '
    "The Fox only smiled.\n"
)


class Texts(Plan):
    def text(self, title: str = "Chapter one", content: str = STORY) -> dict:
        made = self.client.post(f"{self.base}/texts", json={"title": title, "content": content})
        assert made.status_code == 201, made.text
        return made.json()

    def read(self, source_id: str, **params) -> dict:
        got = self.client.get(f"{self.base}/texts/{source_id}", params=params)
        assert got.status_code == 200, got.text
        return got.json()

    def decide(self, **body):
        return self.client.post(f"{self.base}/names", json=body)


def _phrases(detail: dict, status: str | None = None) -> list[str]:
    return [m["phrase"] for m in detail["mentions"] if status is None or m["status"] == status]


def test_a_pasted_text_is_kept_exactly_and_split_at_its_headings(auth_client, fake_container):
    texts = Texts(auth_client, fake_container)
    made = texts.text()
    assert made["versions"] == 1 and made["length"] == len(STORY)

    detail = texts.read(made["id"])
    assert detail["content"] == STORY
    assert [s["title"] for s in detail["sections"]] == ["The shoals", "The chart"]
    first, second = detail["sections"]
    assert STORY[first["start"] : first["end"]].startswith("# The shoals")
    assert STORY[second["start"] : second["end"]].startswith("## The chart")
    assert texts.client.get(f"{texts.base}/texts").json()["items"][0]["title"] == "Chapter one"


def test_names_are_found_without_a_model(auth_client, fake_container):
    texts = Texts(auth_client, fake_container)
    ines = texts.entry("Character", "Ines Varga")
    texts.entry("Location", "Kestrel Point")
    hope = texts.entry("Character", "Hope")
    made = texts.text()

    detail = texts.read(made["id"])
    known = [m for m in detail["mentions"] if m["status"] == "known"]
    assert [m["phrase"] for m in known] == ["Ines Varga", "Kestrel Point"]
    assert known[0]["entity_ids"] == [ines] and known[0]["marked"] is False
    assert STORY[known[0]["start"] : known[0]["end"]] == "Ines Varga"
    # "hope" the word is not Hope the person.
    assert all(hope not in m["entity_ids"] for m in detail["mentions"])
    # Marked names nothing answers to are questions, most used first; nothing is invented.
    assert _phrases(detail, "unknown") == ["Tobin", "the Harbourmaster"]
    assert [q["phrase"] for q in detail["questions"]] == ["Tobin", "the Harbourmaster"]
    assert detail["names"][ines] == "Ines Varga"
    # No model was asked for any of this.
    assert fake_container.ai.calls == []


def test_an_answer_about_a_phrase_is_remembered(auth_client, fake_container):
    texts = Texts(auth_client, fake_container)
    mara = texts.entry("Character", "Mara Oduya")
    made = texts.text()
    assert "The Fox" not in _phrases(texts.read(made["id"]))

    said = texts.decide(phrase="the Fox", verdict="is", entity_id=mara)
    assert said.status_code == 201, said.text
    detail = texts.read(made["id"])
    fox = [m for m in detail["mentions"] if m["phrase"] == "The Fox"]
    assert len(fox) == 2 and all(m["entity_ids"] == [mara] for m in fox)

    # It holds for the next text too.
    other = texts.text("Chapter two", "At noon the Fox came back.")
    assert _phrases(texts.read(other["id"]), "known") == ["the Fox"]

    memory = texts.client.get(f"{texts.base}/names").json()
    assert [(i["phrase"], i["verdict"]) for i in memory["items"]] == [("the Fox", "is")]
    assert memory["names"][mara] == "Mara Oduya"

    gone = texts.client.delete(f"{texts.base}/names/{memory['items'][0]['id']}")
    assert gone.status_code == 204
    assert "The Fox" not in _phrases(texts.read(made["id"]))


def test_a_shared_name_is_asked_about_and_can_be_settled_for_one_text(auth_client, fake_container):
    texts = Texts(auth_client, fake_container)
    father = texts.entry("Character", "Tobin Varga")
    son = texts.entry("Character", "Tobin the Younger")
    for entity_id in (father, son):
        edited = texts.client.patch(
            f"{texts.base}/entities/{entity_id}",
            json={"aliases": ["Tobin"], "expected_revision": texts.head},
        )
        assert edited.status_code == 200, edited.text
        texts.head += 1
    made = texts.text()
    other = texts.text("Chapter two", "Tobin slept.")

    question = texts.read(made["id"])["questions"][0]
    assert question["phrase"] == "Tobin" and sorted(question["candidates"]) == sorted([father, son])

    assert (
        texts.decide(phrase="Tobin", verdict="is", entity_id=son, source_id=made["id"]).status_code
        == 201
    )
    settled = [m for m in texts.read(made["id"])["mentions"] if m["phrase"] == "Tobin"]
    assert settled[0]["status"] == "known" and settled[0]["entity_ids"] == [son]
    # Only in that text: elsewhere it is still a question.
    assert texts.read(other["id"])["mentions"][0]["status"] == "unclear"

    assert texts.decide(phrase="Tobin", verdict="is_not", entity_id=son).status_code == 201
    elsewhere = texts.read(other["id"])["mentions"][0]
    assert elsewhere["status"] == "known" and elsewhere["entity_ids"] == [father]


def test_not_a_name_stops_the_question(auth_client, fake_container):
    texts = Texts(auth_client, fake_container)
    made = texts.text()
    assert texts.decide(phrase="Tobin", verdict="ignore").status_code == 201
    detail = texts.read(made["id"])
    assert [q["phrase"] for q in detail["questions"]] == ["the Harbourmaster"]
    assert "Tobin" not in _phrases(detail)


def test_a_marked_name_becomes_an_entry_that_points_back_at_the_text(auth_client, fake_container):
    texts = Texts(auth_client, fake_container)
    made = texts.text()
    added = texts.client.post(
        f"{texts.base}/texts/{made['id']}/entries",
        json={"phrase": "Tobin", "entity_type": "Character", "expected_revision": texts.head},
    )
    assert added.status_code == 201, added.text
    entry = added.json()
    assert entry["name"] == "Tobin"

    detail = texts.read(made["id"])
    assert [q["phrase"] for q in detail["questions"]] == ["the Harbourmaster"]
    tobin = next(m for m in detail["mentions"] if m["phrase"] == "Tobin")
    assert tobin["status"] == "known" and tobin["entity_ids"] == [entry["id"]]

    # The entry's history leads back to the words.
    provenance = texts.client.get(f"{texts.base}/provenance/entity/{entry['id']}")
    assert provenance.status_code == 200, provenance.text
    refs = [ref for item in _walk(provenance.json()) for ref in item]
    assert refs and refs[0]["source_artefact_id"] == detail["version"]["id"]
    passage = texts.client.get(
        f"{texts.base}/texts/passage",
        params={"version_id": refs[0]["source_artefact_id"], "location": refs[0]["location"]},
    ).json()
    assert passage["quote"] == "@Tobin" and passage["title"] == "Chapter one"
    assert passage["before"].endswith("and ")
    assert fake_container.ai.calls == []


def _walk(node):
    """Every evidence list anywhere in a response."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "evidence" and isinstance(value, list) and value:
                yield value
            else:
                yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def test_changing_the_words_makes_a_new_version_and_keeps_the_old(auth_client, fake_container):
    texts = Texts(auth_client, fake_container)
    made = texts.text()
    first = texts.read(made["id"])["version"]["id"]

    same = texts.client.patch(f"{texts.base}/texts/{made['id']}", json={"content": STORY})
    assert same.json()["versions"] == 1
    changed = texts.client.patch(
        f"{texts.base}/texts/{made['id']}", json={"content": STORY + "\nThe tide turned.\n"}
    )
    assert changed.status_code == 200 and changed.json()["versions"] == 2

    assert texts.read(made["id"])["content"].endswith("The tide turned.\n")
    assert texts.read(made["id"], version_id=first)["content"] == STORY
    assert [v["number"] for v in texts.read(made["id"])["versions"]] == [1, 2]


def test_removing_a_text_erases_its_words(auth_client, fake_container):
    texts = Texts(auth_client, fake_container)
    made = texts.text()
    version = texts.read(made["id"])["version"]["id"]
    assert texts.client.delete(f"{texts.base}/texts/{made['id']}").status_code == 204
    assert texts.client.get(f"{texts.base}/texts").json()["items"] == []
    assert texts.client.get(f"{texts.base}/texts/{made['id']}").status_code == 404
    gone = texts.client.get(f"{texts.base}/texts/passage", params={"version_id": version})
    assert gone.status_code == 404
    assert fake_container.sources.versions == {}


@pytest.mark.parametrize(
    "body",
    [
        {"title": "Empty", "content": "   \n "},
        {"title": "   ", "content": "Words."},
        {"title": "Binary", "content": "abc\x00def"},
    ],
)
def test_text_that_cannot_be_kept_is_refused(auth_client, fake_container, body):
    texts = Texts(auth_client, fake_container)
    assert texts.client.post(f"{texts.base}/texts", json=body).status_code in (400, 422)


def test_a_text_cannot_be_read_from_another_story(auth_client, fake_container):
    texts = Texts(auth_client, fake_container)
    made = texts.text()
    other = f"/api/v1/projects/prj_someone_elses/texts/{made['id']}"
    assert texts.client.get(other).status_code in (403, 404)
