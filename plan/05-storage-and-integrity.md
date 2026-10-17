# Storage and integrity

## Storage responsibilities

### PostgreSQL: canonical journal, normalized state, operations, and audit

PostgreSQL owns projects, branches, immutable commit envelopes, normalized/versioned entities,
facts, beliefs, events, later narrative records, provenance, schema versions, decisions, jobs,
idempotency, outbox, consistency runs/findings, identity mapping, credentials, and operational
audit. Canonical commits update the journal, normalized state, branch head, revision, and outbox in
one transaction.

PostgreSQL also owns versioned authored policy/planning records: story invariants, scene contracts,
reader disclosures, narrated assertions, alias bindings/resolution examples, canon tags, finding
suppressions, and their provenance. Their existence does not make their propositions objective
canon. Purpose-built temporal/dependency views are derived projections with explicit watermarks.

The journal is the replay/recovery source. Normalized canon is the strongly consistent read model
and must be verifiable from the committed journal plus immutable schema versions.

### Neo4j: rebuildable graph projection

Project committed envelopes into Neo4j for bounded traversal, causal paths, relationship
exploration, and context assembly. Record an applied revision/checksum watermark per project/branch.
Neo4j can be deleted and rebuilt by journal replay and is never required for canonical commit
success.

### Object store: authored prose and media

Store exact structured scene versions, rendered text projections, immutable media originals,
derived variants, imports, exports, and potentially large prompt artefacts outside graph
properties. Hosted deployments use object storage with opaque tenant-scoped content-addressed keys
and PostgreSQL metadata; local development uses a compatible filesystem adapter. Client-visible
asset IDs are not object keys.

### Vector index: derived retrieval data

Use PostgreSQL with pgvector for the first implementation, behind replaceable text/multimodal
vector ports. Every entry points back to an exact prose or media segment/version and records its
embedding generation. The complete index can be deleted and rebuilt; incompatible generations are
not queried together.

### Media lifecycle metadata

PostgreSQL owns media assets, immutable versions, technical metadata, variants, segments, explicit
links, processing state, derived observations, retention, and deletion. Neo4j may project reviewed
semantic links for traversal but does not own binary lifecycle. See `17-multimodal-context.md`.

## Canonical write protocol

1. Begin with project, branch, base/head revision, record/dependency preconditions, and idempotency
   key.
2. In one PostgreSQL transaction, authorize, safely rebase or reserve the head, and run the
   authoritative schema/lifecycle/cardinality/review validator.
3. Append the immutable canonical envelope and decision summary.
4. Apply the operations to normalized, versioned PostgreSQL canon.
5. Advance the branch head, append the canonical revision, and enqueue idempotent projection
   messages.
6. Commit and report canonical success.
7. Project the envelope asynchronously into Neo4j/vector/search, updating visible watermarks.

Projection failure creates observable lag, not ambiguous canon. Rebuild or resume a projection by
replaying committed envelopes in revision order.

Before applying a proposed envelope, the transaction evaluates permanently blocking built-in
integrity invariants and any author story invariant version explicitly configured as blocking.
Author invariants default to warnings. Only current, deterministic, complete evaluation may reject
a commit; extraction-dependent uncertainty is persisted as a finding outside the integrity gate.

## Graph representation

Recommended Neo4j projection pattern:

- stable anchor nodes for identity
- explicit Fact/Belief/Event nodes where traversal adds value
- projected relationships for frequent traversal
- project ID on every node and relationship projection
- branch/revision metadata on temporal records
- uniqueness constraints scoped appropriately

Avoid arbitrary property bags as the only domain model. Validate properties in the canonical
PostgreSQL transaction before any projection message exists; projector validation protects against
corrupt delivery but does not establish canon.

## Migrations

Create an ordered migration system before relying on user data. Each migration includes:

- version and description
- forward operation
- preconditions
- verification query
- compatibility note
- backup requirement
- rollback or roll-forward strategy

Migrations cover Neo4j constraints/indexes, core schema, custom schema formats, vector metadata, prompt contracts, and exports. Application startup may verify versions but should not perform risky migrations implicitly.

Embedding dimension/model changes create a new generation and rebuild path rather than altering
active rows in place. The current `vector(384)` MiniLM prototype requires an explicit migration
before a 512- or 768-dimensional multimodal generation can become active.

