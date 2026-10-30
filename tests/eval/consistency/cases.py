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
story bible: appearance, words for when, things in hand, "after" conditions) and ``voice``
(how a scene sounds against the author's usual, backend/domain/voice.py). Tags: ``leakage`` marks a case about knowledge or
a secret reaching a page or a context it must not.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from backend.domain.acquisition import held_at, learned_later, placement, said_before_learned
from backend.domain.appearance import AgeKnown, AppearanceDetail
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
    Supply,
    TimeNeighbour,
    check_draft,
)
from backend.domain.knowledge import Reveal
from backend.domain.movement import Elsewhere, Kept, MovementBrief, PlaceKnown, movement_findings
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
from backend.domain.scene_conditions import (
    Knowing,
    KnowsCheck,
    SceneCondition,
    condition_state,
    planned_change,
)
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


def _knowing(how: str, sources: Iterable[str | None], scene_id: str = "s4", earlier=()) -> set[str]:
    """STY-005: Wren must know (have seen, have heard) Osric's secret before ``scene_id``."""
    need = SceneCondition(
        when="before",
        entity_id="wren",
        knows=KnowsCheck(how=how, subject_id="osric", predicate="MEMBER_OF", other_id="wardens"),
    )
    seen = event("seen_in_s1", kind="witnessed", who=[("wren", "witness")], scene="s1")
    events = [seen, _TOLD]
    knowing = Knowing(
        beliefs=[belief(f"b_{i}", "wren", _SECRET, source) for i, source in enumerate(sources)],
        events={item.id: item for item in events},
        where=_placed(events=events),
        names=names(),
    )
    planned = planned_change(need, list(earlier))
    state = condition_state(scene_id, need, _world(), knowing=knowing, planned=planned)
    return {state.status}


def _moved_away() -> set[str]:
    """STY-005: scene 1 is meant to leave Wren at Marrow Light; scene 2 needs her at the quay."""
    away = SceneCondition(
        when="after",
        entity_id="wren",
        check=Check(kind="has", pattern=Pattern(predicate="LOCATED_AT", other_id="light")),
    )
    return _condition([WorldFact("l1", "wren", "LOCATED_AT", "quay")], [("s1", "S1", [away])])


