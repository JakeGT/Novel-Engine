"""A short manuscript of the Saltglass Ferry with labelled defects in its prose (CON-013).

Six scenes of original prose. Eight defects are planted, each labelled with the
passages it shows in (found by a phrase inside them) and the records a critic needs
beside those passages to see it. The records are the story bible, plan and rules as
short sentences, each with the checks it is evidence for, worked out from the story
graph by ``review_bundles.fact_anchors`` and the plan.

Nothing here is a real author's text.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.domain.canon.entities import CanonEntity
from backend.domain.review_bundles import (
    Anchor,
    Evidence,
    RuleScope,
    SceneFrame,
    fact_anchors,
    tokens_of,
)
from backend.domain.sources import MentionStatus, build_index, find_mentions
from tests.eval.consistency.story import SCOPE

ENTRIES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "wren": ("Character", "Wren Halloway", ("Wren",)),
    "osric": ("Character", "Osric Pell", ("Osric", "the lamp-warden")),
    "ada": ("Character", "Ada Quill", ("Ada",)),
    "bram": ("Character", "Bram Tolley", ("Bram",)),
    "quay": ("Location", "Kestle Quay", ("the quay",)),
    "light": ("Location", "Marrow Light", ()),
    "fen": ("Location", "the Low Fen", ("the fen",)),
    "house": ("Location", "the Ferry House", ()),
    "sextant": ("Object", "the brass sextant", ("the sextant",)),
    "ledger": ("Object", "the tide ledger", ("the ledger",)),
    "wardens": ("Faction", "the Tidewardens", ("Tidewardens",)),
}
TYPES = {key: kind for key, (kind, _name, _aliases) in ENTRIES.items()}

# (frame, paragraphs). Paragraphs are joined with blank lines.
SCENES: list[tuple[SceneFrame, list[str]]] = [
    (
        SceneFrame("s1", 1, point_of_view="wren", place_id="quay", threads=frozenset({"t_lost"})),
        [
            "Dawn came grey over Kestle Quay. Wren Halloway untied the ferry with cold "
            "fingers and felt for the brass sextant in the deep pocket of her coat, where "
            "it had ridden every crossing for eleven years.",
            "She took the sextant out and turned it to the weak light. A crack ran clean "
            "across its mirror, a pale line from rim to rim, left there by the night of the "
            "storm. She had meant to have it mended and never had.",
            "Ada Quill stood at the top of the steps with her lamp. “The old ferry went "
            "down in the spring storm,” she said, as she said every morning, “and "
            "the fen has been hungry since.”",
            "Wren pushed off. The water took the hull without a sound, and the quay "
            "shrank behind her into the mist.",
        ],
    ),
    (
        SceneFrame("s2", 2, point_of_view="wren", place_id="fen"),
        [
            "The Low Fen opened around her in channels and dead reeds. Herons stood in "
            "the shallows like posts. Wren poled slowly, reading the water by its colour.",
            "She thought of Osric Pell kneeling before the Tidewardens' altar, the oath "
            "she knew he had sworn in secret, his hand on the drowned bell.",
            "And she thought of Ada Quill, who was Osric Pell's own sister, though nobody "
            "at the Ferry House would say so aloud.",
            "The mist came down thicker. Somewhere to the north a bittern boomed, once, "
            "and then the fen was silent.",
        ],
    ),
    (
        SceneFrame("s3", 3, point_of_view="wren", place_id="house"),
        [
            "The Ferry House smelled of tar and boiled tea. Bram Tolley was waiting by "
            "the hearth, his oilskin dripping, his boots planted wide.",
            "Without a word Bram Tolley drew a short blade from his boot and cut the "
            "mooring rope that tied her skiff to the rail.",
            "Ada Quill wrote the fare into the tide ledger in her small square hand and "
            "blotted it twice, as if the ink might run away.",
        ],
    ),
    (
        SceneFrame("s4", 4, point_of_view="wren", place_id="light", has_conditions=True),
        [
            "At Marrow Light the stair wound up through the dark. At the top, Osric "
            "trimmed the great lamp while Wren watched the beam swing out over the water.",
            "Wren had left the tide ledger with Ada Quill at the Ferry House, safe in "
            "the drawer under the counter, where it always was.",
            "The beam went round and round. Each time it passed, the whole fen flashed "
            "silver and went black again.",
        ],
    ),
    (
        SceneFrame("s5", 5, point_of_view="wren", place_id="fen", threads=frozenset({"t_lost"})),
        [
            "Halfway home Wren raised the brass sextant and, through its flawless, "
            "unmarked mirror, took a true bearing on the light behind her.",
            "Bram Tolley rowed alongside in the grey. “She sank in the autumn gales,” "
            "he said of the old ferry, “not in spring. I cut her moorings myself.”",
            "She steered for the quay and did not answer him.",
        ],
    ),
    (
        SceneFrame("s6", 6, place_id="light", reveals=frozenset({"kin"})),
        [
            "Snow came early to Marrow Light that year, and lay on the gallery rail "
            "until the spring.",
            "He kept the lamp burning all that winter, alone at the top of the stair, "
            "and spoke to no one.",
            "When the thaw came, Ada Quill climbed the stair with bread and found the "
            "door open, and learned at last what her brother had kept from her.",
        ],
    ),
]

RULES = [
    RuleScope(
        "no_blades",
        entity_ids=frozenset({"bram", "wardens"}),
        predicates=frozenset({"MEMBER_OF", "CARRIES"}),
    )
]

# (key, text, subject, kind of fact, object, author only). Facts take their anchors
# from the story graph; the other records are given theirs below.
FACTS: list[tuple[str, str, str, str, str | None, bool]] = [
    (
        "fact:osric_at_fen",
        "Osric Pell is located at the Low Fen all that winter.",
        "osric",
        "LOCATED_AT",
        "fen",
        False,
    ),
    (
        "fact:wren_at_quay",
        "Wren Halloway is located at Kestle Quay at dawn of the crossing.",
        "wren",
        "LOCATED_AT",
        "quay",
        False,
    ),
    (
        "fact:ada_at_house",
        "Ada Quill is located at the Ferry House.",
        "ada",
        "LOCATED_AT",
        "house",
        False,
    ),
    (
        "fact:sextant_cracked",
        "The brass sextant has status cracked across its mirror.",
        "sextant",
        "HAS_STATUS",
        None,
        False,
    ),
    (
        "fact:wren_owns_sextant",
        "Wren Halloway owns the brass sextant.",
        "wren",
        "OWNS",
        "sextant",
        False,
    ),
    ("fact:ada_owns_ledger", "Ada Quill owns the tide ledger.", "ada", "OWNS", "ledger", False),
    (
        "fact:secret",
        "Osric Pell is a member of the Tidewardens.",
        "osric",
        "MEMBER_OF",
        "wardens",
        True,
    ),
    (
        "fact:bram_member",
        "Bram Tolley is a member of the Tidewardens.",
        "bram",
        "MEMBER_OF",
        "wardens",
        False,
    ),
    (
        "fact:kin",
        "Ada Quill is related to Osric Pell: she is his sister.",
        "ada",
        "RELATED_TO",
        "osric",
        False,
    ),
    ("fact:osric_keeps", "Osric Pell keeps Marrow Light.", "osric", "KEEPS", "light", False),
    ("fact:bram_knows_wren", "Bram Tolley knows Wren Halloway.", "bram", "KNOWS", "wren", False),
    ("fact:ada_age", "Ada Quill has age 52.", "ada", "AGE", None, False),
    (
        "fact:bram_wounded",
        "Bram Tolley has status wounded in the left hand, from last winter.",
        "bram",
        "HAS_STATUS",
        None,
        False,
    ),
    ("fact:wren_knows_ada", "Wren Halloway knows Ada Quill.", "wren", "KNOWS", "ada", False),
]

OTHER: list[tuple[str, str, frozenset[Anchor], frozenset[str]]] = [
    (
        "rule:no_blades",
        "Story rule: no Tidewarden carries a blade.",
        frozenset({Anchor("rule", "no_blades")}),
        frozenset({"wardens"}),
    ),
    (
        "reveal:kin",
        "The plan: the reader learns that Ada Quill is Osric Pell's sister in “Winter”, not before.",
        frozenset({Anchor("reveal", "kin"), Anchor("state", "ada"), Anchor("state", "osric")}),
        frozenset({"ada", "osric"}),
    ),
    (
        "thread:t_lost",
        "Thread: who sank the old ferry, and when? Opened in “The Crossing”, settled in “The Bearing”.",
        frozenset({Anchor("thread", "t_lost")}),
        frozenset(),
    ),
    (
        "condition:s4",
        "Before “Marrow Light”: Wren Halloway must carry the tide ledger.",
        frozenset({Anchor("scene", "s4")}),
        frozenset({"wren", "ledger"}),
    ),
    (
        "belief:wren_ferry",
        "Wren Halloway believes the storm sank the old ferry.",
        frozenset({Anchor("pov", "wren")}),
        frozenset({"wren"}),
    ),
]


@dataclass(frozen=True)
class Defect:
    id: str
    about: str
    # A phrase inside each passage it shows in, as (scene id, phrase).
    passages: tuple[tuple[str, str], ...]
    records: tuple[str, ...]


DEFECTS: list[Defect] = [
    Defect(
        "osric-in-two-places",
        "Osric trims the lamp at Marrow Light while the story bible has him at the Low Fen.",
        (("s4", "trimmed the great lamp"),),
        ("fact:osric_at_fen",),
    ),
    Defect(
        "sextant-cracked-then-flawless",
        "The sextant's mirror is cracked in scene 1 and flawless in scene 5.",
        (("s1", "A crack ran clean"), ("s5", "flawless, unmarked mirror")),
        ("fact:sextant_cracked",),
    ),
    Defect(
        "secret-in-the-viewpoint",
        "Wren thinks of Osric's oath to the Tidewardens, a secret she does not know.",
        (("s2", "Tidewardens' altar"),),
        ("fact:secret",),
    ),
    Defect(
        "warden-with-a-blade",
        "Bram, a Tidewarden, draws a blade; the story rule says no Tidewarden carries one.",
        (("s3", "short blade"),),
        ("rule:no_blades", "fact:bram_member"),
    ),
    Defect(
        "kinship-too-early",
        "Scene 2 says plainly that Ada is Osric's sister; the plan keeps that for scene 6.",
        (("s2", "own sister"),),
        ("fact:kin", "reveal:kin"),
    ),
    Defect(
        "sinking-spring-or-autumn",
        "The old ferry sank in the spring storm (scene 1) or the autumn gales (scene 5).",
        (("s1", "spring storm"), ("s5", "autumn gales")),
        ("thread:t_lost",),
    ),
    Defect(
        "unnamed-keeper-at-the-light",
        "'He' keeps the lamp at Marrow Light all winter: Osric, who is at the Low Fen.",
        (("s6", "kept the lamp burning"),),
        ("fact:osric_at_fen",),
    ),
    Defect(
        "ledger-not-carried",
        "Scene 4 needs Wren to carry the ledger; she has left it with Ada.",
        (("s4", "left the tide ledger"),),
        ("condition:s4",),
    ),
]


def _entities() -> list[CanonEntity]:
    return [
        CanonEntity(
            id=key,
            type=kind,
            name=name,
            aliases=list(aliases),
            schema_version_id="eval",
            created_revision=1,
            **SCOPE,
        )
        for key, (kind, name, aliases) in ENTRIES.items()
    ]


def scene_texts() -> list[tuple[SceneFrame, str]]:
    return [(frame, "\n\n".join(paragraphs)) for frame, paragraphs in SCENES]


def mentions(text: str) -> list[tuple[int, int, str]]:
    """Where each entry is named, by the application's own name-finding."""
    index = build_index(_entities(), [], source_id=None)
    return [
        (mention.start, mention.end, mention.entity_ids[0])
        for mention in find_mentions(text, index)
        if mention.status is MentionStatus.KNOWN
    ]


