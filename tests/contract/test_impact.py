"""What a change touches, shown before it is approved (CON-017, CON-007).

Worked out on read from the plan, story rules, kept drafts and the passage index, in
the change's own line of the story, with no model. Writing that mentions a changed
entry may be affected; records that name it are affected; the rest is said to be
not checked.
"""

import pytest

from tests.contract.test_context import _head, _setup
from tests.contract.test_context_compare import _kept_draft
from tests.contract.test_god_hand import _world
from tests.contract.test_plan_checks import _add, _put
from tests.contract.test_rules import _add as _add_rule
from tests.contract.test_what_ifs import _in, _start

pytestmark = pytest.mark.contract


def _leave_guild(client, w) -> str:
    proposed = client.post(
        f"{w['base']}/mutations",
        json={
            "origin": "model",
            "expected_revision": _head(client, w),
            "operations": [{"op": "close_fact", "id": w["in_guild"]}],
        },
    )
    assert proposed.status_code == 201, proposed.text
    return str(proposed.json()["id"])


def _impact(client, w, path: str, headers: dict | None = None) -> dict:
    got = client.get(f"{w['base']}/{path}/impact", headers=headers or {})
    assert got.status_code == 200, got.text
    return got.json()


def _by_title(items: list[dict]) -> dict[str, dict]:
    return {f"{item['kind']}:{item['title']}": item for item in items}


