# Consistency system

## Objective

Continuously identify continuity violations without sending the entire manuscript to an expensive model after every edit. The system should do the cheapest reliable work first, limit analysis to changed dependencies, and escalate ambiguous literary judgments to bounded model passes.

Consistency checking is a pipeline that produces evidence-backed findings. It is not an autonomous rewriting system.

The default automation boundary is deterministic: Tier 0/1 runs with each generation, while Tier 2/3 model calls require a user action unless the user explicitly enables a budgeted schedule. The UI recommends a compact check after an accepted scene and a deep check at chapter/act boundaries, showing estimated provider usage/cost and reasons.

The grouping/review harness takes architectural inspiration from Alibaba OpenCodeReview. The domain-specific mapping and proposed bundle algorithm are specified in `16-open-code-review-adaptation.md`.

## Inputs

- selected project, branch, world-time interval, and narrative position
- scene plan and required/intended state
- exact prose version or generation candidate
- context manifest used to produce the prose
- accepted canonical facts, events, constraints, and beliefs
- POV knowledge ledger, including false beliefs and explicitly exposed author-only secrets
- unresolved narrative threads and promises
- extracted but uncommitted mutation proposal
- consistency policy/profile and previous check fingerprints

## Outputs

A `ConsistencyRun` records scope, input revisions, profile, stages, duration, model usage, and findings. Each `ConsistencyFinding` contains:

- stable rule/category identifier
- severity: info, warning, error, or blocking
- confidence and detection tier
- prose evidence spans
- canonical evidence and source revisions
- affected entities, facts, events, scenes, or threads
- explanation and suggested correction options
- status: open, accepted, dismissed, fixed, or superseded
- author rationale when dismissed

Findings remain attached to the exact prose/canon revisions checked. Editing either marks affected findings stale.

## Tier 0 — Deterministic preflight

Run before generation. This creates a compact constraint packet and prevents asking a model to solve an already-invalid request.

Checks include:

- referenced entities exist in project and branch
- scene participants can be at the location/time or a travel gap is acknowledged
- required objects/resources are owned, available, and not concurrently elsewhere
- required story-state predicates are satisfied
- planned actions do not violate hard world laws without an explicit exception
- POV knowledge ledger distinguishes known, believed, unknown, and author-only facts
- branch head and timeline selection are current
- scene plan has required POV, location, purpose, and intended change fields

The generated constraint packet contains only relevant assertions and machine-readable rule IDs. It becomes part of the context manifest.

## Tier 1 — Deterministic postflight and incremental comparison

Run after generation or manual edits. Parse deterministic annotations and use structured extraction to create a provisional change/evidence set. Compare the prose and proposal against preflight state.

Checks include:

- names, aliases, pronouns, physical attributes, and stable identity details
- active location and impossible simultaneity
- chronology, age, duration, travel, and event ordering
- possession, resource consumption, injury/state continuity, and dead/retired entities
- relationship and goal state
- magic/technology rules, requirements, and exceptions
- explicit contradictions with active facts
- POV claims unsupported by knowledge/belief state
- accidental use of isolated author-only secrets
- unmet scene-plan requirements or unintended state changes
- duplicate, missing, or incompatible extracted mutations
- unresolved thread progression and premature payoff/reveal

Not every prose statement needs an LLM. Entity mentions, numbers, dates, declared state changes, known predicates, and branch/time constraints should be normalized into facts and checked with domain rules/queries.

## Tier 2 — Compact model-assisted critic

Run when requested by the user or by an explicitly enabled budgeted schedule. Recommend it after an accepted scene or small generation group. Supply a compact evidence packet rather than the whole project:

- scene prose or changed passages
- deterministic findings and unresolved ambiguities
- relevant canonical facts with provenance
- POV knowledge and selected author-only truth in separate sections
- nearby prose continuity excerpts
- character voice/state summaries
- scene-plan obligations

Ask task-specific critic passes rather than one vague “find problems” prompt. Suggested passes:

- POV knowledge and secret leakage
- character motivation/voice drift
- physical and causal continuity
- world-rule adherence
- narrative promise/setup/payoff consistency

The critic emits typed findings with quoted evidence locations and canonical references. Unsupported findings are discarded or labeled low confidence.

## Tier 3 — Deep audit

Run on demand, at chapter/act boundaries, before export, or on a configurable budget schedule. It may:

