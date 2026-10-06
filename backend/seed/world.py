"""The deterministic reference world ("The Lantern Coast").

Every required Phase 0 fixture is represented here: two placeholder
workspaces/projects, two characters, two locations, an object and a resource, a
faction, a system/rule, a public fact, an author-only secret, a false character
belief, narrative ordering with a flashback, two branches with a conflicting
fact, possession and travel constraints, an unresolved thread with setup and
planned payoff, prose snippets with expected entity tags, and expected query
results.
"""

from __future__ import annotations

from backend.graph.schema import EdgeType, NodeType
from backend.seed.manifest import (
    FIXED_TIMESTAMP,
    SEED_VERSION,
    SeedDocument,
    SeedEdge,
    SeedManifest,
    SeedNode,
    SeedQuery,
)

CHARACTER = NodeType.CHARACTER.value
LOCATION = NodeType.LOCATION.value
OBJECT = NodeType.OBJECT.value
RESOURCE = NodeType.RESOURCE.value
FACTION = NodeType.FACTION.value
SYSTEM = NodeType.SYSTEM.value
LAW = NodeType.LAW.value
EVENT = NodeType.EVENT.value
FLASHBACK = NodeType.FLASHBACK.value
SECRET = NodeType.SECRET.value
BELIEF = NodeType.BELIEF.value
WORLD = NodeType.WORLD.value
CONSTRAINT = NodeType.CONSTRAINT.value
ARC = NodeType.ARC.value
BEAT = NodeType.BEAT.value
ACTION = NodeType.ACTION.value

