# ADR-013: Declarative story invariants and enforcement classes

- Status: Accepted
- Date: 2026-10-08
- Owners: project owner
- Related: ADR-012, `13-consistency-system.md`,
  `18-author-rules-canon-queries-and-derived-views.md`

## Context

Novel Engine already requires permanent integrity checks for tenant isolation, references, schema,
branch/time semantics, review policy, and commit atomicity. Authors also need a way to express
project-specific continuity intent such as singular possession, a secret not being learned before a
reveal, or a dead character not acting in later events.

Treating all such rules as mandatory would make evolving a draft brittle, while treating every
rule as advisory would prevent authors from choosing strong continuity gates. Allowing executable
project code would create an unacceptable security and determinism boundary.

## Decision

Novel Engine supports versioned story invariants expressed through a safe declarative predicate
language over governed propositions, branch/time/narrative positions, cardinality, and declared
exceptions. Project content cannot supply executable code.

Enforcement is divided into two classes:

1. Built-in integrity invariants are permanently blocking. They cannot be disabled, overridden,
   waived, or suppressed.
2. Author-defined story invariants default to warnings. An authorized author may explicitly publish
   a version as blocking. Only deterministic evaluation over current, complete inputs may block a
   canonical commit; extraction-dependent or uncertain evaluation produces a finding instead.

An intentional violation of an author rule is resolved through a versioned rule change or an
explicit scoped override allowed by project policy. Both preserve actor, rationale, scope,
dependencies, and revision. A general finding suppression cannot bypass a blocking invariant.

Invariant example cases, including `should_reject` cases, are stored as test/evaluation fixtures and
run when a rule version is validated or published.

## Consequences

- Authors get CI-like manuscript checks without model cost or uploaded code.
- Drafting remains flexible because new rules do not unexpectedly block commits.
- Projects may deliberately strengthen selected continuity rules.
- The commit validator must distinguish permanent integrity checks, deterministic blocking story
  rules, and advisory/uncertain findings.
- Rule/version/dependency changes invalidate prior results, suppressions, and previews.
- The UI must make warning versus blocking behavior and any scoped override conspicuous.

## Validation

- A newly created story invariant emits warnings by default.
- Publishing a rule as blocking requires an explicit authorized action.
- A deterministic violation of an enabled blocking story invariant rejects the transaction without
  advancing the head.
- Missing or uncertain extraction evidence cannot reject a commit.
- No API, import, MCP tool, suppression, or project role can disable a built-in integrity invariant.
- Positive and `should_reject` examples execute deterministically against a fixed fixture.
