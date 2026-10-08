# Author rules, canon queries, and derived views

- Status: Accepted design direction
- Date: 2026-10-08
- Related: ADR-012, ADR-013, `03-domain-and-temporal-model.md`,
  `13-consistency-system.md`

## Objective

Make the immutable canon journal useful to authors as a reasoning system, not only as persistence.
Authors should be able to state continuity intent, ask why a proposition is believed, preview the
consequences of a change, and expose the same safe query capabilities to external assistants.

The design separates three kinds of state:

1. **Authoritative records** establish accepted canon, reviewed beliefs, authored narrative intent,
   and immutable history.
2. **Authored policy and planning records** describe invariants, scene contracts, disclosures,
   setups/payoffs, aliases, and suppressions. They are versioned and reviewed but are not objective
   story truth merely because they exist.
3. **Derived read models** calculate temporal location, possession, relationships, dependencies,
   voice fingerprints, irony, and exports. They carry a source-revision watermark and can be
   rebuilt by journal replay.

No derived view, extraction result, speech propagation rule, or external assistant writes canon
directly. Canon changes remain mutation-set proposals that pass the ordinary review and commit
boundary.

## Declarative story invariants

An author may define safe, declarative rules that are evaluated on every applicable canonical
commit and consistency run. Examples include singular possession, knowledge embargoes, and
post-event participation constraints.

An invariant is a versioned record containing:

- stable identity, name, description, owner, and project/branch scope
- a typed predicate AST using governed propositions, quantifiers, cardinality, temporal/narrative
  bounds, and declared exception forms
- severity and enforcement mode
- dependency declaration and rule-engine version
- effective and optional expiry positions
- positive examples and `should_reject` examples
- provenance, revision, and replacement/supersession history

Invariant expressions are data, never uploaded Python, SQL, Cypher, or arbitrary executable code.
They use the same proposition, `WorldTime`, `NarrativePosition`, entity-reference, and branch
semantics as canonical queries.

There are two enforcement classes:

- **Built-in integrity invariants** protect tenancy, authorization, references, lifecycle, schema,
  branch/time integrity, journal consistency, and commit atomicity. They are permanently blocking,
  cannot be disabled, waived, or suppressed, and execute inside the canonical transaction.
- **Author story invariants** default to `warning`. An authorized author may explicitly enable
  `blocking` for a version of the rule. A blocking story invariant may stop a commit only when all
  required inputs are deterministic, current, and complete. Extraction-dependent or uncertain
  evaluation produces a finding rather than a commit rejection.

An author may resolve an intentional violation by changing or disabling the authored rule, or by a
versioned, scoped override permitted by project policy. The action is explicit and audited; it does
not weaken built-in integrity invariants. Example cases become deterministic tests and labeled
evaluation fixtures.

## Canon query and explanation layer

Define one storage-neutral `CanonQuery` application boundary used by HTTP, the UI, consistency
rules, context assembly, exports, review agents, and MCP. It must support:

- entity/fact/belief/event state at project, branch, canonical revision, and world time
- reader- or character-visible state at a narrative position
- who knows or believes a proposition, with confidence and acquisition provenance
- record history and changes between two revisions
- dependency and downstream-impact queries
- fact/belief explanation from current state back through commit envelope, mutation set,
  operation-level decision, source scene/version, and exact evidence span

Queries return the effective branch/revision/time/visibility scope and projection watermark used.
Neo4j-backed implementations must prove that their watermark satisfies the request or fall back to
PostgreSQL/report lag.

### Tags

A canon tag is an immutable named pointer to `(project, branch, canonical_revision)`, optionally
carrying a description and provenance. Moving a label creates a replacement tag record; it never
changes the historical meaning of an existing tag. Branches may fork from tagged revisions.

### Revert

Undo never erases or rewrites history. Reverting a commit constructs a compensating mutation set
against current canonical state, reports intervening conflicts and downstream impact, and sends
the proposal through normal validation and review. Operations that cannot be safely compensated
require explicit author resolution.

## Reader knowledge and reveal planning

Reader knowledge is represented by a disclosure ledger keyed by branch and
`NarrativePosition`. A `Disclosure` records:

- the proposition presented to the reader
- source scene/version and exact evidence
- position and intended audience/edition
- disclosure mode: explicit, strongly implied, ambiguous, or recontextualized
- confidence, provenance, and supersession history

The ledger records what the manuscript exposed, not an assertion about what every reader inferred.
Explicit disclosures may be authored; extracted disclosures remain proposals until reviewed.

