# Dependency-ordered backlog

Each item is sized to be independently reviewable. IDs are stable references for issues and agent assignments. `P0` blocks later architecture; `P1` is release-one essential; `P2` is valuable but deferrable.

## Foundation

Phase 0 execution details, sub-items, dependencies, and exit evidence are maintained in [`phase-0-todo.md`](phase-0-todo.md).

- **FND-001 · P0 — Runtime lifecycle:** create Neo4j, vector, embedding, and AI clients through application lifespan and injected dependencies. Acceptance: importing `backend.main` performs no network connection or persistent write.
- **FND-002 · P0 — Domain ports:** define repository, journal, vector, AI, clock, and ID interfaces with in-memory fakes. Acceptance: one application workflow runs entirely in memory.
- **FND-003 · P0 — Stable DTOs:** define versioned entity/edge request and response models. Acceptance: create/read/search/update share one representation and OpenAPI examples.
- **FND-004 · P0 — Error contract:** implement typed domain errors and API problem responses. Acceptance: validation, not-found, conflict, provider, and persistence cases are covered.
- **FND-005 · P0 — Migrations:** add explicit graph constraint/index migrations and version check. Acceptance: apply twice safely and verify expected schema.
- **FND-005A · P0 — PostgreSQL foundation:** add PostgreSQL configuration, migrations, health, transaction boundary, and disposable test fixture.
- **FND-006 · P0 — Test layout:** create deterministic unit/application suites and disposable integration fixtures. Acceptance: default tests need no services or secrets.
- **FND-007 · P0 — Reproducible toolchain:** pin supported runtimes, lock dependencies, and configure CI. Acceptance: clean Linux runner passes static checks and unit tests.
- **FND-008 · P0 — Prototype repairs:** fix property-shape mismatch, route specificity, world-service errors, character bio flow, rewritten edge targets, temporal edge persistence, and update-label behavior. Acceptance: each defect has a regression test.
- **FND-009 · P0 — Seed environment:** create deterministic PostgreSQL/Neo4j/vector/object fixtures for two tenants and the reference world, with reset/reseed commands safe only outside production.

## SaaS platform

- **SaaS-001 · P0 — Accounts and sessions:** signup, verification policy, login, secure session lifecycle, recovery, and deletion states.
- **SaaS-002 · P0 — Tenant and membership:** tenant/project ownership plus owner/editor/viewer authorization baseline.
- **SaaS-003 · P0 — Isolation enforcement:** tenant-scoped PostgreSQL, Neo4j, vector, object, job, and export adapters with adversarial tests.
- **SaaS-004 · P0 — Subscription boundary:** plans, entitlements, subscription state, and verified idempotent webhook processing.
- **SaaS-005 · P0 — Credit ledger:** append-only grants, purchases, reservations, releases, reconciliations, refunds, and balance projection.
- **SaaS-006 · P0 — Provider credentials:** encrypted OpenRouter BYOK storage, rotation, revocation, worker access, and redaction.
- **SaaS-007 · P0 — Usage normalization:** requested route, upstream provider/model, input/output/cache usage, provider cost, and policy version.
- **SaaS-008 · P0 — Funded job execution:** choose BYOK/platform funding, reserve before execution, reconcile once, and recover failures.
- **SaaS-009 · P1 — Autonomy policy:** manual/assisted/delegated workflow decisions with mandatory-review categories and audit.
- **SaaS-010 · P1 — Abuse/spend controls:** rate limits, quotas, confirmation thresholds, anomaly alerts, and support overrides.

## Canon and schema

- **CAN-001 · P0 — Project aggregate:** introduce project ID and scope all repository methods.
- **CAN-002 · P0 — Core schema:** define typed core nodes/predicates, property schemas, endpoint constraints, and cardinality.
- **CAN-003 · P1 — Custom schema governance:** project-scoped custom types with validation and schema versions.
- **CAN-003A · P1 — Runtime typed schema compiler:** compile safe project extensions to cached JSON Schema/Pydantic validators for AI output and canon writes; never execute user code.
- **CAN-004 · P0 — Fact model:** implement reified temporal assertions plus query projections.
- **CAN-005 · P0 — Belief model:** implement holder-scoped assertions, confidence, source, and validity.
- **CAN-006 · P0 — Event model:** implement chronological events with participants, location, cause, and scene source.
- **CAN-007 · P0 — Provenance:** require actor/origin/source/revision on canon writes.
- **CAN-008 · P0 — Mutation journal:** append, validate, commit, recover, and verify checksums.
- **CAN-009 · P1 — Entity authoring API/UI:** typed list, search, create, edit, link, and retire workflows.
- **CAN-010 · P1 — Export/import:** versioned complete project archive and clean restore validation.

