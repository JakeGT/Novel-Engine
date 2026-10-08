# ADR-003: PostgreSQL as the operational system of record

- Status: Accepted; canon-authority portion superseded by ADR-012
- Date: 2026-10-05
- Owners: project owner
- Related backlog: FND-005, CAN-008, SCN-001, IDN-001–IDN-006, SaaS-006

## Context

The invite-only hosted profile needs transactional external-identity mapping, workspace, invitation, membership, credential, job, journal, review, and idempotency data. Deferring PostgreSQL would create a costly migration from local-only assumptions.

## Decision

Use PostgreSQL from the foundation phase for identity references, workspaces, invitations, project metadata, memberships, provider credentials, jobs, outbox, mutation journal, reviews, and operational audit. Subscription and credit-ledger tables are not required unless a later commercial ADR reintroduces them.

ADR-012 supersedes the original Neo4j-authority decision: PostgreSQL now owns the immutable journal
and normalized/versioned accepted canon, while Neo4j is a rebuildable query projection. Exact
authored prose lives in versioned text/blob records referenced from PostgreSQL. Use pgvector
initially for derived vector data, behind a replaceable vector port.

Canonical commits are atomic PostgreSQL transactions. PostgreSQL outbox messages drive idempotent
Neo4j and vector projections with explicit lag/rebuild state.

## Consequences

- Local development requires PostgreSQL and Neo4j.
- Commit ordering, journal replay, projection lag, and rebuild are first-class domain behavior.
- Project/tenant identifiers exist in both stores and are verified at each adapter boundary.
- pgvector becomes a strong initial vector-store candidate but remains a separate decision behind the vector port.
- Prototype stores may be reset; production migrations must be forward-safe and tested.

## Validation

Failure-injection tests cover PostgreSQL transaction boundaries and outbox/projector delivery.
Replaying committed envelopes must recreate Neo4j without duplicate mutations or changed canon.
