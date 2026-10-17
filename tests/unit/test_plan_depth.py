"""Thread steps, scene intent and context comparison as pure functions (STY-002/003, CTX-009)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.domain.canon.plans import PlanItem, PlanKind
from backend.domain.context import (
    ContextItem,
    ContextSection,
    SavedManifest,
    assemble,
    compare_contexts,
    stored_manifest,
)
from backend.domain.scene_intent import SceneIntent, intent_sentences, scene_intent, with_intent
from backend.domain.threads import (
    ThreadStep,
    never_moved_along,
    outline_order,
    thread_advances,
    thread_progress,
    with_advances,
)

pytestmark = pytest.mark.unit


def _plan(id_: str, kind: PlanKind, position: int = 0, parent: str | None = None, **fields):
    return PlanItem(
        id=id_,
        workspace_id="w",
        project_id="p",
        branch_id="b",
        kind=kind,
        title=id_,
        parent_id=parent,
        position=position,
        provenance_id="prov",
        created_revision=1,
        **fields,
    )


def test_steps_follow_the_outline_and_beats_count_too() -> None:
    items = [
        _plan("t", PlanKind.THREAD),
        _plan("arc", PlanKind.ARC),
        _plan("beat", PlanKind.BEAT, 0, "arc", sets_up=["t"]),
        _plan("s1", PlanKind.SCENE, 0, "beat"),
        _plan("s2", PlanKind.SCENE, 1, "beat", details=with_advances({}, ["t"])),
        _plan("s3", PlanKind.SCENE, 1, "arc", pays_off=["t"]),
    ]
    assert [item.id for item in outline_order(items)] == ["arc", "beat", "s1", "s2", "s3"]
    assert thread_progress(items)["t"] == [
        ThreadStep("beat", "opens"),
        ThreadStep("s2", "advances"),
        ThreadStep("s3", "resolves"),
    ]


def test_a_gap_only_when_scenes_lie_between_and_nothing_moves_the_thread() -> None:
    def order(*kinds: PlanKind) -> list[PlanItem]:
        return [_plan(f"i{n}", kind, n) for n, kind in enumerate(kinds)]

    three = order(PlanKind.SCENE, PlanKind.SCENE, PlanKind.SCENE)
    opened_and_resolved = [ThreadStep("i0", "opens"), ThreadStep("i2", "resolves")]
    assert never_moved_along(opened_and_resolved, three)
    assert not never_moved_along([*opened_and_resolved, ThreadStep("i1", "advances")], three)
    assert not never_moved_along([ThreadStep("i0", "opens"), ThreadStep("i1", "resolves")], three)
    assert not never_moved_along([ThreadStep("i0", "opens")], three)
    # A beat between is not a scene.
    beat_between = order(PlanKind.SCENE, PlanKind.BEAT, PlanKind.SCENE)
    assert not never_moved_along(opened_and_resolved, beat_between)


def test_unreadable_steps_are_none_and_duplicates_collapse() -> None:
    assert thread_advances({"_advances": "not json"}) == []
    assert thread_advances({"_advances": '{"a": 1}'}) == []
    assert thread_advances({"_advances": '["a", "a", 3, "b"]'}) == ["a", "b"]
    assert with_advances({"_advances": '["a"]', "mood": "cold"}, []) == {"mood": "cold"}


def test_intent_is_cleaned_cleared_and_read_as_plain_sentences() -> None:
    details = with_intent({}, SceneIntent(goal="  get   the key ", outcome="yes_but"))
    assert scene_intent(details) == SceneIntent(goal="get the key", outcome="yes_but")
    assert with_intent(details, SceneIntent()) == {}
    assert scene_intent({"_intent": "{broken"}) is None
    assert intent_sentences(SceneIntent(goal="get the key.", obstacle="a locked door"), "Ines") == (
        "Ines wants: get the key. In the way: a locked door."
    )
    assert intent_sentences(SceneIntent(outcome="no"), None) == "How it ends: they do not get it."


def _item(key: str, text: str, section=ContextSection.TRUE, authority="fact", reason="because"):
    return ContextItem(
        key=key,
        section=section,
        text=text,
        reason=reason,
        authority=authority,
        record_type="fact",
        record_id=key,
        score=5,
    )


def _saved(manifest_items: list[dict], checksum: str = "old") -> SavedManifest:
    return SavedManifest(
        id="ctx_1",
        purpose="scene-draft",
        plan_id="s",
        policy_version="scene-draft/1",
        checksum=checksum,
        words=10,
        budget=2500,
        manifest={"items": manifest_items},
        created_by=None,
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


def test_comparison_finds_added_removed_and_changed_by_key() -> None:
    before = assemble(
        [
            _item("a", "Mara is at the harbour."),
            _item("b", "Tobin is a pilot."),
            _item("c", "The guild meets.", reason="old reason"),
            _item(
                "w", "From “Notes”: the sea.", section=ContextSection.WRITING, authority="writing"
            ),
        ]
    )
    kept = _saved(stored_manifest(before, None)["items"], before.checksum)
    assert compare_contexts(before, kept).same

    after = assemble(
        [
            _item("a", "Mara is at the lighthouse."),
            _item("c", "The guild meets.", reason="new reason"),
            _item("d", "A new fact."),
            _item(
                "w", "From “Notes”: the sea.", section=ContextSection.WRITING, authority="writing"
            ),
        ],
        excludes=["b"],
    )
    result = compare_contexts(after, kept)
    assert not result.same
    assert [item.key for item in result.added] == ["d"]
    assert [(then.key, then.text) for then, _ in result.removed] == [("b", "Tobin is a pilot.")]
    changed = {change.now.key: change for change in result.changed}
    assert (
        changed["a"].what == ["the words"] and changed["a"].then.text == "Mara is at the harbour."
    )
    assert changed["c"].what == ["why it is given"]
    assert result.unchanged == 1  # the passage, compared by its fingerprint alone


def test_a_context_kept_before_fingerprints_compares_by_length() -> None:
    now = assemble([_item("a", "Mara is at the lighthouse now."), _item("b", "Same words.")])
    kept = _saved(
        [
            {"key": "a", "section": "true", "reason": "because", "words": 5},
            {"key": "b", "section": "true", "reason": "because", "words": 2},
        ]
    )
    result = compare_contexts(now, kept)
    assert [change.now.key for change in result.changed] == ["a"]
    assert result.changed[0].then.text is None and result.unchanged == 1
