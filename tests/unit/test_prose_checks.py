"""Checks of a scene's prose against its plan and the story bible (STY-005, SCN-007,
CON-004, CON-014). No model: each reads a signal it can be sure of."""

from __future__ import annotations

from backend.domain.appearance import AppearanceDetail, clashes, detail_words, traits
from backend.domain.drafting import (
    AfterShown,
    DraftBrief,
    Holding,
    Severity,
    TimeNeighbour,
    check_draft,
    days_between,
    prose_findings,
)

ALSO = {
    "Wren Halloway": ("Wren",),
    "Osric Pell": ("Osric",),
    "the brass sextant": ("the sextant",),
}
PEOPLE = ("Wren Halloway", "Osric Pell")
WREN = AppearanceDetail(
    "Wren Halloway", ("Wren Halloway",), "Grey eyes, short dark hair.", "an accepted detail"
)


def _brief(**fields) -> DraftBrief:
    return DraftBrief(people=PEOPLE, also=ALSO, **fields)


def _codes(text: str, brief: DraftBrief) -> list[str]:
    return [finding.code for finding in prose_findings(text, brief)]


# ------------------------------------------------------------------ appearance


def test_colour_words_are_read_before_and_after_the_feature() -> None:
    found = traits("Short dark brown hair, grey eyes; red-haired; her eyes were blue.")
    assert [(t.feature, t.colour) for t in found] == [
        ("hair", "brown"),
        ("eyes", "grey"),
        ("hair", "red"),
        ("eyes", "blue"),
    ]


def test_a_colour_of_something_else_is_not_read_as_the_eyes() -> None:
    assert [t.colour for t in traits("a blue coat and grey eyes")] == ["grey"]


def test_prose_that_gives_other_eyes_is_a_note_quoting_both() -> None:
    text = "Wren looked up from the ropes, her blue eyes narrowed against the glare."
    findings = prose_findings(text, _brief(appearance=[WREN]))
    assert [f.code for f in findings] == ["appearance_differs"]
    finding = findings[0]
    assert finding.severity is Severity.NOTE
    assert text[finding.start : finding.end] == "blue eyes"
    assert "“blue eyes”" in finding.message and "Grey eyes, short dark hair." in finding.message


def test_words_between_two_colours_agree_with_either() -> None:
    hazel = AppearanceDetail("Wren Halloway", ("Wren Halloway",), "Hazel eyes.")
    assert _codes("Wren's green eyes caught the lamp.", _brief(appearance=[hazel])) == []
    assert _codes("Wren's dark hair was wet.", _brief(appearance=[WREN])) == []


def test_a_sentence_that_says_not_or_dyed_is_left_alone() -> None:
    brief = _brief(appearance=[WREN])
    assert _codes("Wren's eyes were not blue, whatever Osric said.", brief) == []
    assert _codes("Wren had dyed her hair red for the crossing.", brief) == []


def test_a_sentence_naming_two_people_is_read_only_through_the_possessive() -> None:
    brief = _brief(appearance=[WREN])
    # Whose blue eyes? Osric is named too, so it is not read.
    assert _codes("Wren watched Osric, his blue eyes on the tide.", brief) == []
    assert _codes("Osric met Wren's blue eyes and looked away.", brief) == ["appearance_differs"]


def test_what_a_character_says_aloud_is_not_read() -> None:
    assert _codes("“Wren has blue eyes,” Osric lied.", _brief(appearance=[WREN])) == []


def test_a_pronoun_alone_is_never_read() -> None:
    assert _codes("She rubbed her blue eyes.", _brief(appearance=[WREN])) == []


BLUE = AppearanceDetail("Wren Halloway", ("Wren Halloway", "Wren"), "Blue eyes; brown hair.")


def test_someone_elses_eyes_in_her_sentence_are_not_read_as_hers() -> None:
    brief = _brief(appearance=[BLUE])
    assert _codes("Wren stared into his grey eyes.", brief) == []
    assert _codes("Wren smiled at the green-eyed boy.", brief) == []
    assert _codes("Wren looked into Tamsin's grey eyes.", brief) == []
    # Still hers.
    assert _codes("Wren rubbed her grey eyes.", brief) == ["appearance_differs"]
    assert _codes("Wren squinted, her grey eyes stinging.", brief) == ["appearance_differs"]
    assert _codes("Grey-eyed Wren rowed on.", brief) == ["appearance_differs"]


