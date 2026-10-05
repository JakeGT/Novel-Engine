# ADR-001: Hosted SaaS and AI usage model

- Status: Accepted
- Date: 2026-10-05
- Owners: project owner
- Related backlog: SaaS-001–SaaS-010, OPS-002, OPS-004

## Context

Novel Engine is intended to be a product users can sign up for, not only a local authoring tool. AI inference has variable cost, and users may prefer platform-managed usage or their own provider account.

## Decision

Build Novel Engine as a hosted, multi-tenant subscription SaaS. Users can consume platform-managed AI credits or provide an OpenRouter API key. Project, account, usage, and billing isolation are foundational requirements.

Subscriptions grant product access and may include allowances. Additional AI usage can be purchased through a ledger-based credit system. Internal entries preserve exact provider cost and pricing-policy versions; the user-facing credit unit and price may abstract raw token/provider cost to support predictable packaging and margin.

Initial collaboration uses project sharing with owner/editor/viewer roles and optimistic concurrency. Real-time co-editing is deferred.

## Consequences

### Positive

- Centralized onboarding, upgrades, operations, and product delivery
- Flexible AI payment choice
- A path to team/shared projects

### Negative

- Authentication, authorization, tenant isolation, billing, abuse prevention, and privacy are required early
- The platform assumes custody of unpublished manuscripts and potentially provider keys
- Usage reconciliation and provider failures become financial correctness concerns

### Follow-up work

- Define roles and sharing depth
- Select subscription/payment provider and tax/refund policy
- Design double-entry credit ledger, reservations, reconciliation, and disputes
- Define encrypted BYOK custody and key rotation
- Add tenant-isolation and billing-invariant tests

## Validation

No private-beta launch until cross-tenant access tests, usage-ledger invariants, key-redaction tests, backup/restore, and account deletion pass.
