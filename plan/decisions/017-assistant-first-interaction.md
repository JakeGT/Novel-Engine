# ADR-017: Assistant-first interaction and progressive disclosure

- Status: Proposed. The principles are the owner's direction of 2026-10-10; the mechanisms below
  await the owner's acceptance.
- Date: 2026-10-10
- Owners: project owner, implementer
- Related: ADR-012, ADR-013, ADR-014; `19-assistant-first-experience.md`; `audit-2026-10-10.md`;
  backlog `AST-EXP-*`

## Context

Through Phase 2 the product exposed canon in the shape it is stored, and each planned phase added
expert concepts with a manual editor. AI was specified as a prose drafter, an extractor, and a
critic. The owner found the result laborious and the learning curve steep, and directed that the
system be easy to start, that everything be automatable through conversation or human-in-the-loop
iteration, and that advanced features be kept and learned over time rather than removed.

## Decision

1. **One loop for every capability:** Describe → Interview (optional) → Draft → Review → Canon,
   with unfinished work surfaced as to-dos that reopen the loop.
2. **Two paths, always:** every capability has an assistant path and a by-hand path. The
   assistant path is the default entrance; the by-hand path is one action away and works with no
   AI provider.
3. **The assistant never writes canon.** It produces proposals through the existing review
   workflow (ADR-014). This ADR does not relax any gate.
4. **Five levels of disclosure** (Start, Shape, Plan, Write, Control) decide what is shown and
   offered by default. No feature is gated; every feature is reachable by search and by asking.
5. **Defaults make advanced concepts optional:** one main branch, unspecified world time, the
   author's own point of view, warning-only story rules, and the starter types. No Level 1–4 task
   requires choosing among them.
6. **Plain language is part of the contract.** Author-facing names are defined once
   (`frontend/src/lib/words.ts`); internal terms appear only under technical details.
7. **A definition of done** (document 19) applies to every feature from Phase 2.5 onward.
8. **Story planning is created by conversation**, and plan records are canon-governed intent,
   distinct from fact.

## Alternatives considered

### Keep manual-first and add an assistant later

Preserves the roadmap. Rejected: it keeps the steep first hour, and retrofitting an assistant onto
screens designed for manual entry produces the bolt-on the owner is objecting to.

### A chat-only product

Simplest to explain. Rejected: a story bible has to be browsed, compared, and corrected; chat is a
poor surface for review at scale, and it removes the by-hand path that makes the product usable
without a provider.

### Hide advanced features behind a mode switch

A "simple/advanced" toggle is easy to build. Rejected: it makes the advanced product a cliff
rather than a slope, and authors would have to know they need it before seeing why.

### Let the assistant write low-risk canon directly from the start

Fewer clicks. Rejected as the default: trust has to be earned, and silent writes are how a story
bible becomes untrustworthy. Delegation stays an option the author turns on later.

## Consequences

### Positive

- A new author can produce reviewed canon from a paragraph, without learning the data model.
- One interaction to learn; later features reuse it.
- Advanced features gain a reason to appear instead of a menu to be found in.

### Negative

- Every feature costs more: two paths, a level, a trigger, and tests of each.
- First impressions now depend on model quality and on the author having a provider key.
- More interface text to keep honest and consistent.

### Follow-up work

- Roadmap phases 2.5–2.7 and the `AST-EXP-*` backlog items.
- A labelled evaluation set for drafted entries, links, and plans.
- The owner decision on how a new author gets a working assistant (audit, decision 1).

## Validation

The measures in document 19: time to first accepted entry, premise-to-linked-entries, first plan,
the plain-language test over rendered screens, both paths tested for every capability, and the
kept-rate of drafted changes. Revisit if authors routinely abandon interviews, discard most of
what the assistant drafts, or stay at Level 1–2 on long projects because later levels never
become visible at the right moment.