def test_bruises_tears_ribbons_and_a_passing_look_are_not_colour() -> None:
    brief = _brief(appearance=[BLUE])
    for text in (
        "Wren had a black eye from the fight.",
        "Wren came in red-eyed.",
        "Wren's eyes were red and swollen.",
        "Wren tied a red hair ribbon round the oar.",
        "Wren's hair gleamed gold in the lamplight.",
        "Wren's hair was white with frost.",
    ):
        assert _codes(text, brief) == [], text
    assert _codes("Wren's hair was black.", brief) == ["appearance_differs"]


def test_a_picture_detail_names_the_picture() -> None:
    seen = AppearanceDetail(
        "Wren Halloway",
        ("Wren Halloway", "Wren"),
        "Red hair tied back.",
        "the picture “Wren at the quay”",
    )
    found = clashes("Wren pushed her black hair out of her face.", [seen])
    assert found and found[0].detail.source == "the picture “Wren at the quay”"
    [finding] = prose_findings(
        "Wren pushed her black hair out of her face.", _brief(appearance=[seen])
    )
    assert "the picture “Wren at the quay”" in finding.message


def test_detail_words_reads_a_stored_value() -> None:
    assert detail_words({"appearance": "Grey eyes."}) == "Grey eyes."
    assert detail_words({"hair": "red", "n": 3}) == "red"
    assert detail_words(None) == ""


# ------------------------------------------------------------------ time words


def _after(days: tuple[float, float], same_day: bool | None = None) -> DraftBrief:
    return _brief(previous=TimeNeighbour("The Crossing", days, same_day, "day 5", "day 5"))


def test_next_morning_on_the_same_day_is_a_warning() -> None:
    [finding] = prose_findings("The next morning, Wren untied the ferry.", _after((0, 1), True))
    assert finding.code == "time_words_next_day"
    assert finding.severity is Severity.WARNING
    assert "“The Crossing” is day 5" in finding.message


def test_next_morning_a_day_later_is_fine() -> None:
    assert _codes("The next morning, Wren untied the ferry.", _after((0, 2), False)) == []


def test_next_day_many_days_later_is_a_note() -> None:
    [finding] = prose_findings("The next day the fog came in.", _after((9, 11), False))
    assert finding.severity is Severity.NOTE and "at least 9 days" in finding.message


def test_that_evening_two_days_on_is_a_warning() -> None:
    [finding] = prose_findings("Later that day, Osric lit the lamp.", _after((2, 4), False))
    assert finding.code == "time_words_same_day" and finding.severity is Severity.WARNING


def test_an_amount_far_from_story_time_is_a_note() -> None:
    assert _codes("Three days later the ferry ran again.", _after((14, 16), False)) == [
        "time_words_amount"
    ]
    assert _codes("Two weeks later the ferry ran again.", _after((14, 16), False)) == []


def test_words_for_when_later_in_the_scene_are_not_read() -> None:
    text = "Wren checked the ropes. She mended a net. She ate.\n\nThe next morning, it rained."
    assert _codes(text, _after((0, 1), True)) == []


def test_words_for_when_spoken_aloud_are_not_read() -> None:
    assert _codes("“The next morning we sail,” said Wren.", _after((0, 1), True)) == []


def test_a_scene_before_the_previous_one_in_story_time() -> None:
    assert _codes("The next day Wren woke early.", _after((-30, -28), False)) == [
        "time_words_before"
    ]


def test_words_for_when_that_are_hedged_or_denied_are_not_read() -> None:
    same_day = _after((0, 1), True)
    assert _codes("Wren promised to be gone by the next morning.", same_day) == []
    assert _codes("Wren did not wait for the next morning.", same_day) == []


def test_days_on_the_road_are_not_days_later() -> None:
    far = _after((14, 16), False)
    assert _codes("Three days on the road had worn Wren thin.", far) == []
    assert _codes("Three days on, Wren reached the quay.", far) == ["time_words_amount"]


def test_a_day_later_is_read_once() -> None:
    assert _codes("A day later, Wren reached the quay.", _after((9, 11), False)) == [
        "time_words_next_day"
    ]


def test_days_between_counts_whole_units() -> None:
    assert days_between((5, 5), (5, 5), "day", whole=True) == (-1.0, 1.0)
    assert days_between((5, 5), (6, 6), "day", whole=True) == (0.0, 2.0)
    assert days_between((1, 1), (2, 2), "year", whole=True) == (0.0, 730.0)
    assert days_between((10, 10), (34, 34), "hour", whole=False) == (1.0, 1.0)
    assert days_between((1, 1), (2, 2), "turn of the wheel", whole=True) is None


# ------------------------------------------------------------------ resources


def test_using_what_someone_else_has_then_is_a_note() -> None:
    held = Holding("the brass sextant", ("the brass sextant",), "Osric Pell", ("Osric Pell",))
    text = "Wren lifted the sextant and took a sighting on the light."
    [finding] = prose_findings(text, _brief(holdings=[held]))
    assert finding.code == "resource_held_elsewhere"
    assert finding.severity is Severity.NOTE
    assert "Osric Pell has it" in finding.message


