# ADR-011: Canon authority, commit saga, and Phase 2 authoring boundary

- Status: Superseded by ADR-012
- Date: 2026-10-08
- Owners: project owner
- Related backlog: CAN-002–CAN-010, EXT-003–EXT-006, AST-001, AST-004, AST-008

## Context

> ADR-012 replaces the split PostgreSQL/Neo4j authority and cross-store commit saga. The Phase 2
> branch/time boundary, author-review policy, media boundary, and generic-graph restrictions below
> remain accepted because ADR-012 carries them forward explicitly.

Phase 2 introduces durable entities, facts, beliefs, events, schema versions, revisions,
provenance, and reviewed media links. PostgreSQL and Neo4j cannot participate in one ordinary
transaction, but a canonical write must not be reported as successful while their state is
ambiguous. The system also needs useful authoring ergonomics without permitting model, import,
bulk, or destructive changes to bypass human review.

The existing architecture gives PostgreSQL responsibility for operational state and Neo4j
responsibility for canonical story queries. This decision defines their exact authority, the
recoverable boundary between them, and the Phase 2 scope needed before later branch/time behavior.

## Decision

### Phase 2 branch and time boundary

Every project has exactly one root branch and a genesis system revision. Phase 2 implements
monotonic system revisions and optimistic branch-head concurrency. It stores reserved branch/time
fields where required for forward compatibility but does not implement forks, inherited overlays,
merges, world-time evaluation, narrative-order evaluation, or POV knowledge filtering. Those are
Phase 3 behaviors and must not be implied by Phase 2 APIs.

### Authority by datum

| Datum | Authority |
| --- | --- |
| Users, sessions, workspaces, projects, membership, and security audit | PostgreSQL |
| Project schema definitions and immutable schema versions | PostgreSQL |
| Mutation workflow, operation decisions, idempotency, commit envelopes, attempts, branch-head reservation/finalization, and outbox | PostgreSQL |
| Accepted entity, fact, belief, event, provenance-link, and canon-history query state | Neo4j, bounded by the committed PostgreSQL branch head |
| Immutable media bytes | Object storage, addressed through PostgreSQL lifecycle metadata |
| Media identity/version lifecycle and source/licence metadata | PostgreSQL |
| Reviewed semantic asset links | Revisioned canon in Neo4j, with lifecycle references checked against PostgreSQL |
| Search, graph shortcuts, and vector representations declared as projections | Rebuildable derived state |

The immutable PostgreSQL commit envelope is the recovery and idempotency record. Neo4j is the
accepted story-query state. Neither store independently establishes externally visible commit
success: a read is bounded by the finalized PostgreSQL head, and a write succeeds only after the
Neo4j application marker and PostgreSQL finalization agree on commit ID, revision, and checksum.

### Commit protocol

Mutation-set workflow status and commit-attempt recovery state are separate. Review states remain
human/product concepts; recovery states describe cross-store progress.

Recommended mutation-set flow:

```text
draft -> validating -> needs_review -> approved -> committing -> committed
                    \-> rejected                    \-> recovery_required
```

An eligible author-direct change records an explicit author approval and moves from validated to
approved without a second confirmation request. It does not skip the mutation set or journal.

Commit-attempt flow:

```text
prepared -> graph_applying -> graph_applied -> committed
    |             |                |
    +-------------+----------------+-> recovery_required -> committed
                                                \-> integrity_blocked
```

The application protocol is:

1. Authorize and validate the complete ordered mutation set against the exact project, root branch,
   schema version, expected committed head, and current permissions.
2. In one PostgreSQL transaction, reserve the branch from the expected head, allocate the next
   revision, and append an immutable prepared envelope with canonical serialization/checksum.
3. Apply the envelope in one Neo4j transaction. Write immutable/version-bounded canon state plus an
   application marker containing commit ID, revision, and checksum. Reapplying the same tuple is a
   no-op; the same ID with another checksum is an integrity error.
4. In one PostgreSQL transaction, verify the reservation, finalize the branch head and envelope,
   and append idempotent outbox messages.
5. Return success only after step 4. Derived-index delivery may lag and does not roll canon back.

While a commit is reserved, a second writer receives a stable conflict/in-progress response rather
than allocating another revision. Canon queries resolve the finalized PostgreSQL head first and
only expose Neo4j state visible at or below that revision. Consequently, a crash after graph
application but before finalization does not expose uncommitted state.

A reconciler inspects the PostgreSQL attempt, Neo4j application marker, revision, and checksum. It
may retry an unapplied envelope, finalize an exactly matching graph application, release a
reservation proven never to have applied, or stop at `integrity_blocked`. It must not guess through
a checksum disagreement or silently delete applied canon.

