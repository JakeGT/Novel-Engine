"""What each pass of a check reads (CON-008)."""

import pytest

from backend.domain.check_passes import (
    DEFAULT_PASSES,
    OPTIONAL_PASSES,
    PASS_ORDER,
    CheckPass,
    choose_passes,
    estimate_pass,
    pass_material,
)
from backend.domain.context import ContextItem, ContextSection, LeftOut, assemble

pytestmark = pytest.mark.unit


def _item(key: str, section: ContextSection, text: str, hard: bool = False) -> ContextItem:
    return ContextItem(
        key=key,
        section=section,
        text=text,
        reason="test",
        authority="fact",
        record_type="fact",
        record_id=key,
        score=5,
        hard=hard,
    )


ITEMS = [
    _item("scene", ContextSection.SCENE, "The Docks. Tobin waits.", hard=True),
    _item("cast", ContextSection.CAST, "Tobin Ashe (character)."),
    _item("true", ContextSection.TRUE, "Mara is at the harbour."),
    _item("knows", ContextSection.KNOWS, "Tobin believes the tide is out."),
    _item("plan", ContextSection.PLAN, "This scene opens the thread “The map”."),
    _item("rule", ContextSection.RULES, "Pilots never swim."),
    _item("writing", ContextSection.WRITING, "The harbour slept."),
    _item("secret", ContextSection.SECRETS, "Mara is in the guild. Tobin does not know this."),
]


def test_passes_are_chosen_in_their_order_once_and_none_means_the_usual_ones() -> None:
    assert choose_passes(None) == DEFAULT_PASSES
    assert not set(DEFAULT_PASSES) & OPTIONAL_PASSES
    assert set(DEFAULT_PASSES) | OPTIONAL_PASSES == set(PASS_ORDER)
    assert choose_passes(["world", "motivation", "physical"]) == (
        CheckPass.MOTIVATION,
        CheckPass.PHYSICAL,
        CheckPass.WORLD,
    )
    assert choose_passes(["clarity", "viewpoint", "clarity"]) == (
        CheckPass.VIEWPOINT,
        CheckPass.CLARITY,
    )
    assert choose_passes([]) == ()
    with pytest.raises(ValueError):
        choose_passes(["spelling"])


def test_the_viewpoint_pass_gets_the_secrets_apart_even_when_left_out_of_the_context() -> None:
    manifest = assemble(ITEMS)  # secrets left out, as by default
    assert all(item.section is not ContextSection.SECRETS for item in manifest.items)
    viewpoint = pass_material(CheckPass.VIEWPOINT, manifest)
    assert "Mara is in the guild" in viewpoint.secrets
    assert "Mara is in the guild" not in viewpoint.story
    assert "Tobin believes the tide is out." in viewpoint.story
    assert "Pilots never swim." not in viewpoint.story
    # No other pass is given a secret the author left out.
    for check in PASS_ORDER[1:]:
        material = pass_material(check, manifest, voice="Short sentences.")
        assert "Mara is in the guild" not in material.story + material.secrets + material.voice


def test_a_secret_the_author_removed_is_not_given_to_the_viewpoint_pass() -> None:
    manifest = assemble(ITEMS, excludes=["secret"])
    [omission] = [o for o in manifest.left_out if o.item.key == "secret"]
    assert omission.reason is LeftOut.EXCLUDED
    assert "Mara is in the guild" not in pass_material(CheckPass.VIEWPOINT, manifest).secrets
    # Cut for space, it is still given: the pass needs it to spot a leak.
    long = _item("secret", ContextSection.SECRETS, "Mara is in the guild. " + "And more. " * 200)
    cut = assemble([*ITEMS[:-1], long], include_secrets=True, budget=1)
    assert any(o.item.key == "secret" and o.reason is LeftOut.BUDGET for o in cut.left_out)
    assert "Mara is in the guild" in pass_material(CheckPass.VIEWPOINT, cut).secrets


def test_each_pass_reads_its_own_part_of_the_story() -> None:
    manifest = assemble(ITEMS)
    continuity = pass_material(CheckPass.CONTINUITY, manifest)
    assert (
        "Pilots never swim." in continuity.story and "Mara is at the harbour." in continuity.story
    )
    assert "opens the thread" not in continuity.story
    plan = pass_material(CheckPass.PLAN, manifest)
    assert "opens the thread" in plan.story and "Pilots never swim." not in plan.story
    voice = pass_material(CheckPass.VOICE, manifest, voice="Short sentences.")
    assert voice.voice == "Short sentences." and "The harbour slept." in voice.story
    assert "Mara is at the harbour." not in voice.story
    clarity = pass_material(CheckPass.CLARITY, manifest)
    assert "The Docks." in clarity.story and "Mara is at the harbour." not in clarity.story


def test_a_secret_the_author_chose_to_give_reaches_continuity_too() -> None:
    manifest = assemble(ITEMS, include_secrets=True)
    assert "Mara is in the guild" in pass_material(CheckPass.CONTINUITY, manifest).story


def test_an_estimate_grows_with_what_a_pass_reads() -> None:
    manifest = assemble(ITEMS)
    small = estimate_pass(pass_material(CheckPass.CLARITY, manifest), "Tobin waited.")
    large = estimate_pass(pass_material(CheckPass.CLARITY, manifest), "Tobin waited. " * 200)
    assert large.prompt_tokens > small.prompt_tokens
    assert small.completion_tokens == large.completion_tokens > 0
    assert small.total == small.prompt_tokens + small.completion_tokens


def test_the_optional_passes_each_read_their_own_part_and_never_a_secret() -> None:
    manifest = assemble(ITEMS)
    motivation = pass_material(CheckPass.MOTIVATION, manifest)
    assert "Tobin believes the tide is out." in motivation.story
    assert "opens the thread" in motivation.story
    assert "Pilots never swim." not in motivation.story
    physical = pass_material(CheckPass.PHYSICAL, manifest)
    assert "Mara is at the harbour." in physical.story
    assert "Pilots never swim." not in physical.story and "opens the thread" not in physical.story
    world = pass_material(CheckPass.WORLD, manifest)
    assert "Pilots never swim." in world.story and "Mara is at the harbour." in world.story
    assert "Tobin believes the tide is out." not in world.story
    for check in OPTIONAL_PASSES:
        material = pass_material(check, assemble(ITEMS, include_secrets=True))
        assert "Mara is in the guild" not in material.story + material.secrets
        assert estimate_pass(material, "Tobin waited.").total > 0