def test_the_holder_named_with_it_is_fine() -> None:
    held = Holding("the brass sextant", ("the brass sextant",), "Osric Pell", ("Osric Pell",))
    assert _codes("Wren took the sextant Osric held out.", _brief(holdings=[held])) == []
    assert _codes("Wren remembered the sextant.", _brief(holdings=[held])) == []
    assert _codes("Osric lifted the sextant.", _brief(holdings=[held])) == []


def test_the_thing_far_from_the_verb_is_not_used() -> None:
    held = Holding("the Kingsblade", ("the Kingsblade",), "Osric Pell", ("Osric Pell",))
    brief = _brief(holdings=[held])
    assert _codes("Wren turned toward the window where the Kingsblade hung.", brief) == []
    assert _codes("Wren held her breath and stared at the Kingsblade.", brief) == []
    assert _codes("Wren passed the Kingsblade on her way out.", brief) == []
    assert _codes("Wren held up the Kingsblade.", brief) == ["resource_held_elsewhere"]


def test_using_what_is_used_up_is_a_note() -> None:
    gone = Holding("the flare", ("the flare",), gone="its status is used up")
    [finding] = prose_findings("Wren lit the flare.", _brief(holdings=[gone]))
    assert finding.code == "resource_used_up" and "used up" in finding.message


# ------------------------------------------------------------------ after conditions


def test_an_after_condition_never_named_together_is_a_note() -> None:
    after = AfterShown("Wren Halloway is at Marrow Light", ("Wren Halloway", "Marrow Light"))
    brief = _brief(after=[after])
    [finding] = prose_findings("Wren rowed hard.\n\nThe light grew.", brief)
    assert finding.code == "after_not_shown" and finding.severity is Severity.NOTE
    assert _codes("Wren rowed hard and reached Marrow Light at dusk.", brief) == []


def test_the_new_checks_run_with_the_others() -> None:
    brief = _brief(appearance=[WREN])
    codes = {f.code for f in check_draft("Wren's blue eyes watered.", brief, [])}
    assert "appearance_differs" in codes


# ------------------------------------------------------------------ gathering pictures


def test_accepted_picture_details_are_gathered_naming_the_picture() -> None:
    import asyncio
    from types import SimpleNamespace

    from backend.domain.media_observations import (
        MediaObservation,
        ObservationKind,
        ObservationState,
    )
    from backend.services.prose_signals import _pictures_seen

    def link(asset: str, target: str, applies_from=None):
        return SimpleNamespace(
            is_active=True,
            target_record_type="entity",
            target_record_id=target,
            asset_id=asset,
            applies_from=applies_from,
        )

    def seen(asset: str, state: ObservationState, kind=ObservationKind.APPEARANCE, subject="wren"):
        return MediaObservation(
            workspace_id="w",
            project_id="p",
            id=f"obs_{asset}_{state.value}_{kind.value}",
            asset_id=asset,
            asset_version_id=f"v_{asset}",
            kind=kind,
            text="Red hair.",
            subject_record_type="entity",
            subject_record_id=subject,
            state=state,
        )

    class Reader:
        async def list_links(self, **_):
            return [
                link("a1", "wren"),
                link("a2", "osric"),
                # Applies only from year 50: the scene, in year 5, is before it.
                link("a3", "wren", {"kind": "at", "at": 50}),
            ]

    class Store:
        async def for_assets(self, **kwargs):
            return [
                seen(asset, state, kind)
                for asset in kwargs["asset_ids"]
                for state in (ObservationState.ACCEPTED, ObservationState.PROPOSED)
                for kind in (ObservationKind.APPEARANCE, ObservationKind.CAPTION)
            ]

    class Pictures:
        _store = Store()

        async def for_context(self, _w, _p, ids):
            return {
                asset: SimpleNamespace(
                    asset=SimpleNamespace(name=f"Picture {asset}"),
                    version=SimpleNamespace(id=f"v_{asset}"),
                )
                for asset in ids
            }

    from backend.domain.worldtime import Interval

    found = asyncio.run(
        _pictures_seen(
            Reader(),
            Pictures(),
            "w",
            "p",
            SimpleNamespace(id="scene"),
            ["wren"],
            {"wren": "Wren Halloway"},
            {"wren": ("Wren Halloway", "Wren")},
            {},
            Interval(5, 5),
        )
    )
    assert [(d.who, d.words, d.source) for d in found] == [
        ("Wren Halloway", "Red hair.", "the picture “Picture a1”")
    ]