### Author and agent review boundary

Ordinary low-impact author-originated edits may commit after the author has seen the validation and
diff. They still create an author-approved mutation set, journal envelope, revision, provenance,
audit event, and outbox records.

Explicit human approval is mandatory for:

- model- or agent-originated canon proposals
- imports and bulk changes
- schema publication or migration
- referenced-entity retirement and other high-impact lifecycle changes
- conflict or merge resolution
- destructive project/archive operations
- any operation classified as protected by a later policy

The system may add stricter user-selected autonomy settings later, but no setting may weaken these
minimum protected actions without a new accepted decision.

### Media boundary

Phase 2 implements media identity, immutable version metadata, authority, provenance,
source/licence/attribution, lifecycle, explicit reviewed links, and export/import semantics. It does
not expose hosted upload/finalize, scanning, transcoding, captions, transcripts, segmentation,
multimodal embeddings, similarity retrieval, or provider delivery. Existing object bytes may enter
through deterministic seed, verified archive import, or an operator-only registration path.

### Generic graph API

Product writes use the mutation service. Existing generic graph mutation endpoints may remain only
behind an explicit local-development/admin capability. Hosted mode disables them, and contract
tests prove they cannot create accepted canon or bypass the journal.

## Alternatives considered

### PostgreSQL as the sole canon store with Neo4j as a projection

This would simplify one transaction boundary and replay, but it reopens the accepted graph-source
architecture and would require duplicating graph semantics into a relational event/read model now.
The selected design keeps Neo4j authoritative for accepted story queries while making visibility
and recovery explicit.

### Neo4j-only journal and branch head

This would make graph writes atomic but leaves operational idempotency, review decisions, recovery,
and outbox delivery outside the existing PostgreSQL control plane. It also weakens durable
coordination with API/job state.

### Distributed two-phase commit

Neither selected adapter provides a practical shared transaction coordinator, and operating one
would add complexity without removing the need for idempotency and reconciliation. A documented
saga with immutable markers is more transparent and testable.

### Require a second approval click for every author edit

This is safe but needlessly burdens ordinary manual authoring. Recording the author as the explicit
approver preserves provenance and policy while keeping low-impact edits usable. Protected and
non-author changes still require a separate review action.

### Implement branch/time/media processing in Phase 2

This makes the first trustworthy-canon slice too broad and risks coupling unproven semantics to the
commit journal. The accepted boundary stores compatible identities now and adds those executable
behaviors in their planned later phases.

## Consequences

### Positive

- Every accepted write has a deterministic revision, checksum, provenance trail, and recovery path.
- A crash cannot silently expose a graph application beyond the finalized branch head.
- Direct authoring and reviewed agent/import flows share one invariant-preserving commit path.
- Phase 2 can deliver useful canon authoring without prematurely implementing Phase 3/4 systems.
- The local development graph surface can remain useful without weakening hosted invariants.

### Negative

- Canon reads must resolve and carry a committed head instead of querying unbounded mutable nodes.
- Neo4j records/projections need revision-bounded representation; in-place mutation is insufficient.
- A stuck branch reservation temporarily blocks new commits until reconciliation.
- The reconciler and cross-store fault matrix are mandatory before the feature is production-ready.
- PostgreSQL envelopes and Neo4j accepted records deliberately duplicate enough information for
  verification, increasing schema and operational complexity.

### Follow-up work

- Implement P2-01 through P2-06 in `phase-2-todo.md` before enabling product canon writes.
- Define exact low-impact/protected operation classifications as typed policy, not endpoint logic.
- Add migrations for root branches, reservations, envelopes, attempts, outbox, and Neo4j markers.
- Add operator reconciliation and verification commands before background automation.
- Revisit authority only through a replacement ADR if operational evidence favors an event-sourced
  PostgreSQL canon with Neo4j as a fully derived projection.

## Validation

- State-machine tests cover every legal/illegal mutation and attempt transition.
- Fault injection stops execution before/after each PostgreSQL/Neo4j boundary and proves that
  retries converge to one commit or an explicit `integrity_blocked` state.
- Concurrent writers from the same expected head produce one reservation and a stable conflict for
  the other writer.
- Reads never expose a graph-applied revision before PostgreSQL head finalization.
- Replaying the same commit is a no-op; replaying its ID with a different checksum is blocking.
- Hosted API contracts prove that generic graph writes are unavailable and protected actions require
  a separate human decision.
- Archive/restore reproduces committed heads, envelopes, canon checksum, and representative queries
  without including derived indexes.
