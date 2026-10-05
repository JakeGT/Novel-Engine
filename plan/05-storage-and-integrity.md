# Storage and integrity

## Storage responsibilities

### Neo4j: canonical graph and query projections

Store projects, branches, stable entities, facts, beliefs, events, story structures, provenance links, and review/commit references. Optimize projections for the queries the product actually runs.

### PostgreSQL: SaaS operations, durable audit, and recovery

Use PostgreSQL from the start for tenant/account references, project metadata, membership, subscriptions, entitlements, platform-credit ledger, encrypted provider credentials, jobs, review state, idempotency, outbox, consistency runs/findings, and ordered immutable commit envelopes. The mutation journal is the recovery boundary for canonical changes.

### Text/blob store: authored artefacts

Store exact structured scene versions, rendered text projections, imports, exports, and potentially large prompt artefacts outside graph properties. Hosted deployments use object storage with content-addressed keys and PostgreSQL metadata; local development uses a compatible filesystem adapter.

### Vector index: derived retrieval data

Use PostgreSQL with pgvector for the first SaaS implementation, behind a replaceable vector adapter. Every entry points back to a canonical source/version. The complete index can be deleted and rebuilt.

## Canonical write protocol

1. Begin with project, branch, expected head revision, and idempotency key.
2. Validate the mutation set and referenced schema version.
3. Append a pending PostgreSQL journal record and outbox intent.
4. Apply graph mutations in one Neo4j transaction.
5. Advance the branch head with optimistic concurrency.
6. Mark the PostgreSQL journal record committed with result IDs/checksum.
7. enqueue derived-index updates using an outbox record.

PostgreSQL and Neo4j cannot share an ordinary transaction, so use a documented saga with recoverable states, idempotent Neo4j application, and reconciliation. Never report success while the journal/graph state is ambiguous.

## Graph representation

Recommended pattern:

- stable anchor nodes for identity
- explicit Fact/Belief/Event nodes where validity, provenance, or review matters
- projected relationships for frequent traversal
- project ID on every node and relationship projection
- branch/revision metadata on temporal records
- uniqueness constraints scoped appropriately

Avoid arbitrary property bags as the only domain model. Validate properties against a schema version before Cypher generation.

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

## Concurrency and idempotency

- Branch heads use optimistic revision checks.
- Mutation-set commits are idempotent.
- Job creation accepts a client idempotency key.
- Credit reservation, provider completion, refund, and reconciliation are idempotent by job/attempt.
- Derived workers record source revision and content hash.
- Duplicate model callbacks or retries must not duplicate facts or edges.
- UI conflict responses include current head and a rebase/review path.

## Backup, restore, and export

Release one needs a user-owned export containing:

- manifest and format version
- project settings and schemas
- entities, facts, beliefs, events, branches, and revisions
- story plans and scene versions
- accepted mutation history and provenance
- original authored assets permitted for export

Embeddings are optional because they are derived. A restore test must create a clean environment, import the export, rebuild indexes, and compare canonical checksums and representative queries.

## Data lifecycle

- Retire canonical entities rather than deleting referenced history.
- Allow permanent deletion of an entire project through an explicit destructive workflow.
- Let users delete uncommitted drafts/jobs independently.
- Configure retention for raw model requests/responses because they may contain manuscript text.
- Remove or cryptographically destroy stored BYOK credentials when revoked/account-deleted, subject to required audit retention that never includes the raw key.
- Redact secrets from logs and diagnostics.

## Near-term repairs to the prototype

- Introduce repository interfaces and application-owned lifecycles for clients.
- Remove network/database activity from imports.
- standardize graph DTO serialization.
- Store edge temporal/provenance fields rather than dropping them.
- Add node retirement and scoped deletion behavior.
- Replace the process-local JSON schema registry with project-scoped durable schema records.
- Add migrations for constraints and the vector index.
- Isolate the current Chroma client behind the vector port so it can be replaced by the selected pgvector adapter without affecting retrieval workflows.
- Introduce PostgreSQL migrations, tenant context, journal/outbox, and seed data; current prototype databases may be reset.
