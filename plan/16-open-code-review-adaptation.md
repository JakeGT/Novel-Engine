# OpenCodeReview adaptation for prose consistency

- Status: Proposed; prototype before accepting as an ADR
- Research date: 2026-10-05
- Upstream: [Alibaba OpenCodeReview](https://github.com/alibaba/open-code-review)
- Primary reference: [OpenCodeReview architecture](https://github.com/alibaba/open-code-review/blob/main/pages/src/content/docs/en/architecture.md)
- Research paper: [Determinism over Non-Determinism for Cost-Effective Agent-Based Code Review](https://arxiv.org/abs/2608.09290)

## Conclusion

Use OpenCodeReview as an architectural inspiration, not as a direct library or fork. Its Go/Git/file assumptions are not reusable as the core of a prose system, but its deterministic envelope around uncertain agents maps unusually well to consistency review.

The key pattern is:

```text
deterministic change discovery
  -> deterministic selection and rule assignment
    -> bounded grouping with guaranteed coverage
      -> isolated parallel review agents with read-only tools
        -> anchored findings
          -> independent falsification/filtering
```

OpenCodeReview's semantic grouping is not fully deterministic: it uses a single LLM call over file metadata. Reliability comes from hard group-size/token caps, coverage checks, and fallback to single-file groups when grouping fails. Novel Engine can make initial grouping more deterministic because it already knows entity, fact, branch, timeline, POV, scene, and narrative-thread relationships.

## Transferable upstream ideas

From the upstream architecture:

- deterministic selection before model invocation
- rule sets selected from known metadata
- semantic grouping of related review units
- hard maximum group size and prompt/token budgets
- coverage fallback for unassigned units
- isolated parallel subtasks per group
- an optional planning phase only for sufficiently complex groups
- bounded tool loops and explicit completion
- effort levels controlling review rounds
- context compression with protected initial instructions and recent active rounds
- precise finding relocation/anchoring
- independent post-review reflection that tries to remove incorrect findings
- a free preview showing selection, grouping, exclusions, and warnings before spending tokens
- append-only session events and pipeline telemetry without attaching content

## Domain mapping

| OpenCodeReview | Novel Engine |
| --- | --- |
| Git range/commit/workspace diff | prose version delta, scene group, chapter range, or canon revision range |
| changed file | changed passage/scene plus proposed mutation operations |
| file metadata | scene, POV, location, time, entities, threads, size, change kind |
| file include/exclude rules | workspace/project/branch/visibility gates and consistency profile |
| language/path rule | entity/schema/world-law/POV/thread rule set |
| semantic file group | dependency-connected prose review bundle |
| source file/diff tools | read-only prose, canon, timeline, belief, event, and search tools |
| code comment | evidence-anchored consistency finding |
| line resolver | structured-document position/range and quote re-anchoring |
| reflection/filter pass | independent falsification of findings against minimal evidence |
| review effort | fast/balanced/thorough/custom consistency profile |
| session JSONL | PostgreSQL run/event log plus redacted telemetry |

## Prose review units

Do not treat chapters as indivisible “files.” Normalize changed material into `ReviewUnit` records:

- stable unit ID and source scene/version
- structured-document range and text hash
- change kind: added, edited, removed, moved, generated, or canon-derived
- POV, location, narrative position, and world-time interval
- mentioned and inferred entity IDs
- extracted fact/belief/event/mutation references
- active rule, secret, goal, and thread references
- estimated tokens
- visibility classification
- author importance and continuity-impact metadata

Units should follow semantic paragraph/event boundaries where possible. A single unit may participate in multiple check bundles because prose consistency dimensions overlap.

## Deterministic dependency graph

Construct a temporary review graph for the selected change set.

Vertices:

- changed prose units
- affected canonical facts/beliefs/events/states
- entities, locations, resources, rules, secrets, and threads
- scene-plan requirements and intended mutations

Edges:

- explicit mention or structured annotation
- extracted subject/predicate/object reference
- same character/location/object/thread
- temporal overlap or prerequisite order
- causal, possession, relationship, or knowledge dependency
- branch inheritance/merge conflict
- preceding/following prose adjacency
- shared rule or constraint

Hard gates remove anything outside workspace, project, selected branch ancestry, allowed visibility, or review scope.

## Deterministic bundle algorithm

1. Compute prose and canon deltas from immutable revisions.
2. Normalize changed passages into review units.
3. Expand only declared dependency edges, with policy-specific hop and result limits.
4. Assign applicable deterministic rule families to each unit.
5. Create initial bundle keys by review dimension and dependency anchor, for example:
   - `pov:character_id:secret_set`
   - `timeline:world_interval:location_id`
   - `state:character_id`
   - `resource:object_id`
   - `rule:magic_system_id:law_id`
   - `thread:thread_id`
6. Merge strongly overlapping keys when shared evidence reduces duplication.
7. Split oversized bundles using deterministic, connectivity-preserving bin packing under token/unit limits.
8. Create a coverage manifest proving every changed unit received all mandatory rule families.
9. Put uncovered or ambiguous units into conservative single-unit fallback bundles.
10. Optionally let a cheap model label, order, or suggest additional cross-bundle links; it cannot remove units or mandatory checks.

Unlike OpenCodeReview's one-group-per-file behavior, a prose unit may appear in several targeted bundles. This costs more, but avoids forcing timeline, knowledge, voice, and plot-thread checks into one noisy prompt.

## Example

A generated scene changes three passages:

- Mara arrives at the northern gate carrying the Sun Blade.
- She privately recalls that Iven killed the king.
- She promises to meet Iven before dawn.

The deterministic graph may create:

- `location/travel` bundle: Mara, prior location, northern gate, elapsed world time
- `possession/resource` bundle: Mara, Sun Blade, last known owner/location
- `POV/secret` bundle: Mara's beliefs, secret access, the author's selected truth, recalled claim
- `thread/promise` bundle: new promise, Iven, before-dawn deadline, existing unresolved threads
- `character/voice` bundle: changed dialogue/interiority plus selected voice exemplars

Each bundle receives only the changed passages and evidence needed for that check family. Results merge by evidence range and canonical dependency.

## Review-agent contract

Each model reviewer gets:

- one immutable bundle manifest
- a narrow task-specific prompt and output schema
- deterministic findings already produced for the bundle
- read-only tools with workspace/project/branch/visibility enforcement
- a strict token, tool-call, round, and provider-usage budget
- permission to report findings only against bundle prose ranges

Recommended read-only tools:

- `read_prose_range`
- `find_prose_mentions`
- `get_active_facts`
- `get_character_knowledge`
- `get_timeline_window`
- `get_entity_state_history`
- `get_world_rules`
- `get_scene_plan`
- `get_thread_history`
- `semantic_search_prose`

Tool results are structured and provenance-bearing. The reviewer cannot mutate prose, canon, schema, branch state, autonomy policy, credentials, or identity/membership state.

## Complexity-gated planning and effort

Skip the model entirely for deterministic-only runs. When a user requests a model check:

- small single-rule bundle: one direct review pass
- multi-unit or multi-rule bundle: one bounded plan/checklist followed by review
- high effort: additional review rounds with confirmed findings supplied, stopping when no new finding appears or the provider-usage budget is reached

The planning phase must not become a coverage ceiling. Mandatory checks come from the deterministic manifest, not from the model's plan.

## Finding anchoring

Source-code line numbers are unstable in rich text. Anchor each finding with:

- scene/version ID
- structured node/range identifiers where available
- start/end offsets in a deterministic plain-text projection
- exact short evidence quote and surrounding hash
- mentioned entity/fact/event IDs

On a newer compatible version, use deterministic quote/context matching to relocate the finding. If ambiguous, mark it stale/unanchored rather than guessing. A bounded model relocation pass may suggest candidates but cannot silently change the anchor.

## Independent falsification

Adapt OpenCodeReview's independent reflection pattern. The verifier sees:

- the candidate finding
- cited changed prose
- only the canonical evidence/rules the finding claims support it
- the relevant POV/branch/time scope

It does not see the reviewer's full exploratory conversation or unsupported retrieved context. Its task is falsification:

- Is the evidence actually present?
- Is the canonical claim active on this branch/time?
- Does the POV have or lack the asserted knowledge?
- Is there an explicit exception or intentional-inconsistency marker?
- Is the finding within review scope?

Hard deterministic contradictions survive without a model verifier. Model-originated high-severity findings require verifier support or author confirmation before becoming blocking.

## Preview and coverage report

Before a paid model run, show:

- changed units and excluded material with reasons
- dependency expansions and rule families
- proposed bundles and duplicate coverage
- deterministic findings already available
- estimated tokens/provider cost per bundle and total cap
- warnings for oversized, ambiguous, stale-index, or unanchored units
- selected effort/profile and expected passes

Users may exclude optional bundles or lower effort, but cannot unknowingly disable protected deterministic checks.

## Failure and fallback

- Grouping/model failure never loses coverage; fall back to deterministic bundles or single-unit review.
- One failed bundle does not discard successful bundle findings.
- Over-budget bundles split; they are not silently skipped.
- Missing vector service degrades to graph/direct-source retrieval.
- Missing model/credential still returns deterministic findings and the preview.
- Stale base revisions cause re-bundling before review or correction.
- Unsupported findings are filtered, not rewritten until they sound plausible.

## Differences from code review

- Prose has fewer formal semantics, so deterministic checks depend on explicit extraction and authored schema.
- Contradictions may be intentional mysteries, unreliable narration, metaphor, dream, or irony; findings need intent/override states.
- Text positions are less stable than code lines; anchoring needs versioned structured ranges and quote hashes.
- A continuity issue may span distant scenes rather than adjacent files.
- One passage may need multiple independent review dimensions.
- Recall/precision preferences vary by author and review stage; thresholds are user-configurable within protected rules.

## Prototype experiment

Before committing fully, implement an offline experiment against the seeded reference world:

1. Create 30–50 labeled prose changes with timeline, possession, secret, state, rule, voice, and thread issues.
2. Compare full-context critic, semantic-retrieval critic, and deterministic-bundle critic.
3. Measure bundle coverage, precision, recall, false-positive dismissal, tokens, latency, and cost.
4. Inject grouping failures, oversize groups, missing vector results, and stale revisions.
5. Confirm deterministic-only mode catches the hard-invariant subset at near-zero inference cost.
6. Accept the ADR only if bundling improves cost/precision without unacceptable recall loss.

## Reuse boundary

OpenCodeReview is Apache-2.0, but direct code reuse is not currently recommended. Novel Engine needs Python/PostgreSQL/Neo4j services, structured-document anchoring, tenant isolation, provider-usage controls, and graph-based overlapping bundles. Reuse the tested architecture pattern and cite upstream inspiration; implement domain-native components and preserve upstream attribution if any source code is later adapted.
