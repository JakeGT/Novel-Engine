# Delivery roadmap

## Delivery strategy

Build vertical slices in dependency order. Each phase must leave the application more usable and the repository more testable. Estimates are deliberately expressed as scope, not calendar promises; team size, deployment choice, and product answers will change elapsed time.

## Phase 0 — Stabilize the prototype

Goal: create a deterministic development baseline before expanding functionality.

Execution checklist: [`phase-0-todo.md`](phase-0-todo.md).

Work:

- choose supported Python/Node versions and add CI
- add PostgreSQL to local Compose and establish migration tooling
- split runtime/dev dependencies and produce reproducible locks
- move connection creation into FastAPI lifespan/dependency injection
- define graph, vector, AI, clock, and ID-generator ports
- standardize entity/edge DTOs and error responses
- repair route ordering and current character/world workflow defects
- move legacy tests into a marked integration suite
- add unit characterization for resolver, policies, summarizer, and schemas
- create disposable Neo4j integration fixtures
- replace implicit startup index creation with migrations
- make OpenAI and semantic embeddings optional at startup
- add safe non-production reset/reseed commands and the reference seed world

Exit criteria:

- clean checkout setup is documented and repeatable
- unit tests collect/run without PostgreSQL, Neo4j, embeddings, or API keys
- integration tests create and destroy their own database state
- no external connection or data write occurs on module import
- lint, unit tests, and frontend build run in CI
- current workflows have documented, stable responses

## Phase 1 — Identity, tenancy, distribution, and project foundation

Goal: make the stabilized prototype installable and establish invite-only hosted tenant boundaries before storing valuable manuscripts.

Work:

- documented Docker Compose self-hosted installation and upgrade path
- shared OIDC identity-provider port and secure application sessions for the portfolio deployment
- invite-only workspace membership with owner/editor/viewer authorization
- explicit local/single-user identity mode that hosted mode cannot enable
- workspace/project-scoped PostgreSQL repositories and audit records
- local secret configuration for OpenRouter and OpenAI-compatible endpoints
- model-usage normalization plus per-job token/cost estimates and caps
- backup, restore, export, and deterministic seed workflows
- offline fake-provider mode and local-model capability discovery
- contributor-facing configuration, diagnostics, and troubleshooting
- invitation, cross-tenant, key-redaction, backup, and upgrade tests

Exit criteria:

- a clean machine can install and start the supported stack from the documentation
- every hosted persistence path enforces workspace/project scope
- a portfolio identity receives no Novel Engine access without a valid invitation
- hosted mode refuses unsafe local-identity configuration
- API responses, logs, and exports never expose provider keys
- deterministic seed data boots a clean development environment
- a project survives backup/restore and complete archive export/import
- no core request depends on a billing API or project-funded inference service

## Phase 2 — Trustworthy canon

Goal: authors can safely create and maintain isolated project canon.

Work:

- project aggregate and project-scoped repositories
- fixed core schema and validated custom extensions
- canonical entity/fact/belief/event models
- revision and provenance model
- entity CRUD/search and relationship/fact editor APIs
- media-asset identity, immutable source versions, authority/provenance, explicit domain links, and export semantics
- mutation journal and atomic commit service
- author-originated mutation review path
- project shell and world/character editors
- basic export/import with schema version

Exit criteria:

- two projects cannot read or reference each other's data
- author edits produce audited, revisioned commits
- facts and beliefs are distinct in storage and UI
- entities can be retired without losing history
- project export round-trips into a clean environment

## Phase 3 — Time, branches, and knowledge

Goal: canonical queries are correct for a selected branch, world time, and point of view.

Work:

- world-time, narrative-order, and system-revision value objects
- temporal facts and belief intervals
- branch creation, overlay reads, comparison, and conflict model
- POV visibility/knowledge policy
- secret handling and common-knowledge rules
- branch/time/POV applicability for media assets, links, and reviewed observations
- branch/timeline selector in the UI
- state/history inspectors
- deterministic contradiction rules for time, cardinality, and knowledge

Exit criteria:

- reference-world invariant suite passes
- branch-local changes never leak to siblings
- false beliefs can drive POV context without being treated as canon
- flashback events preserve world chronology and narrative order
- conflicting merges require explicit resolution
- an agent may propose merge resolutions but cannot finalize a conflict without the configured human approval

## Phase 4 — Explainable context and derived memory

Goal: the author can preview and control reliable text and media scene context.

Work:

- versioned context policies and context-manifest model
- deterministic graph retrieval scoped by project/branch/tick/POV
- vector adapter interface and versioned chunk model
- indexing outbox, worker, and rebuild command
- secure media ingestion lifecycle with immutable originals, variants, segments, and processing status
- provider-neutral multimodal embedding port and generation-based vector migration/rebuild
- EmbeddingGemma 2 evaluation against MiniLM and a strong text-only baseline; select 768/512 only from project metrics
- scene summaries/event blocks/full-text indexing
- image-first, then audio/video cross-modal indexing with captions/transcripts as derived fallbacks
- previous-prose retrieval with semantic, two-clock recency, author importance, narrative proximity, continuity impact, and thread signals
- hybrid ranking, deduplication, diversity, and token budgeting
- context preview API and context inspector UI
- project media library and context cards showing authority, branch/time/POV scope, timestamps/regions, and delivery representation
- pin/exclude author controls
- retrieval and leakage evaluation suite