def _knowing_cases() -> list[Case]:
    return [
        _case(
            "plan/knows-learned-later",
            "plan",
            "Scene 2 needs Wren to know Osric's secret; she is only told it in scene 3.",
            defect=True,
            codes=["not_met"],
            run=lambda: _knowing("knows", ["told_in_s3"], scene_id="s2"),
            tags=["knowledge"],
        ),
        _case(
            "plan/knows-in-time",
            "plan",
            "Scene 4 needs Wren to know it; she was told it in scene 3.",
            defect=False,
            codes=["not_met"],
            run=lambda: _knowing("knows", ["told_in_s3"]),
            tags=["knowledge"],
        ),
        _case(
            "plan/seen-but-only-told",
            "plan",
            "Scene 4 needs Wren to have seen it happen; she was only told it.",
            defect=True,
            codes=["not_met"],
            run=lambda: _knowing("seen", ["told_in_s3"]),
            tags=["knowledge"],
        ),
        _case(
            "plan/seen-it",
            "plan",
            "Scene 4 needs Wren to have seen it; she saw it in scene 1.",
            defect=False,
            codes=["not_met"],
            run=lambda: _knowing("seen", ["seen_in_s1"]),
            tags=["knowledge"],
        ),
        _case(
            "plan/heard-it-told",
            "plan",
            "Scene 4 needs Wren to have heard it; she was told it in scene 3.",
            defect=False,
            codes=["not_met"],
            run=lambda: _knowing("heard", ["told_in_s3"]),
            tags=["knowledge"],
        ),
        _case(
            "plan/is-at-moved-away",
            "plan",
            "Wren is at the quay in the story bible, but scene 1 is meant to take her to "
            "Marrow Light before scene 2, which needs her at the quay.",
            defect=True,
            codes=["not_met"],
            run=_moved_away,
        ),
    ]


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
_WREN_TALL = AppearanceDetail(
    "Wren Halloway",
    ("Wren Halloway", "Wren"),
    "Tall, with a scar across her left cheek; short black hair.",
    "the story bible",
)
_WREN_AGE = AgeKnown(
    "Wren Halloway",
    ("Wren Halloway", "Wren"),
    15,
    16,
    "“Wren is born” is year 5 and this scene is year 21",
)
_FLARES = Supply("the flares", ("the flares",), 4, "the story bible (from day 3)")
_WRENS_FLARES = Supply("Wren Halloway's flares", ("the flares",), 4, "the story bible (from day 3)")
_WREN_SMALL_SCAR = AppearanceDetail(
    "Wren Halloway",
    ("Wren Halloway", "Wren"),
    "Tall, with a small scar above her left eye, a tiny tattoo on her wrist and a short skirt.",
    "the story bible",
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
    counted = ["supply_overspent", "supply_count_differs"]
    identity = ["height_differs", "mark_differs", "age_differs"]
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
            "prose/height-differs",
            "prose",
            "The story bible says Wren is tall; the scene calls her a small woman.",
            defect=True,
            codes=identity,
            run=lambda: _prose(
                "Wren was a small woman, quick on the ropes.", appearance=[_WREN_TALL]
            ),
        ),
        _case(
            "prose/scar-other-side",
            "prose",
            "Wren's scar is on her left cheek; the scene puts it on the right.",
            defect=True,
            codes=identity,
            run=lambda: _prose(
                "The scar on Wren's right cheek pulled as she smiled.", appearance=[_WREN_TALL]
            ),
        ),
        _case(
            "prose/age-differs",
            "prose",
            "Born in year 5 and in a scene in year 21, Wren cannot be nineteen.",
            defect=True,
            codes=identity,
            run=lambda: _prose("Wren, nineteen and tired, rowed on.", ages=[_WREN_AGE]),
        ),
        _case(
            "prose/age-hyphenated",
            "prose",
            "'The twenty-year-old Wren' against an age of 15 or 16.",
            defect=True,
            codes=identity,
            run=lambda: _prose("The twenty-year-old Wren took the oars.", ages=[_WREN_AGE]),
        ),
        _case(
            "prose/scar-by-pronoun-elsewhere",
            "prose",
            "'Her right cheek' after the prose has given Wren 'her' in another sentence.",
            defect=True,
            codes=identity,
            run=lambda: _prose(
                "Wren pulled up her hood. Wren touched the scar on her right cheek.",
                appearance=[_WREN_TALL],
            ),
        ),
        _case(
            "prose/stood-tall",
            "prose",
            "'Stood tall' is how she holds herself, not her height.",
            defect=False,
            codes=identity,
            run=lambda: _prose("Wren stood tall at the rail.", appearance=[_WREN_TALL]),
        ),
        _case(
            "prose/short-of-breath",
            "prose",
            "'Short of breath' and 'short hair' are not her height.",
            defect=False,
            codes=identity,
            run=lambda: _prose(
                "Wren was short of breath. Wren had short hair.", appearance=[_WREN_TALL]
            ),
        ),
        _case(
            "prose/too-short-to-reach",
            "prose",
            "'Too short to reach the lamp' is about the lamp, not a contradiction.",
            defect=False,
            codes=identity,
            run=lambda: _prose("Wren was too short to reach the lamp.", appearance=[_WREN_TALL]),
        ),
        _case(
            "prose/short-with-him",
            "prose",
            "'Short with him' is her manner, not her height.",
            defect=False,
            codes=identity,
            run=lambda: _prose("Wren was short with him all morning.", appearance=[_WREN_TALL]),
        ),
        _case(
            "prose/short-on-patience",
            "prose",
            "'Short on patience' is not her height.",
            defect=False,
            codes=identity,
            run=lambda: _prose("Wren was short on patience that night.", appearance=[_WREN_TALL]),
        ),
        _case(
            "prose/a-small-part",
            "prose",
            "'A small part of the plan' is not her height.",
            defect=False,
            codes=identity,
            run=lambda: _prose("Wren was a small part of the plan.", appearance=[_WREN_TALL]),
        ),
        _case(
            "prose/a-tiny-bit",
            "prose",
            "'A tiny bit annoyed' is not her height.",
            defect=False,
            codes=identity,
            run=lambda: _prose("Wren was a tiny bit annoyed.", appearance=[_WREN_TALL]),
        ),
        _case(
            "prose/small-comfort",
            "prose",
            "'Small comfort' is not her height.",
            defect=False,
            codes=identity,
            run=lambda: _prose("Wren was small comfort to the crew.", appearance=[_WREN_TALL]),
        ),
        _case(
            "prose/short-a-few-coins",
            "prose",
            "'Short a few coins' is money, not height.",
            defect=False,
            codes=identity,
            run=lambda: _prose("Wren was short a few coins for the fare.", appearance=[_WREN_TALL]),
        ),
        _case(
            "prose/small-scar-is-not-height",
            "prose",
            "A small scar, a tiny tattoo and a short skirt in her details say nothing of her height.",
            defect=False,
            codes=identity,
            run=lambda: _prose("Wren was tall.", appearance=[_WREN_SMALL_SCAR]),
        ),
        _case(
            "prose/age-that-summer",
            "prose",
            "'Wren was nineteen that summer' is another time, not this scene.",
            defect=False,
            codes=identity,
            run=lambda: _prose("Wren was nineteen that summer.", ages=[_WREN_AGE]),
        ),
        _case(
            "prose/age-a-list",
            "prose",
            "'Wren, two, three, four' is a count of heads, not an age.",
            defect=False,
            codes=identity,
            run=lambda: _prose("Osric counted them off: Wren, two, three, four.", ages=[_WREN_AGE]),
        ),
        _case(
            "prose/someone-elses-scar",
            "prose",
            "'Wren touched the scar on his right cheek': Osric's scar, not hers.",
            defect=False,
            codes=identity,
            run=lambda: _prose(
                "Wren pulled up her hood. Wren touched the scar on his right cheek.",
                appearance=[_WREN_TALL],
            ),
        ),
        _case(
            "prose/a-fresh-scar",
            "prose",
            "A fresh scar on the other cheek is new, not a contradiction.",
            defect=False,
            codes=identity,
            run=lambda: _prose(
                "Wren pulled up her hood. Wren had a fresh scar on her right cheek.",
                appearance=[_WREN_TALL],
            ),
        ),
        _case(
            "prose/age-when-younger",
            "prose",
            "'When Wren was twelve' is about an earlier time.",
            defect=False,
            codes=identity,
            run=lambda: _prose("When Wren was twelve she ran away to sea.", ages=[_WREN_AGE]),
        ),
        _case(
            "prose/age-looked",
            "prose",
            "'Wren looked nineteen' is how she looked.",
            defect=False,
            codes=identity,
            run=lambda: _prose("In the lamplight Wren looked nineteen.", ages=[_WREN_AGE]),
        ),
        _case(
            "prose/age-a-count",
            "prose",
            "'Wren, one of the crew' and 'three days late' are not ages.",
            defect=False,
            codes=identity,
            run=lambda: _prose(
                "Wren, one of the crew, waved. Wren was three days late.", ages=[_WREN_AGE]
            ),
        ),
        _case(
            "prose/age-said-aloud",
            "prose",
            "Someone saying an age aloud may be lying: dialogue is not read.",
            defect=False,
            codes=identity,
            run=lambda: _prose("“Wren is nineteen,” Osric lied.", ages=[_WREN_AGE]),
        ),
        _case(
            "prose/age-on-someone-else",
            "prose",
            "'Osric was nineteen, and Wren laughed': the age is Osric's.",
            defect=False,
            codes=identity,
            run=lambda: _prose("Osric was nineteen, and Wren laughed.", ages=[_WREN_AGE]),
        ),
        _case(
            "prose/age-by-pronoun",
            "prose",
            "'She was nineteen' with Wren named only before: pronouns are not read for age.",
            defect=True,
            codes=identity,
            run=lambda: _prose("Wren took the oars. She was nineteen.", ages=[_WREN_AGE]),
            known_limit="An age is read only beside the person's name; 'she' needs a model.",
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
            "prose/spends-more-than-there-are",
            "prose",
            "Four flares in the story bible; Wren lights two, then three more.",
            defect=True,
            codes=counted,
            run=lambda: _prose(
                "Wren lit two flares off Marrow Light. Later Wren lit three more flares.",
                supplies=[_FLARES],
            ),
        ),
        _case(
            "prose/more-left-than-possible",
            "prose",
            "Four flares; Wren lights one and still has four left.",
            defect=True,
            codes=counted,
            run=lambda: _prose("Wren lit a flare. Wren had four flares left.", supplies=[_FLARES]),
        ),
        _case(
            "prose/spends-within-the-count",
            "prose",
            "Four flares; Wren lights one and has three left.",
            defect=False,
            codes=counted,
            run=lambda: _prose("Wren lit a flare. Wren had three flares left.", supplies=[_FLARES]),
        ),
        _case(
            "prose/someone-else-fires-flares",
            "prose",
            "Flares fired from the shore by no one in the scene are not Wren's.",
            defect=False,
            codes=counted,
            run=lambda: _prose("Six flares rose from the far shore.", supplies=[_FLARES]),
        ),
        _case(
            "prose/wished-for-flares",
            "prose",
            "Wren wishing for ten flares spends none.",
            defect=False,
            codes=counted,
            run=lambda: _prose("Wren wished she had ten flares.", supplies=[_FLARES]),
        ),
        _case(
            "prose/others-fire-flares-she-watches",
            "prose",
            "Wren watching as the harbour guards fire six flares spends none of hers.",
            defect=False,
            codes=counted,
            run=lambda: _prose(
                "Wren watched as the harbour guards fired six flares.", supplies=[_FLARES]
            ),
        ),
        _case(
            "prose/osric-fires-at-wren",
            "prose",
            "Osric firing six flares at Wren spends none of Wren's own.",
            defect=False,
            codes=counted,
            run=lambda: _prose("Osric fired six flares at Wren.", supplies=[_WRENS_FLARES]),
        ),
        _case(
            "prose/someone-else-has-flares",
            "prose",
            "The guards having nine flares left is not a count of Wren's.",
            defect=False,
            codes=counted,
            run=lambda: _prose(
                "Wren saw that the guards had nine flares left.", supplies=[_FLARES]
            ),
        ),
        _case(
            "prose/she-fires-her-flares",
            "prose",
            "Four of Wren's flares; 'she' fires six of them, with Wren named just before.",
            defect=True,
            codes=counted,
            run=lambda: _prose(
                "Wren reached the rail. She fired six flares.", supplies=[_WRENS_FLARES]
            ),
        ),
        _case(
            "prose/chapters-next-morning-before",
            "prose",
            "A story counted in chapters: 'the next morning', but this scene is placed "
            "before the one it follows.",
            defect=True,
            codes=when,
            run=lambda: _prose(
                "The next morning, Wren untied the ferry at Kestle Quay.",
                previous=TimeNeighbour(
                    "The Crossing", None, None, "chapter 3", "chapter 4", (-2.0, -1.0)
                ),
            ),
        ),
        _case(
            "prose/chapters-three-days-later",
            "prose",
            "A story counted in chapters: 'three days later' cannot be measured, and is "
            "not called wrong.",
            defect=False,
            codes=when,
            run=lambda: _prose(
                "Three days later, Wren untied the ferry at Kestle Quay.",
                previous=TimeNeighbour(
                    "The Crossing", None, None, "chapter 5", "chapter 4", (0.0, 2.0)
                ),
            ),
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


# ----------------------------------------------------------------------- voice

# The author's usual: four scenes of the Saltglass Ferry told close on Wren, in the past
# tense, sentences of a dozen words or so and a little dialogue.
_USUAL_LINES = [
    "Wren walked down to the ferry before the light came up over the estuary.",
    "She counted the crates on the deck and found one more than the manifest said.",
    "Tobin was waiting by the rail with his collar turned up against the wind.",
    '"You are late," he said, and she shrugged and set her bag down beside him.',
    "They watched the gulls fight over a crust somebody had dropped on the boards.",
    "He had brought bread, and she took half of it without asking.",
    "The tide was turning, and the smell of it reached them before the sound did.",
    "It was cold, but neither of them wanted to be the first to say so.",
    "When the bell rang across the water she stood and brushed the salt from her coat.",
    "He did not ask where she was going, and she did not tell him.",
]


def _voice_text(lines: list[str], words: int, seed: int) -> str:
    import random

    rng = random.Random(seed)
    text = ""
    while len(text.split()) < words:
        text += " ".join(rng.sample(lines, k=rng.randint(min(4, len(lines)), len(lines)))) + " "
    return text


def _voice(scene: str) -> Run:
    def run() -> set[str]:
        from backend.domain.voice import baseline, drift, measure

        usual = baseline([measure(_voice_text(_USUAL_LINES, 1600, seed)) for seed in range(6)])
        return {note.code for note in drift(measure(scene), usual)}

    return run


_SPEAKERS = {"Tobin": ("Tobin",), "Wren": ("Wren",), "Ada": ("Ada",)}
# Tobin's usual: short lines with short forms, in exchanges with Wren.
_TOBIN_USUAL = (
    '"You\'re late," said Tobin.\n"The tide," said Wren.\n'
    '"Don\'t. It\'s been turning all night."\n"Then it\'s slow."\n'
    '"I\'m cold. I won\'t wait."\n"You will."\n'
)


def _speakers(scene: str) -> Run:
    def run() -> set[str]:
        from backend.domain.voice import speech_drift, speeches

        usual = [speeches("\n".join([_TOBIN_USUAL] * 10), _SPEAKERS)[0]["Tobin"]] * 3
        found, share = speeches(scene, _SPEAKERS)
        return {
            note.code
            for who, speech in found.items()
            if who == "Tobin"
            for note in speech_drift(speech, usual, share)
        }

    return run


def _voice_cases() -> list[Case]:
    first = [
        line.replace("She ", "I ").replace("she ", "I ").replace("Wren ", "I ")
        for line in _USUAL_LINES
    ]
    present = [
        "Wren walks down to the ferry before the light comes up over the estuary.",
        "She counts the crates on the deck and finds one more than the manifest says.",
        "Tobin waits by the rail with his collar turned up against the wind.",
        '"You are late," he says, and she shrugs and sets her bag down beside him.',
        "They watch the gulls fight over a crust somebody drops on the boards.",
        "He brings bread, and she takes half of it without asking.",
    ]
    long = [
        "Wren walked down to the ferry before the light came up over the estuary, and she "
        "counted the crates on the deck, finding one more than the manifest said, which "
        "meant that somebody had loaded it in the night without telling the harbourmaster.",
        "Tobin was waiting by the rail with his collar turned up against the wind, and when "
        "she came up beside him he only nodded, as though they had arranged to meet there "
        "and had nothing to say that the morning had not already said for them.",
    ]
    talk = [
        '"Where were you?" Tobin said.',
        '"Out," Wren said.',
        '"Out where, exactly, at that hour, with the tide running the way it was?"',
        '"Just out. By the water. Counting crates, if you must know."',
        '"And how many were there?"',
        '"One more than there should have been."',
    ]
    adverbs = [
        "Wren walked slowly and quietly down to the ferry, carefully avoiding the puddles.",
        "She counted the crates nervously and found, surprisingly, one more than expected.",
        "Tobin was waiting patiently by the rail, his collar turned up tightly.",
        '"You are late," he said softly, and she shrugged wearily and sat down heavily.',
    ]
    leaning = [
        line.replace("walked", "glanced")
        .replace("watched", "glanced at")
        .replace("counted", "glanced at")
        for line in _USUAL_LINES
    ]
    slip = (
        _voice_text(_USUAL_LINES, 500, 41)
        + " She opens the hatch and climbs down. "
        + _voice_text(_USUAL_LINES, 300, 42)
    )
    stranger = (
        "Upon arrival, an assessment of conditions aboard the vessel was undertaken by "
        "personnel; findings indicated deterioration of fittings, necessitating remediation "
        "within specified timeframes, pursuant to regulations governing passenger craft. "
        "Therefore it is to be noted that all of the cargo is to be inspected by us. "
    )
    in_dialogue_i = [
        '"I will not wait," Wren said, and she sat down by the rail.',
        '"I am cold," she said. "I want to go home." Tobin nodded.',
        *_USUAL_LINES[:6],
    ]
    general_truth = (
        _voice_text(_USUAL_LINES, 400, 7)
        + " Tides are older than ferries. "
        + _voice_text(_USUAL_LINES, 300, 8)
    )
    return [
        _case(
            "voice/told-with-i",
            "voice",
            "a scene told with “I” in a book told with “she”",
            defect=True,
            codes={"voice_person"},
            run=_voice(_voice_text(first, 600, 1)),
        ),
        _case(
            "voice/present-tense",
            "voice",
            "a scene in the present in a book in the past",
            defect=True,
            codes={"voice_tense"},
            run=_voice(_voice_text(present, 600, 2)),
        ),
        _case(
            "voice/long-sentences",
            "voice",
            "sentences twice as long as the author's usual",
            defect=True,
            codes={"voice_sentence_length", "voice_sentence_variety"},
            run=_voice(_voice_text(long, 600, 3)),
        ),
        _case(
            "voice/all-dialogue",
            "voice",
            "a scene almost all dialogue in a book with little",
            defect=True,
            codes={"voice_dialogue"},
            run=_voice(_voice_text(talk, 500, 4)),
        ),
        _case(
            "voice/many-adverbs",
            "voice",
            "words ending in -ly three times as often as usual",
            defect=True,
            codes={"voice_adverbs"},
            run=_voice(_voice_text(adverbs, 500, 5)),
        ),
        _case(
            "voice/leans-on-a-word",
            "voice",
            "a scene that keeps using “glanced”",
            defect=True,
            codes={"voice_leans_on"},
            run=_voice(_voice_text(leaning, 600, 6)),
        ),
        _case(
            "voice/tense-slip",
            "voice",
            "one sentence slips into the present in past narration",
            defect=True,
            codes={"voice_tense_slip"},
            run=_voice(slip),
        ),
        _case(
            "voice/someone-elses-words",
            "voice",
            "a scene in an official register, as if by someone else",
            defect=True,
            codes={"voice_everyday_words", "voice_sentence_length", "voice_leans_on"},
            run=_voice(_voice_text([stranger], 1600, 9)),
        ),
        _case(
            "voice/short-scene-in-first-person",
            "voice",
            "a 60-word note told with “I”",
            defect=True,
            codes={"voice_person"},
            run=_voice(" ".join(first[:5])),
            known_limit="A scene under 300 words is never compared: too few words to say how it sounds.",
        ),
        _case(
            "voice/same-voice",
            "voice",
            "a new scene in the author's usual voice",
            defect=False,
            codes={
                "voice_person",
                "voice_tense",
                "voice_sentence_length",
                "voice_dialogue",
                "voice_adverbs",
                "voice_leans_on",
                "voice_everyday_words",
                "voice_tense_slip",
                "voice_echoes",
            },
            run=_voice(_voice_text(_USUAL_LINES, 900, 99)),
        ),
        _case(
            "voice/i-only-in-dialogue",
            "voice",
            "“I” said aloud, never in the narration",
            defect=False,
            codes={"voice_person", "voice_person_mixed"},
            run=_voice(_voice_text(in_dialogue_i, 600, 10)),
        ),
        _case(
            "voice/speaker-speaks-differently",
            "voice",
            "Tobin, tagged, makes long speeches with no short forms; usually clipped and full of them",
            defect=True,
            codes={"voice_speaker_length", "voice_speaker_short_forms"},
            run=_speakers(
                "\n".join(
                    [
                        '"You are late, and I would have you know that I have stood upon '
                        "these stones since the first bell, and I do not intend to stand "
                        'upon them for one moment longer than I must," said Tobin.\n'
                        '"The tide," said Wren.\n'
                        '"I do not believe that the tide is to blame, for I have watched '
                        'it all night and it has done nothing it does not do every night."\n'
                        '"Then it is slow."\n'
                    ]
                    * 4
                )
            ),
        ),
        _case(
            "voice/speaker-by-pronoun-only",
            "voice",
            "long formal lines with only “he said”: whose they are is not read",
            defect=False,
            codes={"voice_speaker_length", "voice_speaker_short_forms"},
            run=_speakers(
                "\n".join(
                    [
                        '"I do not believe that the tide is to blame, for I have watched '
                        'it all night and it has done nothing it does not do," he said.\n'
                        '"Then it is slow," Wren said.\n'
                    ]
                    * 6
                )
            ),
        ),
        _case(
            "voice/speaker-in-a-crowded-exchange",
            "voice",
            "untagged long lines in an exchange that names a third person: turns are not given",
            defect=False,
            codes={"voice_speaker_length", "voice_speaker_short_forms"},
            run=_speakers(
                "\n".join(
                    [
                        '"Short," said Tobin.\n"Short," said Wren.\n'
                        'Ada came down the steps. "I do not believe that the tide is to '
                        'blame, for I have watched it all night and it has done nothing."\n'
                        '"I would have you know that I have stood upon these stones since '
                        'the first bell, and I shall not stand upon them a moment longer."\n'
                    ]
                    * 6
                )
            ),
        ),
        _case(
            "voice/speaker-as-usual",
            "voice",
            "Tobin's lines sound as they usually do",
            defect=False,
            codes={"voice_speaker_length", "voice_speaker_short_forms"},
            run=_speakers("\n".join([_TOBIN_USUAL] * 4)),
        ),
        _case(
            "voice/general-truth",
            "voice",
            "a present-tense truth about tides, in past narration",
            defect=False,
            codes={"voice_tense", "voice_tense_mixed"},
            run=_voice(general_truth),
        ),
    ]


# ------------------------------------------------------------------ travel and lasting statuses
# SCN-007 travel read from the words, and CON-004 statuses that last (imprisoned, exiled,
# missing, married) with the event that ends them honoured. All notes.

_PLACES = [
    PlaceKnown("quay", "Kestle Quay", ("Kestle Quay", "the quay")),
    PlaceKnown("light", "Marrow Light", ("Marrow Light",)),
    PlaceKnown("fen", "the Low Fen", ("the Low Fen", "Low Fen")),
]
_PEOPLE = {"Wren Halloway": ("Wren Halloway", "Wren"), "Osric Pell": ("Osric Pell", "Osric")}


def _travel(text: str, **fields) -> set[str]:
    brief = MovementBrief(people=dict(_PEOPLE), places=list(_PLACES), **fields)
    return {item.code for item in movement_findings(text, brief)}


def _travel_cases() -> list[Case]:
    moved = ["moves_elsewhere", "travel_too_soon", "moves_while_held", "moves_into_exile"]
    road = [("quay", "light", 3.0, "3 days")]
    soon = Elsewhere(
        "Wren Halloway", "quay", "the scene “The Crossing” (day 6)", 1.0, "1 day", True
    )
    held = Kept("Wren Halloway", "held", "Wren Halloway is imprisoned at Kestle Quay", "quay")
    return [
        _case(
            "prose/rides-away-from-the-planned-place",
            "prose",
            "The scene is planned at Kestle Quay; the words have Wren sail to Marrow Light.",
            defect=True,
            codes=moved,
            run=lambda: _travel("At dusk Wren sailed to Marrow Light.", scene_place="quay"),
        ),
        _case(
            "prose/rides-within-the-planned-place",
            "prose",
            "Walking to the quay, the scene's own place, is not leaving it.",
            defect=False,
            codes=moved,
            run=lambda: _travel("Wren walked back to the quay.", scene_place="quay"),
        ),
        _case(
            "prose/journey-quicker-than-the-table",
            "prose",
            "Marrow Light is three days from the quay; Wren is at the quay a day later.",
            defect=True,
            codes=moved,
            run=lambda: _travel("Wren rode to Marrow Light.", travel=road, elsewhere=[soon]),
        ),
        _case(
            "prose/journey-wished-for",
            "prose",
            "Wren only wants to sail to Marrow Light.",
            defect=False,
            codes=moved,
            run=lambda: _travel("Wren wanted to sail to Marrow Light.", scene_place="quay"),
        ),
        _case(
            "prose/journey-said-aloud",
            "prose",
            "Osric says Wren rode to Marrow Light; the narration does not.",
            defect=False,
            codes=moved,
            run=lambda: _travel("“Wren rode to Marrow Light,” Osric said.", scene_place="quay"),
        ),
        _case(
            "prose/journey-in-a-clause-about-something-else",
            "prose",
            "'The horse that Wren rode to Marrow Light was lame' is about the horse.",
            defect=False,
            codes=moved,
            run=lambda: _travel(
                "The horse that Wren rode to Marrow Light was lame.", scene_place="quay"
            ),
        ),
        _case(
            "prose/journey-in-a-dream",
            "prose",
            "Wren wanders to Marrow Light only in her dreams.",
            defect=False,
            codes=moved,
            run=lambda: _travel("Wren wandered to Marrow Light in her dreams.", scene_place="quay"),
        ),
        _case(
            "prose/walks-out-while-imprisoned",
            "prose",
            "Wren is imprisoned at the quay then; the words walk her to the Low Fen.",
            defect=True,
            codes=moved,
            run=lambda: _travel("Wren walked to the Low Fen.", kept=[held]),
        ),
        _case(
            "prose/journey-by-pronoun",
            "prose",
            "'She rode to Marrow Light' after naming Wren: a pronoun is not read.",
            defect=True,
            codes=moved,
            run=lambda: _travel("Wren saddled up. She rode to Marrow Light.", scene_place="quay"),
            known_limit="A pronoun is never read as the person named before; that needs a model.",
        ),
    ]


def _lifecycle_cases() -> list[Case]:
    lasting = [
        "held_elsewhere",
        "exile_returned",
        "missing_seen",
        "marries_while_married",
        "two_spouses",
    ]
    held = fact("f_held", "wren", "IMPRISONED_AT", "light", starts=At(at=2), ends=At(at=9))
    freed = event("wren_freed", At(at=3), kind="released", who=["wren"])
    exiled = fact("f_exile", "osric", "EXILED_FROM", "quay", starts=At(at=1))
    missing = fact(
        "f_missing", "ada", "HAS_STATUS", {"kind": "string", "value": "missing"}, starts=At(at=1)
    )
    married = fact("f_wed", "bram", "MARRIED_TO", "ada", starts=At(at=1))
    wedding = event("bram_weds", At(at=6), kind="wedding", who=["bram", "wren"])
    return [
        _case(
            "continuity/held-and-elsewhere",
            "continuity",
            "Wren is imprisoned at Marrow Light from day 2 to 9; a day-4 scene has her at the quay.",
            defect=True,
            codes=lasting,
            run=lambda: _continuity(
                facts=[held], scenes=[scene("quay_day4", At(at=4), place="quay", who=["wren"])]
            ),
        ),
        _case(
            "continuity/held-but-released",
            "continuity",
            "Wren is released on day 3, before the day-4 scene at the quay.",
            defect=False,
            codes=lasting,
            run=lambda: _continuity(
                facts=[held],
                events=[freed],
                scenes=[scene("quay_day4", At(at=4), place="quay", who=["wren"])],
            ),
        ),
        _case(
            "continuity/exile-back-home",
            "continuity",
            "Osric, exiled from the quay since year 1, is at the quay in year 5.",
            defect=True,
            codes=lasting,
            run=lambda: _continuity(
                facts=[exiled], scenes=[scene("home", At(at=5), place="quay", who=["osric"])]
            ),
        ),
        _case(
            "continuity/missing-among-friends",
            "continuity",
            "Ada is missing from year 1, yet a year-4 scene has her with Wren.",
            defect=True,
            codes=lasting,
            run=lambda: _continuity(
                facts=[missing],
                scenes=[scene("inn", At(at=4), place="house", who=["ada", "wren"])],
            ),
        ),
        _case(
            "continuity/missing-loose-time",
            "continuity",
            "The scene is only 'some time before year 3': it may come before she goes missing.",
            defect=False,
            codes=lasting,
            run=lambda: _continuity(
                facts=[missing],
                scenes=[
                    scene(
                        "inn", Between(earliest=None, latest=3), place="house", who=["ada", "wren"]
                    )
                ],
            ),
        ),
        _case(
            "continuity/marries-while-married",
            "continuity",
            "Bram, married to Ada, marries Wren in year 6 with nothing ending the first.",
            defect=True,
            codes=lasting,
            run=lambda: _continuity(facts=[married], events=[wedding]),
        ),
        _case(
            "continuity/marries-after-a-divorce",
            "continuity",
            "A divorce in year 4 ends Bram's marriage before the year-6 wedding.",
            defect=False,
            codes=lasting,
            run=lambda: _continuity(
                facts=[married],
                events=[wedding, event("parting", At(at=4), kind="divorce", who=["bram", "ada"])],
            ),
        ),
    ]


def all_cases() -> list[Case]:
    return [
        *_continuity_cases(),
        *_rules_cases(),
        *_knowledge_cases(),
        *_drafting_cases(),
        *_plan_cases(),
        *_knowing_cases(),
        *_prose_cases(),
        *_travel_cases(),
        *_lifecycle_cases(),
        *_voice_cases(),
    ]


__all__ = ["Case", "all_cases", "entity"]
