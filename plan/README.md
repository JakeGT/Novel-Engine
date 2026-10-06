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
17. [Phase 0 todo](phase-0-todo.md) — execution-ready stabilization checklist, dependencies, and exit evidence.
18. [API surface](api-surface.md) — canonical `/api/v1` routes, deprecated aliases, and the problem error contract.
19. [Phase 0 baseline](phase-0-baseline.md) — pre-refactor API, storage, workflow, and worktree inventory.
20. [Phase 1 todo](phase-1-todo.md) — execution-ready identity, tenancy, distribution, provider-credential, and isolation checklist.

Architectural decisions that become settled should be recorded in `decisions/` using [the ADR template](decisions/000-template.md).

## Planning principles

- **Canon is deterministic.** Models propose; domain code validates and commits.
- **Perspective is explicit.** Objective truth, a character's belief, and narrated assertion are different records.
- **History is retained.** Changes create traceable versions instead of silently erasing prior state.
- **Retrieval is inspectable.** Every context item has a reason, source, scope, and token cost.
- **Derived stores are disposable.** Neo4j plus the mutation journal can reconstruct search indexes and summaries.
- **One vertical slice at a time.** Each delivery phase ends in a user-observable workflow, not only infrastructure.
- **Local development is first-class.** A contributor or agent should be able to validate most changes without paid APIs.
- **Open source and hosted use share one core.** Self-hosted local identity and invite-only portfolio identity enter the same scoped application contracts.
- **Tenancy is explicit; commerce is absent.** Hosted workspace isolation is foundational, while billing and platform-funded inference are not release dependencies.
- **Consistency is tiered.** Cheap deterministic checks run continuously; paid model audits escalate only where they add value.

## Status vocabulary

| Status | Meaning |
| --- | --- |
| Proposed | Direction is documented but has not been approved. |
| Accepted | Owner has confirmed the direction; record it as an ADR. |
| In progress | Implementation is underway. |
| Implemented | Acceptance criteria and quality gates pass. |
| Deferred | Intentionally excluded from the current release. |

Core domain decisions are recorded under `decisions/`. ADR-008 proposes the open-source/free distribution pivot and ADR-009 accepts invite-only tenancy through shared portfolio identity (Authentik). Licensing and governance remain owner decisions in `11-decisions-and-questions.md`.

## Current repository baseline

Implemented or partially implemented:

- FastAPI application and graph CRUD routes
- Neo4j node, edge, text search, and vector search access
- custom schema registry stored in JSON
- explicit entity tags and graph traversal policies
- character anchor/questionnaire/graph-extraction workflow
- a minimal ChromaDB wrapper

Not yet product-ready:

- stable domain and API representations
- reviewable canon mutation workflow
- temporal and branch semantics
- story planning and scene generation
- reliable vector synchronization
- frontend workflows
- isolated tests, migrations, background jobs, authentication, and operational telemetry