def evidence() -> tuple[list[Evidence], dict[str, str]]:
    """The records, and their words (for the lexical stand-in for a vector index)."""
    items: list[Evidence] = []
    words: dict[str, str] = {}
    for number, (key, text, subject, predicate, obj, secret) in enumerate(FACTS):
        anchors = set(fact_anchors(subject, predicate, obj, TYPES, RULES))
        if secret:
            # A secret goes to the viewpoint check of every scene seen by someone who
            # does not know it: here Wren, who sees every scene she is in.
            anchors.add(Anchor("pov", "wren"))
        items.append(
            Evidence(
                key=key,
                tokens=tokens_of(text),
                anchors=frozenset(anchors),
                entity_ids=frozenset({subject} | ({obj} if obj else set())),
                author_only=secret,
                order=float(number),
            )
        )
        words[key] = text
    for number, (key, text, anchors, entity_ids) in enumerate(OTHER, start=len(FACTS)):
        items.append(
            Evidence(
                key=key,
                tokens=tokens_of(text),
                anchors=anchors,
                entity_ids=entity_ids,
                order=float(number),
            )
        )
        words[key] = text
    return items, words


__all__ = [
    "DEFECTS",
    "RULES",
    "SCENES",
    "TYPES",
    "Defect",
    "evidence",
    "mentions",
    "scene_texts",
]
