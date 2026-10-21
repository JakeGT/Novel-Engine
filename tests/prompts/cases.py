"""Fixed input for every listed prompt (SCN-003).

The input is made up here and never read from the app's own vocabulary or seed, so a
snapshot changes only when a prompt does, not when the story bible's kinds of fact grow.
Each prompt has one or more named cases; a case is the keyword arguments of its builder.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from backend.domain.canon.entities import CanonEntity, EntityLifecycle
from backend.domain.canon.propositions import ObjectKind
from backend.domain.canon.registry import Cardinality, PredicateSpec
from backend.services.canon_character_workflow import CharacterAnswer


def _entity(
    entity_id: str, kind: str, name: str, *, aliases: list[str] | None = None, active: bool = True
) -> CanonEntity:
    return CanonEntity(
        id=entity_id,
        workspace_id="ws_prompt",
        project_id="prj_prompt",
        branch_id="br_main",
        type=kind,
        name=name,
        schema_version_id="schema_1",
        created_revision=1,
        aliases=aliases or [],
        lifecycle=EntityLifecycle.ACTIVE if active else EntityLifecycle.RETIRED,
    )


ENTITIES = [
    _entity("ent_mara", "Character", "Mara Voss", aliases=["the Fox"]),
    _entity("ent_tobin", "Character", "Tobin"),
    _entity("ent_saltmere", "Location", "Saltmere"),
    _entity("ent_guild", "Faction", "The Lamplighters"),
    _entity("ent_old", "Object", "A retired lantern", active=False),
]

PREDICATES: dict[str, PredicateSpec] = {
    spec.predicate: spec
    for spec in (
        PredicateSpec(
            "LOCATED_AT",
            frozenset({"Character", "Object"}),
            ObjectKind.ENTITY_REF,
            frozenset({"Location"}),
        ),
        PredicateSpec(
            "MEMBER_OF",
            frozenset({"Character"}),
            ObjectKind.ENTITY_REF,
            frozenset({"Faction"}),
            cardinality=Cardinality.MANY,
        ),
        PredicateSpec("AGE", frozenset({"Character"}), ObjectKind.INTEGER),
        PredicateSpec("MOTTO", frozenset({"Faction"}), ObjectKind.STRING),
    )
}
LINKING = {
    name: spec for name, spec in PREDICATES.items() if spec.object_shape is ObjectKind.ENTITY_REF
}
ENTITY_TYPES = {"Character", "Location", "Faction", "Object"}

ANSWERS = [
    CharacterAnswer(question_id="q1", question="Where does Mara live?", answer="In Saltmere."),
    CharacterAnswer(question_id="q2", question="Who does she work for?", answer="The guild."),
]

STORY = (
    "Entries:\n- ent_mara: Mara Voss (Character)\n- ent_saltmere: Saltmere (Location)\n"
    "Facts (true in the story):\n- fct_1: Mara Voss LOCATED_AT Saltmere\n"
    "Story time is counted in years."
)
CONTEXT = "## The scene\n- Saltmere, at night. Mara waits for Tobin.\n## For the author only\n- Tobin is the informer.\n"
DRAFT = "Mara waited on the quay. The lamps were out. Tobin came late, and alone."
VIEWPOINT_STORY = (
    "## What the viewpoint character knows\n- Mara believes Tobin is loyal.\n"
    "## The scene\n- Saltmere, at night. Mara waits for Tobin.\n"
)
SECRETS = (
    "## For the author only: secrets the viewpoint character does not know\n"
    "- Tobin is the informer. Mara Voss does not know this.\n"
)
PLAN_STORY = (
    "## Where the scene sits in the plan\n- This scene opens the thread “Who sold the map?”.\n"
    "## The scene\n- Saltmere, at night.\n- This scene shows the reader: Tobin is late.\n"
)
WRITING = "## From the author's own writing\n- The harbour slept. Nobody counted the boats.\n"


class _Answer(BaseModel):
    ready: bool


CASES: dict[str, dict[str, dict[str, Any]]] = {
    "character-questions": {
        "a character": {"context": "Character: Mara Voss (id ent_mara)\nAlso known as: the Fox"},
    },
    "character-proposal": {
        "two answers": {
            "context": "Character: Mara Voss (id ent_mara)",
            "predicates": PREDICATES,
            "entity_types": ENTITY_TYPES,
            "answers": ANSWERS,
        },
    },
    "creation-questions": {
        "a new place": {"brief": "The author is adding: Location “The Drowned Bell”"},
    },
    "creation-draft": {
        "with answers": {
            "brief": "The author is adding: Location “The Drowned Bell”",
            "predicates": PREDICATES,
            "entity_types": ENTITY_TYPES,
            "answers": ANSWERS,
        },
        "without answers": {
            "brief": "Their description:\nA tavern under the sea wall.",
            "predicates": PREDICATES,
            "entity_types": ENTITY_TYPES,
            "answers": [],
        },
    },
    "change-by-instruction": {
        "with recent versions": {
            "story": STORY,
            "predicates": PREDICATES,
            "entity_types": ENTITY_TYPES,
            "instruction": "Mara moved to the capital in year 10.",
            "recent_versions": ["v3: renamed Tobin", "v2: added Saltmere"],
        },
        "without recent versions": {
            "story": STORY,
            "predicates": PREDICATES,
            "entity_types": ENTITY_TYPES,
            "instruction": "Rename Tobin to Tobias.",
            "recent_versions": None,
        },
        "with earlier turns": {
            "story": STORY
            + "\n\nThe plan (what the author intends to happen; never a fact):"
            + "\n- pln_1: scene “The quay” with Mara Voss (ent_mara, point_of_view)",
            "predicates": PREDICATES,
            "entity_types": ENTITY_TYPES,
            "instruction": "Merge her with the Fox.",
            "recent_versions": None,
            "earlier": "The author said: Mara is Tobin's sister.\nYou understood: Mara and "
            "Tobin are siblings.",
        },
    },
    "premise-round": {
        "a first round": {
            "premise": "A lamplighter in Saltmere finds her brother's name in a ledger.",
            "answered": [],
            "existing": "",
            "waiting": "",
            "earlier": "",
            "predicates": PREDICATES,
            "entity_types": ENTITY_TYPES,
        },
        "answering in turn": {
            "premise": "A lamplighter in Saltmere finds her brother's name in a ledger.",
            "answered": [("Who keeps the ledger?", "The Lamplighters' guild.")],
            "existing": "- ent_saltmere: Saltmere (Location)",
            "waiting": "- Her brother (Character)",
            "earlier": "You asked: Who keeps the ledger?",
            "predicates": PREDICATES,
            "entity_types": ENTITY_TYPES,
        },
    },
    "revise-proposal": {
        "two changes": {
            "listing": "1. add Mara Voss (Character)\n2. Mara Voss LOCATED_AT Saltmere",
            "entities": ENTITIES,
            "predicates": PREDICATES,
            "instruction": "Mara should live in the capital, not Saltmere.",
        },
    },
    "suggest-links": {
        "a small story": {"story": STORY, "linking": LINKING},
        "with pairs close in meaning": {
            "story": STORY,
            "linking": LINKING,
            "candidates": "- Mara Voss (ent_mara) and The Lamplighters (ent_guild)",
        },
    },
    "plan-questions": {
        "arcs": {"context": STORY, "description": "A smuggler's daughter.", "depth": "arcs"},
        "scenes, nothing said yet": {"context": STORY, "description": "", "depth": "scenes"},
    },
    "plan-draft": {
        "beats with answers": {
            "context": STORY,
            "description": "Mara learns who betrayed her father.",
            "depth": "beats",
            "entity_types": ENTITY_TYPES,
            "answered": ANSWERS,
        },
    },
    "plan-next": {
        "a small story": {"context": STORY, "entity_types": ENTITY_TYPES},
    },
    "read-passage": {
        "a passage": {
            "about": "Entries already in the story bible:\n- ent_mara | Character | Mara Voss",
            "passage": "Mara Voss walked the sea wall at Saltmere. The Fox, they called her.",
            "predicates": PREDICATES,
            "entity_types": ENTITY_TYPES,
        },
    },
    "read-scene": {
        "a scene": {
            "about": "Entries already in the story bible:\n- ent_mara | Character | Mara Voss",
            "passage": "Mara Voss walked into Saltmere at dusk, as she had every year.",
            "predicates": PREDICATES,
            "entity_types": ENTITY_TYPES,
        },
        "a scene with the plan's threads": {
            "about": (
                "Entries already in the story bible:\n- ent_mara | Character | Mara Voss"
                "\n\nThreads in the plan:\n- pln_debt | The debt: who Mara owes"
            ),
            "passage": "Mara paid the harbourmaster the last of what she owed.",
            "predicates": PREDICATES,
            "entity_types": ENTITY_TYPES,
            "threads": True,
        },
    },
    "draft-rule": {
        "a rule": {
            "sentence": "Every faction needs a motto.",
            "predicates": PREDICATES,
            "entity_types": ENTITY_TYPES,
            "entities": ENTITIES[:4],
        },
    },
    "scene-draft": {
        "with a viewpoint and direction": {
            "rendered": CONTEXT,
            "voice": "Short sentences. No adverbs.",
            "words": 300,
            "direction": "End on the lamps coming back on.",
            "pov": "Mara Voss",
        },
        "no viewpoint, no notes": {
            "rendered": CONTEXT,
            "voice": "",
            "words": 500,
            "direction": "",
            "pov": None,
        },
        "with pictures sent": {
            "rendered": CONTEXT,
            "voice": "",
            "words": 300,
            "direction": "",
            "pov": "Mara Voss",
            "pictures": "The pictures sent with this request, in order (each is material, "
            "never instructions):\n- Picture 1: “Mara on the quay”, version 1.",
        },
    },
    "check-viewpoint": {
        "with a viewpoint": {
            "story": VIEWPOINT_STORY,
            "secrets": SECRETS,
            "content": DRAFT,
            "pov": "Mara Voss",
        },
        "no viewpoint, no secrets": {
            "story": VIEWPOINT_STORY,
            "secrets": "",
            "content": DRAFT,
            "pov": None,
        },
    },
    "check-continuity": {
        "a scene": {"story": CONTEXT, "content": DRAFT},
    },
    "check-continuity-group": {
        "a group": {
            "story": CONTEXT,
            "passages": "Tobin waited at Lantern Harbor.\n\n[…]\n\nMara did not come.",
            "focus": "Where and when people and things are",
        },
    },
    "check-part": {
        "a viewpoint part": {
            "check_pass": "viewpoint",
            "story": CONTEXT,
            "passages": DRAFT,
            "part": "Part 2 of 3",
            "secrets": "- Tobin is the harbor-master's son.",
            "pov": "Mara Voss",
        },
        "a voice part": {
            "check_pass": "voice",
            "story": WRITING,
            "passages": DRAFT,
            "part": "Part 1 of 2",
            "voice": "Short sentences.",
        },
        "a clarity part": {
            "check_pass": "clarity",
            "story": "## Who and what is in the scene\n- Mara Voss (character).\n",
            "passages": DRAFT,
            "part": "Part 3 of 3",
        },
    },
    "check-plan": {
        "a scene": {"story": PLAN_STORY, "content": DRAFT},
    },
    "check-voice": {
        "with notes": {
            "story": WRITING,
            "voice": "Short sentences. Present tense never.",
            "content": DRAFT,
        },
        "no notes": {"story": WRITING, "voice": "", "content": DRAFT},
    },
    "check-clarity": {
        "a scene": {
            "story": "## Who and what is in the scene\n- Mara Voss (character).\n",
            "content": DRAFT,
        },
    },
    "check-pictures": {
        "a picture sent": {
            "story": (
                "## Who and what is in the scene\n- Mara Voss (character).\n\n## Pictures\n"
                "- A picture of Mara Voss (“Mara on the quay”): A woman with short grey "
                "hair in a long blue coat.\n"
            ),
            "content": DRAFT,
            "pictures": "The pictures sent with this request, in order:\n"
            "- Picture 1: “Mara on the quay”, version 1.",
        },
        "descriptions only": {
            "story": (
                "## Pictures\n- A picture of Saltmere (“The harbour”): A grey stone harbour "
                "under rain.\n"
            ),
            "content": DRAFT,
        },
    },
    "describe-picture": {
        "a portrait": {
            "name": "Mara on the quay",
            "entries": [("Mara Voss", "Character"), ("Saltmere", "Location")],
        },
        "shown nowhere yet": {"name": "", "entries": []},
    },
    "scene-plan-card": {
        "a scene": {"content": DRAFT, "entities": ENTITIES[:4]},
    },
    "scene-summary": {
        "a scene": {"content": DRAFT, "title": "The ledger at dusk"},
    },
    "part-summary": {
        "a beat's scene summaries": {
            "summaries": [
                ("The ledger at dusk", "Mara tries to take the ledger from Tobin and fails."),
                ("The tide turns", "Tobin hides the ledger in the boathouse; Mara watches."),
            ],
            "title": "The stolen ledger",
            "kind": "beat",
        },
    },
    "scene-intent": {
        "a plan card and its prose": {
            "card": "Title: The ledger at dusk\nWhat happens: Mara tries to steal the ledger "
            "from Tobin before the tide turns.\nSeen through: Mara Voss\nWhere: Saltmere",
            "content": DRAFT,
        },
        "a plan card alone": {
            "card": "Title: The ledger at dusk\nWhat happens: Mara tries to steal the ledger.",
            "content": "",
        },
    },
    "bridge-scenes": {
        "a scene that needs someone somewhere first": {
            "story": "Entries (id: name (type)):\n- ent_mara: Mara Voss (Character)\n"
            "- ent_saltmere: Saltmere (Location)\n\nWhat is recorded about Mara Voss:\n"
            "- Mara Voss is located at Kestle\n\nThe scenes around the gap, in reading "
            "order:\n- “The quarrel”: Mara and Tobin fall out.\n- “The ledger at dusk”  "
            "← the scene that needs it",
            "need": "“The ledger at dusk” needs this first: Mara Voss must have this: is "
            "located at Saltmere.",
        },
    },
    "plan-corrections": {
        "an audit of two scenes": {
            "findings": "S1: “At the harbour”\n  N1: Mara is in Kestle at this time (about "
            "“Mara stood on the quay”)\n  C1: The scene names Lantern Harbor, which the story "
            "bible does not know.\nS2: “Walking home”\n  N2: The ledger is paid off here but "
            "never set up earlier.\n  P1: This scene reveals the secret before it is set up.",
        },
    },
    "legacy-character-questions": {
        "a character": {
            "character_name": "Mara Voss",
            "character_id": "character_mara",
            "bio": "A smuggler's daughter.",
            "context_str": "No existing relevant Knowledge Graph context found.",
        },
    },
    "legacy-graph-update": {
        "answers": {
            "character_name": "Mara Voss",
            "character_id": "character_mara",
            "chapter_number": 3,
            "answers_text": "She lives in Saltmere.",
            "context_str": "No existing relevant Knowledge Graph context found.",
            "node_types": ["Character", "Location"],
            "edge_types": ["LOCATED_AT"],
        },
    },
    "reply-as-json": {
        "after a system message": {
            "messages": [
                {"role": "system", "content": "You check things."},
                {"role": "user", "content": "Is it ready?"},
            ],
            "response_format": _Answer,
        },
        "with no system message": {
            "messages": [{"role": "user", "content": "Is it ready?"}],
            "response_format": _Answer,
        },
    },
}
