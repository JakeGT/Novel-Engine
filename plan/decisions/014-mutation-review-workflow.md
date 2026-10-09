# ADR-014: Mutation review workflow and the high-impact gate

- Status: Proposed (implemented in Phase 2; awaiting owner acceptance with the Phase 2 evidence)
- Date: 2026-10-09
- Owners: project owner, implementer
- Related: ADR-012 (decision 1), ADR-013, `phase-2-todo.md` 2E

## Context

ADR-012 decision 1 says ordinary low-impact author edits may commit directly, while model, import,
bulk, destructive, schema-changing, and conflict-resolution proposals need a distinct human
approval. Before this decision the gate looked only at a mutation set's origin and at whether its
latest review decision was "approved"; a set's status was never updated, operations were immutable
once proposed, and a reviewer could only accept or reject a set as a whole. A destructive author
operation (retiring an entity) committed with no separate approval, and the author-direct approval
record was written after the commit transaction rather than inside it.

## Decision

1. **A mutation set is a state machine.** Legal transitions are defined once
   (`backend/domain/review.py`) and enforced by the journal and by database constraints.
   `committed` and `rejected` are terminal. Every transition appends an immutable row with actor,
   time, reason, and correlation/idempotency ids.
2. **Review happens per operation.** A reviewer accepts or rejects individual operations. Editing
   an operation appends a new immutable *operation revision*; the earlier revision and any decision
   made against it stay as evidence and no longer apply. The set that commits is the current
   revision of every operation whose latest applicable decision is not a rejection.
3. **Approval is a status, and it is perishable.** Editing an operation, changing an operation
   decision, or retargeting the expected head after approval returns the set to `needs_review`.
4. **One classification decides whether approval is required**: a protected origin (anything but
   `author` or the trusted `migration` replay), a destructive operation (`retire_entity`,
   `retire_event`), a bulk change (more than 25 operations), or a revert. Any reason requires an
   explicit approval.
5. **The gate runs inside the canonical commit transaction.** The repository recomputes the
   effective operations from the stored revisions and decisions, checks them against the envelope,
   and evaluates the gate before validation. A service, import, or direct repository caller cannot
   skip it. A low-impact author commit records its own approval and both transitions in that same
   transaction.
6. **Dedicated product actions carry an explicit acknowledgement.** Retire and revert endpoints
   accept `acknowledge_high_impact`. Without it the request is parked as a proposal in the review
   queue and the API answers `409 review_required`. A protected-origin proposal can never be
   self-acknowledged through those endpoints.

Schema changes are governed separately (immutable schema versions published by a project
administrator) and are not canon mutations.

## Alternatives considered

### Keep origin-only gating

Simplest, but a destructive or bulk author change would commit with no distinct approval, which
ADR-012 decision 1 forbids.

### Mutate operations in place when a reviewer edits them

Fewer rows, but it destroys the evidence of what was originally proposed and what a decision was
made against.

### Enforce the gate only in the service

Easier to test, but an import path or a second writer could bypass it. The repository transaction
is the only place every writer passes through.

## Consequences

### Positive

- No writer can commit canon that needs approval without one, and approval cannot outlive a change
  to what was approved.
- Review evidence (proposed, edited, decided, approved, committed) is complete and append-only.
- Clients get one stable signal (`409 review_required` with the proposal id) for "this needs review".

### Negative

- Retire and revert requests need an extra acknowledgement field; unacknowledged requests create
  proposals that someone must resolve.
- The bulk threshold is a fixed constant, not yet a project setting.

### Follow-up work

- Phase 2.5: grouped, confidence-tiered proposals and bulk-safe review actions build on this model.
- Phase 3: record/dependency-level rebasing replaces set-level retargeting for stale proposals.
- Consider making the bulk threshold and the destructive set project policy.

## Validation

`tests/unit/test_review_policy.py`, `tests/application/test_review_workflow.py`,
`tests/integration/test_review_workflow.py`, `tests/contract/test_canon_mutations.py`, and the
Hypothesis state machine in `tests/unit/test_properties.py` (checked against deliberately broken
invariants). Revisit if reviewers routinely approve without reading, which would mean the
classification is too broad.