## Time, branches, and visibility

- **TMP-001 · P0 — Three-clock types:** world, narrative, and revision positions with explicit conversion limits.
- **TMP-002 · P0 — Temporal query:** active state at branch/tick using half-open intervals.
- **TMP-003 · P0 — Branch overlay:** fork, inherited reads, local mutation, and head revision.
- **TMP-004 · P1 — Branch comparison:** mutations from common ancestor and conflict categorization.
- **TMP-005 · P1 — Merge review:** author-resolved merge mutation set.
- **KNW-001 · P0 — Visibility policy:** public, private, secret, author-only, and character-accessible classification.
- **KNW-002 · P0 — Knowledge acquisition:** beliefs linked to perception/report/inference events.
- **KNW-003 · P1 — POV context gate:** deterministic filtering and leakage explanations.

## Retrieval and memory

- **CTX-001 · P0 — Context manifest:** durable typed package and source trace.
- **CTX-002 · P0 — Policy engine:** versioned purpose-specific structural retrieval policies.
- **CTX-003 · P1 — Mention resolver:** parse, validate, autocomplete, and repair stable entity mentions.
- **CTX-004 · P0 — Vector port:** versioned documents, filters, health, deletion, and rebuild contract; implement pgvector first unless ADR changes it.
- **CTX-005 · P0 — Index outbox:** idempotent derived updates and lag reporting.
- **CTX-006 · P1 — Scene chunker:** summary, event-block, and full-scene layers.
- **CTX-006A · P1 — Previous-prose units:** preceding passages, nearby scene text, event blocks, summaries, voice exemplars, and thread-linked passages.
- **CTX-007 · P1 — Hybrid ranker:** explicit, graph, semantic, two-clock temporal decay, author importance, continuity impact, narrative/thread, and diversity signals.
- **CTX-008 · P1 — Token budgeter:** hard/soft categories and deterministic truncation.
- **CTX-009 · P1 — Context preview API/UI:** inspect, pin, exclude, and compare packages.
- **CTX-010 · P0 — Retrieval evaluations:** relevance, omission, leakage, and branch correctness fixtures.

## Story and scene workflows

- **STY-001 · P1 — Story hierarchy:** story, act, arc, beat, and scene-plan CRUD.
- **STY-002 · P1 — Narrative threads:** open/progress/resolve with foreshadow/payoff links.
- **STY-003 · P1 — Scene intent:** POV, location, participants, goal, conflict, rule pressure, required and intended state.
- **STY-004 · P1 — Readiness rules:** explain unmet prerequisites and suggest author-controlled bridge plans.
- **SCN-001 · P0 — Job model:** persistence, leases, stages, progress, cancellation, retry, and usage.
- **SCN-002 · P0 — AI provider port:** structured generation, prose, embeddings, normalized errors/usage, fake adapter, and OpenRouter adapter.
- **SCN-002A · P1 — Local inference connector:** authenticated device registration, outbound job channel, secure updates/revocation, capability discovery, and OpenAI-compatible Ollama/llama.cpp adapter.
- **SCN-003 · P1 — Prompt registry:** versioned templates, input/output schemas, rendering tests.
- **SCN-004 · P1 — Scene studio:** plan, editor, mentions, context, variants, findings, and review.
- **SCN-005 · P1 — Scene versions:** immutable text, parent lineage, context and model references.
- **SCN-006 · P1 — Generation workflow:** preview-to-job-to-draft with budget/cancellation.
- **SCN-007 · P1 — Consistency rules:** deterministic findings for time, knowledge, location, resources, and prerequisites.
- **SCN-008 · P2 — Model critic:** evidence-citing soft checks for voice and narrative intent.

