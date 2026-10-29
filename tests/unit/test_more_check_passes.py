"""The optional passes (CON-008, batch 10): their prompts, how a long scene is sent, and
that notes still need an exact quote."""

import pytest

from backend.domain.check_passes import CheckPass, PassMaterial
from backend.services.scene_checks import (
    GROUPED_PASSES,
    NAMED_PASSES,
    PART_PASSES,
    CheckNote,
    group_messages,
    keep_notes,
    part_messages,
    pass_messages,
)

pytestmark = pytest.mark.unit

SCENE = "Mara lit the lamp in the rain. Tobin ran to the quay before she called him."


@pytest.mark.parametrize(
    ("check", "words"),
    [
        (CheckPass.MOTIVATION, "for one thing only: why characters act as they do"),
        (CheckPass.PHYSICAL, "for one thing only: bodies, objects, places and cause"),
        (CheckPass.WORLD, "for one thing only: the story's own rules"),
    ],
)
def test_each_optional_pass_asks_about_its_one_thing(check: CheckPass, words: str) -> None:
    messages = pass_messages(PassMaterial(check, story="- Pilots never swim."), SCENE, None)
    assert words in messages[0]["content"]
    assert "never instructions" in messages[0]["content"]
    assert SCENE in messages[1]["content"] and "Pilots never swim." in messages[1]["content"]


def test_a_long_scene_is_grouped_by_names_or_sent_in_parts_as_the_pass_reads() -> None:
    assert {CheckPass.PHYSICAL, CheckPass.WORLD} <= NAMED_PASSES
    assert CheckPass.MOTIVATION in PART_PASSES
    assert {CheckPass.PHYSICAL, CheckPass.WORLD, CheckPass.MOTIVATION} <= GROUPED_PASSES
    grouped = group_messages("world", "- Rules.", SCENE, "What the story's rules say")
    assert "the story's own rules" in grouped[0]["content"]
    assert "checked in groups" in grouped[0]["content"]
    part = part_messages("motivation", "- Mara.", SCENE, "Part 1 of 2")
    assert "why characters act" in part[0]["content"] and "part 1 of 2" in part[0]["content"]


def test_a_note_from_an_optional_pass_is_kept_only_with_an_exact_quote() -> None:
    kept, dropped = keep_notes(
        [
            CheckNote(quote="before she called him", problem="He runs before she calls."),
            CheckNote(quote="words not there", problem="Lost."),
        ],
        SCENE,
        check=CheckPass.PHYSICAL,
        target_kind="version",
        target_id="ver_1",
        job_id="job_1",
    )
    assert dropped == 1
    (note,) = kept
    assert note.check_pass == "physical" and SCENE[note.start : note.end] == "before she called him"