## Concurrency and idempotency

- Branch heads provide total commit order. Record/dependency preconditions permit safe automatic
  rebase when intervening commits are disjoint.
- Mutation-set commits are idempotent.
- Job creation accepts a client idempotency key.
- Provider attempts, usage recording, cancellation, and output acceptance are idempotent by job/attempt.
- Derived workers record source revision and content hash.
- Media processing stages key idempotency by asset-version checksum plus processor configuration.
- Duplicate model callbacks, outbox delivery, or projector retries must not duplicate facts or
  projection edges.
- UI conflict responses include current head and a rebase/review path.

## Query history, tags, and compensation

Canonical query repositories expose state/history at a revision and provenance through operation
decisions and source evidence. Immutable tags point to a branch/revision. Undo constructs a
compensating mutation set against current state and uses the normal impact, validation, review, and
commit flow; no committed envelope is deleted or edited.

## Derived read models

Outbox consumers may build location, possession, relationship, timeline, reader-disclosure,
dependency/impact, and voice-profile views. Each view records workspace/project, branch, source
revision/checksum, projector version, last error, and rebuild status. A reader sees stale state only
with an explicit lag marker; critical checks fall back to canonical PostgreSQL queries. Replaying
the journal and versioned authored records must reproduce the view.

## Backup, restore, and export

Release one needs a user-owned export containing:

- manifest and format version
- project settings and schemas
- entities, facts, beliefs, events, branches, and revisions
- story plans and scene versions
- accepted mutation history and provenance
- original authored assets permitted for export
- media links, reviewed observations, source/licence/attribution, and branch/time/visibility metadata

Embeddings are optional because they are derived. A restore test must create a clean environment, import the export, rebuild indexes, and compare canonical checksums and representative queries.

As built (`backend/services/canon_archive.py`, format v4, 2026-10-17): the archive also carries
every what-if (the version it started from, its own saved changes and named versions, which text
holds each of its scenes, what was read from them and the findings dismissed there), the main
story's readings and dismissed findings, and the conversations with the assistant. A what-if is
restored by starting it again from the main story at that version and replaying its own changes,
and its history is checked against the archive's checksums. Drafts waiting to be kept or discarded,
unsaved proposals, jobs and usage records are left out, and the archive says so in its `left_out`
list. Version 3 archives (main story only) still restore.

## Data lifecycle

- Retire canonical entities rather than deleting referenced history.
- Allow permanent deletion of an entire project through an explicit destructive workflow.
- Let users delete uncommitted drafts/jobs independently.
- Configure retention for raw model requests/responses because they may contain manuscript text.
- Remove or cryptographically destroy stored BYOK credentials when revoked/account-deleted, subject to required audit retention that never includes the raw key.
- Redact secrets from logs and diagnostics.
- Deleting a media asset covers originals, variants, segments, vectors, caches, and remote-provider uploads where supported, while preserving only an authorized audit tombstone.

## Phase 2 corrections (done)

The corrections listed here before 2026-10-10 were completed in Phase 2: normalized and versioned
PostgreSQL canon with one commit unit of work, commit-time validation, the Neo4j projection as an
idempotent consumer with a visible watermark, journal replay and equivalence checks, history and
provenance queries, tags, and compensating reverts. Evidence: `phase-2-evidence.md`.

Still open from that list: reserving serialized identities for story invariants, scene contracts,
disclosures, narrated assertions, aliases, suppressions, and dependency edges as each is built;
the `WorldTime` value object (Phase 3); and the representative branch/time/POV query prototype in
PostgreSQL and Neo4j that decides whether Neo4j remains required infrastructure (an owner
decision, see `audit-2026-10-10.md`).

## Revision 2026-10-10: storage for the assistant

- **Conversations.** A description, the questions asked, the answers, and the proposal they
  produced are stored together, workspace- and project-scoped, immutable once the proposal is
  decided, and cited as evidence by the operations they produced (Phase 2.5; today only an
  evidence reference exists).
- **Plan records.** Story, arc, beat, scene plan, and thread records live in canon tables under
  the journal, flagged as intent (Phase 2.6).
- **Derived, not stored:** the to-fill-in list, open plan items, and link suggestions are computed
  from canon and the derived index, and can be rebuilt.
