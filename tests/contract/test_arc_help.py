"""The assistant's help with arcs and with gaps in the plan (STY-006, STY-004): an arc's
stages suggested from the plan and the story bible, and one scene that moves a quiet
thread along or reaches a stage no scene reaches, each a plan change for review."""

import asyncio

import pytest

from backend.services.arc_help import StageDraft, StageSheet
from backend.services.planning_help import BridgeChoice, BridgeDraft, BridgeSheet
from tests.contract.test_god_hand import _world
from tests.contract.test_plan_assist import _accept
from tests.contract.test_plan_checks import _add, _outline, _put

pytestmark = pytest.mark.contract


def _run(container) -> list:
    return asyncio.run(container.job_runner.run_pending())


def _item(client, w, plan_id: str) -> dict:
    (item,) = [i for i in client.get(f"{w['base']}/plan").json()["items"] if i["id"] == plan_id]
    return item


def _arc(client, w, **fields) -> dict:
    return _add(
        client,
        w,
        "arc",
        "Learning to trust",
        refs=[{"entity_id": w["mara"], "role": "about"}],
        **fields,
    )


def test_an_arcs_stages_are_suggested_shown_by_words_and_wait_for_review(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    arc = _arc(auth_client, w, summary="Mara stops working alone and learns to lean on Tobin.")
    quarrel = _add(
        auth_client,
        w,
        "scene",
        "The quarrel",
        summary="Mara and Tobin fall out over the ledger.",
        refs=[{"entity_id": w["mara"], "role": "point_of_view"}],
    )
    ask = _add(
        auth_client,
        w,
        "scene",
        "The ask",
        summary="Mara asks Tobin to keep watch at the harbour.",
        refs=[{"entity_id": w["mara"], "role": "point_of_view"}],
    )
    estimate = auth_client.get(f"{w['base']}/plan/{arc['id']}/stages/estimate")
    assert estimate.status_code == 200, estimate.text
    assert estimate.json()["total_tokens"] > 0 and fake_container.ai.calls == []
    fake_container.ai.queue(
        StageSheet(
            stages=[
                StageDraft(name="Works alone", words="Mara stops working alone"),
                StageDraft(
                    name="Fights with Tobin",
                    scene_id=quarrel["id"],
                    words="Mara and Tobin fall out",
                ),
                StageDraft(
                    name="Leans on Tobin",
                    scene_id=ask["id"],
                    fact_id=w["knows"],
                    words="Mara asks Tobin to keep watch",
                ),
                # Not shown by the material: dropped.
                StageDraft(name="Betrays him", words="she betrays him in the end"),
                # A scene not listed: kept, but not placed.
                StageDraft(
                    name="Trusts him", scene_id="pln_nowhere", words="learns to lean on Tobin"
                ),
            ]
        )
    )
    started = auth_client.post(f"{w['base']}/plan/{arc['id']}/stages/suggest")
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    assert done.result["stages"] == 4 and done.result["dropped"] == 1
    assert done.result["cleared"] == 1
    sent = fake_container.ai.calls[-1]["messages"][-1]["content"]
    assert f"- {quarrel['id']}: “The quarrel” (in it)" in sent
    assert f"- {w['knows']}: Mara knows Tobin Ashe" in sent
    # Nothing changes until the author keeps it.
    assert _item(auth_client, w, arc["id"])["path"] == []
    detail = auth_client.get(f"{w['base']}/mutations/{done.result['mutation_id']}").json()
    assert detail["origin"] == "model" and detail["requires_review"] is True
    _accept(auth_client, w, done.result["mutation_id"])
    path = _item(auth_client, w, arc["id"])["path"]
    assert [(s["name"], s.get("scene_id")) for s in path] == [
        ("Works alone", None),
        ("Fights with Tobin", quarrel["id"]),
        ("Leans on Tobin", ask["id"]),
        ("Trusts him", None),
    ]
    assert path[2]["fact_id"] == w["knows"]

    # An arc with stages is never filled again.
    refused = auth_client.get(f"{w['base']}/plan/{arc['id']}/stages/estimate")
    assert refused.status_code == 400 and "already has stages" in refused.json()["detail"]


def test_an_arc_needs_its_character_and_something_to_go_on(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    nobody = _add(auth_client, w, "arc", "Nobody's arc")
    got = auth_client.get(f"{w['base']}/plan/{nobody['id']}/stages/estimate")
    assert got.status_code == 400 and got.json()["detail"] == "Say whose arc this is first."
    scene = _add(auth_client, w, "scene", "A scene")
    got = auth_client.get(f"{w['base']}/plan/{scene['id']}/stages/estimate")
    assert got.status_code == 400
    assert fake_container.ai.calls == []


def test_a_quiet_thread_gets_a_scene_that_moves_it_along(auth_client, fake_container) -> None:
    w = _world(auth_client, fake_container)
    thread = _add(auth_client, w, "thread", "Who took the ledger?")
    opens = _add(auth_client, w, "scene", "The theft", sets_up=[thread["id"]])
    _add(auth_client, w, "scene", "The market")
    resolves = _add(auth_client, w, "scene", "The reckoning", pays_off=[thread["id"]])
    assert "needs_progress" in {o["code"] for o in _outline(auth_client, w)["open"]}
    estimate = auth_client.get(f"{w['base']}/plan/{thread['id']}/gap/estimate")
    assert estimate.status_code == 200, estimate.text
    fake_container.ai.queue(
        BridgeSheet(
            bridges=[
                BridgeDraft(
                    title="A torn page",
                    summary="Mara finds a page of the ledger in the harbour.",
                    how_it_helps="It keeps the question of the ledger open.",
                    point_of_view=BridgeChoice(id=w["mara"], name="Mara"),
                ),
                BridgeDraft(title="A second one", summary="Only one is kept."),
            ]
        )
    )
    started = auth_client.post(f"{w['base']}/plan/{thread['id']}/gap/suggest")
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    sent = fake_container.ai.calls[-1]["messages"][-1]["content"]
    assert "nothing between them moves it along" in sent and "just before “The reckoning”" in sent
    _accept(auth_client, w, done.result["mutation_id"])
    outline = _outline(auth_client, w)
    scenes = sorted(
        (item for item in outline["items"] if item["kind"] == "scene"),
        key=lambda item: item["position"],
    )
    assert [s["title"] for s in scenes] == [
        "The theft",
        "The market",
        "A torn page",
        "The reckoning",
    ]
    torn = next(s for s in scenes if s["title"] == "A torn page")
    assert torn["advances"] == [thread["id"]]
    assert "needs_progress" not in {o["code"] for o in outline["open"]}
    assert opens["id"] and resolves["id"]
    # No gap any more: nothing to suggest.
    again = auth_client.get(f"{w['base']}/plan/{thread['id']}/gap/estimate")
    assert again.status_code == 400


def test_a_stage_no_scene_reaches_gets_a_scene_placed_after_the_stage_before(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    one = _add(auth_client, w, "scene", "One")
    two = _add(auth_client, w, "scene", "Two")
    arc = _arc(auth_client, w)
    _put(
        auth_client,
        w,
        arc["id"],
        "path",
        {
            "stages": [
                {"name": "Alone"},
                {"name": "Wary", "scene_id": one["id"]},
                {"name": "Trusting"},
                {"name": "Loyal", "scene_id": two["id"]},
            ]
        },
    )
    assert "arc_stage_not_placed" in {o["code"] for o in _outline(auth_client, w)["open"]}
    assert (
        auth_client.get(
            f"{w['base']}/plan/{arc['id']}/gap/estimate", params={"stage": 1}
        ).status_code
        == 400
    )
    fake_container.ai.queue(
        BridgeSheet(
            bridges=[
                BridgeDraft(
                    title="The watch",
                    summary="Tobin keeps watch while Mara sleeps.",
                    how_it_helps="She trusts him with her sleep.",
                )
            ]
        )
    )
    started = auth_client.post(f"{w['base']}/plan/{arc['id']}/gap/suggest", params={"stage": 2})
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    sent = fake_container.ai.calls[-1]["messages"][-1]["content"]
    assert "goes from “Wary” to “Trusting”, then “Loyal”" in sent and "just after “One”" in sent
    _accept(auth_client, w, done.result["mutation_id"])
    outline = _outline(auth_client, w)
    scenes = sorted(
        (item for item in outline["items"] if item["kind"] == "scene"),
        key=lambda item: item["position"],
    )
    assert [s["title"] for s in scenes] == ["One", "The watch", "Two"]
    watch = next(s for s in scenes if s["title"] == "The watch")
    path = _item(auth_client, w, arc["id"])["path"]
    assert path[2]["scene_id"] == watch["id"]
    assert "arc_stage_not_placed" not in {o["code"] for o in outline["open"]}


def test_the_plan_check_is_told_each_arc_a_scene_moves_to_look_for_the_change(
    auth_client, fake_container
) -> None:
    """STY-006: a stage reached in a scene reaches the plan's check, for the viewpoint
    character's arc and anyone else's, so a scene with no sign of the change gets a note
    that quotes where it belongs. Someone else's stage never brings its fact along."""
    from backend.services.scene_checks import CheckNote, CheckNotes
    from tests.contract.test_studio import _save

    w = _world(auth_client, fake_container)
    scene = _add(
        auth_client,
        w,
        "scene",
        "The watch",
        refs=[
            {"entity_id": w["mara"], "role": "point_of_view"},
            {"entity_id": w["tobin"], "role": "participant"},
        ],
    )
    tobin = _add(
        auth_client, w, "arc", "Tobin's doubt", refs=[{"entity_id": w["tobin"], "role": "about"}]
    )
    _put(
        auth_client,
        w,
        tobin["id"],
        "path",
        {
            "stages": [
                {"name": "Loyal to the guild"},
                {"name": "Doubts the guild", "scene_id": scene["id"], "fact_id": w["in_guild"]},
            ]
        },
    )
    context = auth_client.get(f"{w['base']}/plan/{scene['id']}/context").json()
    [given] = [i for i in context["items"] if i["key"] == f"arc:{scene['id']}:{tobin['id']}"]
    assert given["section"] == "plan"
    assert given["text"] == (
        "Tobin Ashe's arc “Tobin's doubt”: going in, Tobin Ashe is at “Loyal to the guild”; "
        "in this scene, Tobin Ashe reaches “Doubts the guild”."
    )
    prose = "Tobin kept watch on the quay. Mara slept in the boat."
    _save(auth_client, w, scene, prose, None)
    fake_container.ai.queue(
        CheckNotes(
            notes=[
                CheckNote(
                    quote="Tobin kept watch on the quay",
                    problem="The plan says Tobin starts to doubt the guild here; nothing shows it.",
                )
            ]
        )
    )
    started = auth_client.post(
        f"{w['base']}/plan/{scene['id']}/studio/reviews", json={"passes": ["plan"]}
    )
    assert started.status_code == 202, started.text
    (done,) = _run(fake_container)
    assert done.result["findings"] == 1
    call = fake_container.ai.calls[-1]
    assert "reaches a stage of their arc" in call["messages"][0]["content"]
    assert "reaches “Doubts the guild”" in call["messages"][1]["content"]


def _staged_arc(client, w) -> tuple[dict, dict, dict]:
    one = _add(client, w, "scene", "One")
    two = _add(client, w, "scene", "Two")
    arc = _arc(client, w)
    _put(
        client,
        w,
        arc["id"],
        "path",
        {
            "stages": [
                {"name": "Alone"},
                {"name": "Wary", "scene_id": one["id"]},
                {"name": "Trusting"},
                {"name": "Loyal", "scene_id": two["id"]},
            ]
        },
    )
    return one, two, arc


def _while_thinking(container, change) -> None:
    """Make ``change`` while the stand-in model is answering."""
    answer = container.ai.generate_structured

    async def answering(**kwargs):  # type: ignore[no-untyped-def]
        change()
        return await answer(**kwargs)

    container.ai.generate_structured = answering


def _watch() -> BridgeSheet:
    return BridgeSheet(
        bridges=[
            BridgeDraft(
                title="The watch",
                summary="Tobin keeps watch while Mara sleeps.",
                how_it_helps="She trusts him with her sleep.",
            )
        ]
    )


def test_a_stage_gap_keeps_what_the_author_changed_on_the_arc_while_it_thought(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    one, two, arc = _staged_arc(auth_client, w)
    started = auth_client.post(f"{w['base']}/plan/{arc['id']}/gap/suggest", params={"stage": 2})
    assert started.status_code == 202, started.text
    fake_container.ai.queue(_watch())
    _while_thinking(
        fake_container,
        lambda: _put(
            auth_client,
            w,
            arc["id"],
            "path",
            {
                "stages": [
                    {"name": "Lonely"},
                    {"name": "Wary", "scene_id": one["id"]},
                    {"name": "Trusting"},
                    {"name": "Loyal", "scene_id": two["id"]},
                    {"name": "Devoted"},
                ]
            },
        ),
    )
    (done,) = _run(fake_container)
    assert done.status.value == "succeeded", done
    _accept(auth_client, w, done.result["mutation_id"])
    path = _item(auth_client, w, arc["id"])["path"]
    # The author's renamed first stage and new last stage are kept; only the gap is filled.
    assert [stage["name"] for stage in path] == ["Lonely", "Wary", "Trusting", "Loyal", "Devoted"]
    watch = next(i for i in _outline(auth_client, w)["items"] if i["title"] == "The watch")
    assert path[2]["scene_id"] == watch["id"]


def test_a_stage_gap_is_refused_when_the_stage_was_taken_off_while_it_thought(
    auth_client, fake_container
) -> None:
    w = _world(auth_client, fake_container)
    one, two, arc = _staged_arc(auth_client, w)
    started = auth_client.post(f"{w['base']}/plan/{arc['id']}/gap/suggest", params={"stage": 2})
    assert started.status_code == 202, started.text
    fake_container.ai.queue(_watch())
    _while_thinking(
        fake_container,
        lambda: _put(
            auth_client,
            w,
            arc["id"],
            "path",
            {
                "stages": [
                    {"name": "Alone"},
                    {"name": "Wary", "scene_id": one["id"]},
                    {"name": "Loyal", "scene_id": two["id"]},
                ]
            },
        ),
    )
    (done,) = _run(fake_container)
    assert done.status.value == "failed" and done.error_code == "refused", done
    assert "The arc changed while the assistant was thinking" in done.error_message
    assert [s["name"] for s in _item(auth_client, w, arc["id"])["path"]] == [
        "Alone",
        "Wary",
        "Loyal",
    ]