- summarize and compare a broader prose range
- recompute character/state/timeline ledgers
- inspect unresolved threads and long-distance setups/payoffs
- detect gradual voice, theme, or relationship drift
- compare branches and merge resolutions
- recommend correction plans spanning multiple scenes

Deep audits are asynchronous, cancellable jobs with a pre-run cost estimate. Results form a review workspace; corrections create new prose versions or mutation proposals.

## Consistency profiles

Suggested user-facing profiles:

| Profile | Every generation | Scene acceptance | Chapter/act |
| --- | --- | --- | --- |
| Default | Tier 0 + deterministic Tier 1 | recommend manual Tier 2 | recommend manual Tier 3 |
| Balanced | Tier 0 + Tier 1 | automatic selected Tier 2 passes within budget | recommend/schedule Tier 3 |
| Thorough | Tier 0 + Tier 1 + leakage Tier 2 | all Tier 2 passes | scheduled Tier 3 |
| Custom | author-configured checks and spend caps | author-configured | author-configured |

Blocking deterministic invariants cannot be disabled silently. Authors may override eligible findings with a recorded rationale.

## Incremental invalidation

Build a dependency index from facts/rules/entities/scene plans to prose versions and findings. Each check computes a fingerprint from:

- rule version
- affected source IDs and revisions
- prose content hash
- branch/timeline/POV
- policy/profile version
- model and prompt version where applicable

When prose or canon changes, enqueue only checks whose dependencies changed. Cache results for identical fingerprints. A deep audit can verify the incremental system and reveal missing dependencies.

## Previous-prose retrieval

Previous prose is always eligible when relevant, but semantic similarity alone is insufficient. Keep embeddings about content meaning; store changing importance signals as metadata and calculate a hybrid ranking score.

A starting score model is:

```text
score =
  semantic_similarity
  * branch_validity
  * visibility_gate
  + explicit_anchor_boost
  + author_importance_boost
  + continuity_impact_boost
  + narrative_proximity_boost
  + recency_decay
  + unresolved_thread_boost
  - redundancy_penalty
```

Weights are policy-versioned and evaluated empirically. `branch_validity` and `visibility_gate` are hard filters rather than soft preferences. Recency uses both narrative distance and world-time distance; flashbacks demonstrate why one clock is inadequate. Author-pinned passages bypass ordinary ranking but still obey workspace/project/branch access rules.

Recommended prose units:

- preceding passage for local flow
- current/previous scene full text when budget permits
- event blocks for specific continuity
- scene summaries for distant history
- voice/dialogue exemplars
- passages linked to active threads, secrets, promises, objects, or rules

Changing importance should not require regenerating the semantic vector. Re-embed only when the content, embedding representation, or embedding model changes.

## Rule engine design

Rules are versioned code/config with declared:

- required input types
- dependency extraction
- applicable scope
- cost class
- default severity
- override policy
- finding formatter
- remediation templates

Project schema extensions may attach declarative constraints, but cannot upload executable code. Safe constraint forms include cardinality, allowed endpoints, value ranges/enums, required resources, incompatibilities, implication rules, and lifecycle transitions.

## User correction workflow

For each finding, the author may:

- inspect both prose and canonical evidence
- edit the prose manually
- ask an agent for one or more patch proposals
- amend the scene plan or context
- propose a canon correction through normal mutation review
- mark intentional inconsistency/mystery with scope and rationale
- dismiss a false positive, feeding evaluation data

Agent corrections always produce diffs and never overwrite the accepted prose version directly.

## Cost and quality controls

- deterministic checks run synchronously when fast, otherwise as high-priority jobs
- group model checks by affected scenes and task, not by arbitrary token maximum
- estimate provider tokens/cost and enforce the author's limit before model-assisted checks
- skip unchanged evidence through fingerprints
- prefer summaries for distant context, but fetch exact prose before issuing a high-severity finding
- calibrate thresholds using labeled reference-world cases
- track precision, recall, false-positive dismissal, latency, and cost per finding

## Failure behavior

- Provider failure never blocks deterministic findings.
- Extraction uncertainty produces a review request, not invented canon.
- Stale runs cannot block or modify newer prose without revalidation.
- Index lag is visible; critical checks fall back to canonical queries and direct source reads.
- A critic response without verifiable evidence cannot produce a blocking finding.
