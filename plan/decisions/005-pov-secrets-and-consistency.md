# ADR-005: POV secrets and tiered consistency validation

- Status: Accepted
- Date: 2026-10-05
- Owners: project owner
- Related backlog: KNW-003, CON-001–CON-012

## Context

Dramatic irony and foreshadowing may require a prose model to know facts that a POV character does not. This increases accidental knowledge leakage. Full-manuscript model checks are expensive, while many continuity failures can be identified deterministically.

## Decision

Allow the author to include selected secrets as clearly isolated author-only context while supplying an explicit POV knowledge ledger. Run a separate POV leakage validation after generation.

Implement consistency as tiers:

1. deterministic preflight and postflight on every generation
2. retrieval-assisted incremental comparison over affected entities and prose
3. compact model critic for selected scene/group boundaries
4. deep chapter/act/project audit on demand or schedule

All findings cite evidence and create proposed corrections. They do not silently rewrite prose or canon.

Tier 0/1 runs for each generation. Paid/model-assisted Tier 2/3 runs are manual by default; the product recommends scene and chapter/act checks with cost estimates. Users may later opt into budgeted schedules.

## Consequences

- Context manifests label truth and POV accessibility independently.
- Generation prompts use separate author-truth and character-knowledge sections.
- Consistency state and dependency fingerprints allow unchanged checks to be reused.
- Users can trade cost, latency, and coverage through a consistency profile.
- Leakage evaluation becomes a release gate.

## Validation

The reference world includes unknown secrets, false beliefs, dramatic irony, temporal conflicts, and resource constraints. Each tier has measured detection, false-positive, latency, and cost targets.
