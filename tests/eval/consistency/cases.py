"""Labelled consistency cases for the deterministic checks (CON-012).

Each case is a small piece of the Saltglass Ferry story, labelled by hand as holding a
defect (something that cannot all be true, or that the scene should show) or as clean
(a near miss a check must leave alone). Running it calls the production check exactly
as the application does and returns the finding codes it raised.

A case whose label the check is *expected* to disagree with carries ``known_limit``:
the reason, in plain words. Those are what the deterministic checks do not cover (a
death recorded with a word the vocabulary lacks, a pronoun, a negation); the eval
reports them instead of hiding them, and the pinned test fails if one starts or stops
disagreeing, so a change in coverage is always a decision.

Families: ``continuity`` (backend/domain/continuity.py), ``rules`` (rules.py),
``knowledge`` (acquisition.py), ``drafting`` (drafting.py), ``plan`` (reveals,
thread order and scene conditions) and ``prose`` (the prose against the plan and the
story bible: appearance, words for when, things in hand, "after" conditions). Tags: ``leakage`` marks a case about knowledge or
a secret reaching a page or a context it must not.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from backend.domain.acquisition import held_at, learned_later, placement, said_before_learned
from backend.domain.appearance import AppearanceDetail
from backend.domain.canon.events import EventClaim
from backend.domain.continuity import (
    HowLong,
    Lasting,
    StoryParts,
    TravelTime,
    continuity_findings,
)
from backend.domain.drafting import (
    AfterShown,
    DraftBrief,
    Holding,
    LaterKnowledge,
    PlannedReveal,
    SecretToGuard,
    TimeNeighbour,
    check_draft,
)
from backend.domain.knowledge import Reveal
from backend.domain.plan_checks import order_findings, reveal_findings
from backend.domain.rules import (
    Check,
    Exceptions,
    Pattern,
    RuleBody,
    Selector,
    World,
    WorldBelief,
    WorldEntry,
    WorldFact,
    violations,
)
from backend.domain.scene_conditions import SceneCondition, condition_state, planned_change
from backend.domain.worldtime import At, Between, Interval
from tests.eval.consistency.story import (
    ENTRIES,
    belief,
    entities,
    entity,
    event,
    fact,
    names,
    proposition,
    scene,
    thread,
)

Run = Callable[[], set[str]]


@dataclass(frozen=True)
class Case:
    id: str
    family: str
    about: str
    defect: bool
    # The finding codes that count as the check flagging this case.
    codes: frozenset[str]
    run: Run
    tags: frozenset[str] = field(default_factory=frozenset)
    # Why the check is expected to disagree with the label, when it is: a limit of what
    # a deterministic check can read, stated in its coverage or plainly out of reach.
    known_limit: str | None = None
    # Or: what looks like a defect in the check itself, for the owner to decide on.
    suspected_bug: str | None = None


def _case(
    id_: str,
    family: str,
    about: str,
    *,
    defect: bool,
    codes: Iterable[str],
    run: Run,
    tags: Iterable[str] = (),
    known_limit: str | None = None,
    suspected_bug: str | None = None,
) -> Case:
    return Case(
        id_,
        family,
        about,
        defect,
        frozenset(codes),
        run,
        frozenset(tags),
        known_limit,
        suspected_bug,
    )


# ------------------------------------------------------------------ continuity


def _continuity(*, facts=(), events=(), scenes=(), unit="year", how_long=None, more=()) -> set[str]:
    return {
        item.code
        for item in continuity_findings(
            StoryParts(
                entities=entities(more),
                facts=list(facts),
                events=list(events),
                scenes=list(scenes),
                unit=unit,
                how_long=how_long or HowLong(),
            )
        )
    }


def _continuity_cases() -> list[Case]:
    hours = {"unit": "hour"}
    road = HowLong(travel=[TravelTime(from_id="quay", to_id="light", amount=3)])
    quick = HowLong(travel=[TravelTime(from_id="quay", to_id="light", amount=1)])
    inside = HowLong(travel=[TravelTime(from_id="house", to_id="quay", amount=0)])
    born = event("wren_is_born", At(at=0), kind="birth", who=["wren"])
    osric_dies = event("osric_dies", At(at=10), kind="death", who=["osric"])
    built = event("light_built", At(at=10), kind="built", who=["light"])
    razed = event("house_razed", At(at=50), kind="razed", where=["house"])
    carried = fact("f_carry_w", "wren", "CARRIES", "sextant", starts=At(at=1), ends=At(at=5))
    owns_ledger = fact("f_owns", "wren", "OWNS", "ledger")
    joins = fact("f_joins", "bram", "MEMBER_OF", "wardens", starts=At(at=5))
    age = {"kind": "integer", "value": 10}
    two = "two_places_at_once"
    return [
        _case(
            "continuity/two-places",
            "continuity",
            "Wren is at the Low Fen and at Marrow Light in the same hour.",
            defect=True,
            codes=[two],
            run=lambda: _continuity(
                scenes=[
                    scene("fen_dawn", At(at=3), place="fen", who=["wren"]),
                    scene("light_dawn", At(at=3), place="light", pov="wren"),
                ],
                **hours,
            ),
        ),
        _case(
            "continuity/two-places-loose-times",
            "continuity",
            "One of the two scenes is only 'some time between hours 2 and 4': not certain.",
            defect=False,
            codes=[two],
            run=lambda: _continuity(
                scenes=[
                    scene("fen_dawn", Between(earliest=2, latest=4), place="fen", who=["wren"]),
                    scene("light_dawn", At(at=3), place="light", who=["wren"]),
                ],
                **hours,
            ),
        ),
        _case(
            "continuity/place-inside-place",
            "continuity",
            "The Ferry House stands on Kestle Quay (travel time 0): one place, not two.",
            defect=False,
            codes=[two],
            run=lambda: _continuity(
                scenes=[
                    scene("a", At(at=3), place="house", who=["ada"]),
                    scene("b", At(at=3), place="quay", who=["ada"]),
                ],
                how_long=inside,
                **hours,
            ),
        ),
        _case(
            "continuity/two-places-place-only-in-words",
            "continuity",
            "The second scene's place is written only in its summary, not given as a place.",
            defect=True,
            codes=[two],
            run=lambda: _continuity(
                scenes=[
                    scene("fen_dawn", At(at=3), place="fen", who=["wren"]),
                    scene(
                        "light_dawn",
                        At(at=3),
                        who=["wren"],
                        details={"where": "up the stair of Marrow Light"},
                    ),
                ],
                **hours,
            ),
            known_limit="A place written only in words is not read; the scene needs its place set.",
        ),
        _case(
            "continuity/too-little-travel",
            "continuity",
            "Kestle Quay to Marrow Light takes three years; Wren does it within two.",
            defect=True,
            codes=["too_little_travel_time"],
            run=lambda: _continuity(
                scenes=[
                    scene("leaving", At(at=1), place="quay", who=["wren"]),
                    scene("arriving", At(at=2), place="light", who=["wren"]),
                ],
                how_long=road,
            ),
        ),
        _case(
            "continuity/enough-travel",
            "continuity",
            "The same journey with a one-year travel time.",
            defect=False,
            codes=["too_little_travel_time"],
            run=lambda: _continuity(
                scenes=[
                    scene("leaving", At(at=1), place="quay", who=["wren"]),
                    scene("arriving", At(at=2), place="light", who=["wren"]),
                ],
                how_long=quick,
            ),
        ),
        _case(
            "continuity/leaves-as-the-scene-starts",
            "continuity",
            "Wren is at Marrow Light until hour 5; her scene at the quay is at hour 5.",
            defect=False,
            codes=[two],
            run=lambda: _continuity(
                facts=[fact("f_at", "wren", "LOCATED_AT", "light", starts=At(at=1), ends=At(at=5))],
                scenes=[scene("s", At(at=5), place="quay", who=["wren"])],
                **hours,
            ),
        ),
        _case(
            "continuity/age-against-birth",
            "continuity",
            "Born in year 0, Wren is said to be 10 in year 12.",
            defect=True,
            codes=["age_against_birth"],
            run=lambda: _continuity(
                events=[born], facts=[fact("f_age", "wren", "AGE", age, starts=At(at=12))]
            ),
        ),
        _case(
            "continuity/age-right",
            "continuity",
            "Born in year 0, Wren is 10 in year 10.",
            defect=False,
            codes=["age_against_birth", "ages_disagree"],
            run=lambda: _continuity(
                events=[born], facts=[fact("f_age", "wren", "AGE", age, starts=At(at=10))]
            ),
        ),
        _case(
            "continuity/ages-disagree",
            "continuity",
            "Osric is 10 in year 5 and 30 in year 10.",
            defect=True,
            codes=["ages_disagree"],
            run=lambda: _continuity(
                facts=[
                    fact("a1", "osric", "HAS_AGE", age, starts=At(at=5)),
                    fact(
                        "a2", "osric", "HAS_AGE", {"kind": "integer", "value": 30}, starts=At(at=10)
                    ),
                ]
            ),
        ),
        _case(
            "continuity/stay-wrong-length",
            "continuity",
            "Osric's watch at the light lasts four years, but runs from year 1 to year 2.",
            defect=True,
            codes=["span_wrong_length"],
            run=lambda: _continuity(
                facts=[
                    fact("f_watch", "osric", "LOCATED_AT", "light", starts=At(at=1), ends=At(at=2))
                ],
                how_long=HowLong(
                    lasting=[Lasting(subject_kind="fact", subject_id="f_watch", amount=4)]
                ),
            ),
        ),
        _case(
            "continuity/place-not-yet",
            "continuity",
            "A scene at Marrow Light in year 5; the light is built in year 10.",
            defect=True,
            codes=["place_not_yet"],
            run=lambda: _continuity(
                events=[built], scenes=[scene("early", At(at=5), place="light", who=["osric"])]
            ),
        ),
        _case(
            "continuity/place-gone",
            "continuity",
            "A scene at the Ferry House in year 60; it was razed in year 50.",
            defect=True,
            codes=["place_gone"],
            run=lambda: _continuity(
                events=[razed], scenes=[scene("late", At(at=60), place="house", who=["ada"])]
            ),
        ),
        _case(
            "continuity/place-standing",
            "continuity",
            "A scene at Marrow Light in year 20, after it is built and before anything ends it.",
            defect=False,
            codes=["place_not_yet", "place_gone"],
            run=lambda: _continuity(
                events=[built, razed],
                scenes=[scene("fine", At(at=20), place="light", who=["osric"])],
            ),
        ),
        _case(
            "continuity/two-holders",
            "continuity",
            "Wren carries the sextant from year 1 to 5; Osric from year 3 to 8.",
            defect=True,
            codes=["two_holders_at_once"],
            run=lambda: _continuity(
                facts=[
                    carried,
                    fact(
                        "f_carry_o", "osric", "CARRIES", "sextant", starts=At(at=3), ends=At(at=8)
                    ),
                ]
            ),
        ),
        _case(
            "continuity/handed-on",
            "continuity",
            "Osric carries the sextant only from year 5, when Wren's ends.",
            defect=False,
            codes=["two_holders_at_once", "two_holders"],
            run=lambda: _continuity(
                facts=[carried, fact("f_carry_o", "osric", "CARRIES", "sextant", starts=At(at=5))]
            ),
        ),
        _case(
            "continuity/object-away-from-holder",
            "continuity",
            "Wren owns the tide ledger; it is in Ada's scene and Wren is not.",
            defect=True,
            codes=["object_away_from_holder"],
            run=lambda: _continuity(
                facts=[owns_ledger],
                scenes=[scene("audit", At(at=3), place="house", who=["ada", "ledger"])],
            ),
        ),
        _case(
            "continuity/object-with-holder",
            "continuity",
            "The ledger's scene has Wren in it too.",
            defect=False,
            codes=["object_away_from_holder"],
            run=lambda: _continuity(
                facts=[owns_ledger],
                scenes=[scene("audit", At(at=3), place="house", who=["ada", "ledger", "wren"])],
            ),
        ),
        _case(
            "continuity/after-death",
            "continuity",
            "Osric dies in year 10 and is in a scene in year 12.",
            defect=True,
            codes=["after_death"],
            run=lambda: _continuity(
                events=[osric_dies],
                scenes=[scene("after", At(at=12), place="light", who=["osric"])],
            ),
        ),
        _case(
            "continuity/before-death",
            "continuity",
            "Osric's scene in year 8 comes before his death.",
            defect=False,
            codes=["after_death"],
            run=lambda: _continuity(
                events=[osric_dies],
                scenes=[scene("before", At(at=8), place="light", who=["osric"])],
            ),
        ),
        _case(
            "continuity/death-of-whom-unsaid",
            "continuity",
            "A 'death' with Osric and Bram in it and no part saying who died.",
            defect=False,
            codes=["after_death"],
            run=lambda: _continuity(
                events=[event("duel", At(at=10), kind="death", who=["osric", "bram"])],
                scenes=[scene("after", At(at=12), place="light", who=["osric"])],
            ),
        ),
        _case(
            "continuity/after-drowning",
            "continuity",
            "Osric drowns in the fen in year 10 (his part: died) and is in a scene in year 12.",
            defect=True,
            codes=["after_death"],
            run=lambda: _continuity(
                events=[event("osric_drowns", At(at=10), kind="drowned", who=[("osric", "died")])],
                scenes=[scene("after", At(at=12), place="light", who=["osric"])],
            ),
        ),
        _case(
            "continuity/murdered-alone",
            "continuity",
            "A 'murdered' event with only Osric in it and no part: he may be the murderer.",
            defect=False,
            codes=["after_death"],
            run=lambda: _continuity(
                events=[event("deed", At(at=10), kind="murdered", who=["osric"])],
                scenes=[scene("after", At(at=12), place="light", who=["osric"])],
            ),
        ),
        _case(
            "continuity/before-birth",
            "continuity",
            "Wren is born in year 20 and is in a scene in year 5.",
            defect=True,
            codes=["before_birth"],
            run=lambda: _continuity(
                events=[event("born", At(at=20), kind="born", who=["wren"])],
                scenes=[scene("s", At(at=5), place="quay", who=["wren"])],
            ),
        ),
        _case(
            "continuity/dies-twice",
            "continuity",
            "Osric dies in year 10 and again in year 30.",
            defect=True,
            codes=["dies_twice"],
            run=lambda: _continuity(
                events=[osric_dies, event("dies_again", At(at=30), kind="death", who=["osric"])]
            ),
        ),
        _case(
            "continuity/wounded-in-scene",
            "continuity",
            "Bram is wounded from year 1; a scene in year 3 has him (a note: show it).",
            defect=True,
            codes=["condition_then"],
            run=lambda: _continuity(
                facts=[
                    fact(
                        "f_hurt",
                        "bram",
                        "CONDITION",
                        {"kind": "string", "value": "Wounded"},
                        starts=At(at=1),
                    )
                ],
                scenes=[scene("s", At(at=3), place="quay", who=["bram"])],
            ),
        ),
        _case(
            "continuity/joins-off-page",
            "continuity",
            "Bram joins the Tidewardens in year 5 with no scene or event then.",
            defect=True,
            codes=["relationship_changes_off_page"],
            run=lambda: _continuity(facts=[joins]),
        ),
        _case(
            "continuity/joins-on-page",
            "continuity",
            "A scene in year 5 has Bram in it.",
            defect=False,
            codes=["relationship_changes_off_page"],
            run=lambda: _continuity(
                facts=[joins], scenes=[scene("oath", At(at=5), place="quay", who=["bram"])]
            ),
        ),
        _case(
            "continuity/goal-ends-off-page",
            "continuity",
            "Wren stops pursuing a safe crossing in year 9 with nothing on the page.",
            defect=True,
            codes=["goal_changes_off_page"],
            run=lambda: _continuity(
                facts=[fact("f_goal", "wren", "PURSUES", "crossing", ends=At(at=9))]
            ),
        ),
    ]


# ------------------------------------------------------------------ rules

_CHARACTERS = Selector(type="Character")


def _world(facts: Iterable[WorldFact] = (), beliefs: Iterable[WorldBelief] = (), more=()) -> World:
    entries = {key: WorldEntry(key, kind, name) for key, (kind, name) in ENTRIES.items()}
    entries.update({item.id: item for item in more})
    return World(
        entries=entries,
        facts={item.id: item for item in facts},
        beliefs={item.id: item for item in beliefs},
    )


def _broken(body: RuleBody, world: World) -> set[str]:
    return {"broken" if v.certain else "may_break" for v in violations("rule", body, world)}


def _rules_cases() -> list[Case]:
    one_faction = RuleBody(
        applies_to=_CHARACTERS,
        checks=[
            Check(kind="at_most", pattern=Pattern(predicate="MEMBER_OF", other_type="Faction"))
        ],
    )
    lampwrights = WorldEntry("lamps", "Faction", "the Lampwrights")
    ledger_bearer = RuleBody(
        applies_to=_CHARACTERS,
        checks=[
            Check(
                kind="implies",
                if_has=Pattern(predicate="CARRIES", other_id="ledger"),
                pattern=Pattern(predicate="MEMBER_OF", other_id="wardens"),
            )
        ],
    )
    no_warden_keeper = RuleBody(
        applies_to=_CHARACTERS,
        checks=[
            Check(
                kind="not_both",
                pattern=Pattern(predicate="MEMBER_OF", other_id="wardens"),
                not_with=Pattern(predicate="KEEPS", other_id="light"),
            )
        ],
    )
    plausible_age = RuleBody(
        applies_to=_CHARACTERS,
        checks=[Check(kind="between", pattern=Pattern(predicate="AGE"), low=0, high=120)],
    )
    one_place = RuleBody(
        applies_to=_CHARACTERS,
        time="every_moment",
        checks=[Check(kind="at_most", pattern=Pattern(predicate="LOCATED_AT"))],
    )
    sworn = RuleBody(
        applies_to=Selector(entity_id="bram"),
        checks=[Check(kind="believes", pattern=Pattern(predicate="MEMBER_OF", other_id="wardens"))],
    )
    member = WorldFact("m1", "bram", "MEMBER_OF", "wardens")
    return [
        _case(
            "rules/at-most-one-faction",
            "rules",
            "A character belongs to at most one faction; Bram belongs to two.",
            defect=True,
            codes=["broken"],
            run=lambda: _broken(
                one_faction,
                _world([member, WorldFact("m2", "bram", "MEMBER_OF", "lamps")], more=[lampwrights]),
            ),
        ),
        _case(
            "rules/one-faction",
            "rules",
            "Bram belongs only to the Tidewardens.",
            defect=False,
            codes=["broken", "may_break"],
            run=lambda: _broken(one_faction, _world([member], more=[lampwrights])),
        ),
        _case(
            "rules/excepted",
            "rules",
            "Bram is named as an exception to the one-faction rule.",
            defect=False,
            codes=["broken", "may_break"],
            run=lambda: _broken(
                one_faction.model_copy(update={"except_for": Exceptions(entity_ids=["bram"])}),
                _world([member, WorldFact("m2", "bram", "MEMBER_OF", "lamps")], more=[lampwrights]),
            ),
        ),
        _case(
            "rules/ledger-bearer-must-be-warden",
            "rules",
            "Whoever carries the tide ledger is a Tidewarden; Ada carries it and is not.",
            defect=True,
            codes=["broken"],
            run=lambda: _broken(
                ledger_bearer, _world([WorldFact("c1", "ada", "CARRIES", "ledger")])
            ),
        ),
        _case(
            "rules/ledger-bearer-is-warden",
            "rules",
            "Bram carries the ledger and is a Tidewarden.",
            defect=False,
            codes=["broken", "may_break"],
            run=lambda: _broken(
                ledger_bearer, _world([member, WorldFact("c1", "bram", "CARRIES", "ledger")])
            ),
        ),
        _case(
            "rules/no-warden-keeps-the-light",
            "rules",
            "No Tidewarden keeps Marrow Light; Bram is both.",
            defect=True,
            codes=["broken"],
            run=lambda: _broken(
                no_warden_keeper, _world([member, WorldFact("k1", "bram", "KEEPS", "light")])
            ),
        ),
        _case(
            "rules/age-out-of-range",
            "rules",
            "Ages run from 0 to 120; Osric is 300.",
            defect=True,
            codes=["broken"],
            run=lambda: _broken(
                plausible_age, _world([WorldFact("g1", "osric", "AGE", value="300")])
            ),
        ),
        _case(
            "rules/age-in-range",
            "rules",
            "Osric is 64.",
            defect=False,
            codes=["broken", "may_break"],
            run=lambda: _broken(
                plausible_age, _world([WorldFact("g1", "osric", "AGE", value="64")])
            ),
        ),
        _case(
            "rules/age-with-a-unit",
            "rules",
            "Osric's age is written '300 years'.",
            defect=True,
            codes=["broken", "may_break"],
            run=lambda: _broken(
                plausible_age, _world([WorldFact("g1", "osric", "AGE", value="300 years")])
            ),
        ),
        _case(
            "rules/age-in-range-with-a-unit",
            "rules",
            "Osric's age is written '64 years', inside the range.",
            defect=False,
            codes=["broken", "may_break"],
            run=lambda: _broken(
                plausible_age, _world([WorldFact("g1", "osric", "AGE", value="64 years")])
            ),
        ),
        _case(
            "rules/one-place-at-a-time",
            "rules",
            "Wren is at Kestle Quay from 1 to 10 and at Marrow Light from 5 to 12.",
            defect=True,
            codes=["broken"],
            run=lambda: _broken(
                one_place,
                _world(
                    [
                        WorldFact(
                            "l1", "wren", "LOCATED_AT", "quay", starts=At(at=1), ends=At(at=10)
                        ),
                        WorldFact(
                            "l2", "wren", "LOCATED_AT", "light", starts=At(at=5), ends=At(at=12)
                        ),
                    ]
                ),
            ),
        ),
        _case(
            "rules/one-place-then-another",
            "rules",
            "Wren is at the quay from 1 to 5 and at the light from 5 to 12.",
            defect=False,
            codes=["broken"],
            run=lambda: _broken(
                one_place,
                _world(
                    [
                        WorldFact(
                            "l1", "wren", "LOCATED_AT", "quay", starts=At(at=1), ends=At(at=5)
                        ),
                        WorldFact(
                            "l2", "wren", "LOCATED_AT", "light", starts=At(at=5), ends=At(at=12)
                        ),
                    ]
                ),
            ),
        ),
        _case(
            "rules/sworn-belief-missing",
            "rules",
            "Bram must believe he is a Tidewarden; no such belief is recorded.",
            defect=True,
            codes=["broken"],
            run=lambda: _broken(sworn, _world([member])),
        ),
        _case(
            "rules/sworn-belief-held",
            "rules",
            "Bram believes he is a Tidewarden.",
            defect=False,
            codes=["broken", "may_break"],
            run=lambda: _broken(
                sworn,
                _world(
                    [member], [WorldBelief("b1", "bram", "bram", "MEMBER_OF", object_id="wardens")]
                ),
            ),
        ),
    ]


# ------------------------------------------------------------------ knowledge timing

_SECRET = proposition("osric", "MEMBER_OF", "wardens")
_SECRET_FACT = fact("f_secret", "osric", "MEMBER_OF", "wardens", secret=True)
_ORDER = [
    scene("s1", position=1, pov="wren", place="quay"),
    scene("s2", position=2, pov="wren", place="fen"),
    scene("s3", position=3, pov="wren", place="light"),
    scene("s4", position=4, pov="wren", place="house"),
]
_TOLD = event(
    "told_in_s3",
    kind="telling",
    who=[("ada", "speaker"), ("wren", "listener")],
    scene="s3",
)


def _placed(scenes=_ORDER, events=(), moments=None) -> object:
    moments = moments or {}
    return placement(
        scenes,
        events,
        scene_when=lambda item: moments.get(item.id),
        event_when=lambda item: moments.get(item.id),
    )


def _later(scene_id: str, *, facts=(_SECRET_FACT,), events=(_TOLD,), source="told_in_s3", **kw):
    beliefs = [belief("b_wren", "wren", _SECRET, source)]
    by_id = {item.id: item for item in events}
    found = learned_later(
        "wren", scene_id, beliefs, list(facts), by_id, _placed(events=events, **kw)
    )
    return {"learned_later"} if found else set()


def _said(modality: str) -> set[str]:
    claim = event(
        "wren_says",
        kind="claim",
        who=[("wren", "speaker")],
        claim=EventClaim(proposition=_SECRET, modality=modality, scene_id="s1"),  # type: ignore[arg-type]
    )
    beliefs = [belief("b_wren", "wren", _SECRET, "told_in_s3")]
    events = {item.id: item for item in (_TOLD, claim)}
    found = said_before_learned(
        [claim], beliefs, [_SECRET_FACT], events, _placed(events=list(events.values()))
    )
    return {"said_before_learned"} if found else set()


def _twice(scene_id: str) -> set[str]:
    seen = event("seen_in_s1", kind="witnessed", who=[("wren", "witness")], scene="s1")
    beliefs = [
        belief("b_seen", "wren", _SECRET, "seen_in_s1"),
        belief("b_told", "wren", _SECRET, "told_in_s3", rev=2),
    ]
    events = {item.id: item for item in (seen, _TOLD)}
    found = learned_later(
        "wren", scene_id, beliefs, [_SECRET_FACT], events, _placed(events=list(events.values()))
    )
    return {"learned_later"} if found else set()


def _held(scene_id: str) -> set[str]:
    beliefs = [belief("b_wren", "wren", _SECRET, "told_in_s3")]
    kept = held_at("wren", scene_id, beliefs, {_TOLD.id: _TOLD}, _placed(events=[_TOLD]))
    return set() if any(item.id == "b_wren" for item in kept) else {"kept_out"}


def _knowledge_cases() -> list[Case]:
    flashback = [
        scene("s1", position=1, pov="wren"),
        scene("s2", position=2, pov="wren"),
    ]
    seen_at_10 = event("seen", At(at=10), kind="witnessed", who=[("wren", "witness")])
    return [
        _case(
            "knowledge/uses-it-before-told",
            "knowledge",
            "Wren is told Osric's secret in scene 3; scene 1 is checked.",
            defect=True,
            codes=["learned_later"],
            run=lambda: _later("s1"),
            tags=["leakage"],
        ),
        _case(
            "knowledge/after-told",
            "knowledge",
            "The same secret, checked in scene 4, after she is told.",
            defect=False,
            codes=["learned_later"],
            run=lambda: _later("s4"),
        ),
        _case(
            "knowledge/learned-twice",
            "knowledge",
            "Wren sees it for herself in scene 1 and is told again in scene 3; scene 2 is checked.",
            defect=False,
            codes=["learned_later"],
            run=lambda: _twice("s2"),
        ),
        _case(
            "knowledge/common-knowledge",
            "knowledge",
            "Everyone in the story knows it, so it is known all along.",
            defect=False,
            codes=["learned_later"],
            run=lambda: _later(
                "s1", facts=[fact("f_common", "osric", "MEMBER_OF", "wardens", common=True)]
            ),
        ),
        _case(
            "knowledge/recorded-without-a-moment",
            "knowledge",
            "The author recorded the belief with no event: when she learned it is unknown.",
            defect=False,
            codes=["learned_later"],
            run=lambda: _later("s1", source="author"),
        ),
        _case(
            "knowledge/flashback-by-story-time",
            "knowledge",
            "Scene 2 is a flashback to year 2; Wren sees it happen in year 10.",
            defect=True,
            codes=["learned_later"],
            run=lambda: _later(
                "s2",
                events=[seen_at_10],
                source="seen",
                scenes=flashback,
                moments={"s1": Interval(20, 20), "s2": Interval(2, 2), "seen": Interval(10, 10)},
            ),
            tags=["leakage"],
        ),
        _case(
            "knowledge/says-it-before-told",
            "knowledge",
            "In scene 1 Wren says Osric is a Tidewarden; she is only told in scene 3.",
            defect=True,
            codes=["said_before_learned"],
            run=lambda: _said("states"),
            tags=["leakage"],
        ),
        _case(
            "knowledge/only-wonders",
            "knowledge",
            "In scene 1 Wren only wonders whether he is.",
            defect=False,
            codes=["said_before_learned"],
            run=lambda: _said("wonders"),
        ),
        _case(
            "knowledge/kept-out-of-earlier-context",
            "knowledge",
            "What Wren believes in scene 1 leaves out what she is told in scene 3.",
            defect=True,
            codes=["kept_out"],
            run=lambda: _held("s1"),
            tags=["leakage"],
        ),
    ]


# ------------------------------------------------------------------ drafting

_BRIEF = DraftBrief(
    point_of_view="Wren Halloway",
    point_of_view_names=("Wren Halloway",),
    place="Kestle Quay",
    place_names=("Kestle Quay",),
    secrets=[
        SecretToGuard(
            "Osric Pell is a member of the Tidewardens", ("Osric Pell", "the Tidewardens")
        )
    ],
    also={
        "Wren Halloway": ("Wren",),
        "Osric Pell": ("Osric", "the lamp-warden"),
        "Kestle Quay": ("the quay",),
        "the Tidewardens": ("Tidewardens",),
    },
)


def _draft(text: str, brief: DraftBrief = _BRIEF) -> set[str]:
    return {item.code for item in check_draft(text, brief, [])}


def _with(**changes) -> DraftBrief:
    return DraftBrief(**{**_BRIEF.__dict__, **changes})


_LATER = LaterKnowledge(
    sentence="Ada Quill owns the tide ledger",
    names=("Ada Quill", "the tide ledger"),
    who="Wren Halloway",
    where="in “The Audit”",
)


def _drafting_cases() -> list[Case]:
    secret = ["secret"]
    return [
        _case(
            "drafting/secret-on-the-page",
            "drafting",
            "Wren's paragraph has Osric pinning the Tidewardens' badge inside his coat.",
            defect=True,
            codes=secret,
            run=lambda: _draft(
                "Wren tied the ferry off at the quay.\n\n"
                "Osric pinned the Tidewardens' badge inside his coat and thought she had not seen."
            ),
            tags=["leakage"],
        ),
        _case(
            "drafting/secret-names-apart",
            "drafting",
            "Osric and the Tidewardens appear, but in different paragraphs about other things.",
            defect=False,
            codes=secret,
            run=lambda: _draft(
                "Wren watched Osric trim the wick at Kestle Quay.\n\n"
                "Far off, a Tidewardens bell rang for the evening tide."
            ),
        ),
        _case(
            "drafting/secret-by-another-name",
            "drafting",
            "The lamp-warden is named by his title, with the Tidewardens in the same paragraph.",
            defect=True,
            codes=secret,
            run=lambda: _draft(
                "At Kestle Quay, Wren saw the lamp-warden take the Tidewardens' oath by lantern light."
            ),
            tags=["leakage"],
        ),
        _case(
            "drafting/secret-by-pronoun",
            "drafting",
            "'He' (Osric) takes the Tidewardens' oath; his name is two paragraphs back.",
            defect=True,
            codes=secret,
            run=lambda: _draft(
                "Wren saw Osric climb down from the light at Kestle Quay.\n\n"
                "Later he knelt in the reeds and took the Tidewardens' oath."
            ),
            tags=["leakage"],
            known_limit="A pronoun is not read as a name; telling who 'he' is needs a model.",
        ),
        _case(
            "drafting/secret-denied",
            "drafting",
            "A paragraph names both only to say Osric has never spoken to the Tidewardens "
            "(a note that it still names both, not a warning).",
            defect=False,
            codes=secret,
            run=lambda: _draft(
                "At Kestle Quay Wren knew one thing for certain: Osric had never so much as "
                "spoken to the Tidewardens."
            ),
        ),
        _case(
            "drafting/uses-it-early",
            "drafting",
            "Wren already knows Ada owns the ledger, which she only learns in a later scene.",
            defect=True,
            codes=["learned_later"],
            run=lambda: _draft(
                "At Kestle Quay Wren watched Ada Quill lock the tide ledger in her own chest, "
                "as she always did.",
                _with(learned_later=[_LATER]),
            ),
            tags=["leakage"],
        ),
        _case(
            "drafting/says-it-early",
            "drafting",
            "The plan records Wren saying it in this scene, before she learns it.",
            defect=True,
            codes=["learned_later_said"],
            run=lambda: _draft(
                "Wren said nothing at Kestle Quay.",
                _with(learned_later=[LaterKnowledge(**{**_LATER.__dict__, "said": True})]),
            ),
            tags=["leakage"],
        ),
        _case(
            "drafting/reveal-missing",
            "drafting",
            "The plan says the reader learns the sextant is cracked here; the text never says.",
            defect=True,
            codes=["reveal_missing"],
            run=lambda: _draft(
                "Wren poled the ferry out from Kestle Quay into the mist.",
                _with(
                    reveals=[PlannedReveal("the brass sextant is cracked", ("the brass sextant",))]
                ),
            ),
        ),
        _case(
            "drafting/reveal-present",
            "drafting",
            "The text shows the brass sextant and its crack.",
            defect=False,
            codes=["reveal_missing"],
            run=lambda: _draft(
                "At Kestle Quay Wren turned the brass sextant over: a hairline crack ran "
                "through its mirror.",
                _with(
                    reveals=[PlannedReveal("the brass sextant is cracked", ("the brass sextant",))]
                ),
            ),
        ),
        _case(
            "drafting/place-never-named",
            "drafting",
            "A scene at Kestle Quay that never names the quay.",
            defect=True,
            codes=["place_missing"],
            run=lambda: _draft("Wren tied off the ferry and waited for the bell."),
        ),
        _case(
            "drafting/place-by-another-name",
            "drafting",
            "The quay is named as 'the quay'.",
            defect=False,
            codes=["place_missing"],
            run=lambda: _draft("Wren tied off the ferry at the quay and waited for the bell."),
        ),
        _case(
            "drafting/viewpoint-unnamed-but-someone-says-i",
            "drafting",
            "A third-person scene seen through Wren that never names her; Ada says 'I' aloud.",
            defect=True,
            codes=["viewpoint_missing"],
            run=lambda: _draft(
                "\u201cI will not take the ferry tonight,\u201d said Ada at Kestle Quay, and the "
                "bell rang on."
            ),
        ),
        _case(
            "drafting/viewpoint-never-named",
            "drafting",
            "A scene seen through Wren that never names her.",
            defect=True,
            codes=["viewpoint_missing"],
            run=lambda: _draft("The ferry knocked against Kestle Quay and the bell rang."),
        ),
    ]


# ------------------------------------------------------------------ plan


def _shown(fact_id: str, *, main: bool = False) -> Reveal:
    return Reveal(fact_id=fact_id, how="shown", main_reveal=main)


def _hinted(fact_id: str) -> Reveal:
    return Reveal(fact_id=fact_id, how="hinted")


def _reveals(*scenes) -> set[str]:
    return {item.code for item in reveal_findings(list(scenes), [_SECRET_FACT], names())}


def _order(*items) -> set[str]:
    return {item.code for item in order_findings(list(items))}


_AT_QUAY = SceneCondition(
    when="before",
    entity_id="wren",
    check=Check(kind="has", pattern=Pattern(predicate="LOCATED_AT", other_id="quay")),
)


def _condition(world_facts: Iterable[WorldFact], earlier=()) -> set[str]:
    planned = planned_change(_AT_QUAY, list(earlier))
    state = condition_state("s2", _AT_QUAY, _world(world_facts), planned=planned)
    return {state.status}


def _plan_cases() -> list[Case]:
    lost = thread("t_lost", "Who sank the old ferry?")
    return [
        _case(
            "plan/reveal-too-early",
            "plan",
            "Osric's secret comes out in scene 4 but scene 2 shows it plainly.",
            defect=True,
            codes=["reveals_too_early"],
            run=lambda: _reveals(
                scene("s1", reveals=[_hinted("f_secret")], position=1),
                scene("s2", reveals=[_shown("f_secret")], position=2),
                scene("s4", reveals=[_shown("f_secret", main=True)], position=4),
            ),
            tags=["leakage"],
        ),
        _case(
            "plan/reveal-twice",
            "plan",
            "The secret is marked to come out in scene 2 and again in scene 4.",
            defect=True,
            codes=["planned_reveal_twice"],
            run=lambda: _reveals(
                scene("s1", reveals=[_hinted("f_secret")], position=1),
                scene("s2", reveals=[_shown("f_secret", main=True)], position=2),
                scene("s4", reveals=[_shown("f_secret", main=True)], position=4),
            ),
        ),
        _case(
            "plan/reveal-without-clue",
            "plan",
            "The secret comes out in scene 4 with no hint before it.",
            defect=True,
            codes=["reveal_without_clue"],
            run=lambda: _reveals(scene("s4", reveals=[_shown("f_secret", main=True)], position=4)),
        ),
        _case(
            "plan/reveal-set-up",
            "plan",
            "Hinted in scene 1, shown in scene 4 where it comes out.",
            defect=False,
            codes=["reveals_too_early", "planned_reveal_twice", "reveal_without_clue"],
            run=lambda: _reveals(
                scene("s1", reveals=[_hinted("f_secret")], position=1),
                scene("s4", reveals=[_shown("f_secret", main=True)], position=4),
            ),
        ),
        _case(
            "plan/resolved-before-opened",
            "plan",
            "Scene 1 answers who sank the old ferry; scene 3 first asks it.",
            defect=True,
            codes=["resolves_before_opening"],
            run=lambda: _order(
                lost,
                scene("s1", position=1, pays_off=["t_lost"]),
                scene("s3", position=3, sets_up=["t_lost"]),
            ),
        ),
        _case(
            "plan/opened-then-resolved",
            "plan",
            "Asked in scene 1, answered in scene 3.",
            defect=False,
            codes=["resolves_before_opening", "moves_along_before_opening"],
            run=lambda: _order(
                lost,
                scene("s1", position=1, sets_up=["t_lost"]),
                scene("s3", position=3, pays_off=["t_lost"]),
            ),
        ),
        _case(
            "plan/condition-not-ready",
            "plan",
            "Scene 2 needs Wren at Kestle Quay; the story bible has her at Marrow Light.",
            defect=True,
            codes=["not_met"],
            run=lambda: _condition([WorldFact("l1", "wren", "LOCATED_AT", "light")]),
        ),
        _case(
            "plan/condition-ready",
            "plan",
            "Wren is at Kestle Quay.",
            defect=False,
            codes=["not_met"],
            run=lambda: _condition([WorldFact("l1", "wren", "LOCATED_AT", "quay")]),
        ),
        _case(
            "plan/condition-planned",
            "plan",
            "Not recorded yet, but scene 1 is meant to bring Wren to the quay.",
            defect=False,
            codes=["not_met"],
            run=lambda: _condition(
                [],
                earlier=[("s1", "S1", [_AT_QUAY.model_copy(update={"when": "after"})])],
            ),
        ),
    ]


# ------------------------------------------------------------------ against the prose
# STY-005 (an "after" condition the prose never shows), SCN-007 (words for when against
# story time; using what someone else has or what is used up), CON-004 and CON-014 (eye,
# hair and beard colours against accepted details, from the story bible or a picture).

_PROSE = DraftBrief(
    point_of_view="Wren Halloway",
    point_of_view_names=("Wren Halloway",),
    place="Kestle Quay",
    place_names=("Kestle Quay",),
    people=("Wren Halloway", "Osric Pell"),
    also={
        "Wren Halloway": ("Wren",),
        "Osric Pell": ("Osric", "the lamp-warden"),
        "Kestle Quay": ("the quay",),
        "the brass sextant": ("the sextant",),
    },
)
_WREN_LOOKS = AppearanceDetail(
    "Wren Halloway", ("Wren Halloway", "Wren"), "Grey eyes; short black hair.", "the story bible"
)
_WREN_PICTURE = AppearanceDetail(
    "Wren Halloway",
    ("Wren Halloway", "Wren"),
    "Red hair cut short above the ears.",
    "the picture “Wren at the quay”",
)
_WREN_BLUE = AppearanceDetail(
    "Wren Halloway", ("Wren Halloway", "Wren"), "Blue eyes; brown hair.", "the story bible"
)
_SEXTANT_WITH_ADA = Holding(
    "the brass sextant", ("the brass sextant",), "Ada Quill", ("Ada Quill", "Ada")
)
_LEDGER_BURNED = Holding("the tide ledger", ("the tide ledger",), gone="its status is “burned”")
_AT_LIGHT = AfterShown("Wren Halloway is at Marrow Light", ("Wren Halloway", "Marrow Light"))


def _prose(text: str, **changes) -> set[str]:
    return {
        item.code for item in check_draft(text, DraftBrief(**{**_PROSE.__dict__, **changes}), [])
    }


def _after_crossing(days: tuple[float, float], same_day: bool | None) -> TimeNeighbour:
    return TimeNeighbour("The Crossing", days, same_day, "day 5", "day 5")


def _prose_cases() -> list[Case]:
    looks = ["appearance_differs"]
    when = ["time_words_next_day", "time_words_same_day", "time_words_amount", "time_words_before"]
    held = ["resource_held_elsewhere", "resource_used_up"]
    return [
        _case(
            "prose/eyes-differ",
            "prose",
            "The story bible gives Wren grey eyes; the scene gives her blue ones.",
            defect=True,
            codes=looks,
            run=lambda: _prose(
                "Wren squinted at the light, her blue eyes stinging.", appearance=[_WREN_LOOKS]
            ),
        ),
        _case(
            "prose/hair-differs-from-picture",
            "prose",
            "Wren's accepted picture shows red hair; the scene says black.",
            defect=True,
            codes=looks,
            run=lambda: _prose(
                "Osric watched Wren's black hair whip in the wind.", appearance=[_WREN_PICTURE]
            ),
        ),
        _case(
            "prose/eyes-agree-in-other-words",
            "prose",
            "'Slate-coloured' would be grey; 'slate eyes' agrees with grey eyes.",
            defect=False,
            codes=looks,
            run=lambda: _prose("Wren narrowed her slate eyes.", appearance=[_WREN_LOOKS]),
        ),
        _case(
            "prose/hair-dyed",
            "prose",
            "Wren has dyed her hair red for a disguise: not a contradiction.",
            defect=False,
            codes=looks,
            run=lambda: _prose(
                "Wren had dyed her hair red for the crossing.", appearance=[_WREN_LOOKS]
            ),
        ),
        _case(
            "prose/someone-elses-eyes",
            "prose",
            "'Wren stared into his grey eyes': the grey eyes are not hers.",
            defect=False,
            codes=looks,
            run=lambda: _prose("Wren stared into his grey eyes.", appearance=[_WREN_BLUE]),
        ),
        _case(
            "prose/a-green-eyed-boy",
            "prose",
            "'The green-eyed boy' in Wren's sentence is someone else.",
            defect=False,
            codes=looks,
            run=lambda: _prose(
                "Wren smiled at the green-eyed boy on the quay.", appearance=[_WREN_BLUE]
            ),
        ),
        _case(
            "prose/her-mothers-eyes",
            "prose",
            "'Wren's mother had green eyes': the green eyes are her mother's.",
            defect=False,
            codes=looks,
            run=lambda: _prose("Wren's mother had green eyes.", appearance=[_WREN_BLUE]),
        ),
        _case(
            "prose/her-brother-green-eyed",
            "prose",
            "'Wren's brother, green-eyed and silent': the brother is green-eyed, not Wren.",
            defect=False,
            codes=looks,
            run=lambda: _prose(
                "Wren's brother, green-eyed and silent, waited on the quay.",
                appearance=[_WREN_BLUE],
            ),
        ),
        _case(
            "prose/her-horses-hair",
            "prose",
            "'Wren's old horse had black hair': the horse's, not hers.",
            defect=False,
            codes=looks,
            run=lambda: _prose("Wren's old horse had black hair.", appearance=[_WREN_BLUE]),
        ),
        _case(
            "prose/possessive-with-describing-words",
            "prose",
            "'Wren's tired, green eyes' are still Wren's, against her blue ones.",
            defect=True,
            codes=looks,
            run=lambda: _prose("Wren's tired, green eyes narrowed.", appearance=[_WREN_BLUE]),
        ),
        _case(
            "prose/a-black-eye",
            "prose",
            "A black eye is a bruise, not an eye colour.",
            defect=False,
            codes=looks,
            run=lambda: _prose(
                "Wren had a black eye from the fight at the quay.", appearance=[_WREN_BLUE]
            ),
        ),
        _case(
            "prose/red-eyed",
            "prose",
            "Red-eyed from crying or no sleep is not an eye colour.",
            defect=False,
            codes=looks,
            run=lambda: _prose("Wren came down to the quay red-eyed.", appearance=[_WREN_BLUE]),
        ),
        _case(
            "prose/eyes-red-and-swollen",
            "prose",
            "Eyes red and swollen from crying are not an eye colour.",
            defect=False,
            codes=looks,
            run=lambda: _prose("Wren's eyes were red and swollen.", appearance=[_WREN_BLUE]),
        ),
        _case(
            "prose/a-hair-ribbon",
            "prose",
            "A red hair ribbon says nothing of the colour of the hair.",
            defect=False,
            codes=looks,
            run=lambda: _prose(
                "Wren tied a red hair ribbon round the oar.", appearance=[_WREN_BLUE]
            ),
        ),
        _case(
            "prose/hair-gleamed-gold",
            "prose",
            "Hair that gleamed gold in the lamplight is a passing look.",
            defect=False,
            codes=looks,
            run=lambda: _prose(
                "Wren's hair gleamed gold in the lamplight.", appearance=[_WREN_BLUE]
            ),
        ),
        _case(
            "prose/hair-white-with-frost",
            "prose",
            "Hair white with frost is not white hair.",
            defect=False,
            codes=looks,
            run=lambda: _prose("Wren's hair was white with frost.", appearance=[_WREN_BLUE]),
        ),
        _case(
            "prose/eyes-by-pronoun",
            "prose",
            "'Her blue eyes' with Wren named only in the sentence before.",
            defect=True,
            codes=looks,
            run=lambda: _prose(
                "Wren reached the quay. She rubbed her blue eyes.", appearance=[_WREN_LOOKS]
            ),
            known_limit="A pronoun is not read as a name; telling whose eyes these are needs a model.",
        ),
        _case(
            "prose/next-morning-same-day",
            "prose",
            "The scene opens 'The next morning' but is placed on the same day as the scene before.",
            defect=True,
            codes=when,
            run=lambda: _prose(
                "The next morning, Wren untied the ferry at Kestle Quay.",
                previous=_after_crossing((-1, 1), True),
            ),
        ),
        _case(
            "prose/next-morning-next-day",
            "prose",
            "'The next morning', on the day after the scene before.",
            defect=False,
            codes=when,
            run=lambda: _prose(
                "The next morning, Wren untied the ferry at Kestle Quay.",
                previous=_after_crossing((0, 2), False),
            ),
        ),
        _case(
            "prose/that-evening-days-later",
            "prose",
            "'Later that day', but the scene is placed four days after the one before.",
            defect=True,
            codes=when,
            run=lambda: _prose(
                "Later that day, Osric lit the lamp above Kestle Quay.",
                previous=_after_crossing((3, 5), False),
            ),
        ),
        _case(
            "prose/a-week-later-fits",
            "prose",
            "'A week later', placed six to eight days later.",
            defect=False,
            codes=when,
            run=lambda: _prose(
                "A week later the fog lifted from Kestle Quay.",
                previous=_after_crossing((6, 8), False),
            ),
        ),
        _case(
            "prose/next-morning-promised",
            "prose",
            "'Promised to be gone by the next morning' says nothing of when this scene is.",
            defect=False,
            codes=when,
            run=lambda: _prose(
                "Wren promised to be gone by the next morning.",
                previous=_after_crossing((-1, 1), True),
            ),
        ),
        _case(
            "prose/did-not-wait-for-morning",
            "prose",
            "'Did not wait for the next morning': the scene is still the same day.",
            defect=False,
            codes=when,
            run=lambda: _prose(
                "Wren did not wait for the next morning, and untied the ferry.",
                previous=_after_crossing((-1, 1), True),
            ),
        ),
        _case(
            "prose/days-on-the-road",
            "prose",
            "'Three days on the road' is how long she travelled, not how long since.",
            defect=False,
            codes=when,
            run=lambda: _prose(
                "Three days on the road had worn Wren thin.",
                previous=_after_crossing((-1, 1), True),
            ),
        ),
        _case(
            "prose/dawn-without-a-cue",
            "prose",
            "'Dawn found Wren at the quay' on the same day as an evening scene: no 'next' word.",
            defect=True,
            codes=when,
            run=lambda: _prose(
                "Dawn found Wren at Kestle Quay, still awake.",
                previous=_after_crossing((-1, 1), True),
            ),
            known_limit="Only words that say how a scene follows the last are read ('the next "
            "morning', 'later that day', 'three days later'); a time of day alone is not.",
        ),
        _case(
            "prose/uses-what-another-has",
            "prose",
            "Wren takes a sighting with the sextant, which Ada has at this time and is not here.",
            defect=True,
            codes=held,
            run=lambda: _prose(
                "Wren lifted the sextant and took a sighting on Marrow Light.",
                holdings=[_SEXTANT_WITH_ADA],
            ),
        ),
        _case(
            "prose/holder-hands-it-over",
            "prose",
            "Ada hands Wren the sextant in the same sentence: fine.",
            defect=False,
            codes=held,
            run=lambda: _prose(
                "Wren took the sextant from Ada and lifted it to the light.",
                holdings=[_SEXTANT_WITH_ADA],
            ),
        ),
        _case(
            "prose/reads-a-burned-ledger",
            "prose",
            "Osric opens the tide ledger, whose status says it was burned before this scene.",
            defect=True,
            codes=held,
            run=lambda: _prose(
                "Osric opened the tide ledger and ran a finger down the soundings.",
                holdings=[_LEDGER_BURNED],
            ),
        ),
        _case(
            "prose/remembers-the-ledger",
            "prose",
            "Osric only thinks of the burned ledger.",
            defect=False,
            codes=held,
            run=lambda: _prose(
                "Osric thought of the tide ledger, and of the fire.", holdings=[_LEDGER_BURNED]
            ),
        ),
        _case(
            "prose/turned-toward-it",
            "prose",
            "Wren turns toward where the sextant hangs; she does not use it.",
            defect=False,
            codes=held,
            run=lambda: _prose(
                "Wren turned toward the window where the sextant hung.",
                holdings=[_SEXTANT_WITH_ADA],
            ),
        ),
        _case(
            "prose/held-her-breath",
            "prose",
            "Wren holds her breath and looks at the sextant; she does not hold it.",
            defect=False,
            codes=held,
            run=lambda: _prose(
                "Wren held her breath and stared at the sextant.", holdings=[_SEXTANT_WITH_ADA]
            ),
        ),
        _case(
            "prose/uses-it-by-pronoun",
            "prose",
            "'She raised it to her eye' — the sextant named only in the sentence before.",
            defect=True,
            codes=held,
            run=lambda: _prose(
                "The sextant lay on the thwart. Wren raised it to her eye.",
                holdings=[_SEXTANT_WITH_ADA],
            ),
            known_limit="'It' is not read as the thing named before; that needs a model.",
        ),
        _case(
            "prose/after-never-shown",
            "prose",
            "The plan says Wren ends the scene at Marrow Light; the light is never named.",
            defect=True,
            codes=["after_not_shown"],
            run=lambda: _prose(
                "Wren rowed until her arms burned.\n\nThe fog closed in.", after=[_AT_LIGHT]
            ),
        ),
        _case(
            "prose/after-shown",
            "prose",
            "Wren reaches Marrow Light at the end of the scene.",
            defect=False,
            codes=["after_not_shown"],
            run=lambda: _prose("At last Wren tied up beneath Marrow Light.", after=[_AT_LIGHT]),
        ),
        _case(
            "prose/after-named-but-denied",
            "prose",
            "Wren and Marrow Light share a sentence, which says she never gets there.",
            defect=True,
            codes=["after_not_shown"],
            run=lambda: _prose("Wren never reached Marrow Light that night.", after=[_AT_LIGHT]),
            known_limit="Naming both together is read as showing it; whether the sentence "
            "says it happens is left to the assistant's check of the plan.",
        ),
    ]


def all_cases() -> list[Case]:
    return [
        *_continuity_cases(),
        *_rules_cases(),
        *_knowledge_cases(),
        *_drafting_cases(),
        *_plan_cases(),
        *_prose_cases(),
    ]


__all__ = ["Case", "all_cases", "entity"]
