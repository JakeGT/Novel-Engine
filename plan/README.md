# Novel Engine implementation plan

This directory is the maintained planning source for Novel Engine. It turns the original concept in `architecture.md` into an implementable product and engineering programme.

## How to use this plan

Read in this order:

1. [Product and scope](01-product-and-scope.md) — who the product serves and what the first release must do.
2. [Target architecture](02-target-architecture.md) — system boundaries, control flow, and architectural constraints.
3. [Domain and temporal model](03-domain-and-temporal-model.md) — canon, beliefs, events, branches, and provenance.
4. [AI and retrieval](04-ai-and-retrieval.md) — context assembly, generation, extraction, validation, and evaluations.
5. [Storage and integrity](05-storage-and-integrity.md) — persistence responsibilities, migrations, and recovery.
6. [API and frontend](06-api-and-frontend.md) — product surfaces and contracts.
7. [Security and operations](07-security-and-operations.md) — privacy, observability, jobs, and deployment.
8. [Testing strategy](08-testing-and-quality.md) — test pyramid and quality gates.
9. [Delivery roadmap](09-delivery-roadmap.md) — ordered phases with exit criteria.
10. [Backlog](10-backlog.md) — implementable epics and stories.
11. [Decisions and questions](11-decisions-and-questions.md) — provisional choices that require owner confirmation.
12. [Current gap analysis](12-current-gap-analysis.md) — how the prototype maps to the target and where refactoring starts.
13. [Consistency system](13-consistency-system.md) — deterministic, incremental, and model-assisted continuity validation.
14. [Distribution and invite-only hosting](14-saas-platform.md) — Authentik OIDC identity, invitations, tenancy, self-hosting, provider keys, and packaging. The legacy filename is retained for stable links.
15. [Prose editor and memory](15-prose-editor-and-memory.md) — canonical manuscript versions, editing, external integrations, and prose retrieval.
16. [OpenCodeReview adaptation](16-open-code-review-adaptation.md) — deterministic review bundles and bounded agents for long-form prose.
17. [Multimodal context and media memory](17-multimodal-context.md) — assets, cross-modal retrieval, capability-aware model context, and media consistency.
18. [Phase 0 todo](phase-0-todo.md) — execution-ready stabilization checklist, dependencies, and exit evidence.
19. [API surface](api-surface.md) — canonical `/api/v1` routes and the problem error contract.
20. [Phase 0 baseline](phase-0-baseline.md) — pre-refactor API, storage, workflow, and worktree inventory.
21. [Phase 1 todo](phase-1-todo.md) — execution-ready identity, tenancy, distribution, provider-credential, and isolation checklist.
22. [Phase 2 todo](phase-2-todo.md) — execution plan and live completion dashboard for governed schema, revisioned canon, mutation commits, evidence assets, authoring UI, and archive recovery.
23. [Author rules, canon queries, and derived views](18-author-rules-canon-queries-and-derived-views.md) — story invariants, explanation/history queries, reader disclosure, scene contracts, derived projections, MCP, and suppressions.

Architectural decisions that become settled should be recorded in `decisions/` using [the ADR template](decisions/000-template.md).

## Planning principles

- **Canon is deterministic.** Models propose; domain code validates and commits.
- **Perspective is explicit.** Objective truth, a character's belief, and narrated assertion are different records.
- **History is retained.** Changes create traceable versions instead of silently erasing prior state.
- **Retrieval is inspectable.** Every context item has a reason, source, scope, and token cost.
- **Derived stores are disposable.** The PostgreSQL journal and normalized canon can reconstruct Neo4j, search indexes, summaries, and purpose-built read models.
- **Author intent is executable data, not code.** Safe declarative invariants and scene contracts are versioned, testable, and evaluated without allowing project-supplied executable rules.
- **One vertical slice at a time.** Each delivery phase ends in a user-observable workflow, not only infrastructure.
- **Local development is first-class.** A contributor or agent should be able to validate most changes without paid APIs.
- **Open source and hosted use share one core.** Self-hosted local identity and invite-only portfolio identity enter the same scoped application contracts.
- **Tenancy is explicit; commerce is absent.** Hosted workspace isolation is foundational, while billing and platform-funded inference are not release dependencies.
- **Consistency is tiered.** Cheap deterministic checks run continuously; paid model audits escalate only where they add value.
- **Context is multimodal and authority-aware.** Text, images, audio, and video retain original evidence, explicit links, provenance, branch/time/POV scope, and honest provider fallbacks.

## Status vocabulary

| Status | Meaning |
| --- | --- |
| Proposed | Direction is documented but has not been approved. |
| Accepted | Owner has confirmed the direction; record it as an ADR. |
| In progress | Implementation is underway. |
| Implemented | Acceptance criteria and quality gates pass. |
| Deferred | Intentionally excluded from the current release. |

Core domain decisions are recorded under `decisions/`. ADR-008 proposes the open-source/free distribution pivot, ADR-009 accepts invite-only tenancy through shared portfolio identity (Authentik), ADR-010 accepts multimodal context with a shared embedding-space boundary, ADR-012 makes PostgreSQL authoritative canon with Neo4j as a replayable projection (superseding ADR-011), and ADR-013 accepts warning-default declarative story invariants while making built-in integrity checks permanently blocking. Licensing and governance remain owner decisions in `11-decisions-and-questions.md`.

## Current repository baseline

Implemented or partially implemented:

- Phase 0 lifecycle, ports, migrations, deterministic tests, seed/reset, stable `/api/v1`, and
  PostgreSQL/pgvector + Neo4j infrastructure
- Phase 1 OIDC/local identity, invitation-only tenancy, scoped stores, encrypted OpenRouter BYOK,
  object storage, project transfer, and the minimal authenticated React shell
- Phase 2 typed entity/fact/belief/event domain, immutable project schema versions/runtime
  compilation, atomic PostgreSQL journal + normalized canon, history/provenance/tags/revert APIs,
  media identity/lifecycle, canonical archive v3, and a replayable Neo4j projection
- a minimal TypeScript project/entity authoring shell backed by the generated `/api/v1` client

Not yet product-ready:

- branch-overlay closure and a structured fuzzy/invented-calendar `WorldTime` model
- mutation-state transitions, per-operation review, high-impact author-operation classification,
  and record/dependency-level rebasing
- full schema/review/media UI, automated accessibility and browser coverage, generalized evidence
  provenance, richer seed fixtures, and durable archive import/audit status
- projection query-equivalence checks, performance budgets, and clean-host/backup rehearsals
- manuscript import, extraction/entity-resolution evaluation, story planning, scene generation,
  consistency workflows, temporal branching, and production operations
- declarative story invariants, reader disclosures, narrated assertions/belief propagation, scene
  contracts, impact preview, spoiler-safe exports, purpose-built temporal views, and MCP transport
