"""Judging a run's findings now (CON-001): open, dismissed or fixed, and out of date."""

from datetime import UTC, datetime

import pytest

from backend.domain.consistency_runs import (
    ConsistencyRun,
    RecordedCheck,
    RunInput,
    Staleness,
    judge_check,
    judge_note,
    story_change_touches,
    words_gone,
)
from backend.domain.dismissals import DismissReason, FindingDismissal, FindingKind

pytestmark = pytest.mark.unit

THEN = "Tobin waited. The air was palpable. Mara did not come."
SPAN = (THEN.index("palpable"), THEN.index("palpable") + len("palpable"))


def _dismissal(key: str) -> FindingDismissal:
    return FindingDismissal(
        workspace_id="w",
        project_id="p",
        branch_id="b",
        id="d",
        plan_id="s",
        fingerprint=key,
        kind=FindingKind.CHECK,
        code="stock_phrase",
        reason=DismissReason.INTENDED,
        note="",
        expires_at=None,
        created_by=None,
        created_at=datetime.now(UTC),
    )


def test_a_check_is_fixed_by_its_words_or_by_the_story():
    common = {"key": "k", "then": THEN, "start": SPAN[0], "end": SPAN[1], "dismissed": {}}
    still = judge_check(now=THEN, found_now={"k"}, **common)
    assert still.status == "open"
    words = judge_check(
        now=THEN.replace("The air was palpable.", "Cold."), found_now=set(), **common
    )
    assert (words.status, words.fixed_by) == ("fixed", "words")
    story = judge_check(now=THEN, found_now=set(), **common)
    assert (story.status, story.fixed_by) == ("fixed", "story")
    dismissed = judge_check(
        now=THEN, found_now={"k"}, **{**common, "dismissed": {"k": _dismissal("k")}}
    )
    assert dismissed.status == "dismissed" and dismissed.dismissal is not None
    # A finding about the whole scene, gone after the words changed: the words fixed it.
    whole = judge_check(
        key="k", then=THEN, now=THEN + " More.", start=None, end=None, found_now=set(), dismissed={}
    )
    assert whole.fixed_by == "words"


def test_a_note_is_fixed_only_when_its_words_are_gone():
    common = {"key": "k", "then": THEN, "start": SPAN[0], "end": SPAN[1], "dismissed": {}}
    assert judge_note(now=THEN + " Later.", **common).status == "open"
    assert judge_note(now="Something else entirely.", **common).fixed_by == "words"
    assert judge_note(now=None, **common).status == "fixed"
    assert words_gone(THEN, THEN, None, None) is False


def test_staleness_reasons_and_what_a_change_names():
    stale = Staleness(words_changed=True, story_changed=True)
    assert stale.stale and stale.reasons() == [
        "The words have changed since.",
        "The story bible has changed since, in what this scene names.",
    ]
    assert not Staleness().stale
    change = {"proposition": {"subject_id": "tobin", "object": {"entity_id": "harbor"}}}
    assert story_change_touches(change, "fact:1", {"harbor"})
    assert not story_change_touches(change, "fact:1", {"mara"})
    assert story_change_touches({}, "plan:s", {"plan:s"})


def test_inputs_and_findings_keep_their_shape_through_json():
    item = RunInput(plan_id="s", source_id="t", version_id="v")
    assert RunInput.from_json(item.as_json()) == item
    check = RecordedCheck("s", "stock_phrase", "note", "words", 1, 4, "palpable")
    assert RecordedCheck.from_json(check.as_json()) == check
    run = ConsistencyRun(
        workspace_id="w",
        project_id="p",
        id="r",
        branch_id="b",
        kind="audit",
        scope="part",
        plan_id="beat",
        story_revision=3,
        inputs=(item,),
    )
    assert run.touches("s") and not run.touches("other")
