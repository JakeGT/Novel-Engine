# ADR-012: PostgreSQL canonical state and replayable Neo4j projection

- Status: Accepted
- Date: 2026-10-08
- Owners: project owner
- Supersedes: ADR-011; the Neo4j-authority portion of ADR-003
- Related backlog: CAN-004–CAN-010, TMP-001–TMP-005, EXT-003–EXT-006

## Context

The first Phase 2 implementation made PostgreSQL authoritative for mutation workflow and branch
coordination while making Neo4j authoritative for accepted story queries. A cross-store saga joined
the two. The PostgreSQL commit envelope already contains the complete, canonically serialized
operation sequence, so Neo4j can in principle be reconstructed by replaying committed envelopes.

The implementation also exposed a correctness problem with split authority: Neo4j applies updates
in place before PostgreSQL finalizes the branch head, while ordinary graph reads are not inherently
bounded by that finalized head. Preventing premature visibility would require pervasive versioned
graph reads and continuing reconciliation complexity.

The dominant future queries combine branch ancestry, system revision, world-time intervals,
cardinality, and POV visibility. These are at least as naturally relational/temporal as graphical.
Neo4j may still earn its operational cost for bounded traversal, causal paths, neighborhood
exploration, and context assembly, but it should not be required to recover or accept canon.

Prototype data is disposable and no production deployment exists, so this is the least expensive
point at which to correct the authority boundary.

## Decision

### Canonical authority

PostgreSQL owns both:

- the immutable, ordered commit journal; and
- normalized, versioned canonical state for entities, facts, beliefs, events, provenance, branch
  effects, and later narrative records.

The journal is the replay/recovery source. Normalized tables are updated in the same transaction so
ordinary canonical reads do not need to fold the entire journal. They must remain derivable and
verifiable from committed envelopes plus schema versions.

Neo4j is a rebuildable, disposable query projection. It is never part of the canonical commit
success condition. pgvector, search documents, summaries, and other indexes are projections under
the same rule.

### Canonical commit

One PostgreSQL transaction must:

1. authorize the actor and resolve the exact project/branch/schema scope;
2. reserve or safely rebase from the expected branch head;
3. run the authoritative mutation validator against state visible at that head;
4. verify required review decisions and protected-action policy;
5. append the immutable envelope and decision summary;
6. apply operations to normalized, versioned canon tables;
7. advance the branch head and append the canonical revision; and
8. enqueue idempotent projection/outbox messages.

Only that transaction establishes commit success. A Neo4j or vector outage cannot make accepted
PostgreSQL canon ambiguous or roll it back.

### Projection and replay

A projector consumes committed envelopes in branch/revision order and applies them idempotently to
Neo4j. Each project/branch projection publishes a durable watermark containing its highest applied
revision, commit ID, envelope checksum, projection schema version, and status.

Graph-backed responses expose or internally verify that watermark. A caller requiring current
state may wait within a bounded timeout, fall back to PostgreSQL, or receive an explicit projection
lag response; it must not be told stale graph state is current.

The system provides commands to:

- rebuild a project or the entire Neo4j projection from genesis;
- resume from a verified watermark;
- compare projection checksums/representative queries with PostgreSQL canon; and
- discard and recreate a corrupt projection without changing accepted canon.

Projection checksum disagreement stops that projection and requires rebuild or operator review. It
does not corrupt PostgreSQL canon.

### Query-store evaluation gate

Before Phase 3 fixes the branch/time/POV query design, implement the same representative query in
PostgreSQL and Neo4j:

> Resolve facts active for branch B and its ancestors, at world-time T, visible to POV P, with
> provenance and source revision.

Compare correctness, implementation complexity, explainability, p50/p95 latency, rebuild cost, and
operational burden on the reference corpus and a scaled synthetic corpus. Neo4j remains optional
unless this evidence justifies keeping it in the supported deployment.

### Decisions retained from ADR-011

ADR-011's other product boundaries remain accepted:

- Phase 2 executes one root branch and system revisions; fork/merge and world/narrative-time
  evaluation remain Phase 3.
- Low-impact author changes use the same journal with recorded author approval. Model, import,
  bulk, protected, and high-impact changes require a distinct human decision.
- Phase 2 media work remains limited to identity/version metadata, provenance, explicit links, and
  archive behavior.
- Generic graph mutation APIs remain local-development/admin-only and cannot create accepted canon.

## Alternatives considered

### Keep the ADR-011 split-authority saga

This preserves the current code but requires recovery and read-visibility coordination forever. It
also makes canon acceptance depend on a store that is already reproducible from the journal.

### PostgreSQL journal with Neo4j as the only read model

This makes replay possible but still leaves ordinary product reads unavailable or stale during a
projection outage. Normalized PostgreSQL canon provides a strongly consistent baseline and makes
Neo4j genuinely optional.

### Neo4j as the sole journal and canon store

This would simplify graph-local writes but separates canon from the existing transactional review,
identity, idempotency, outbox, and operational control plane. It is a poor fit for the expected
temporal/branch workload and removes the simple relational recovery boundary.

## Consequences

### Positive

- Canon acceptance becomes one transactional operation with no distributed success ambiguity.
- Neo4j can be dropped, rebuilt, upgraded, or tuned without migrating accepted canon.
- Branch/time/POV and record-level conflict rules can use relational constraints and indexes.
- Projection lag and failure become observable degradation rather than a locked authoring branch.
- Export, restore, checksum verification, and clean-room recovery have one canonical source.

### Negative

- Existing Phase 2 writer/query code is transitional and must be refactored before more canon
  features build on it.
- PostgreSQL needs normalized/versioned canon tables in addition to the append-only journal.
- Some data is duplicated across PostgreSQL and Neo4j by design.
- Graph-backed reads require watermark/lag handling and query-contract tests.
- A projector and full replay/rebuild path become required if Neo4j remains supported.

### Follow-up work

- Reopen Phase 2 storage and commit workstreams; do not begin Phase 2H/2I on split authority.
- Replace `CanonWriterPort` as the canonical write boundary with a PostgreSQL canon repository/unit
  of work and a separate `CanonProjectionPort`.
- Add a forward migration for normalized/versioned canon and projection watermarks. Prototype rows
  may be reset or replayed rather than preserved through a compatibility bridge.
- Move validation and review-policy enforcement into the canonical PostgreSQL commit transaction.
- Implement projection consumption, lag health, replay, verification, and rebuild tooling.
- Complete the branch-overlay and `WorldTime` design before final normalized fact/event tables are
  declared stable.

## Validation

- Killing Neo4j before, during, or after a commit does not fail or ambiguously accept PostgreSQL
  canon.
- Replaying all envelopes into an empty Neo4j produces the same projection checksum and
  representative query results.
- Duplicate outbox delivery and projector restart do not duplicate nodes or relationships.
- Product reads either use canonical PostgreSQL state or prove the Neo4j watermark satisfies the
  requested revision.
- A corrupt/mismatched projection is quarantined and rebuildable without canon mutation.
- The Phase 3 representative query report records both-store correctness and cost before Neo4j is
  retained as a required dependency.