_NODES: tuple[SeedNode, ...] = (
    # Placeholder workspaces and projects (no production auth in Phase 0).
    SeedNode(
        id="ws_aurora",
        type=WORLD,
        properties={
            "name": "Aurora Workspace",
            "kind": "workspace",
            "tenant_id": "tenant_aurora",
            "placeholder": True,
        },
    ),
    SeedNode(
        id="proj_tideglass",
        type=WORLD,
        properties={
            "name": "Tideglass",
            "kind": "project",
            "workspace_id": "ws_aurora",
            "project_id": "proj_tideglass",
            "placeholder": True,
        },
    ),
    SeedNode(
        id="ws_borealis",
        type=WORLD,
        properties={
            "name": "Borealis Workspace",
            "kind": "workspace",
            "tenant_id": "tenant_borealis",
            "placeholder": True,
        },
    ),
    SeedNode(
        id="proj_emberfall",
        type=WORLD,
        properties={
            "name": "Emberfall",
            "kind": "project",
            "workspace_id": "ws_borealis",
            "project_id": "proj_emberfall",
            "placeholder": True,
        },
    ),
    # Characters.
    SeedNode(
        id="char_mara",
        type=CHARACTER,
        properties={
            "name": "Mara Voss",
            "role": "lantern keeper",
            "project_id": "proj_tideglass",
        },
    ),
    SeedNode(
        id="char_ivo",
        type=CHARACTER,
        properties={
            "name": "Ivo Renn",
            "role": "glasswright",
            "project_id": "proj_tideglass",
        },
    ),
    # Locations.
    SeedNode(
        id="loc_lantern_harbor",
        type=LOCATION,
        properties={
            "name": "Lantern Harbor",
            "region": "The Lantern Coast",
            "project_id": "proj_tideglass",
        },
    ),
    SeedNode(
        id="loc_glasslight_tower",
        type=LOCATION,
        properties={
            "name": "Glasslight Tower",
            "region": "The Lantern Coast",
            "travel_days_from_harbor": 3,
            "project_id": "proj_tideglass",
        },
    ),
    # Object and resource.
    SeedNode(
        id="obj_tideglass_lens",
        type=OBJECT,
        properties={
            "name": "Tideglass Lens",
            "material": "ember glass",
            "origin": "forged",
            "project_id": "proj_tideglass",
        },
    ),
    SeedNode(
        id="res_ember_salt",
        type=RESOURCE,
        properties={
            "name": "Ember Salt",
            "scarcity": "high",
            "unit": "crate",
            "project_id": "proj_tideglass",
        },
    ),
    # Faction.
    SeedNode(
        id="fac_kindlers",
        type=FACTION,
        properties={
            "name": "The Kindlers",
            "stance": "hostile",
            "base": "loc_glasslight_tower",
            "project_id": "proj_tideglass",
        },
    ),
    # System and rule.
    SeedNode(
        id="sys_lantern_pact",
        type=SYSTEM,
        properties={
            "name": "Lantern Pact",
            "description": "Laws binding lantern keepers along the coast",
            "project_id": "proj_tideglass",
        },
    ),
    SeedNode(
        id="law_pact_rule",
        type=LAW,
        properties={
            "name": "Pact Rule 7",
            "rule": "No lens may be relit by its maker",
            "project_id": "proj_tideglass",
        },
    ),
    SeedNode(
        id="act_relight",
        type=ACTION,
        properties={
            "name": "Relight the lens",
            "project_id": "proj_tideglass",
        },
    ),
    # Facts: public fact, author-only secret, false character belief.
    SeedNode(
        id="evt_harbor_fire",
        type=EVENT,
        properties={
            "name": "Harbor Fire",
            "summary": "A fire destroyed the old lighthouse",
            "visibility": "public",
            "truth_value": True,
            "narrative_order": 3,
            "project_id": "proj_tideglass",
        },
    ),
    SeedNode(
        id="sec_lens_forged",
        type=SECRET,
        properties={
            "name": "Lens Forgery",
            "proposition": "Ivo forged the tideglass lens",
            "visibility": "author",
            "truth_value": True,
            "known_by": [],
            "project_id": "proj_tideglass",
        },
    ),
    SeedNode(
        id="bel_mara_lens_true",
        type=BELIEF,
        properties={
            "name": "The lens is genuine",
            "holder": "char_mara",
            "proposition": "The tideglass lens is genuine",
            "visibility": "character",
            "truth_value": False,
            "project_id": "proj_tideglass",
        },
    ),
    # Narrative ordering with a flashback.
    SeedNode(
        id="evt_pact_signing",
        type=EVENT,
        properties={
            "name": "Pact Signing",
            "narrative_order": 2,
            "truth_value": True,
            "project_id": "proj_tideglass",
        },
    ),
    SeedNode(
        id="evt_fire_flashback",
        type=FLASHBACK,
        properties={
            "name": "The Fire (flashback)",
            "narrative_order": 1,
            "depicts": "evt_harbor_fire",
            "is_flashback": True,
            "project_id": "proj_tideglass",
        },
    ),
    # Two branches with one intentional conflicting fact.
    SeedNode(
        id="branch_main",
        type=WORLD,
        properties={
            "name": "Main Branch",
            "kind": "branch",
            "project_id": "proj_tideglass",
        },
    ),
    SeedNode(
        id="branch_alt",
        type=WORLD,
        properties={
            "name": "Alternate Branch",
            "kind": "branch",
            "project_id": "proj_tideglass",
            "forks_from": "branch_main",
        },
    ),
    SeedNode(
        id="fact_pact_signed_main",
        type=EVENT,
        properties={
            "name": "Pact Signed",
            "outcome": "signed",
            "branch_id": "branch_main",
            "narrative_order": 2,
            "project_id": "proj_tideglass",
        },
    ),
    SeedNode(
        id="fact_pact_broken_alt",
        type=EVENT,
        properties={
            "name": "Pact Broken",
            "outcome": "broken",
            "branch_id": "branch_alt",
            "narrative_order": 2,
            "conflicts_with": "fact_pact_signed_main",
            "project_id": "proj_tideglass",
        },
    ),
    # Travel constraint.
    SeedNode(
        id="con_tower_closed",
        type=CONSTRAINT,
        properties={
            "name": "Tower Closed",
            "applies_to": "loc_glasslight_tower",
            "reason": "Kindler blockade",
            "project_id": "proj_tideglass",
        },
    ),
    # Unresolved thread, setup, and planned payoff.
    SeedNode(
        id="thr_lens_truth",
        type=ARC,
        properties={
            "name": "The Lens Truth",
            "status": "unresolved",
            "resolved": False,
            "project_id": "proj_tideglass",
        },
    ),
    SeedNode(
        id="setup_pact",
        type=BEAT,
        properties={
            "name": "Setup: the forged lens",
            "kind": "setup",
            "payoff": "payoff_lens",
            "project_id": "proj_tideglass",
        },
    ),
    SeedNode(
        id="payoff_lens",
        type=BEAT,
        properties={
            "name": "Payoff: the lens revealed",
            "kind": "payoff",
            "setup": "setup_pact",
            "planned": True,
            "project_id": "proj_tideglass",
        },
    ),
)