## Consistency and correction

- **CON-001 · P0 — Consistency run/finding model:** scopes, revisions, profiles, stages, evidence, status, and staleness.
- **CON-002 · P0 — Constraint packet:** deterministic generation preflight for location, time, resources, rules, prerequisites, and POV knowledge.
- **CON-003 · P0 — Temporal/spatial rules:** chronology, age/duration/travel, simultaneity, and location availability.
- **CON-004 · P0 — Entity/state rules:** identity details, possession, injury/state, lifecycle, relationship, and goal continuity.
- **CON-005 · P0 — World-rule engine:** declarative requirements, ranges, incompatibilities, implications, and exceptions from governed schema.
- **CON-006 · P0 — Knowledge/leakage rules:** POV knowledge/beliefs versus isolated author truth and secret access.
- **CON-007 · P1 — Incremental dependency index:** rule/source/prose fingerprints, invalidation, cache, and stale finding behavior.
- **CON-008 · P1 — Task-specific model critics:** typed POV, motivation/voice, physical/causal, world-rule, and narrative-thread passes.
- **CON-009 · P1 — Consistency profiles:** fast/balanced/thorough/custom cost, latency, and blocking policies.
- **CON-010 · P1 — Finding review UI:** evidence comparison, dismiss rationale, manual fix, agent patch, and canon proposal actions.
- **CON-011 · P1 — Deep audit jobs:** chapter/act/project recomputation, estimates, cancellation, and correction plans.
- **CON-012 · P0 — Consistency evaluations:** labeled defects, leakage, false positives, detection/latency/cost reporting.
- **CON-013 · P0 — Deterministic bundle prototype:** implement prose review units, dependency graph, rule-specific grouping, token splitting, coverage manifest, preview, and fallbacks; benchmark against full-context and vector-only critics.

## Extraction and commit

- **EXT-001 · P0 — Extraction schema:** evidence-backed events/facts/beliefs/state/thread operations.
- **EXT-002 · P1 — Entity resolution:** deterministic IDs, candidate matches, and ambiguity review.
- **EXT-003 · P0 — Mutation validator:** schema, references, cardinality, time, branch, and permissions.
- **EXT-004 · P1 — Mutation review UI:** accept/edit/reject individual operations and rerun validation.
- **EXT-005 · P0 — Commit service:** optimistic head, idempotency, journal, Neo4j transaction, outbox.
- **EXT-006 · P1 — Recovery:** reconcile pending/failed commits and requeue derived updates.
- **EXT-007 · P1 — End-to-end loop:** commit scene, advance state, update threads, and inspect resulting history.

## Operations and product quality

- **OPS-001 · P0 — Structured diagnostics:** correlation IDs, redaction, workflow/job/provider metrics.
- **OPS-002 · P1 — Cost controls:** estimates, caps, confirmation thresholds, and per-job actuals.
- **OPS-003 · P1 — Backup/restore:** automated operation, verification, and runbook.
- **OPS-004 · P0 — Hosted identity operations:** production session security, verification/recovery, roles, audit, and development identity adapter.
- **OPS-005 · P1 — Privacy controls:** retention and deletion for drafts/model artefacts/log content.
- **OPS-006 · P1 — Health/degradation:** readiness and clear graph/vector/provider/worker status.
- **QLT-001 · P0 — Reference world:** synthetic cross-layer fixture with branches, secrets, and false beliefs.
- **QLT-002 · P1 — Release workflow E2E:** automate the complete release-one loop.
- **QLT-003 · P1 — Evaluation baseline:** approved AI/retrieval metrics and comparison report.
- **QLT-004 · P1 — Accessibility:** keyboard and automated accessibility gates for core workspaces.

## Suggested first implementation sequence

`FND-005A → FND-001 → FND-002 → FND-003 → FND-006 → FND-008 → SaaS-001 → SaaS-002 → SaaS-003 → SaaS-005 → CAN-001 → CAN-002 → CAN-008 → QLT-001 → CON-001 → CON-002`

This sequence creates safe seams and tests before changing durable data semantics.