def test_a_proposal_shows_what_depends_on_it_before_it_is_approved(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    docks = _setup(auth_client, w)  # Tobin's point of view, at the harbor, Mara in it
    thread = _add(
        auth_client,
        w,
        "thread",
        "Is Mara in the guild?",
        refs=[{"entity_id": w["mara"], "role": "about"}],
    )
    market = _add(auth_client, w, "scene", "The Market")
    _put(auth_client, w, market["id"], "reveals", {"reveals": [{"fact_id": w["in_guild"]}]})
    quay = _add(auth_client, w, "scene", "The Quay")
    _put(
        auth_client,
        w,
        quay["id"],
        "conditions",
        {
            "conditions": [
                {
                    "when": "before",
                    "entity_id": w["mara"],
                    "check": {
                        "kind": "has",
                        "pattern": {"predicate": "MEMBER_OF", "other_id": w["guild"]},
                    },
                },
                {"when": "after", "words": "The tide has turned."},
            ]
        },
    )
    _add(
        auth_client,
        w,
        "scene",
        "Saltmere at dusk",
        refs=[{"entity_id": w["saltmere"], "role": "place"}],
    )
    assert _add_rule(auth_client, w).status_code == 201
    text = auth_client.post(
        f"{w['base']}/texts",
        json={"title": "Chapter one", "content": "Mara walked the harbor wall.\n\nNobody came."},
    )
    assert text.status_code == 201, text.text
    _kept_draft(auth_client, fake_container, w, docks)
    calls = len(fake_container.ai.calls)

    mutation_id = _leave_guild(auth_client, w)
    impact = _impact(auth_client, w, f"mutations/{mutation_id}")

    assert impact["about"] == ["No longer true: Mara is a member of Harbor Guild"]
    affected = _by_title(impact["affected"])
    assert set(affected) == {
        "scene:The Docks",
        "scene:The Market",
        "scene:The Quay",
        "thread:Is Mara in the guild?",
        "rule:Guilds have members",
        "draft:The Docks",
    }
    assert affected["scene:The Docks"]["reasons"] == ["Mara is in it"]
    assert affected["scene:The Market"]["reasons"] == [
        "It shows the reader: Mara is a member of Harbor Guild"
    ]
    assert affected["scene:The Quay"]["reasons"] == [
        "A condition before it: “Mara must have this: it is a member of Harbor Guild.”"
    ]
    assert affected["thread:Is Mara in the guild?"]["reasons"] == ["It is about Mara"]
    assert affected["thread:Is Mara in the guild?"]["plan_id"] == thread["id"]
    assert affected["rule:Guilds have members"]["reasons"] == ["It applies to Harbor Guild"]
    (drafted,) = affected["draft:The Docks"]["reasons"]
    assert drafted.startswith("Version 2 was drafted with ") and "in what was given" in drafted
    assert all(item["level"] == "affected" for item in impact["affected"])

    maybe = _by_title(impact["may_be_affected"])
    assert maybe["text:Chapter one"]["reasons"] == ["Mentions Mara by name in 1 passage"]
    assert maybe["text:Chapter one"]["start"] == 0
    assert maybe["scene_text:The Docks"]["plan_id"] == docks["id"]
    assert all(item["level"] == "may_be_affected" for item in impact["may_be_affected"])

    gaps = " ".join(impact["not_checked"])
    assert "without a name or alias" in gaps
    assert "A scene condition written in your own words" in gaps
    assert "Checks and notes in the scene studio" in gaps
    # No model was asked to work any of this out.
    assert len(fake_container.ai.calls) == calls

    # A skipped change no longer touches anything.
    skipped = auth_client.post(
        f"{w['base']}/mutations/{mutation_id}/operations/0/decision",
        json={"decision": "rejected", "note": None},
    )
    assert skipped.status_code == 200, skipped.text
    nothing = _impact(auth_client, w, f"mutations/{mutation_id}")
    assert nothing["about"] == [] and nothing["affected"] == []
    assert nothing["may_be_affected"] == [] and nothing["not_checked"]


def test_an_entry_shows_what_depends_on_it_in_the_line_of_the_story_being_worked_in(
    auth_client, fake_container
):
    w = _world(auth_client, fake_container)
    _add(
        auth_client, w, "scene", "Main only", refs=[{"entity_id": w["mara"], "role": "participant"}]
    )
    what_if = _start(auth_client, w)
    head = _head(auth_client, w)
    made = auth_client.post(
        f"{w['base']}/plan",
        json={
            "kind": "scene",
            "title": "Only in the what-if",
            "refs": [{"entity_id": w["mara"], "role": "point_of_view"}],
            "expected_revision": head,
        },
        headers=_in(what_if["id"]),
    )
    assert made.status_code == 201, made.text

    main = _impact(auth_client, w, f"entities/{w['mara']}")
    assert main["about"] == ["Mara is changed"]
    assert {item["title"] for item in main["affected"]} == {"Main only"}
    assert any("what-ifs" in gap for gap in main["not_checked"])

    inside = _impact(auth_client, w, f"entities/{w['mara']}", _in(what_if["id"]))
    assert {item["title"] for item in inside["affected"]} == {"Main only", "Only in the what-if"}
    reasons = _by_title(inside["affected"])["scene:Only in the what-if"]["reasons"]
    assert reasons == ["Mara is the point of view"]
    assert any("Only this what-if is looked at" in gap for gap in inside["not_checked"])

    missing = auth_client.get(f"{w['base']}/entities/ent_not_here/impact")
    assert missing.status_code == 404


def test_a_proposal_from_another_project_is_not_found(auth_client, fake_container):
    w = _world(auth_client, fake_container)
    got = auth_client.get(f"{w['base']}/mutations/mut_not_here/impact")
    assert got.status_code == 404


def test_only_each_scenes_newest_kept_draft_is_read_and_the_rest_is_said(
    auth_client, fake_container, monkeypatch
):
    w = _world(auth_client, fake_container)
    docks = _add(
        auth_client, w, "scene", "The Docks", refs=[{"entity_id": w["mara"], "role": "participant"}]
    )
    _kept_draft(auth_client, fake_container, w, docks)
    newest = _kept_draft(auth_client, fake_container, w, docks)
    manifests = fake_container.manifests
    original = manifests.get
    read: list[str] = []

    async def counted(**kwargs):
        read.append(kwargs["manifest_id"])
        return await original(**kwargs)

    monkeypatch.setattr(manifests, "get", counted)
    impact = _impact(auth_client, w, f"entities/{w['mara']}")

    # One kept context read for the scene, however many drafts it kept.
    assert len(read) == 1
    drafted = _by_title(impact["affected"])["draft:The Docks"]
    assert drafted["version_id"] == newest["id"]
    assert len(drafted["reasons"]) == 1
    assert "1 older kept draft" not in " ".join(impact["not_checked"])
    assert any(
        gap.startswith("An older kept draft: only each scene's newest kept draft is looked at")
        for gap in impact["not_checked"]
    )