Joining disclosures with character beliefs produces an irony map: propositions available to the
reader but not a character, held by a character but withheld from the reader, or understood
differently by each. Setups, promises, foreshadowing, and payoffs remain narrative-planning records.
A dependency-aware Chekhov-debt view orders unresolved setups by reading distance and criticality.
Deterministic checks identify premature explicit reveals and payoffs without a declared setup;
ambiguous implication remains advisory or model-assisted.

## Narrated assertions, speech acts, and belief propagation

`NarratedAssertion` represents a proposition communicated by narration, dialogue, thought, text,
dream/vision, or reported speech. A speech act specializes it with speaker, addressed listeners,
known overhearers, location/event, and evidence. Its authored interpretation may be `sincere`,
`lie`, `mistaken`, `unreliable_narration`, or `figurative`; this classification may itself be
secret and visibility-scoped.

Deterministic policies can propose belief changes from reviewed events and assertions:

- explicitly perceiving participants may acquire an event belief
- addressed listeners may acquire a reported proposition weighted by directional,
  context-specific trust
- explicit overhearers may acquire it at lower confidence
- later correction, contradiction, or forgetting may revise/close the belief

Scene participation alone never proves perception or audibility. Propagation output is an ordinary
mutation set with evidence and policy version. It never writes beliefs directly.

## Checkable scene contracts and character arcs

A `SceneContract` binds a plan to branch, world-time range, and narrative position. It contains:

- hard preconditions that must be true before the scene
- soft expectations that produce warnings
- intended postconditions to compare with reviewed committed mutations
- declared participants, perceptions, outcomes, setups, disclosures, and payoffs

The readiness query lists satisfied and missing propositions without requiring a model. After a
scene commit, postflight reports which intended changes were established, contradicted, or remain
unproven.

Character arcs are authored paths across project-defined state dimensions and story beats. A
dimension declares an ordinal or qualitative scale, evidence expectations, and allowable
transitions. The engine can query whether a reviewed sequence moved toward the target; it must not
claim that a numeric score is objective literary truth.

## Alias, epithet, and entity-resolution memory

An `AliasBinding` maps normalized reference text to an entity within explicit branch, world-time,
narrative-position, POV/speaker, language, and source scope. It records confidence, provenance,
review status, and whether it is author-declared or inferred.

Review corrections create durable positive or negative `ResolutionExample` records with bounded
context. The shared resolver uses these records for editor suggestions, imports, structured
extraction, and deterministic checks. A correction improves future candidate ranking but never
silently changes historical extraction or establishes a global alias outside its scope.

## Impact preview and dependency index

Before commit, apply a mutation set to a transaction-local or in-memory canonical overlay and run
the same authoritative validator and deterministic rules used at commit. Join the changed record
set to the dependency index to report:

- affected scenes, plans, context manifests, exports, and read models
- newly failing or restored invariants and scene conditions
- findings and suppressions that become stale
- indexes/projections that must be refreshed

Preview is advisory and cannot replace transactional validation. Results distinguish definitely
affected, potentially affected, and not evaluated because coverage is unavailable.

## Derived read models

Idempotent outbox consumers may build purpose-specific PostgreSQL or Neo4j projections for:

- entity location and impossible-presence checks
- possession/resource custody over time
- relationship state over time
- world timeline and narrative-order indexes
- reader disclosures and irony
- dependency/impact traversal
- character/POV voice fingerprints

Every read model is tenant/project scoped, identifies its source branch and applied canonical
revision, exposes lag, and supports replay/rebuild. It is never accepted canon. New projections are
added only for measured query/check needs rather than mirroring every canonical table.

### Deterministic voice fingerprints

Compute language-aware dialogue and POV-narration statistics such as sentence-length distribution,
vocabulary, contraction rate, formality proxies, and recurring verbal patterns. Profiles require a
minimum sample size and may vary by character role and narrative period. Threshold drift produces
an advisory Tier 1 finding with exact comparison evidence; only flagged passages need a model
voice critic.

## Spoiler-safe story bible exports

Generate character sheets, gazetteers, timelines, recaps, and context blocks as of a selected
branch, canonical revision, narrative position, and viewpoint/audience (`author`, `reader`, or a
character). Each export stores a manifest of those coordinates, schema/policy versions, included
sources, and checksum.