Exit criteria:

- every context item shows source and inclusion reason
- vector deletion/rebuild preserves canonical state and meets retrieval baseline
- stale index state is visible
- token budgets preserve hard constraints over soft style context
- curated POV leakage tests pass
- text-to-media and media-to-text retrieval meets the approved baseline
- incompatible vector generations cannot mix, and reindex/rollback is observable
- original media is never silently dropped when a selected model lacks the modality

## Phase 5 — Story planning, consistency, and scene studio

Goal: an author can plan and draft a scene using controlled multimodal context.

Work:

- story/arc/beat/scene-plan models and APIs
- narrative threads, requirements, intended changes, reveals, and payoffs
- scene readiness checks
- async job framework, progress stream, cancellation, and budgets
- provider-neutral generation gateway
- typed text/image/audio/video context parts and capability-aware OpenRouter/local adapter translation
- OpenRouter BYOK/local-provider routing and normalized usage records
- versioned generation prompts and fake adapter
- scene editor, entity mentions, context preview, and draft variants
- immutable scene versions and lineage
- Tier 0/1 deterministic consistency preflight/postflight
- multimodal consistency checks over reviewed media facts plus evidence-citing image/audio/video critic passes
- user-triggered Tier 2 model critics, including isolated POV secret-leakage validation, with contextual recommendations and opt-in budgeted automation
- consistency findings/correction review UI and incremental invalidation
- deterministic dependency grouping/coverage prototype inspired by OpenCodeReview

Exit criteria:

- a planned scene can be generated through an observable cancellable job
- draft lineage and exact context are inspectable
- manual editing works without an AI provider
- plan requirements and findings are visible beside the draft
- failures do not lose plans or manual prose
- deterministic consistency runs on each generation without paid inference
- provider attempts and actual usage are recorded exactly once
- context manifests reproduce whether original media or a derived fallback reached the provider

## Phase 6 — Extraction, review, and commit loop

Goal: a drafted scene can safely evolve canon.

Work:

- event/fact/belief extraction contract
- caption/transcript/visual/audio observations may propose evidence-backed mutations through the same review contract
- evidence spans and entity-resolution workflow
- mutation-set validation and status machine
- per-operation accept/edit/reject UI
- optimistic head check and canonical commit
- derived indexing after commit
- consistency rerun and thread progression
- repair/recovery for partially failed commits
- end-to-end workflow evaluations

Exit criteria:

- no model operation writes canon directly
- every accepted mutation links to scene evidence and review decision
- committing twice is idempotent
- stale reviews receive a conflict rather than overwriting newer state
- full release-one workflow passes from a clean project

## Phase 7 — Open-source release hardening

Goal: make user data dependable and operation understandable.

Work:

- portfolio OIDC, session, invitation, and authorization hardening
- backup/restore automation and runbooks
- migration compatibility tests
- opt-in telemetry, cost controls, diagnostics, and privacy settings
- performance profiling and query/index tuning
- accessibility and keyboard workflow audit
- dependency/security scanning
- onboarding, sample project, and recovery UX
- Tier 3 scheduled/on-demand chapter, act, and project audits
- reproducible release artefacts, upgrade notes, contributor/security policies, and license notices
- direct OpenAI-compatible local inference route and provider capability diagnostics
- media codec/scanner sandboxing, quotas, provider-retention/deletion behavior, orphan cleanup, and multimodal backup/restore hardening

Exit criteria:

- release quality gates in `08-testing-and-quality.md` pass
- recovery drills and upgrade tests pass
- known data-loss/security risks are resolved or explicitly accepted
- beta users can complete the workflow without developer assistance
- the self-hosted profile operates without a project-operated cloud dependency

## Later options

- richer branch merge/rebase tools
- generative image/audio/video creation and editing (multimodal retrieval/context is in release one)
- richer local-model capability negotiation and offline workflows
- real-time collaborative editing, presence, and comments if not included in release one
- advanced graph/timeline visualization
- manuscript-wide revision and continuity reports
- importers for common writing tools
- agentic planning assistants operating strictly on proposals
- public demo or managed multi-user hosting, with a separate funding/privacy/operations decision
- open signup or public community hosting beyond the invite-only portfolio deployment

## Dependency chain

```text
stable boundaries
  -> OIDC identity + invite-only tenancy + self-hosted packaging
    -> project-scoped canon + journal
      -> time/branch/knowledge semantics
        -> explainable text/media context + indexing
          -> planning + consistency + generation jobs
            -> extraction + review + commit
              -> open-source release hardening
```

Skipping an earlier semantic layer creates expensive ambiguity later. In particular, generation should not precede a stable review/commit model, and branching should not be bolted onto unscoped mutable graph records.
