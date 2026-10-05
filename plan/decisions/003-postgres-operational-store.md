# ADR-003: PostgreSQL as the operational system of record

- Status: Accepted
- Date: 2026-10-05
- Owners: project owner
- Related backlog: FND-005, CAN-008, SCN-001, SaaS-001–SaaS-010

## Context

The hosted product needs transactional account, subscription, credit, job, journal, review, and idempotency data. Deferring PostgreSQL would create a costly migration from local-only assumptions.

## Decision

Use PostgreSQL from the foundation phase for identity references, tenancy, project metadata, memberships, subscription state, credit ledger, provider credentials, jobs, outbox, mutation journal, reviews, and operational audit.

Neo4j remains the authoritative query model for accepted story canon and graph relationships. Exact authored prose lives in versioned text/blob records referenced from PostgreSQL. Use pgvector initially for derived vector data, behind a replaceable vector port.

Cross-store operations use a PostgreSQL journal/outbox and idempotent Neo4j application with explicit recovery states rather than pretending to have a distributed transaction.

## Consequences

- Local development requires PostgreSQL and Neo4j.
- Commit ordering, recovery, and reconciliation are first-class domain behavior.
- Project/tenant identifiers exist in both stores and are verified at each adapter boundary.
- pgvector becomes a strong initial vector-store candidate but remains a separate decision behind the vector port.
- Prototype stores may be reset; production migrations must be forward-safe and tested.

## Validation

Failure-injection tests cover crashes before and after journal append, Neo4j apply, head advance, and outbox publication. Reconciliation must converge without duplicate mutations or charges.