Exports use the same visibility query boundary as generation. Beta-reader sharing is an
artifact-specific permission and must not implicitly grant full project viewer access. Automated
leakage fixtures prove that later reveals, author-only truth, inaccessible media metadata, and
hidden sincerity classifications are excluded.

## Read-only MCP transport

Expose the canon query and explanation operations through a scoped MCP server after their
application contracts stabilize. MCP is another transport, not a second domain layer.

- Authenticated server context establishes workspace/project permissions; model-supplied IDs never
  establish tenant scope.
- Read tools accept explicit branch/revision/time/POV coordinates and report them in results.
- Write-like tools may only create mutation-set, scene-patch, or correction proposals.
- No MCP tool directly commits canon, weakens review policy, or bypasses invariant enforcement.
- Hosted remote MCP and local/self-hosted MCP document their distinct authentication and network
  boundaries.

This lets authors use assistants they already fund while preserving Novel Engine's review and
continuity guarantees.

## Prompt-cache-friendly context rendering

Context assembly remains semantic and visibility-correct first, then renders deterministic stable
prefixes before volatile material:

1. system/task safety and policy
2. world laws, governed schema, and invariant summaries
3. stable style guidance and character/location bibles
4. relevant canonical state and visibility-filtered history
5. scene contract and current task
6. recent prose, retrieved evidence, and volatile user input

Canonical serialization, section identifiers, and policy versions make repeated prefixes
reproducible. Tenant/project/POV/secret boundaries apply before rendering; cache efficiency never
permits cross-scope content reuse. Usage records store provider-reported cache-read/write tokens or
hits where available and record `unsupported` otherwise rather than inferring success.

## Scoped suppressions and calibration

Dismissing an eligible story finding may create a versioned `FindingSuppression` scoped to a
scene, character, rule/invariant, branch, or narrative range with reason, author, expiry, and
dependency fingerprint. Suppressions are visible in future runs and become stale when the rule,
evidence, dependency, or scoped content changes.

Suppressions never apply to built-in integrity invariants, authorization/isolation failures,
schema/reference corruption, or journal/commit integrity. Dismissals and suppressions become
labeled evaluation examples. Per-project threshold changes are explicit policy versions with an
audit trail; the system never silently retrains or changes thresholds from author behavior.

## Delivery order

### Data-compatible foundations (before Phase 2 storage freezes)

- finalize immutable assertions plus branch-local effect/tombstone semantics
- finalize `WorldTime` and `NarrativePosition`
- reserve stable identities/serialization for invariant, scene-contract, narrated-assertion,
  disclosure, alias, tag, suppression, dependency, and projection-watermark records
- keep deferred record tables out of the Phase 2 migration unless the Phase 2 workflow needs them

### Phase 2 canonical query foundation

- authoritative PostgreSQL history/provenance queries
- revision comparison, immutable tags, and compensating revert proposals
- dependency/outbox projection framework and visible watermarks
- invariant evaluator interface, with built-in commit checks separated from author story rules

### Thin text-first workflow immediately after Phase 2

- manuscript/scene paste or Markdown import
- entity marks, alias candidate correction, and bounded extraction
- grouped mutation review and commit
- scene pre/postconditions and warning-default author invariants
- provenance/history inspection and basic read-only MCP access if authentication is ready

This slice validates extraction quality and review ergonomics before broad generation or
multimodal scope.

### Time, knowledge, and planning

- branch/time overlays, reader disclosure ledger, irony/reveal planning
- narrated assertions and reviewable belief propagation
- location, possession, relationship, and timeline read models
- spoiler-safe queries and exports

### Later consistency and generation increments

- dependency-complete impact preview and scoped suppressions
- voice fingerprints after sufficient reviewed prose exists
- prompt-cache rendering and provider cache-usage telemetry
- richer MCP proposal tools after the mutation review contract is stable

Cross-modal retrieval and media critics remain outside the text-first critical path.

## Acceptance principles

- The same query returns the same effective state through UI, HTTP, rules, exports, and MCP.
- Every explanation reaches immutable provenance and exact evidence or explicitly reports a gap.
- Built-in integrity invariants cannot be disabled or suppressed.
- New author invariants are warnings until blocking behavior is explicitly enabled.
- Uncertain/extraction-dependent checks cannot block canon commits.
- Reverts compensate through new reviewed history; they never erase history.
- Derived views expose freshness and rebuild from the journal.
- Reader/character exports pass spoiler and secret-leakage tests.
- External assistants can propose but cannot silently commit or weaken policy.
