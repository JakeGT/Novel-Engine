# ADR-006: OpenCodeReview-inspired prose review harness

- Status: Proposed
- Date: 2026-10-05
- Owners: project owner, consistency-system implementer
- Related backlog: CON-007–CON-013

## Context

Long manuscripts exceed practical model context, while reviewing isolated passages misses distant continuity dependencies. Alibaba OpenCodeReview demonstrates a hybrid approach: deterministic selection/rule dispatch surrounds bounded review agents, groups receive isolated context, coverage and token guards provide fallbacks, findings are anchored, and an independent reflection stage removes unsupported comments.

Its implementation is specific to Git diffs and source files. Its semantic grouping also uses an LLM call, so it is not itself purely deterministic.

## Proposed decision

Adopt the architectural pattern but implement a Novel Engine-native harness:

- derive review units from structured prose/version and canon deltas
- build a deterministic dependency graph from entities, facts, time, POV, rules, and narrative threads
- create overlapping rule-specific bundles with token caps and guaranteed coverage
- use narrow read-only review agents only when requested/policy-enabled
- anchor findings to immutable prose versions and structured ranges
- independently falsify model findings against minimal cited evidence
- fall back to deterministic/single-unit bundles on grouping or provider failure

The detailed algorithm is in `plan/16-open-code-review-adaptation.md`.

## Alternatives considered

### One full-manuscript critic

Simple but expensive, difficult to ground, hard to parallelize, and eventually exceeds context limits.

### Independent per-scene critics

Cheap and parallel, but misses distant entity, thread, causal, secret, and timeline dependencies.

### Pure vector retrieval per finding

Useful for candidates but cannot guarantee rule or changed-unit coverage and is difficult to audit.

## Consequences

### Positive

- Predictable coverage and spend
- More relevant cross-scene evidence per prompt
- Parallelizable and incrementally cacheable
- Clear preview and fallback behavior
- Independent filtering reduces self-confirming hallucinations

### Negative

- Review-unit extraction and dependency tracking are substantial new infrastructure
- Overlapping bundles may duplicate work
- Structured prose anchoring and relocation are harder than code line anchoring
- Deterministic coverage is only as good as extraction/schema dependencies

### Follow-up work

- Add CON-013 grouping prototype and benchmark
- Define review-unit and bundle schemas
- Extend the reference world with labeled cross-scene defects
- Evaluate full-context, vector-only, and deterministic-bundle approaches

## Validation

Accept this ADR after the offline benchmark shows complete mandatory-rule coverage and a meaningful precision/cost improvement without unacceptable recall loss. Record results and thresholds in the ADR.
