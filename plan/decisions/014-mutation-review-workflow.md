# ADR-014: Mutation review workflow and the high-impact gate

- Status: Accepted by the project owner on 2026-10-10 (implemented in Phase 2)
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

## Amendment (2026-10-18): keep the safe ones for me (AST-EXP-015, OSS-007)

The author may let some of the assistant's suggestions be approved for them. Nothing in the
classification above changes: a model suggestion still needs an explicit approval, and that
approval is still a decision in the journal. What is new is who records it.

1. **Opt-in, per story, per kind.** Off by default. Three kinds exist, all purely additive: a new
   entry whose name and nicknames match no existing entry's name or nickname (compared without
   case or spacing); details for an entry that has none of its own (marks such as tags,
   importance and trust do not count, and a kept change may not add, change or drop one); a new
   fact that is not a secret and names no one and nothing a secret is held about. A kind can be
   switched on only after the author approved suggestions holding it by hand three times
   (counted when they commit in the story itself; approvals in a what-if do not count). A
   workspace owner can forbid it for every story. Changing a story's rules and forbidding it for
   the workspace are written to the workspace audit log (`delegation.rules_changed` with the
   kinds before and after, `delegation.workspace_forbidden` with who did it).
2. **Everything else always waits:** removals, replacements, ending or amending anything, beliefs,
   events, the plan, rules, pictures, anything a check warns about or a blocking rule stops,
   imports and readings of a text, reverts, suggestions made before the story last changed,
   anything in a what-if, more than 12 changes at once or more than 50 kept in a day (counting
   those still on their way in, and counted again after marking, so two decided at once cannot
   both pass), suggestions through the general API, and other assistants connected with a key —
   always: they only ever suggest, and a story cannot allow them (the setting is refused). A
   suggestion is kept whole or not at all. The decision is pure
   (`backend/domain/delegation.py::decide`) and checks where it came from, then what it changes,
   then what checks say, so the reason shown is the one that matters most.
3. **Through the ordinary path.** A kept suggestion is approved with the note "Kept for you under
   your rule “…”." (the review decision history shows) and committed by `MutationService.commit`.
   A `pending` record marks it first; the commit transaction reads the story's and workspace's
   settings rows `FOR SHARE` and refuses it (`kept_rule_off`) unless the rule is still on, so
   switching off stops one already on its way in. When the check passes, the same transaction marks
   the record `kept` with its revision, so a switch-off waiting on that lock finds nothing to send
   back. Switching off also sends anything left approved but uncommitted back to the review queue:
   the suggestion is moved back first and its record rewritten only after, so a commit that won
   the race is never relabelled as waiting, and a kept record is never counted as approved by
   hand. A suggestion that waits keeps its reason
   (`delegation_records`, migration 0042).
4. **Listed and reversible.** Every kept suggestion is listed under Review → Kept for you and marked
   in history; Undo proposes the revert, which waits for review like any other.

Why opt-in by kind after hand approvals rather than a single switch: the research on approval
fatigue and auto-approve rules (GitHub auto-merge turning itself off when unreviewed changes
arrive; mail filters stating "when … then …"; agent tools that show the matched rule on the
decision) points the same way: narrow rules the person has already exercised, the rule named on
each decision, and limits on what can happen unseen.

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