_EDGES: tuple[SeedEdge, ...] = (
    SeedEdge("proj_tideglass", "ws_aurora", EdgeType.PART_OF.value),
    SeedEdge("proj_emberfall", "ws_borealis", EdgeType.PART_OF.value),
    SeedEdge("char_mara", "char_ivo", EdgeType.RELATED_TO.value, {"kind": "siblings"}),
    SeedEdge("char_mara", "loc_lantern_harbor", EdgeType.LOCATED_AT.value),
    SeedEdge("char_ivo", "loc_glasslight_tower", EdgeType.LOCATED_AT.value),
    SeedEdge("loc_lantern_harbor", "proj_tideglass", EdgeType.PART_OF.value),
    SeedEdge("loc_glasslight_tower", "proj_tideglass", EdgeType.PART_OF.value),
    SeedEdge("char_mara", "obj_tideglass_lens", EdgeType.OWNS.value, {"since": "chapter_1"}),
    SeedEdge("char_ivo", "fac_kindlers", EdgeType.MEMBER_OF.value),
    SeedEdge("loc_glasslight_tower", "res_ember_salt", EdgeType.CONTAINS.value),
    SeedEdge("obj_tideglass_lens", "res_ember_salt", EdgeType.REQUIRES_RESOURCE.value),
    SeedEdge("law_pact_rule", "sys_lantern_pact", EdgeType.PART_OF.value),
    SeedEdge("act_relight", "law_pact_rule", EdgeType.FORBIDDEN_BY.value),
    SeedEdge("char_mara", "bel_mara_lens_true", EdgeType.BELIEVES.value),
    SeedEdge("char_ivo", "sec_lens_forged", EdgeType.KNOWS_SECRET.value),
    SeedEdge("sec_lens_forged", "obj_tideglass_lens", EdgeType.AFFECTS.value),
    SeedEdge("evt_harbor_fire", "loc_lantern_harbor", EdgeType.OCCURRED_AT.value),
    SeedEdge("evt_fire_flashback", "evt_harbor_fire", EdgeType.FOLLOWS.value),
    SeedEdge("evt_pact_signing", "evt_fire_flashback", EdgeType.FOLLOWS.value),
    SeedEdge("evt_harbor_fire", "evt_pact_signing", EdgeType.FOLLOWS.value),
    SeedEdge("fact_pact_signed_main", "branch_main", EdgeType.OCCURS_IN.value),
    SeedEdge("fact_pact_broken_alt", "branch_alt", EdgeType.OCCURS_IN.value),
    SeedEdge("branch_alt", "branch_main", EdgeType.PART_OF.value),
    SeedEdge("con_tower_closed", "loc_glasslight_tower", EdgeType.AFFECTS.value),
    SeedEdge("loc_glasslight_tower", "res_ember_salt", EdgeType.DEPENDS_ON.value),
    SeedEdge("thr_lens_truth", "obj_tideglass_lens", EdgeType.AFFECTS.value),
    SeedEdge("thr_lens_truth", "setup_pact", EdgeType.CONTAINS.value),
    SeedEdge("thr_lens_truth", "payoff_lens", EdgeType.CONTAINS.value),
    SeedEdge("setup_pact", "payoff_lens", EdgeType.CAUSES.value),
)

_DOCUMENTS: tuple[SeedDocument, ...] = (
    SeedDocument(
        id="prose_scene_1",
        text=(
            "Mara turned the tideglass lens toward the harbor fire, unaware that Ivo had forged it."
        ),
        metadata={
            "project_id": "proj_tideglass",
            "branch_id": "branch_main",
            "chapter": 1,
            "expected_entities": [
                "char_mara",
                "char_ivo",
                "obj_tideglass_lens",
                "evt_harbor_fire",
                "loc_lantern_harbor",
            ],
        },
    ),
    SeedDocument(
        id="prose_scene_2",
        text=(
            "In the Glasslight Tower, Ivo paid the Kindlers in ember salt to keep the "
            "pact rule buried."
        ),
        metadata={
            "project_id": "proj_tideglass",
            "branch_id": "branch_main",
            "chapter": 2,
            "expected_entities": [
                "char_ivo",
                "loc_glasslight_tower",
                "fac_kindlers",
                "res_ember_salt",
                "law_pact_rule",
            ],
        },
    ),
)

_QUERIES: tuple[SeedQuery, ...] = (
    SeedQuery(
        id="q_name_mara",
        description="Name search for Mara Voss returns the character",
        kind="name_search",
        argument="Mara Voss",
        expected_ids=("char_mara",),
    ),
    SeedQuery(
        id="q_type_character",
        description="All Character nodes",
        kind="node_type",
        argument=CHARACTER,
        expected_ids=("char_ivo", "char_mara"),
    ),
    SeedQuery(
        id="q_type_flashback",
        description="All Flashback nodes",
        kind="node_type",
        argument=FLASHBACK,
        expected_ids=("evt_fire_flashback",),
    ),
    SeedQuery(
        id="q_neighbors_mara",
        description="Nodes adjacent to Mara",
        kind="neighbors",
        argument="char_mara",
        expected_ids=(
            "bel_mara_lens_true",
            "char_ivo",
            "loc_lantern_harbor",
            "obj_tideglass_lens",
        ),
    ),
)

REFERENCE_WORLD = SeedManifest(
    version=SEED_VERSION,
    timestamp=FIXED_TIMESTAMP,
    nodes=_NODES,
    edges=_EDGES,
    documents=_DOCUMENTS,
    queries=_QUERIES,
)
